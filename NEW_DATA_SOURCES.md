# NetRange data sources

Where every row in the database came from, who is allowed to say so, and exactly
where the k-anonymity rule is enforced. Written so this is answerable without
reading eight files.

The short version: **three inbound paths, three trust models, one suppression
rule.** No path can publish a small-n cell, and there is no code path that skips
that rule for any source.

---

## 1. The three inbound paths at a glance

| | App scans | Widget scans | SMS reports |
|---|---|---|---|
| **Client** | Expo/React Native app | Any website visitor's browser | Any phone that texts the shortcode |
| **Endpoint** | `POST /api/scan` | `POST /api/widget-scan` | `POST /api/sms/inbound` |
| **Trust model** | Per-install device token | Origin check + IP throttle | Carrier webhook, session state machine |
| **Attribution** | `device_id` = install id | `widget_site_id` from `Origin` | `widget_site_id` only if opted in, else NULL |
| **Carries a signal level** | Yes (`signal_dbm`) | **No** — browsers cannot read it | No (self-reported 1–10) |
| **Carries a measured speed** | Yes | No — see §4 | No |
| **Stored in** | `scans` | `scans` | `sms_reports` |
| **Needs phone permission** | Yes | No | No |
| **Can be suppressed by a site owner** | No | No | Yes (`sms_reporting_enabled`) |

### Why SMS is not a `scans` row

The three paths carry genuinely different fields, and forcing them into one row
shape means either six permanently-NULL columns or a lossy encoding of the
answers. A scan has `signal_dbm`, `channel`, `bssid`. An SMS report has a
1–10 quality rating, a 1–10 frustration level, an optional gender, a carrier,
and free-text location. None of the first set exists in the second.

So there is a **shared parent / per-source detail** split:

- The **shared parent** is the site/owner relationship plus the suppression gate:
  `ingest_source` on `scans`, and `widget_site_id` on both `scans` and
  `sms_reports`.
- The **per-source detail** is the existing `scans` table for app + widget, and
  the dedicated `sms_reports` table for SMS.

This was the smaller change against `db/schema.sql`. A three-way detail table
split would have been larger and, for SMS, no more honest — the shape genuinely
differs.

### `source` vs `ingest_source` — read this before adding a column

`scans.source` already existed and means the **radio type**: `'cellular'` or
`'mobile'` (the default, for WiFi). It is *not* the ingestion channel.

Adding `'app' | 'widget' | 'sms'` to it, as originally specced, would have
overwritten that distinction and silently lost the wifi-vs-cellular information
on every existing row. So `ingest_source` is a separate column:

- `source` — which radio (`cellular`, `mobile`, or NULL when genuinely unknown,
  as for browser visitors).
- `ingest_source` — which system sent it (`app`, `widget`).

---

## 2. Trust models, and why they differ

These three are genuinely different problems, and using one scheme for all three
would be worse than three honest ones.

### App scans — `/api/scan`

Authenticated by a **per-install device token** issued by
`/api/register-device` and validated in `device_auth.require_device_token`.
There is a persistent device identity to attach a secret to, so that is what is
used. Registration is IP-throttled (`device_auth.registration_throttled`).

### Widget scans — `/api/widget-scan`

A website visitor has no app install, so **there is nothing to attach a device
token to**. Origin checking plus IP throttling is the correct substitute, not a
weaker copy of the same control.

**Its limits, stated plainly** (fuller reasoning in `owner_auth.origin_allowed`):

- `Origin` is trustworthy only because browsers refuse to let script set it.
  `curl`, a proxy, or a native client sets whatever it likes. This is a strong
  signal against drive-by third-party scripts and a weak one against a
  determined non-browser caller.
- It says "this page is on your domain", not "this page is your widget". Any
  XSS or compromised script on the registered site inherits the allowance.
- IP throttling bounds the damage but is shared across a whole NAT, so a busy
  venue can exhaust its own budget.

What makes that acceptable *for this data*: a widget row is already anonymous,
and it cannot be published until three distinct contributors agree. The worst an
origin-spoofing caller achieves is one low-value pseudonymous sample in a cell
that will most likely be suppressed. It is **not** acceptable for privileged
actions — so origin is never used for authorization anywhere. Origins only ever
select which `widget_site` a row is *attributed* to, and that id comes from the
server's own `widget_sites` lookup, never from the request body.

### SMS — `/api/sms/inbound`

Unauthenticated by design: the **carrier** is the caller, not the sender, and
Africa's Talking's basic webhook carries no shared secret. A forged request's
blast radius is a junk session, not a data leak, because:

- it can only add answers to a session that is keyed by an HMAC of the sender
  number, so an attacker cannot continue someone else's report without
  knowing their number;
- sessions expire after 30 minutes of inactivity;
- output is suppression-gated before anything is visible.

#### SMS attribution — carrier is not a location

`attribute_site()` decides which owner's dashboard a report belongs on. It
requires **both** a carrier match and coordinates that fall in the same
analytics cell as a registered site:

- **No coordinates → no attribution.** The report still feeds the
  network-wide aggregate. This is the common case, because forward geocoding
  is off by default (see section 6).
- **Coordinates that no registered site covers → no attribution.**
- **A site with no `lat`/`lon` cannot be attributed to at all**, so a venue is
  never shown traffic for a location it did not describe.

Matching on carrier name alone would have been wrong: a venue registering
"MTN" would have received every MTN report in the country, including its
competitors' venues and every road outside its own walls, presented on its
dashboard as its own. Under-attributing costs a quieter dashboard;
over-attributing hands a business its competitor's complaints.

---

## 3. Where k-anonymity suppression is enforced

**`analytics.MIN_DEVICES_PER_CELL = 3`** (in `backend/analytics.py`) is the
single constant. A cell is published only if at least 3 **distinct contributors**
reported into it. Suppression is a hard drop — the cell is withheld entirely,
not coarsened, rounded, or blurred. A coarsened cell is still a cell someone
lives in.

| Path | Enforcement point | Distinct contributor = |
|---|---|---|
| App scans | `analytics.aggregate_coverage_cells()` — `len(c["devices"]) < min_devices: continue` | `scans.device_id` (install id) |
| Widget scans | **The same** `aggregate_coverage_cells()`, called unmodified from `owner_site_stats()` | `scans.device_id` (the browser's random `contributor_id`) |
| Public map / `/api/data/*` | `aggregate_coverage_cells`, `carrier_comparison`, `daily_quality_trend` — all default to `MIN_DEVICES_PER_CELL` | as above |
| Owner dashboard | `owner_site_stats()` in `app.py` — **same function, same constant**, applied per site | as above |
| SMS reports | `sms_reports.aggregate_sms_cells()` — imports the same `MIN_DEVICES_PER_CELL` | distinct `sms_reports.phone_hash` |

The owner dashboard is deliberately **not** a separate, laxer path. It calls
`aggregate_coverage_cells` unmodified, so an owner can never see a cell the
public map would suppress, nor a cell the public map shows without suppression.

`sms_reports.aggregate_sms_cells` reuses the constant rather than defining a
second threshold. Gender and frustration broken down by location is exactly the
small-n breakdown suppression exists for, and giving SMS its own laxer floor
would be the single easiest way to leak it.

**Repeated reports from one person do not count toward the threshold** — counting
is over distinct `phone_hash` values, so one prolific texter cannot unlock a cell
on their own. `test_single_reporter_cell_is_suppressed` and
`test_repeated_reports_from_one_person_do_not_reach_the_threshold` pin this.

---

## 4. Data-integrity notes

**`navigator.connection.downlink` is not a speed measurement.** It is the
browser's guess at the connection class, derived from a previous page load or
the network's advertised speed. It is not comparable with a real measurement, so
it is stored in `downlink_estimate_mbps`, **not** `download_speed_mbps`.
`aggregate_coverage_cells` reads only `download_speed_mbps`, so estimates are
structurally excluded from the speed aggregates — otherwise a guess would
outvote real data in the per-cell average.

**No client-side speed test.** A real measurement needs a multi-megabyte
download, which would make the widget heavier than the thing it measures and
would work against the site embedding it.

**Browsers cannot read an SSID, BSSID, channel, or signal level.** Widget rows
store NULL for all of them. A row with no signal level is expected on that path
and is not a defect.

**Radio type is left NULL for widget rows.** A browser cannot know whether it
is on WiFi or cellular, and defaulting to `'mobile'` would assert a fact we do
not have (and would let widget rows pass wifi-only or cellular-only filters).

**No fabricated padding.** Nothing is written to make a path's row look like
another path's row.

---

## 5. Privacy properties

- **Phone numbers are never stored raw.** Only `phone_hash`, an HMAC-SHA256
  keyed with `SMS_PHONE_PEPPER`. A plain SHA-256 would not be acceptable:
  Ghanaian mobile numbers have ~10^8 possible values, so an unsalted digest is
  reversible by brute force in seconds by anyone holding the row. The keyed HMAC
  means a database read or a stolen backup does not reveal "this number rated
  this location 2/10 and is female". Same reasoning as the device-token hashing
  in `device_auth.py`.
- **`phone_hash` is never returned, logged, or displayed.** It is used only to
  count distinct contributors.
- **Gender is optional and genuinely skippable.** Accepts `skip` and similar;
  anything unrecognised becomes NULL rather than a guess. It is never inferred.
  `test_unrecognised_gender_stores_null_and_continues` pins this.
- **The widget stores no cookie, no fingerprint, no advertising id.** The
  `contributor_id` is a random UUID in `localStorage`, used solely as a
  distinct-contributor token. No canvas, font, or screen-metric fingerprinting.
  If storage is unavailable, no id is sent — which makes a cell *more* likely to
  be suppressed, never less safe.
- **The widget asks on every page load.** "Once per visit" in the consent copy
  is implemented literally: the decision lives in page memory only, with no
  persistence. A reload can ask again.
- **Nothing is sent before the visitor taps Allow.** The prompt is real DOM in a
  shadow root, and `No thanks` sends nothing and stores nothing.
- **The two consent buttons have identical visual weight.** No "recommended"
  styling, no pre-selected default, no guilt copy, and Allow is not the default
  focus target.
- **Owner accounts are separate from the admin dashboard.** Different secret,
  different password store, different guard. `app.secret_key` is
  `OWNER_SESSION_SECRET`; `DASHBOARD_SECRET` is the HTTP Basic password and
  signs nothing. The dashboard never used Flask sessions, so there is no shared
  key to reuse. Were the dashboard ever to become session-based, this pairing
  would need revisiting.

### Turning SMS reporting off

`sms_reporting_enabled` is **display-only, and off by default**.

- **Off means:** those reports stop appearing in that owner's dashboard and are
  no longer bundled for them.
- **Off does NOT mean:** collection stops, or received reports are deleted.

A single site owner cannot switch off data collection for the network-wide map,
which is a shared good — that would let any one venue suppress its area from
public coverage. Deletion is handled by request, not by a self-serve toggle.

---

## 6. How a venue gets access — it is not self-serve

Coverage reporting is a relationship, not a signup. A venue wants to know about
dead spots on its own WiFi; whether NetRange does that for free or for a fee
depends on the scale of the network, and that is a conversation, not a button.

**The path:**

1. `/get-access` shows an intent form. It writes a row to `venue_requests` and
   renders a prefilled email to `netrange@ashonlogist.website`. Scale is
   captured up front because free-vs-billed turns on it.
2. A person reads the request and decides. `/api/access-requests/<id>` records
   `approved` / `declined` with a note.
3. If the venue also registered an account via `/owner/register`, that account
   is **unapproved** and a person approves it separately via
   `/api/owners/<id>/approval`. Approving a request does not approve an
   account; the two are separate events.

**Creating an account and collecting data are separate events.** An account
records interest. It does not grant a collector.

**Enforcement is at the endpoint, not in the template.** Adding a domain and
toggling SMS reporting are both wrapped in `require_approval`, and the
dashboard withholds the snippet, so a pending owner cannot obtain a live
collector by ignoring the UI. A pending owner gets `owner-pending.html` and a
`403` on state changes.

**There is no self-service approval**, on purpose. If a venue could approve
itself the gate would be decorative.

**Auth for the admin API.** The decision routes use HTTP Basic auth
(`DASHBOARD_SECRET`) *and* require `X-Netrange-Admin: 1`. Basic auth alone is
not enough: a browser re-sends cached Basic credentials to the same origin even
from a page on another site, and an `owner_id` is a small sequential integer,
so a plain cross-site form POST could otherwise approve an arbitrary account.
A form cannot set a custom header, and a `fetch()` that tries triggers a CORS
preflight that fails for unlisted origins. Session CSRF is unchanged for owner
routes, where a session actually exists.

**CORS is scoped, not global.** Only `/api/widget-scan` and `/api/sms/inbound`
are cross-origin (`origins="*"`); every other route gets no CORS headers.
`allow_credentials` is off everywhere, which is what stops a browser attaching
Basic credentials or cookies cross-origin. The widget endpoint independently
checks `Origin` against registered domains, so the allowlist is the second
layer, not the only one.

---

## 7. Not yet live

- **Africa's Talking is not connected.** No credentials are configured and no
  real webhook payload has been observed, so the exact field names are
  unverified. `sms_reports.parse_inbound()` accepts several common spellings and
  `WEBHOOK_FIELD_MAP` is the single place to correct them. Set `AT_API_KEY` /
  `AT_SHORTCODE` and deploy before pointing the number at a user.
- **Forward geocoding is off by default.** `backend/geocoding.py` is a
  *reverse* geocoder — an offline table mapping lat/lon to Ghanaian place names
  — so given free text it has nothing to return. Forward geocoding is opt-in via
  `SMS_FORWARD_GEOCODER_URL`, because forwarding a person's location text to a
  third-party API is a disclosure the consent prompt does not mention. Without
  it, SMS reports store the raw text and still feed carrier/frustration
  aggregates.
- **The schema is live.** `db/schema_data_sources.sql` has been applied to
  Supabase, including `venue_requests`, the `network_owners.approved_at` /
  `approved_by` / `decision_note` columns, and `widget_sites.lat` / `lon`. The
  script is idempotent, so re-running it is safe. It remains separate from
  `db/schema.sql`.
- **A site must be verified before it collects anything.** Registering a domain
  is not permission to collect from it. See section 8.
- **The add-site form does not collect `lat`/`lon` or `sms_carrier` yet**, so a
  freshly registered site is not eligible for SMS attribution and cannot have
  SMS reporting enabled meaningfully. Both columns exist; only the UI is
  missing.
- **No email is sent server-side.** The intent flow records the row and hands
  the visitor a prefilled `mailto:`; there is no SMTP configuration. If a venue
  closes the mail client, the request is still in `venue_requests`.
- Required env vars, none with defaults: `OWNER_SESSION_SECRET`, `SMS_PHONE_PEPPER`.

---

## 8. Domain ownership verification

**The problem.** Approval answers "is this person a real venue?". It does not
answer "does this person control `example.com`?". An approved owner could
register any domain and collect that domain's traffic — a competitor's
included. Under a per-venue, scale-based pricing model that is not a nuisance
bug; it is the central promise failing, because a venue pays for reports about
*its* network and would be handed someone else's.

**The proof.** A DNS TXT record containing an unguessable per-site token:

    _netrange.<the-domain>   TXT   "<token>"

Adding one requires control of the domain's DNS, which is exactly the claim
being made. A fixed child name is used even for an apex domain, so the
instruction is uniform and the token never lands in the record a domain's
SPF/DKIM policy lives in.

The resolver is **DNS-over-HTTPS** (`dns.google/resolve`), not UDP/53: cloud
PaaS commonly blocks outbound port 53, and DoH needs no new dependency. A
resolver that is unreachable, slow, or returns anything unparseable is a
**failed** check, never a pass. A non-TXT answer is never a pass either — an A
record matching the token proves nothing.

**Where it is enforced.** `site_for_domain()` filters on
`verification_status = 'verified'` and is the only lookup the widget endpoint
uses. An unverified domain is invisible there, so no other route can be used to
collect from one. It is answered with the same `unregistered origin` `403` as a
domain nobody claimed, so the response does not confirm that a pending site
exists.

**Hand verification.** Some venues run a captive-portal network whose operator
will not add a record. `POST /api/sites/<id>/verification` lets a person verify
by hand, under the same Basic auth + `X-Netrange-Admin` guard as the other
admin decisions, and it unlocks collection exactly as the DNS path does. The
same route un-verifies, which stops collection immediately.

**Not just an obscurity check.** The token is 24 bytes of `secrets` entropy,
compared with `hmac.compare_digest`. The resolver is rate limited to one lookup
per site per 20s, since it is the only outbound call an owner can aim at will.
Minting a token is free and unscoped by cooldown. A failed check *keeps* the
token, because the usual cause is a typo or slow propagation and the owner
should retry the same value; a successful one clears it, since a live token has
no reason to outlive its use.
