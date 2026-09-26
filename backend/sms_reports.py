"""
SMS coverage reports over Africa's Talking.

WHY SMS AT ALL
--------------
Coverage data is biased toward people who own a smartphone and open an app.
SMS reaches the people the map currently cannot see, which is the whole point
of collecting it. It is also the most dangerous thing in this repository to
get wrong, because it invites a *person* to describe their own experience at a
*place* -- and then we ask for their gender and how frustrated they are.

So the rules encoded here are not negotiable conveniences:

  1. The phone number is never stored raw. Only a keyed HMAC digest
     (owner_auth.hash_phone). A dump of the database must not be able to say
     "this number rated this location 2/10 and is female".
  2. Gender is optional in the prompt, accepts an explicit skip keyword, and
     stays NULL when skipped. It is never inferred.
  3. Every completed report passes the same k-anonymity gate as everything
     else (analytics.MIN_DEVICES_PER_CELL distinct contributors) before it can
     reach an owner dashboard, /api/data/*, or the admin dashboard.
  4. A report only attaches to a site when that owner opted in AND the carrier
     matches. There is no code path where an SMS report is exposed without
     passing both checks.

PROVIDER: Africa's Talking, the standard for Ghana. It has a plain webhook that
POSTs form-encoded inbound SMS, no per-message cost beyond the SMS itself, and
works on feature phones.

INTEGRATION STATUS -- READ THIS BEFORE ENABLING
-----------------------------------------------
The webhook endpoint and the conversation are implemented and tested, but this
has NOT been exercised against a live Africa's Talking account: no credentials
are configured, so the exact webhook payload field names have not been observed
on the wire. `parse_inbound()` therefore accepts several common spellings of
each field, and `WEBHOOK_FIELD_MAP` is the single place to correct them once
you have a real payload. Set AT_API_KEY / AT_SHORTCODE and deploy before
turning the number up.
"""

import os
import re
from datetime import datetime, timedelta, timezone

from db import get_client
from analytics import DEFAULT_CELL_SIZE_M, MIN_DEVICES_PER_CELL, _cell_id
from owner_auth import hash_phone

SESSION_TIMEOUT_MINUTES = 30

# Field names Africa's Talking has used for inbound webhook POSTs. Normalized in
# parse_inbound(); correct here if your account's payload differs.
WEBHOOK_FIELD_MAP = {
    "from": ("from", "From", "msisdn", "phone", "phoneNumber", "sender"),
    "to": ("to", "To", "dst", "shortcode", "shortCode", "recipient"),
    "text": ("text", "Text", "message", "Message", "body", "keyword"),
    "id": ("id", "Id", "messageId", "smsId"),
}

GENDER_STEPS_HINT = 'Reply with m, f, or "skip" (optional).'

PROMPTS = {
    "quality": "NetRange coverage report. On a scale of 1 (unusable) to 10 (excellent), how is your network right now? Reply with a number 1-10.",
    "carrier": "Which network is this about? Reply with your carrier name (for example MTN, Telecel, AirtelTigo).",
    "location": ("Where are you? Reply with an area or landmark, for example "
                 "\"Cantonments\" or \"KNUST\". This is geocoded to a map "
                 "cell, not shared as text."),
    "gender": "Optional: your gender, m or f. Reply \"skip\" to leave this blank. (" + GENDER_STEPS_HINT + ")",
    "frustration": "Last one: how frustrated are you with the service today, 1 (not at all) to 10 (very)?",
}

SKIP_WORDS = {"skip", "s", "no", "n", "n/a", "na", "prefer not", "rather not", "-", "none"}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def phone_hash(number: str) -> str:
    return hash_phone(number)


# --------------------------------------------------------------------------
# Inbound parsing
# --------------------------------------------------------------------------

def is_inbound_message(payload: dict) -> bool:
    """
    True if this callback is an incoming message rather than a delivery report.

    Africa's Talking posts both to the same callback URL and distinguishes them
    with `isActive`. Without this check a delivery report -- which carries a
    sender number and no text -- parses as an inbound message with empty text
    and starts a junk session that then sits open for 30 minutes.

    Payloads with no isActive field are treated as inbound. That is the
    conservative direction: it is what this endpoint is for, and rejecting an
    unlabelled payload would silently drop real reports if the account is
    configured differently than the docs suggest.
    """
    flag = (payload.get("isActive") or payload.get("IsActive") or "").strip().lower()
    if flag in ("false", "0", "no"):
        return False
    return True


def parse_inbound(payload: dict) -> tuple[str | None, str | None, str | None]:
    """Extract (from_number, to_number, text) from a webhook payload."""
    out = []
    for key, names in WEBHOOK_FIELD_MAP.items():
        val = None
        for name in names:
            if name in payload and payload[name] not in (None, ""):
                val = str(payload[name]).strip()
                break
        out.append(val)
    return out[0], out[1], out[2]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _norm_carrier(text: str) -> str:
    t = _clean(text).lower()
    t = re.sub(r"\b(ghana|network|limited|ltd|inc)\b", "", t)
    return re.sub(r"[^a-z ]", "", t).strip() or _clean(text).lower()


def parse_rating(text: str) -> int | None:
    """A 1-10 rating, or None. Refuses anything else, including '0' and '11'."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*", text or "")
    if not m:
        return None
    val = int(m.group(1))
    return val if 1 <= val <= 10 else None


def parse_gender(text: str) -> str | None:
    """
    Optional, skippable. Returns 'm', 'f', or None for skip/unrecognised.

    Anything we do not explicitly recognise becomes NULL rather than a guess --
    inferring this from a name or a typo would be a serious privacy failure.
    """
    t = _clean(text).lower().strip(" .")
    if not t or t in SKIP_WORDS:
        return None
    if t in ("m", "male", "man"):
        return "m"
    if t in ("f", "female", "woman"):
        return "f"
    return None


# --------------------------------------------------------------------------
# Session state machine
# --------------------------------------------------------------------------

ORDER = ["quality", "carrier", "location", "gender", "frustration"]


def next_step(current: str) -> str | None:
    if current not in ORDER:
        return ORDER[0]
    idx = ORDER.index(current)
    return ORDER[idx + 1] if idx + 1 < len(ORDER) else None


def apply_answer(current_step: str, text: str) -> tuple[dict | None, str | None]:
    """
    Fold one reply into the answers dict.

    Returns (patch, error). patch is None when the reply was unusable, in which
    case the same prompt is re-sent -- a person typing "great" into a 1-10
    question should be asked again, not have their answer silently dropped.
    """
    t = _clean(text)
    if not t:
        return None, PROMPTS.get(current_step, "Please reply.")

    if current_step == "quality":
        val = parse_rating(t)
        if val is None:
            return None, "That wasn't a number from 1 to 10. " + PROMPTS["quality"]
        return {"quality_rating": val}, None

    if current_step == "frustration":
        val = parse_rating(t)
        if val is None:
            return None, "That wasn't a number from 1 to 10. " + PROMPTS["frustration"]
        return {"frustration": val}, None

    if current_step == "carrier":
        if len(t) < 2 or len(t) > 60:
            return None, "Please send your carrier name. " + PROMPTS["carrier"]
        return {"carrier": t[:60]}, None

    if current_step == "location":
        if len(t) < 2:
            return None, "Please send an area or landmark. " + PROMPTS["location"]
        return {"location_text": t[:200]}, None

    if current_step == "gender":
        # Deliberately never an error: skip is a first-class answer, and so is
        # anything unrecognised.
        return {"gender": parse_gender(t)}, None

    return None, "Sorry, that report is already complete. Reply START to begin again."


# --------------------------------------------------------------------------
# Session persistence
# --------------------------------------------------------------------------

def purge_expired_sessions(client=None) -> int:
    """Discard incomplete conversations idle past the timeout."""
    client = client or get_client()
    cutoff = _iso(_now() - timedelta(minutes=SESSION_TIMEOUT_MINUTES))
    resp = client.table("sms_sessions").delete().lt("last_activity_at", cutoff).execute()
    return len(resp.data or [])


def get_session(client, phash: str):
    resp = client.table("sms_sessions").select("*").eq("phone_hash", phash).limit(1).execute()
    return (resp.data or [None])[0]


def start_session(client, phash: str) -> dict:
    resp = client.table("sms_sessions").insert({
        "phone_hash": phash,
        "current_step": "quality",
        "answers": {},
    }).execute()
    return resp.data[0]


def handle_inbound(number: str, text: str, client=None) -> str:
    """
    Advance the conversation for one inbound SMS and return the reply to send.

    `text` of START (or an empty inbox) opens a new report.
    """
    client = client or get_client()
    phash = phone_hash(number)
    if not phash:
        return "NetRange could not read that number. Please try again later."

    purge_expired_sessions(client)
    t = _clean(text)
    existing = get_session(client, phash)

    # A new session, or an explicit restart. An inbound SMS is the only way to
    # open a conversation -- there is no proactive send -- so the first thing a
    # person sends IS their first answer.
    #
    # Discarding it and re-asking the opening question would waste a paid SMS on
    # every single report, and would strand anyone whose first message was not a
    # number. Someone who opens with "MTN" or "Osu" would then be judged against
    # the 1-10 question forever, with no way out except START. So: try to read
    # the opening message as a rating, and only ask the question when it is not
    # one. Falling through to the question costs nothing extra, because the
    # unreadable text was never going to be a valid answer.
    if not existing or re.fullmatch(r"(?i)\s*(start|report|hello|hi)\s*", t or ""):
        if existing:
            client.table("sms_sessions").delete().eq("phone_hash", phash).execute()
        start_session(client, phash)
        first = parse_rating(t) if not re.fullmatch(r"(?i)\s*(start|report|hello|hi)\s*", t or "") else None
        if first is None:
            return PROMPTS["quality"]
        # They led with their rating; move the session on rather than repeat it.
        client.table("sms_sessions").update({
            "answers": {"quality_rating": first},
            "current_step": next_step("quality"),
            "last_activity_at": _iso(_now()),
        }).eq("phone_hash", phash).execute()
        return PROMPTS[next_step("quality")]

    if t.lower() in ("cancel", "stop", "quit"):
        client.table("sms_sessions").delete().eq("phone_hash", phash).execute()
        return "Report cancelled. Reply START any time to begin a new one."

    answers = dict(existing.get("answers") or {})
    step = existing.get("current_step") or "quality"
    patch, err = apply_answer(step, t)
    if err:
        return err

    answers.update(patch)
    nxt = next_step(step)
    if nxt:
        client.table("sms_sessions").update({
            "answers": answers, "current_step": nxt, "last_activity_at": _iso(_now()),
        }).eq("phone_hash", phash).execute()
        return PROMPTS[nxt]

    # Resolve the location BEFORE attributing, and keep the result on answers.
    # save_report used to geocode for itself, after attribution had already run
    # -- so attribute_site was handed answers.get("lat"), which was never set,
    # and returned None for every report ever submitted. The function was
    # correct; it was simply never given the coordinates to work with.
    lat, lon = geocode_free_text(answers.get("location_text") or "")
    answers["lat"], answers["lon"] = lat, lon

    site_id = attribute_site(client, answers.get("carrier"), lat, lon)
    client.table("sms_sessions").delete().eq("phone_hash", phash).execute()
    save_report(client, phash, answers, site_id)
    return (
        "Thank you. Your report has been recorded and will only be shown in "
        "aggregate, grouped with other people in the same area."
    )


# --------------------------------------------------------------------------
# Attribution + geocoding
# --------------------------------------------------------------------------

def attribute_site(client, carrier: str | None, lat=None, lon=None):
    """
    Which opted-in site does this report belong to?

    Returns None unless it can be tied to a specific place.

    Earlier this matched on carrier name alone. That was wrong in a way the
    docstring here used to warn about while doing it anyway: a venue registering
    "MTN" would have been handed every MTN report in the country -- its
    competitors' venues, and every road outside its own walls -- presented on
    its dashboard as its own. Carrier is not a location.

    So a report is attributed only when it has coordinates that fall inside the
    site we already know about, and the carrier matches too. A report with no
    location, or one whose location no registered site covers, returns None and
    feeds the general aggregate only. Under-attributing is the safe direction:
    the cost is a quieter dashboard, not a competitor's complaints on your wall.
    """
    if not carrier or lat is None or lon is None:
        return None
    want = _norm_carrier(carrier)
    if not want:
        return None
    resp = (
        client.table("widget_sites")
        .select("id,sms_carrier,lat,lon")
        .eq("sms_reporting_enabled", True)
        .execute()
    )
    for site in resp.data or []:
        site_carrier = (site.get("sms_carrier") or "").strip()
        if not site_carrier or _norm_carrier(site_carrier) != want:
            continue
        site_lat, site_lon = site.get("lat"), site.get("lon")
        if site_lat is None or site_lon is None:
            # Registered without a location we can test against. Refuse rather
            # than guess; a wrong guess here is the exact leak this guards.
            continue
        if (_cell_id(float(lat), float(lon), DEFAULT_CELL_SIZE_M)[0]
                == _cell_id(float(site_lat), float(site_lon), DEFAULT_CELL_SIZE_M)[0]):
            return site["id"]
    return None


def geocode_free_text(text: str) -> tuple[float | None, float | None]:
    """
    Turn "Cantonments" into coordinates.

    Two paths, in order:

    1. The offline table in geocoding.py, read backwards. No network call, no
       third party, no new disclosure -- a person naming a neighbourhood is
       naming a place, not a position, and the result is only ever used to pick
       the cell a report falls into.
    2. SMS_FORWARD_GEOCODER_URL, if an operator has explicitly set one. This
       sends the raw text to a third party, which is a disclosure the SMS
       consent prompt does not make, so it stays OFF by default and the offline
       table is what makes the feature usable without it.

    Returns (None, None) for anything unrecognised, and the caller stores the
    raw text regardless -- so the report still feeds carrier and frustration
    aggregates even when it cannot be placed.
    """
    import geocoding

    hit = geocoding.forward_geocode(text)
    if hit != (None, None):
        return hit

    url = os.environ.get("SMS_FORWARD_GEOCODER_URL")
    if not url or not text:
        return None, None
    try:
        import requests
        resp = requests.get(url, params={"q": text}, timeout=5)
        if resp.status_code != 200:
            return None, None
        data = resp.json()
        lat, lon = data.get("lat"), data.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon)
    except Exception:
        pass
    return None, None


def save_report(client, phash: str, answers: dict, site_id) -> dict:
    # Prefer what the caller already resolved. Geocoding twice would be
    # wasteful at best and, with an operator-configured third-party geocoder,
    # would send the same person's location to that third party twice.
    lat, lon = answers.get("lat"), answers.get("lon")
    if lat is None or lon is None:
        lat, lon = geocode_free_text(answers.get("location_text") or "")
    row = {
        "widget_site_id": site_id,
        "phone_hash": phash,
        "quality_rating": answers.get("quality_rating"),
        "frustration": answers.get("frustration"),
        "gender": answers.get("gender"),
        "carrier": answers.get("carrier"),
        "location_text": answers.get("location_text"),
        "lat": lat,
        "lon": lon,
    }
    resp = client.table("sms_reports").insert(row).execute()
    return (resp.data or [row])[0]


# --------------------------------------------------------------------------
# k-anonymity -- the same gate analytics.py applies to scans
# --------------------------------------------------------------------------

def aggregate_sms_cells(reports: list[dict], cell_size_m: float = 500.0,
                        min_contributors: int = MIN_DEVICES_PER_CELL) -> list[dict]:
    """
    Group SMS reports into cells and suppress any with too few contributors.

    Deliberately reuses analytics.MIN_DEVICES_PER_CELL rather than inventing a
    second, laxer threshold. Gender and frustration broken down by location is
    precisely the small-n breakdown that suppression exists for: a cell of three
    people is already the minimum, and a cell of one would otherwise reveal an
    individual's rating and mood.

    Contributors are counted by phone_hash -- the keyed digest, so it counts
    distinct reporters without ever revealing a number. It is used here as a
    counting token and nothing else.
    """
    from collections import defaultdict
    cells = defaultdict(lambda: {
        "quality": [], "frustration": [], "genders": [],
        "contributors": set(), "lat_sum": 0.0, "lon_sum": 0.0, "n": 0,
    })
    for r in reports or []:
        lat, lon = r.get("lat"), r.get("lon")
        if lat is None or lon is None:
            continue
        key, _, _ = _cell_id(lat, lon, cell_size_m)
        c = cells[key]
        if r.get("quality_rating") is not None:
            c["quality"].append(r["quality_rating"])
        if r.get("frustration") is not None:
            c["frustration"].append(r["frustration"])
        if r.get("gender"):
            c["genders"].append(r["gender"])
        if r.get("phone_hash"):
            c["contributors"].add(r["phone_hash"])
        c["lat_sum"] += lat
        c["lon_sum"] += lon
        c["n"] += 1

    out = []
    for c in cells.values():
        if len(c["contributors"]) < min_contributors:
            continue  # suppressed: too few distinct reporters to publish safely
        out.append({
            "lat": round(c["lat_sum"] / c["n"], 5),
            "lon": round(c["lon_sum"] / c["n"], 5),
            "quality_avg": round(sum(c["quality"]) / len(c["quality"]), 2) if c["quality"] else None,
            "frustration_avg": round(sum(c["frustration"]) / len(c["frustration"]), 2) if c["frustration"] else None,
            "gender_split": {g: c["genders"].count(g) for g in sorted(set(c["genders"]))},
            "report_count": c["n"],
            "contributor_count": len(c["contributors"]),
        })
    return out
