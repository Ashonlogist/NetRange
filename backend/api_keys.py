"""
API key management and rate limiting for the public data API.

Stores a SHA-256 *hash* of each key in a Supabase `api_keys` table, with
per-key rate limits and usage tracking. Endpoints guarded by
require_api_key() return 401/429 instead of data when the key is missing,
invalid, or exhausted.

Why hashed
----------
Keys used to be stored -- and looked up -- in plaintext. That meant anyone with
read access to the table (a leaked backup, an over-broad Supabase policy, a
`select *` in the dashboard) held working credentials for the public data API.
Only the hash is stored now, so a table dump yields nothing usable. The
plaintext key is returned exactly once, at creation, and is unrecoverable
afterwards -- which is the point, and also why `create_api_key` is the
supported way to mint one.

Keys are 48 hex characters of `secrets.token_hex(24)` (~192 bits), so a plain
SHA-256 is the right tool: there is no low-entropy guessing to slow down, and
no salt is needed because the input space is already uniform. This matches how
device scan tokens are hashed in device_auth.py.

Supabase table (current shape):
    create table api_keys (
        id          bigserial primary key,
        key_hash    text unique not null,   -- sha256 hex of the key
        customer    text not null,
        daily_limit int not null default 1000,
        active      boolean not null default true,
        created_at  timestamptz not null default now()
    );

    -- Usage is tracked in a separate table so api_keys stays small:
    create table api_key_usage (
        id         bigserial primary key,
        key_id     bigint references api_keys(id),
        day        date not null default current_date,
        hits       int not null default 1,
        unique(key_id, day)
    );

MIGRATION from the old plaintext schema
----------------------------------------
BREAKING: keys issued before this change stop working, because they are
matched against a hash they were never hashed into. The old column held the
key itself, so the transition is reversible *only until* the column is
dropped -- migrate first, verify, then drop.

    -- 1. add the new column
    alter table api_keys add column if not exists key_hash text;

    -- 2. backfill by hashing the existing plaintext values
    --    pgcrypto's digest() is required for encode(digest(...)):
    --      create extension if not exists pgcrypto;
    update api_keys
       set key_hash = encode(digest(key, 'sha256'), 'hex')
     where key_hash is null;

    -- 3. stop accepting NULLs before adding the constraint, or it fails
    alter table api_keys alter column key_hash set not null;

    -- 4. enforce uniqueness, then retire the plaintext column
    create unique index if not exists api_keys_key_hash_uniq on api_keys (key_hash);
    alter table api_keys drop column key;

Order matters: step 3 before step 4, and do not run step 4 until you have
confirmed real keys still authenticate. On an empty table the whole sequence
is a no-op beyond the rename.
"""

import hashlib
import os
import secrets
from datetime import datetime, timezone, date

from flask import request, jsonify, g
from db import get_client

_DEFAULT_DAILY_LIMIT = 1000


def hash_api_key(key):
    """Return the stored form of an API key: lowercase sha256 hex.

    Not secret -- it is a one-way hash of a high-entropy random string.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_api_key(customer, daily_limit=_DEFAULT_DAILY_LIMIT):
    """Mint a key, store only its hash, and return the plaintext key.

    Call from a management script, not at runtime. The returned key is the only
    time the plaintext exists anywhere; it cannot be looked up later.
    """
    key = f"nr_{secrets.token_hex(24)}"
    get_client().table("api_keys").insert({
        "key_hash": hash_api_key(key),
        "customer": customer,
        "daily_limit": daily_limit,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return key


def _today():
    return date.today().isoformat()


def require_api_key(fn):
    """
    Decorator for routes that need a valid API key.

    Reads ?key= from query params or X-API-Key header.
    Returns None on success (request proceeds), or a Flask response on failure.
    The decorated function receives the validated key record as a kwarg.
    """
    def wrapper(*args, **kwargs):
        key = request.args.get("key") or request.headers.get("X-API-Key", "").strip()
        if not key:
            return jsonify({"error": "Missing API key. Pass ?key=... or X-API-Key header."}), 401

        client = get_client()
        resp = (
            client.table("api_keys")
            .select("id, customer, daily_limit, active")
            .eq("key_hash", hash_api_key(key))
            .limit(1)
            .execute()
        )
        if not resp.data:
            return jsonify({"error": "Invalid API key."}), 401

        rec = resp.data[0]
        if not rec["active"]:
            return jsonify({"error": "API key is deactivated."}), 401

        # Check / increment daily usage
        today = _today()
        usage_resp = (
            client.table("api_key_usage")
            .select("hits")
            .eq("key_id", rec["id"])
            .eq("day", today)
            .limit(1)
            .execute()
        )
        current_hits = usage_resp.data[0]["hits"] if usage_resp.data else 0

        if current_hits >= rec["daily_limit"]:
            return jsonify({
                "error": "Daily rate limit reached.",
                "limit": rec["daily_limit"],
                "used": current_hits,
                "reset": "tomorrow (UTC)",
            }), 429

        # Increment usage
        if usage_resp.data:
            client.table("api_key_usage").update({"hits": current_hits + 1}).eq(
                "key_id", rec["id"]
            ).eq("day", today).execute()
        else:
            client.table("api_key_usage").insert({
                "key_id": rec["id"], "day": today, "hits": 1,
            }).execute()

        g.api_key = rec
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper
