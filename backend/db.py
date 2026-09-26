"""
Shared, persistent scan storage backed by Supabase (Postgres).

Replaces the old local-disk data/scans.json approach. That approach broke
"global" coverage in two ways:
  1. It only ever held whatever happened to be on the single backend
     instance's filesystem.
  2. On Render's free tier, that filesystem is wiped on every restart
     (redeploy, or the automatic spin-down after ~15 min idle) -- so
     scans routinely vanished between sessions.

This module keeps the exact same function names/shapes app.py already
used (save_scan, load_scans) so nothing else has to change -- only the
storage underneath does. Every scan from every device now lands in one
shared `scans` table, so any client (phone or laptop browser) hitting
the same backend sees the same live, durable dataset.

Setup
-----
1. Run db/schema.sql once against your Supabase project (SQL editor,
   or `psql`).
2. Set these two environment variables wherever the backend runs
   (Render dashboard -> Environment, and a local .env for dev):
     SUPABASE_URL          -- from Project Settings -> API
     SUPABASE_SERVICE_KEY   -- the service_role key (NOT the anon key --
                                this is the server, not the browser, so
                                it's fine to hold the privileged key;
                                never ship it to the mobile app or web
                                frontend)
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

_client = None


def get_client() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables "
                "must be set (Render dashboard -> Environment, or a local .env)."
            )
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def _to_iso(ts):
    """Normalize a client-supplied timestamp (epoch seconds/ms, or string) to ISO8601."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        secs = ts / 1000.0 if ts > 1e12 else float(ts)
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    return str(ts)


def save_scan(records):
    """Insert scan records into the shared `scans` table. Returns count inserted."""
    if not records:
        return 0
    client = get_client()
    rows = [{
        "ssid": r.get("ssid"),
        "bssid": r.get("bssid"),
        "signal_dbm": r.get("signal_dbm"),
        "signal_pct": r.get("signal_pct"),
        "strength_raw": r.get("strength_raw"),
        "channel": str(r["channel"]) if r.get("channel") is not None else None,
        "frequency": r.get("frequency"),
        "lat": r.get("lat"),
        "lon": r.get("lon"),
        "accuracy": r.get("accuracy"),
        "device_id": r.get("device_id"),
        # Radio type ('cellular' / 'mobile'), NOT the ingestion channel.
        "source": r.get("source", "mobile"),
        # Ingestion channel ('app' / 'widget' / 'sms'). Defaults to 'app' so
        # every pre-existing caller keeps working unchanged; see
        # db/schema_data_sources.sql for why this is a separate column.
        "ingest_source": r.get("ingest_source", "app"),
        "client_timestamp": _to_iso(r.get("timestamp")),
        "download_speed_mbps": r.get("download_speed_mbps"),
        # Browser-only fields (widget path). See db/schema_data_sources.sql for
        # why the estimate is not folded into download_speed_mbps.
        "widget_site_id": r.get("widget_site_id"),
        "effective_type": r.get("effective_type"),
        "conn_type": r.get("conn_type"),
        "downlink_estimate_mbps": r.get("downlink_estimate_mbps"),
        "rtt_ms": r.get("rtt_ms"),
        # Cellular network identity. `ssid` is a human-readable label the OS
        # and the user can both change; these are the stable grouping key, so
        # a rebrand or a case variant cannot split one network's coverage.
        "carrier_identity": r.get("carrier_identity"),
        "carrier_numeric": r.get("carrier_numeric"),
        "carrier_name": r.get("carrier_name"),
    } for r in records]

    resp = client.table("scans").insert(rows).execute()
    return len(resp.data) if resp.data else 0


def load_widget_scans(site_ids, limit=2000):
    """
    Scans belonging to specific widget sites, newest first.

    Takes the site ids the caller already proved they own (owner_sites filters
    by owner_id). The `.in_` filter is the security boundary: an owner cannot
    widen this by passing a site id they do not own, because the set they can
    obtain is itself owner-scoped upstream.
    """
    if not site_ids:
        return []
    client = get_client()
    resp = (
        client.table("scans")
        .select("*")
        .in_("widget_site_id", list(site_ids))
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    out = []
    for row in resp.data or []:
        out.append({
            "ssid": row.get("ssid"),
            "signal_dbm": row.get("signal_dbm"),
            "lat": row.get("lat"),
            "lon": row.get("lon"),
            "device_id": row.get("device_id"),
            "source": row.get("source"),
            "ingest_source": row.get("ingest_source"),
            "widget_site_id": row.get("widget_site_id"),
            "effective_type": row.get("effective_type"),
            "downlink_estimate_mbps": row.get("downlink_estimate_mbps"),
            "download_speed_mbps": row.get("download_speed_mbps"),
            "timestamp": row.get("client_timestamp") or row.get("created_at"),
        })
    return out


def load_scans(limit=5000):
    """
    Load recent scans (most recent first), reshaped to look exactly like
    the old JSON records so algorithm.py and app.py don't need to change.
    """
    client = get_client()
    resp = (
        client.table("scans")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    out = []
    for row in resp.data or []:
        out.append({
            "ssid": row.get("ssid"),
            "bssid": row.get("bssid"),
            "signal_dbm": row.get("signal_dbm"),
            "signal_pct": row.get("signal_pct"),
            "strength_raw": row.get("strength_raw"),
            "channel": row.get("channel"),
            "frequency": row.get("frequency"),
            "lat": row.get("lat"),
            "lon": row.get("lon"),
            "accuracy": row.get("accuracy"),
            "device_id": row.get("device_id"),
            "source": row.get("source"),
            "ingest_source": row.get("ingest_source") or "app",
            "timestamp": row.get("client_timestamp") or row.get("created_at"),
            "download_speed_mbps": row.get("download_speed_mbps"),
            "effective_type": row.get("effective_type"),
            "conn_type": row.get("conn_type"),
            "downlink_estimate_mbps": row.get("downlink_estimate_mbps"),
            "rtt_ms": row.get("rtt_ms"),
            "carrier_identity": row.get("carrier_identity"),
            "carrier_numeric": row.get("carrier_numeric"),
            "carrier_name": row.get("carrier_name"),
        })
    return out
