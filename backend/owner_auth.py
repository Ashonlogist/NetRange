"""
Network-owner accounts, widget-site registration, and the trust model for
browser-submitted widget scans.

This is deliberately a SEPARATE identity system from the admin dashboard.

  * The admin dashboard (app.py `_check_dashboard_auth`) is HTTP Basic with
    DASHBOARD_USERNAME / DASHBOARD_PASSWORD over every /dashboard* route. It is
    not session-based and not user-facing.
  * These accounts are password-only, session-based, and scoped to a single
    owner's own rows. They are not self-serve: an account records interest and
    is unapproved until a person approves it, because whether NetRange reports
    a venue's coverage for free or for a fee depends on the scale of the
    network.

They do not share a signing key, a password store, or a guard function. A
flaw in owner auth must not be able to reach the admin dashboard, and rotating
DASHBOARD_SECRET must not log every site owner out. `OWNER_SESSION_SECRET` is
its own required env var for exactly that reason.

WHY THE SESSION SECRET IS NOT DASHBOARD_SECRET
----------------------------------------------
app.secret_key is DASHBOARD_SECRET today. Flask signs every session cookie with
app.secret_key, so pointing a second, unrelated login system at the same key
would make one leak (a session cookie captured from an owner, a weak key chosen
for the dashboard, a careless log) sufficient to mint admin sessions too. Separate
key, separate blast radius. Both are required -- there is no fallback, because a
silent fallback to the admin secret is precisely the coupling we're avoiding.

WHY PASSWORD HASHING IS werkzeug's, NOT OURS
---------------------------------------------
`generate_password_hash(method="scrypt")` from werkzeug.security -- a library
that ships with Flask. scrypt is memory-hard, which is the property that makes
GPU cracking expensive. Hand-rolling a hash (even with hashlib) is how you end
up with something without a salt policy, without a work factor, or without
constant-time comparison. Swapping in bcrypt later is a two-line change.
"""

import hmac
import os
import re
import secrets
import threading
import time
from functools import wraps
from hashlib import sha256
from urllib.parse import urlsplit

import requests

from datetime import datetime, timezone

from flask import abort, g, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_client

OWNER_SESSION_SECRET = os.environ.get("OWNER_SESSION_SECRET")
SMS_PHONE_PEPPER = os.environ.get("SMS_PHONE_PEPPER")

# Session cookie name is distinct from anything the admin dashboard sets, so the
# two never shadow each other in the same jar.
OWNER_SESSION_KEY = "owner_id"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()

MIN_PASSWORD_LENGTH = 10
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")

# Login throttling. Owner accounts are password-only with no email, so there is
# no second factor and no lockout-by-notification: an unthrottled endpoint here
# is a free offline-cracking oracle.
_LOGIN_MAX_PER_WINDOW = 10
_LOGIN_WINDOW_SECONDS = 600
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_LOCK = threading.Lock()

# Origin-checked widget submissions.
#
# Sized for the real deployment shape, which is a single public IP per venue:
# a cafe or campus WiFi puts every visitor behind one address, so a limit tuned
# to stop one abusive client would also stop that venue's ordinary visitors. At
# 200/hour a busy venue still has headroom while a single IP is bounded.
#
# This is the same shape as device_auth.registration_throttled (a fixed window
# over an in-process map, not a shared store), which is deliberate -- it is a
# cheap, dependency-free brake on volume, not the security boundary. The actual
# boundary is the Origin check plus k-anonymity suppression downstream.
#
# Known limitation: the counter lives in process memory, so on Render's
# multi-instance deploys the effective limit is per-instance. That makes the
# ceiling softer, not the trust weaker.
_WIDGET_MAX_PER_WINDOW = 200
_WIDGET_WINDOW_SECONDS = 3600
_WIDGET_ATTEMPTS: dict[str, list[float]] = {}
_WIDGET_LOCK = threading.Lock()


def require_owner_config():
    """Fail loudly and early rather than running with a guessable key."""
    missing = [
        name
        for name, val in (
            ("OWNER_SESSION_SECRET", OWNER_SESSION_SECRET),
            ("SMS_PHONE_PEPPER", SMS_PHONE_PEPPER),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )


def hash_phone(number: str) -> str:
    """
    Keyed digest of a phone number. See the table comment in
    db/schema_data_sources.sql for why an unsalted hash is not acceptable.
    """
    require_owner_config()
    digits = re.sub(r"[^\d+]", "", (number or "").strip())
    if not digits:
        return ""
    return hmac.new(
        SMS_PHONE_PEPPER.encode(), digits.encode(), sha256
    ).hexdigest()


# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------

def _throttled(store, lock, key, max_hits, window):
    now = time.time()
    cutoff = now - window
    with lock:
        hits = [t for t in store.get(key, []) if t > cutoff]
        if len(hits) >= max_hits:
            store[key] = hits
            return True
        hits.append(now)
        store[key] = hits
        return False


def login_throttled() -> bool:
    ip = client_ip()
    return _throttled(_LOGIN_ATTEMPTS, _LOGIN_LOCK, ip,
                      _LOGIN_MAX_PER_WINDOW, _LOGIN_WINDOW_SECONDS)


def widget_throttled() -> bool:
    ip = client_ip()
    return _throttled(_WIDGET_ATTEMPTS, _WIDGET_LOCK, ip,
                      _WIDGET_MAX_PER_WINDOW, _WIDGET_WINDOW_SECONDS)


def client_ip() -> str:
    # Render terminates TLS and sets X-Forwarded-For; the left-most entry is the
    # real client. Same helper shape device_auth.py already relies on.
    return request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or "unknown"


# --------------------------------------------------------------------------
# Domains and origins
# --------------------------------------------------------------------------

def normalize_domain(raw: str) -> str:
    """
    Reduce whatever the owner pasted to a bare hostname.

    Owners paste full URLs, host:port, paths and stray whitespace, and the
    comparison in /api/widget-scan has to be exact. Normalizing once at
    registration and comparing the parsed Origin later is what makes that
    comparison meaningful.
    """
    if not raw:
        return ""
    value = raw.strip().lower()
    if "://" not in value:
        value = "https://" + value
    host = urlsplit(value).hostname or ""
    host = host.lstrip("www.")
    return host


def domain_is_valid(domain: str) -> bool:
    if not domain or len(domain) > 253:
        return False
    # A single label like "localhost" is deliberately rejected: it would let
    # any site on a developer's machine claim any other. No scheme, no port,
    # no path, no wildcard.
    if "." not in domain:
        return False
    return bool(re.match(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$", domain))


def origin_allowed(domain: str) -> bool:
    """
    Does this request actually come from the registered site?

    TRUST MODEL -- why this is not a weaker /api/scan
    --------------------------------------------------
    /api/scan is authenticated by a device token: a registered install presents
    a secret it holds, the server validates it, and every row is attributable to
    a device row we can revoke. A website visitor has no install and no secret
    to hold, so there is nothing to attach a token to. Origin checking plus IP
    throttling is the honest substitute, not a watered-down version of the same
    control.

    Its limits are real and worth stating plainly:
      * `Origin` is only trustworthy because browsers refuse to let script set
        it. curl, a server-side proxy, or a native HTTP client sets whatever it
        likes. So this is a strong signal against drive-by third-party scripts
        and a weak one against a determined non-browser caller.
      * It says "this page is on your domain", not "this page is your widget".
        Any XSS or compromised script on the site inherits the allowance.
      * IP throttling bounds the damage but is shared across a whole NAT, so a
        busy venue can exhaust its own budget.

    What makes this acceptable for the data it produces: a widget row is
    already anonymous and k-anonymity-gated before anything is published, so the
    worst an origin-spoofing caller achieves is contributing a low-value
    pseudonymous sample to a cell that will be suppressed unless three distinct
    contributors agree. It is NOT acceptable for privileged actions, which is
    why nothing in this module trusts an origin for authorization -- origins only
    ever pick which widget_site a row is attributed to.
    """
    header = request.headers.get("Origin") or ""
    if not header:
        # Some same-origin navigations omit Origin. Fall back to Referer, but
        # never treat "neither present" as consent to write.
        ref = request.headers.get("Referer") or ""
        header = ref if ref else ""
        if not header:
            return False
    parts = urlsplit(header if "://" in header else "https://" + header)
    host = (parts.hostname or "").lstrip("www.")
    return bool(host) and host == domain


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

def create_owner(username: str, password: str) -> tuple[int | None, str | None]:
    """Returns (owner_id, error_message)."""
    require_owner_config()
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        return None, "Username must be 3-32 characters: lowercase letters, digits, dot, dash, underscore."
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return None, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    client = get_client()
    # Check first for a friendly message, but the unique constraint is the real
    # enforcement -- a lost race here must not create a duplicate.
    existing = client.table("network_owners").select("id").eq("username", username).execute()
    if existing.data:
        return None, "That username is taken."
    try:
        resp = client.table("network_owners").insert({
            "username": username,
            "password_hash": generate_password_hash(password, method="scrypt"),
        }).execute()
    except Exception:
        return None, "Could not create that account."
    return (resp.data[0]["id"] if resp.data else None), None


def verify_login(username: str, password: str) -> int | None:
    """Returns owner_id on success, None on any failure (same message for both)."""
    require_owner_config()
    client = get_client()
    username = (username or "").strip().lower()
    resp = client.table("network_owners").select("id,password_hash").eq("username", username).execute()
    row = (resp.data or [None])[0]
    if not row:
        # Hash a dummy value anyway so a missing username and a wrong password
        # cost the same wall-clock time, and cannot be told apart by timing.
        check_password_hash(
            "scrypt:32768:8:1$placeholder$" + sha256(b"x").hexdigest(), password or ""
        )
        return None
    if not check_password_hash(row["password_hash"], password or ""):
        return None
    return row["id"]


def csrf_token() -> str:
    tok = session.get("owner_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["owner_csrf"] = tok
    return tok


def check_csrf() -> None:
    """
    Reject state-changing owner requests without a matching CSRF token.

    Both the stored and the submitted token must be non-empty before they are
    compared. Comparing two empty strings would be a bypass: compare_digest("", "")
    is True, and a session that has never minted a token (nobody has opened a
    form yet) stores "", so an attacker could post with no token field at all
    and pass the check.
    """
    sent = request.headers.get("X-CSRF-Token") or (request.form.get("csrf_token") or "")
    stored = session.get("owner_csrf") or ""
    if not sent or not stored or not hmac.compare_digest(sent, stored):
        abort(400, "Invalid or missing CSRF token.")


def owner_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        oid = session.get(OWNER_SESSION_KEY)
        if not oid:
            # A plain redirect, not abort(): these are browser pages, and the
            # visitor should land on the login form rather than a 401.
            return redirect("/owner/login")
        g.owner_id = oid
        return fn(*args, **kwargs)

    return wrapper


def owner_sites(owner_id: int) -> list[dict]:
    client = get_client()
    resp = (
        client.table("widget_sites")
        .select("*")
        .eq("owner_id", owner_id)          # query-level scoping, not UI hiding
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


def site_for_domain(domain: str) -> dict | None:
    """
    The site a widget scan may be attributed to -- VERIFIED ones only.

    This is the enforcement point for domain ownership, and it is the reason
    registering a domain is not the same as being able to collect from it. An
    unverified or failed site is invisible here, so the widget endpoint cannot
    be made to accept one by any other route.

    An unverified domain gets "unregistered origin", the same answer as a domain
    nobody claimed. It does not confirm that the site exists.
    """
    resp = (
        get_client().table("widget_sites").select("*")
        .eq("domain", domain).eq("verification_status", "verified")
        .limit(1).execute()
    )
    return (resp.data or [None])[0]


def site_for_domain_any_status(domain: str) -> dict | None:
    """Unfiltered lookup, for the owner's own dashboard and for admin. Never
    call this from a path that collects or attributes data."""
    resp = get_client().table("widget_sites").select("*").eq("domain", domain).limit(1).execute()
    return (resp.data or [None])[0]


def register_site(owner_id: int, domain: str, label: str) -> tuple[dict | None, str | None]:
    domain = normalize_domain(domain)
    if not domain_is_valid(domain):
        return None, "Enter a bare domain like volta.example.com (no https://, no path)."
    label = (label or "").strip()[:80]
    client = get_client()
    existing = (
        client.table("widget_sites").select("id")
        .eq("owner_id", owner_id).eq("domain", domain).execute()
    )
    if existing.data:
        if label:
            client.table("widget_sites").update({"label": label}).eq("id", existing.data[0]["id"]).execute()
        resp = client.table("widget_sites").select("*").eq("id", existing.data[0]["id"]).execute()
        return (resp.data or [None])[0], None
    try:
        resp = client.table("widget_sites").insert({
            "owner_id": owner_id, "domain": domain, "label": label,
            # Explicit, not left to the column DEFAULT: registering a domain
            # must never be mistaken for being allowed to collect from it.
            "verification_status": "pending",
        }).execute()
    except Exception:
        return None, "Could not register that domain."
    return (resp.data or [None])[0], None


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------
# Registering an account records interest. It does not grant the widget.
#
# Coverage reporting is not self-serve: a venue reaches out by email, NetRange
# decides whether to approve, and whether it is free or billed depends on the
# scale of the network. Without this gate, /owner/register would hand out a
# working widget to anyone who wanted one, which skips that decision entirely.
#
# The gate is enforced here, at the point of use, rather than by hiding buttons
# in the templates. A hidden button is a UI convention; this is the rule.

def owner_approved(owner_id: int) -> bool:
    """Has this account been approved for widget access?"""
    resp = (
        get_client()
        .table("network_owners")
        .select("approved_at")
        .eq("id", owner_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return bool(rows and rows[0].get("approved_at"))


def require_approval(fn):
    """
    Decorator for owner routes that grant or depend on widget access.

    A pending owner can sign in and see why they are waiting, but cannot add a
    site, and by extension cannot obtain a widget snippet that would collect
    anything.
    """
    def wrapper(*args, **kwargs):
        oid = session.get(OWNER_SESSION_KEY)
        if not oid:
            return redirect("/owner/login")
        if not owner_approved(oid):
            return render_template(
                "owner-pending.html",
                csrf=csrf_token(),
            ), 403
        return fn(*args, **kwargs)
    wrapper.__name__ = getattr(fn, "__name__", "wrapper")
    return wrapper


def set_approval(owner_id: int, approved: bool, decided_by: str,
                 note: str = "") -> None:
    """Record a decision. Called from the Basic-auth admin route only."""
    get_client().table("network_owners").update({
        "approved_at": _iso(_now()) if approved else None,
        "approved_by": decided_by or None,
        "decision_note": (note or None),
    }).eq("id", owner_id).execute()


def log_venue_request(org_name: str, contact_email: str, domain: str | None,
                      network_type: str | None, scale: str | None,
                      use_case: str | None, message: str | None) -> int | None:
    """
    Record what a venue asked for, before any human decides anything.

    Kept in the database rather than only emailed, because an intent that lives
    only in someone's inbox is lost the first time a mail is missed. The email is
    the notification; this is the record.
    """
    clean = lambda v, n: (v or "").strip()[:n] or None  # noqa: E731
    resp = (
        get_client()
        .table("venue_requests")
        .insert({
            "org_name": clean(org_name, 160) or "(unnamed)",
            "contact_email": clean(contact_email, 200) or "(not given)",
            "domain": normalize_domain(domain) if domain else None,
            "network_type": clean(network_type, 80),
            "scale": clean(scale, 200),
            "use_case": clean(use_case, 400),
            "message": clean(message, 2000),
        })
        .execute()
    )
    rows = resp.data or []
    return rows[0].get("id") if rows else None


def pending_requests(status: str = "new") -> list[dict]:
    resp = (
        get_client()
        .table("venue_requests")
        .select("*")
        .eq("status", status)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


def pending_owners() -> list[dict]:
    resp = (
        get_client()
        .table("network_owners")
        .select("id,username,created_at")
        .is_("approved_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


# ---------------------------------------------------------------------------
# Domain ownership verification
# ---------------------------------------------------------------------------
# Approval answers "is this person a real venue?". It does not answer "does this
# person control example.com?" An approved owner could otherwise register any
# domain and collect its traffic -- a competitor's included -- and under a
# per-venue pricing model that is not a nuisance bug, it is the core promise
# failing.
#
# Proof is a DNS TXT record holding an unguessable per-site token. Adding one
# requires control of the domain's DNS, which is exactly the claim being made.
#
# The resolver is DNS-over-HTTPS rather than UDP/53 because cloud PaaS commonly
# blocks outbound port 53, and DoH needs no new dependency. Google is used
# because it returns plain JSON with no Accept negotiation.

SITE_VERIFICATION_PREFIX = "_netrange"
SITE_VERIFICATION_TTL = 300          # seconds; a DNS change takes time to settle
_DOH_ENDPOINT = "https://dns.google/resolve"
_VERIFY_COOLDOWN = 20                # seconds between DNS checks for one site
_verify_last_check: dict[int, float] = {}


def verification_txt_name(domain: str) -> str:
    """
    The name a venue must publish the token at.

    Always a fixed prefix under the domain, including for an apex domain. Using
    the apex directly would force a second, differently-shaped instruction for
    the most common case, and a fixed child name keeps the published token out
    of the record a domain's SPF/DKIM policy lives in.
    """
    return f"{SITE_VERIFICATION_PREFIX}.{domain}"


def new_verification_token() -> str:
    """32 chars of entropy. Short enough to paste into a DNS panel, long enough
    that guessing one is not a way to claim someone else's domain."""
    return secrets.token_urlsafe(24)


def check_dns_txt(name: str, token: str) -> bool:
    """
    True if `name` publishes a TXT record containing `token`.

    Returns False on ANY error -- resolver down, malformed JSON, timeout. The
    caller treats False as "not verified yet" and says so; it must never be
    read as "verified".
    """
    # "_" is legal in a DNS label and our own prefix uses it, so excluding it
    # here would reject every name this function generates.
    if not re.fullmatch(r"[a-z0-9_.-]{1,253}", name or ""):
        return False
    try:
        resp = requests.get(
            _DOH_ENDPOINT,
            params={"name": name, "type": "TXT", "cd": "false"},
            timeout=5,
        )
        if resp.status_code != 200:
            return False
        answers = (resp.json() or {}).get("Answer") or []
    except Exception:
        # A resolver we cannot reach is a failed check, not a pass.
        return False

    for a in answers:
        if not isinstance(a, dict) or a.get("type") != 16:
            continue
        for chunk in re.findall(r'"((?:[^"\\]|\\.)*)"', str(a.get("data", ""))):
            if hmac.compare_digest(chunk.strip(), token):
                return True
    return False


def request_site_verification(owner_id: int, site_id: int) -> tuple[dict | None, str | None]:
    """
    Mint (or re-mint) the token for one of THIS owner's sites and return the
    instructions to publish it.

    Scoped by owner_id in the update itself: a site_id belonging to someone else
    must update zero rows, never reissue a token against another venue's domain.
    """
    token = new_verification_token()
    try:
        resp = (
            get_client().table("widget_sites")
            .update({
                "verification_token": token,
                "verification_status": "pending",
                "verification_method": None,
                "verified_at": None,
                "verified_by": None,
            })
            .eq("id", site_id)
            .eq("owner_id", owner_id)          # <- the scoping that matters
            .execute()
        )
    except Exception:
        return None, "Could not start verification."
    if not (resp.data or None):
        return None, "That site is not yours."
    return resp.data[0], None


def run_site_verification(owner_id: int, site_id: int) -> tuple[dict | None, str | None]:
    """
    Look up the token the owner was given and see if it is published.

    Returns the site on success. On a mismatch the token is kept, because the
    usual cause is a typo or DNS that has not propagated yet, and the owner
    needs to retry the same token rather than be handed a new one each time.
    """
    site = _owned_site(owner_id, site_id)
    if not site:
        return None, "That site is not yours."
    if site.get("verification_status") == "verified":
        return site, None

    # The resolver is the only outbound call an owner can aim at will, so it is
    # the thing worth rate limiting. Minting a token costs nothing and stays free.
    if time.time() - _verify_last_check.get(site_id, 0) < _VERIFY_COOLDOWN:
        return None, "Wait a few seconds before checking again."
    _verify_last_check[site_id] = time.time()

    token = site.get("verification_token")
    if not token:
        return None, "Start verification first."

    if not check_dns_txt(verification_txt_name(site["domain"]), token):
        return None, (
            f"No matching TXT record at {verification_txt_name(site['domain'])} yet. "
            "DNS can take a few minutes to propagate."
        )
    return set_site_verification(site_id, True, "owner-dns", token=token), None


def set_site_verification(site_id: int, verified: bool, by: str,
                          token: str | None = None) -> dict | None:
    """
    Record a verification decision.

    The token is cleared on success: it has done its job, and a live token
    sitting in the table is a credential-shaped string with no reason to exist.
    On failure it is kept so the owner can retry with the same value.
    """
    patch = {
        "verification_status": "verified" if verified else "failed",
        "verification_method": by,
        "verified_at": _now() if verified else None,
        "verified_by": by if verified else None,
    }
    if verified:
        patch["verification_token"] = None
    resp = (
        get_client().table("widget_sites").update(patch)
        .eq("id", site_id).execute()
    )
    return (resp.data or [None])[0]


def _owned_site(owner_id: int, site_id: int) -> dict | None:
    resp = (
        get_client().table("widget_sites").select("*")
        .eq("id", site_id).eq("owner_id", owner_id).limit(1).execute()
    )
    return (resp.data or [None])[0]
