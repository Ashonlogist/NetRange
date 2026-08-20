"""
API key management and rate limiting for the public data API.

Stores keys in a Supabase `api_keys` table with per-key rate limits
and usage tracking. Endpoints guarded by require_api_key() return
401/429 instead of data when the key is missing, invalid, or exhausted.

Supabase table (run once):
    create table api_keys (
        id          bigserial primary key,
        key         text unique not null,
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
"""

import os
import secrets
from datetime import datetime, timezone, date

from flask import request, jsonify, g
from db import get_client

_DEFAULT_DAILY_LIMIT = 1000


def generate_key():
    """Generate a random API key. Call from a management script, not at runtime."""
    return f"nr_{secrets.token_hex(24)}"


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
            .eq("key", key)
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
