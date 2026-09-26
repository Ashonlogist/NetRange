-- NetRange: data sources beyond the mobile app.
-- Run once in Supabase (SQL Editor -> New query -> Run).
-- Every statement is idempotent, so re-running is safe.
--
-- WHY ingest_source AND NOT source
-- ---------------------------------
-- `scans.source` already exists and is NOT the ingestion channel. It records
-- the radio type: 'cellular' (app.py writes it for cellular scans) or 'mobile'
-- (the default, for WiFi). Overwriting it with 'app'|'widget'|'sms' would
-- destroy the wifi-vs-cellular distinction that /api/scan, the export, and
-- every existing row depend on -- 32+ production rows would silently lose
-- their radio type.
--
-- So the ingestion channel gets its own column. `ingest_source` answers "which
-- system sent this row", `source` keeps answering "which radio".

alter table scans add column if not exists ingest_source text default 'app';
update scans set ingest_source = 'app' where ingest_source is null;

-- Browser-reported connection info, widget path only.
--
-- downlink_estimate_mbps is DELIBERATELY a different column from
-- download_speed_mbps, which holds real measurements. navigator.connection
-- .downlink is the browser's guess at the connection class, not a measurement
-- of throughput, and it is not comparable with one. Writing it into
-- download_speed_mbps would let an estimate outvote real measurements in the
-- per-cell average and quietly corrupt the speed layer of the map.
--
-- analytics.aggregate_coverage_cells reads only download_speed_mbps, so these
-- estimates are structurally excluded from the speed aggregates. They are kept
-- for connection-type context, not for averaging.
-- Which registered site a widget row came from.
--
-- NEEDED for the owner dashboard's core promise: "an owner sees ONLY the data
-- tied to their own widget_sites rows". Without this column a widget row is
-- attributable to nobody, because the browser sends no site id (the server
-- derives the site from the Origin header) and nothing else on the row records
-- it. Scoping would then be impossible to enforce at the query level, which is
-- the only place it is worth enforcing.
--
-- Set by the server from the Origin, never from the request body -- a
-- client-supplied site id would let any caller file rows against any owner.
alter table scans add column if not exists widget_site_id bigint;

create index if not exists scans_widget_site_idx on scans (widget_site_id, created_at desc);

alter table scans add column if not exists effective_type text;
alter table scans add column if not exists conn_type text;
alter table scans add column if not exists downlink_estimate_mbps double precision;
alter table scans add column if not exists rtt_ms double precision;

-- Widget scans and SMS-derived data are queried per-site far more often than
-- app scans, so the site id needs an index once the widget table exists.
create index if not exists scans_ingest_source_idx on scans (ingest_source);
create index if not exists scans_created_ingest_idx on scans (created_at desc, ingest_source);


-- ===================== NETWORK OWNER ACCOUNTS =====================
-- Password-only by design: these are self-serve, unverified accounts for site
-- owners, not identities. password_hash holds a scrypt digest from
-- werkzeug.security -- the hashing is a library's job, not ours.

create table if not exists network_owners (
    id            bigint generated always as identity primary key,
    username      text not null unique,
    password_hash text not null,
    created_at    timestamptz not null default now()
);

-- A registered origin allowed to post widget scans, and the dashboard it
-- feeds. sms_reporting_enabled defaults to false: a network owner has to opt
-- IN to receiving SMS reports about their location, never opt out of them.
create table if not exists widget_sites (
    id                    bigint generated always as identity primary key,
    owner_id              bigint not null references network_owners(id) on delete cascade,
    domain                text not null,
    label                 text not null default '',
    sms_reporting_enabled boolean not null default false,
    -- Which carrier this site's SMS reports are about, e.g. 'MTN'.
    --
    -- ADDITION beyond the original spec, which listed no carrier column. Without
    -- it there is no defensible way to decide which site an inbound SMS belongs
    -- to: SMS arrives with no site reference at all. Matching on a free-text
    -- label or guessing by location would mean handing one business a view of
    -- what people said about another business's area, so attribution is left
    -- NULL (general aggregates only) unless the owner names their carrier here.
    sms_carrier           text,
    created_at            timestamptz not null default now(),
    -- One site per owner per domain; a re-register updates the label instead
    -- of silently creating a second, differently-scoped site.
    unique (owner_id, domain)
);

create index if not exists widget_sites_owner_idx on widget_sites (owner_id);

alter table network_owners enable row level security;
alter table widget_sites enable row level security;


-- ===================== SMS REPORTING =====================
-- Multi-step state, one row per in-flight conversation. The raw phone number
-- is never stored here: `phone_hash` is an HMAC-SHA256 keyed with a server
-- secret (SMS_PHONE_PEPPER).
--
-- A plain SHA-256 would NOT be enough. Ghanaian mobile numbers have ~10^8
-- possible values, so an unsalted digest is reversible by brute force in
-- seconds by anyone holding the row. The keyed HMAC means a database read --
-- or a stolen backup -- does not hand over "this number rated this exact
-- location 2/10". This is the same reasoning as the device-token hashing in
-- device_auth.py.

create table if not exists sms_sessions (
    id               bigint generated always as identity primary key,
    phone_hash       text not null unique,
    current_step     text not null default 'quality',
    answers         jsonb not null default '{}'::jsonb,
    widget_site_id   bigint references widget_sites(id) on delete set null,
    started_at       timestamptz not null default now(),
    last_activity_at timestamptz not null default now()
);

create index if not exists sms_sessions_activity_idx on sms_sessions (last_activity_at);

alter table sms_sessions enable row level security;


-- A completed report. Deliberately NOT a `scans` row.
--
-- The three inbound paths carry genuinely different fields, and padding one
-- row shape with NULLs for the others is how you end up with a "gender" column
-- on WiFi coverage data. A report has a quality rating, a frustration level, an
-- optional gender, a carrier, and free-text location; a scan has none of those
-- and instead has signal_dbm/channel/bssid. Forcing SMS into `scans` would
-- have meant either 6 always-NULL columns or a lossy encoding of the answers.
--
-- So: shared parent = the site/owner relationship and the k-anonymity gate;
-- per-source detail = this table for SMS, the existing `scans` for app+widget.
--
-- phone_hash is a foreign-ish reference to sms_sessions.phone_hash, never a raw
-- number, and it is deliberately NOT the same row as the demographics in
-- spirit: the hash lives here, but it is only ever used to count *distinct
-- contributors* for the k-anonymity threshold. It is never returned, logged, or
-- displayed.

create table if not exists sms_reports (
    id               bigint generated always as identity primary key,
    widget_site_id   bigint references widget_sites(id) on delete set null,
    phone_hash       text not null,
    quality_rating   integer check (quality_rating between 1 and 10),
    frustration      integer check (frustration between 1 and 10),
    gender           text,
    carrier          text,
    location_text    text,
    lat              double precision,
    lon              double precision,
    created_at       timestamptz not null default now()
);

-- The owner dashboard's hot path is "reports for my site, newest first".
create index if not exists sms_reports_site_idx on sms_reports (widget_site_id, created_at desc);
-- k-anonymity groups by cell then counts distinct contributors.
create index if not exists sms_reports_geo_idx on sms_reports (lat, lon);

alter table sms_reports enable row level security;

-- ---------------------------------------------------------------------------
-- Venue approval. Coverage reporting is NOT self-serve.
--
-- A venue expresses intent by email, NetRange decides whether to approve, and
-- whether it is free or billed depends on the scale of the network. So account
-- creation and widget access are separate events: registering records interest,
-- and only an explicit approval unlocks the widget.
--
-- approved_at NULL  = intent recorded, awaiting a decision. No widget, no sites.
-- approved_at set   = approved; the widget and site registration are unlocked.
-- ---------------------------------------------------------------------------
alter table network_owners add column if not exists approved_at timestamptz;
alter table network_owners add column if not exists approved_by text;
alter table network_owners add column if not exists decision_note text;

create index if not exists network_owners_pending_idx
  on network_owners (created_at) where approved_at is null;

-- A durable record of what a venue asked for, kept even if they never send the
-- email. Scale is captured up front because free-vs-billed is decided on it.
create table if not exists venue_requests (
  id bigserial primary key,
  org_name text not null,
  contact_email text not null,
  domain text,
  network_type text,          -- campus wifi / ISP / telco / hotspot / venue ...
  scale text,                 -- sites, or approximate concurrent users
  use_case text,
  message text,
  status text not null default 'new',   -- new | approved | declined
  decided_at timestamptz,
  decision_note text,
  created_at timestamptz not null default now()
);

create index if not exists venue_requests_status_idx
  on venue_requests (status, created_at desc);

alter table venue_requests enable row level security;

-- A site needs a location before an SMS report can be tied to it. Without
-- lat/lon there is nothing to compare a reporter's position against, and
-- attribute_site() refuses to guess -- see the note there about why carrier
-- alone is not a location.
alter table widget_sites add column if not exists lat double precision;
alter table widget_sites add column if not exists lon double precision;
