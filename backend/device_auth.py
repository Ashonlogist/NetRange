"""
Per-install device tokens for the mobile scan write path.

Why this exists
---------------
`POST /api/scan` was reachable by anyone who knew the backend URL. It writes
rows into the shared `scans` table that every coverage map, analytics view and
dashboard is built from, so an unauthenticated caller can pollute the dataset
for every user of the deployment.

A shared secret shipped in the APK would not fix this: an APK is a zip file
anyone can download and strings can be pulled out of it. So each install mints
its own random token once, keeps it in the device keystore (SecureStore), and
presents it as `Authorization: Bearer <token>`.

Only the SHA-256 of the token is stored server-side. A dump of the
`device_tokens` table therefore does not hand anyone the ability to write
scans -- the same reasoning that makes password/API-key hashing worthwhile.

What this does and does not buy
-------------------------------
It stops drive-by junk: casual scanning, a bored script, a crawler that
finds the URL. It does NOT stop a determined actor, because
`/api/register-device` is deliberately open -- an attacker can simply mint
their own token. Closing that would need device attestation (e.g. Play
Integrity), which is a much larger change and is out of scope here. Treat
this as raising the cost of abuse, not eliminating it.

Supabase table (run once):
    create table device_tokens (
        id           bigserial primary key,
        device_id    text not null,
        token_hash   text unique not null,
        created_at   timestamptz not null default now(),
        last_seen_at timestamptz not null default now()
    );
    create index device_tokens_device_id_idx on device_tokens (device_id);
"""

import hashlib
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from flask import request, jsonify, g
from db import get_client

# Throttle writes to last_seen_at: the background scan task can post every
# few minutes, and stamping the row on every request would turn a cheap
# read into a write on the hot path.
_LAST_SEEN_WRITE_INTERVAL_SECONDS = 300

# Cap on legacy (tokenless) writes during the migration window, per device per
# day. The window exists only so an app build predating device tokens is not
# locked out; it must not become a free-for-all for anyone who finds the URL.
_LEGACY_MAX_PER_DAY = 200
_LEGACY_COUNTS = {}
_LEGACY_LOCK = threading.Lock()


def legacy_budget_exhausted(device_id):
    """True if this device has already spent its tokenless-write budget today."""
    today = datetime.now(timezone.utc).date().isoformat()
    key = (device_id, today)
    with _LEGACY_LOCK:
        for stale in [k for k in _LEGACY_COUNTS if k[1] != today]:
            del _LEGACY_COUNTS[stale]
        used = _LEGACY_COUNTS.get(key, 0)
        if used >= _LEGACY_MAX_PER_DAY:
            return True
        _LEGACY_COUNTS[key] = used + 1
        return False


def scan_auth_enforced():
    """True when tokenless scan writes must be rejected.

    Reads SCAN_AUTH_ENFORCED_AT lazily rather than at import, so the deadline
    passes on its own without a redeploy, and so tests can exercise both sides
    of it. An unset or unparseable value means "enforce now": a deploy should
    never silently leave the endpoint open because of a typo in a date.
    """
    raw = (os.environ.get("SCAN_AUTH_ENFORCED_AT") or "").strip()
    if not raw:
        return True
    try:
        cutoff = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= cutoff


def legacy_write_allowed(device_id):
    """True if a tokenless scan write may be tolerated right now.

    `device_id` is a high-entropy random string minted per install, so an
    empty/absent one means we have nothing to even attribute the write to.
    """
    if not device_id or not isinstance(device_id, str):
        return False
    return not legacy_budget_exhausted(device_id)


def mint_token():
    """Return a fresh plaintext token. Only ever returned once, to the caller."""
    return secrets.token_urlsafe(32)


def hash_token(token):
    """Hash a bearer token for storage/lookup. Tokens are high-entropy random
    strings, so a plain SHA-256 is appropriate here (no salting or slow KDF
    is needed -- there is nothing to brute-force)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token_for_device(device_id):
    """Mint, persist, and return a token for a device, replacing any earlier one.

    Re-registration deliberately re-mints rather than refusing, so a client
    that lost its token can always recover -- otherwise a cleared token with a
    surviving deviceId would be permanently unable to write. Replacing rather
    than accumulating also keeps one live token per install.

    The abuse case is someone revoking another device by re-registering its
    deviceId, which requires already knowing a 128-bit random id; and the
    victim simply re-registers on its next launch. So the recovery path is
    worth more than the narrow DoS it admits.
    """
    client = get_client()
    token = mint_token()
    now = datetime.now(timezone.utc).isoformat()

    # Drop superseded tokens first so a device never has more than one live.
    client.table("device_tokens").delete().eq("device_id", device_id).execute()
    client.table("device_tokens").insert({
        "device_id": device_id,
        "token_hash": hash_token(token),
        "created_at": now,
        "last_seen_at": now,
    }).execute()
    return token


# --- registration throttle -------------------------------------------------
# Deliberately in-process: gunicorn runs 2 workers, so the effective cap is
# per-worker rather than global. That is fine for its purpose (raising the
# cost of minting tokens in bulk) and avoids a database round trip on an open
# endpoint. Do not treat it as a precise quota.
_REG_ATTEMPTS = {}
_REG_LOCK = threading.Lock()
_REG_WINDOW_SECONDS = 3600
_REG_MAX_PER_WINDOW = 20


def registration_throttled():
    """True if this IP has exceeded the registration budget for the window."""
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or "unknown"
    now = time.time()
    cutoff = now - _REG_WINDOW_SECONDS
    with _REG_LOCK:
        attempts = [t for t in _REG_ATTEMPTS.get(ip, []) if t > cutoff]
        if len(attempts) >= _REG_MAX_PER_WINDOW:
            _REG_ATTEMPTS[ip] = attempts
            return True
        attempts.append(now)
        _REG_ATTEMPTS[ip] = attempts
        return False


def require_device_token(fn):
    """Guard a route so only a registered device can write.

    On success the request proceeds; the validated row is available as
    g.device_token.
    """
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not token:
            # Migration window: an app build from before device tokens would
            # otherwise be locked out by its own backend update. Tolerate it
            # until the cutoff, but only under a per-device budget -- and only
            # if the deadline has actually passed.
            if not scan_auth_enforced():
                payload = request.get_json(silent=True) or {}
                device_id = payload.get("deviceId")
                if not device_id or not isinstance(device_id, str):
                    # Nothing to attribute the write to, so nothing to budget.
                    return jsonify({
                        "error": "Missing device token. Register this install "
                                 "via POST /api/register-device and send "
                                 "'Authorization: Bearer <token>'.",
                    }), 401
                if legacy_budget_exhausted(device_id):
                    # 429, not 401: the credential is fine, the request rate is
                    # not, and the client should not conclude it must re-register.
                    return jsonify({
                        "error": "Too many unauthenticated writes for this "
                                 "device. Update the app to register.",
                    }), 429
                g.device_token = None  # legacy, unauthenticated caller
                g.legacy_device = True
                return fn(*args, **kwargs)
            return jsonify({
                "error": "Missing device token. Register this install via "
                         "POST /api/register-device and send "
                         "'Authorization: Bearer <token>'.",
            }), 401

        client = get_client()
        resp = (
            client.table("device_tokens")
            .select("id, device_id, last_seen_at")
            .eq("token_hash", hash_token(token))
            .limit(1)
            .execute()
        )
        if not resp.data:
            return jsonify({"error": "Unknown device token."}), 401

        rec = resp.data[0]
        try:
            last_seen = datetime.fromisoformat(rec["last_seen_at"])
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last_seen).total_seconds()
        except (TypeError, ValueError):
            age = _LAST_SEEN_WRITE_INTERVAL_SECONDS + 1

        if age >= _LAST_SEEN_WRITE_INTERVAL_SECONDS:
            try:
                client.table("device_tokens").update({
                    "last_seen_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", rec["id"]).execute()
            except Exception:
                pass  # activity tracking is best-effort; never fail a scan for it

        g.device_token = rec
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper
