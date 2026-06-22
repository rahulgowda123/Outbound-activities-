"""
HubSpot Rep Dashboard - Flask web app.

Pulls SMB Team contacts whose lead_source is one of the 21 sources from
the filter screenshot, then per rep counts:
    contacts, calls made, emails sent, connected, opportunities.

A date picker filters calls / emails / connected / opportunities to
activities or deals that happened ON that day. Contacts is always the
rep's full pool of qualifying contacts.

The data fetch (contacts + associations + engagement details + deals) is
heavy, so the first /api/dashboard call kicks off a background refresh
and the frontend polls /api/job/<id> for progress. Result is cached
in-memory for an hour.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from requests.adapters import HTTPAdapter

# ---------- Paths (work both from source and from PyInstaller .exe) ----------

def _bundle_dir() -> Path:
    """Where templates/static live. Inside the unpacked PyInstaller bundle
    when frozen, otherwise next to this file."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent


def _user_dir() -> Path:
    """Where .env lives. Next to the .exe when frozen, otherwise next to
    this file. Putting .env outside the bundle lets the user edit it."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# ---------- Config ----------

load_dotenv(_user_dir() / ".env")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip()
# If APP_PASSWORD is set (production), all routes require HTTP Basic Auth.
# If unset (local dev), no auth is enforced.
APP_USERNAME = os.environ.get("APP_USERNAME", "team").strip()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
WARMUP_ON_STARTUP = os.environ.get("WARMUP_ON_STARTUP", "").lower() in ("1", "true", "yes")
BASE = "https://api.hubapi.com"

SMB_TEAM_ID = "43195955"

# 20 lead sources for the Current view ("Sales extension" excluded per request).
LEAD_SOURCE_VALUES = [
    "Contact",
    "Cold Call",
    "Inbound Call",
    "0365-2022",
    "M365 conference leads 2023",
    "Outbound SDR",
    "Linkedin",
    "Google NXT",
    "Outbound Source",
    "ZoomInfo",
    "other",
    "Google NXT 2025",
    "MO365 2025",
    "Mailchimp Campaign",
    "Outbound Email",
    "CF Manage Zoominfo",
    "Apollo & Clay",
    "Marketplace Lead",
    "M365-Con 2026",
    "Google Cloud Next2026",
]

# Old Outbound view = SMB Team contacts whose lead_source is NONE of the 20
# above AND not "Sales extension" - so Sales extension contacts are excluded
# from both views (matches the "remove from all places" requirement).
LEAD_SOURCE_EXCLUDE = LEAD_SOURCE_VALUES + ["Sales extension"]

VIEW_CURRENT = "current"
VIEW_OLD_OUTBOUND = "old_outbound"
VIEW_ALL_OUTBOUND = "all_outbound"
# Only Current and Old Outbound have their own HubSpot pulls. All Outbound is
# a virtual view that merges the two cached datasets.
PRIMARY_VIEWS = (VIEW_CURRENT, VIEW_OLD_OUTBOUND)
VIEWS = (VIEW_CURRENT, VIEW_OLD_OUTBOUND, VIEW_ALL_OUTBOUND)

# SMB Team reps (Chitradip Saha removed per request).
REPS: list[tuple[str, str]] = [
    ("Vicky Cariappa", "1666358904"),
    ("Yogesh Vig", "81629252"),
    ("Kritika Gupta", "81998159"),
    ("Rutuja Kawade", "89997288"),
    ("Aparajit Jha", "91950110"),
    ("Divyansh Singh", "91643713"),
    ("Lennis Brown", "89333902"),
]
REP_NAME_BY_OWNER = {oid: name for name, oid in REPS}
SMB_OWNER_IDS = set(REP_NAME_BY_OWNER.keys())

# HubSpot stock "Connected" call disposition UUID.
CONNECTED_DISPOSITION_ID = "f240bbac-87c9-4f6e-bf70-924b57d47db7"

# In-memory cache, one slot per *primary* view. All Outbound is computed on
# the fly by merging the two primary caches.
CACHES: dict[str, dict[str, Any]] = {v: {"fetched_at": 0.0, "data": None} for v in PRIMARY_VIEWS}
CACHE_TTL_SEC = 3600


def _merged_data() -> dict[str, Any] | None:
    """Return the union of Current + Old Outbound cached datasets, or None if
    either is missing. Contact IDs are disjoint between the two views so a
    plain dict union is correct."""
    cur = CACHES[VIEW_CURRENT]["data"]
    old = CACHES[VIEW_OLD_OUTBOUND]["data"]
    if cur is None or old is None:
        return None
    merged: dict[str, Any] = {}
    for key in ("contact_owner", "contact_lead_source",
                "c2calls", "c2emails", "c2deals",
                "call_props", "email_props", "deal_props"):
        merged[key] = {**cur.get(key, {}), **old.get(key, {})}
    return merged


def _data_for_view(view: str) -> tuple[dict[str, Any] | None, float]:
    """Return (data, fetched_at) for the requested view. For All Outbound,
    fetched_at is the older of the two primary fetched_at values."""
    if view == VIEW_ALL_OUTBOUND:
        cur = CACHES[VIEW_CURRENT]
        old = CACHES[VIEW_OLD_OUTBOUND]
        if cur["data"] is None or old["data"] is None:
            return None, 0.0
        return _merged_data(), min(cur["fetched_at"] or 0, old["fetched_at"] or 0)
    c = CACHES.get(view)
    if not c:
        return None, 0.0
    return c["data"], c["fetched_at"]


def _view_arg() -> str:
    v = (request.args.get("view") or VIEW_CURRENT).lower()
    return v if v in VIEWS else VIEW_CURRENT

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


# ---------- HubSpot helpers (copied / extended from smb_activity_report.py) ----------


# Shared session with connection pooling. Reuses TCP/TLS connections across
# all HubSpot calls (was opening a fresh handshake per call before, adding
# ~500ms per request - across 2k+ calls in a refresh that's 15+ minutes of
# wasted handshake time).
_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json"})
_adapter = HTTPAdapter(pool_connections=24, pool_maxsize=24, max_retries=0)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)

# Counters surfaced via /api/status for visibility into rate-limit health
_RETRY_STATS = {"429_hits": 0, "5xx_hits": 0}
_RETRY_STATS_LOCK = threading.Lock()


def _bump(key: str) -> None:
    with _RETRY_STATS_LOCK:
        _RETRY_STATS[key] = _RETRY_STATS.get(key, 0) + 1


def _post(url: str, payload: dict, max_retries: int = 6) -> dict:
    """POST with connection reuse and HubSpot-aware retry. Honors the
    Retry-After header on 429 so we wait exactly as long as HubSpot asks."""
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}
    last_resp = None
    for attempt in range(max_retries):
        resp = _SESSION.post(url, headers=headers, json=payload, timeout=90)
        last_resp = resp
        if resp.status_code == 429:
            _bump("429_hits")
            # HubSpot returns Retry-After (seconds). Cap at 10s so a hostile
            # value can't stall the whole refresh.
            try:
                wait = min(float(resp.headers.get("Retry-After", "1")), 10.0)
            except ValueError:
                wait = 1.0
            time.sleep(max(wait, 0.5))
            continue
        if resp.status_code >= 500:
            _bump("5xx_hits")
            time.sleep(1 + attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    if last_resp is not None:
        last_resp.raise_for_status()
    return {}


def _date_to_ms_range(d: date) -> tuple[int, int]:
    start = int(datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000) + 999
    return start, end


def _current_year_start_ms() -> int:
    """Jan 1 of the current UTC year, 00:00:00, in milliseconds.
    Used as the createdate ceiling for the Old Outbound view."""
    y = datetime.now(timezone.utc).year
    return int(datetime(y, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _ms_to_date(ms_str: str | None) -> date | None:
    if not ms_str:
        return None
    try:
        # HubSpot returns ISO strings for hs_timestamp; epoch ms for createdate.
        if isinstance(ms_str, str) and "T" in ms_str:
            return datetime.fromisoformat(ms_str.replace("Z", "+00:00")).date()
        return datetime.fromtimestamp(int(ms_str) / 1000.0, tz=timezone.utc).date()
    except Exception:
        return None


def fetch_qualifying_contacts(view: str, progress=None) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({contact_id: owner_id}, {contact_id: lead_source}) for SMB Team
    contacts. Current view uses lead_source IN the 20 sources; Old Outbound
    uses lead_source NOT_IN the 21-item exclude list."""
    url = f"{BASE}/crm/v3/objects/contacts/search"
    owners: dict[str, str] = {}
    sources: dict[str, str] = {}
    after = None
    page = 0
    if view == VIEW_OLD_OUTBOUND:
        # Old Outbound = NONE-OF the 21 sources, SMB Team, AND createdate before
        # Jan 1 of the current year (matches the user's HubSpot filter screenshot).
        filters = [
            {"propertyName": "lead_source", "operator": "NOT_IN", "values": LEAD_SOURCE_EXCLUDE},
            {"propertyName": "hubspot_team_id", "operator": "EQ", "value": SMB_TEAM_ID},
            {"propertyName": "createdate", "operator": "LT", "value": str(_current_year_start_ms())},
        ]
    else:
        filters = [
            {"propertyName": "lead_source", "operator": "IN", "values": LEAD_SOURCE_VALUES},
            {"propertyName": "hubspot_team_id", "operator": "EQ", "value": SMB_TEAM_ID},
        ]
    while True:
        page += 1
        body = {
            "filterGroups": [{"filters": filters}],
            "properties": ["hubspot_owner_id", "lead_source"],
            "limit": 100,
            "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        data = _post(url, body)
        for c in data.get("results", []):
            cid = str(c.get("id"))
            props = c.get("properties") or {}
            owners[cid] = str(props.get("hubspot_owner_id") or "")
            sources[cid] = str(props.get("lead_source") or "")
        if progress:
            progress(f"Fetched contacts: page {page}, running total {len(owners):,}")
        nxt = data.get("paging", {}).get("next")
        if not nxt:
            break
        after = nxt.get("after")
        if len(owners) >= 30000:
            break
    return owners, sources


def batch_associations(from_type: str, to_type: str, ids: list[str], progress=None, label="") -> dict[str, list[str]]:
    url = f"{BASE}/crm/v4/associations/{from_type}/{to_type}/batch/read"
    chunks = [ids[i : i + 100] for i in range(0, len(ids), 100)]
    if not chunks:
        return {}

    def fetch_chunk(chunk):
        return _post(url, {"inputs": [{"id": str(x)} for x in chunk]})

    result: dict[str, list[str]] = defaultdict(list)
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for data in ex.map(fetch_chunk, chunks):
            done += 1
            for row in data.get("results", []):
                src = str(row.get("from", {}).get("id"))
                for t in row.get("to", []):
                    result[src].append(str(t.get("toObjectId")))
            if progress and (done % 4 == 0 or done == len(chunks)):
                progress(f"{label} associations: batch {done}/{len(chunks)}")
    return result


def batch_read(obj_type: str, ids, props: list[str], progress=None, label="") -> dict[str, dict]:
    url = f"{BASE}/crm/v3/objects/{obj_type}/batch/read"
    unique = list({str(x) for x in ids})
    chunks = [unique[i : i + 100] for i in range(0, len(unique), 100)]
    if not chunks:
        return {}

    def fetch_chunk(chunk):
        return _post(url, {"properties": props, "inputs": [{"id": x} for x in chunk]})

    out: dict[str, dict] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for data in ex.map(fetch_chunk, chunks):
            done += 1
            for r in data.get("results", []):
                out[str(r.get("id"))] = r.get("properties", {})
            if progress and (done % 4 == 0 or done == len(chunks)):
                progress(f"{label} details: batch {done}/{len(chunks)}")
    return out


# ---------- Aggregation ----------


def _fetch_all(view: str, progress) -> dict[str, Any]:
    """One full pull for a given view: contacts + all associated calls/emails/
    deals, then enrich each engagement and deal with the props we care about.

    Speed-tuned: 3 association reads run concurrently, then 3 detail reads
    run concurrently. Each batch internally uses 12 worker threads.
    """
    started = time.time()
    progress(f"Pulling qualifying contacts ({view}) ...")
    contact_owner, contact_lead_source = fetch_qualifying_contacts(view, progress)
    contact_ids = list(contact_owner.keys())
    t1 = time.time() - started
    progress(f"Got {len(contact_ids):,} contacts in {t1:.1f}s. Reading calls/emails/deals associations in parallel ...")

    with ThreadPoolExecutor(max_workers=3) as ex:
        c2calls_f = ex.submit(batch_associations, "contacts", "calls", contact_ids, progress, "call")
        c2emails_f = ex.submit(batch_associations, "contacts", "emails", contact_ids, progress, "email")
        c2deals_f = ex.submit(batch_associations, "contacts", "deals", contact_ids, progress, "deal")
        c2calls = c2calls_f.result()
        c2emails = c2emails_f.result()
        c2deals = c2deals_f.result()

    call_ids = list({x for v in c2calls.values() for x in v})
    email_ids = list({x for v in c2emails.values() for x in v})
    deal_ids = list({x for v in c2deals.values() for x in v})

    t2 = time.time() - started
    progress(f"Associations done at {t2:.1f}s. Reading {len(call_ids):,} calls, {len(email_ids):,} emails, {len(deal_ids):,} deals in parallel ...")

    with ThreadPoolExecutor(max_workers=3) as ex:
        cp_f = ex.submit(
            batch_read, "calls", call_ids,
            ["hs_timestamp", "hubspot_owner_id", "hs_call_direction", "hs_call_disposition"],
            progress, "call",
        )
        ep_f = ex.submit(
            batch_read, "emails", email_ids,
            ["hs_timestamp", "hubspot_owner_id", "hs_email_direction"],
            progress, "email",
        )
        dp_f = ex.submit(
            batch_read, "deals", deal_ids,
            ["dealname", "amount", "dealstage", "closedate", "createdate", "hubspot_owner_id", "pipeline"],
            progress, "deal",
        )
        call_props = cp_f.result()
        email_props = ep_f.result()
        deal_props = dp_f.result()

    total = time.time() - started
    progress(f"Done in {total:.1f}s.")
    return {
        "contact_owner": contact_owner,
        "contact_lead_source": contact_lead_source,
        "c2calls": c2calls,
        "c2emails": c2emails,
        "c2deals": c2deals,
        "call_props": call_props,
        "email_props": email_props,
        "deal_props": deal_props,
    }


def _activity_date(props: dict) -> date | None:
    ts = props.get("hs_timestamp")
    return _ms_to_date(ts)


def _deal_created_date(props: dict) -> date | None:
    return _ms_to_date(props.get("createdate"))


def _in_range(d: date | None, from_d: date | None, to_d: date | None) -> bool:
    if d is None:
        return False
    if from_d and d < from_d:
        return False
    if to_d and d > to_d:
        return False
    return True


def _aggregate(data: dict, from_d: date | None, to_d: date | None) -> list[dict]:
    """Build per-rep rows. If from_d / to_d are set, calls/emails/connected/opps
    are restricted to activity that falls in that range. Contacts is always
    the rep's full qualifying pool."""
    contact_owner = data["contact_owner"]
    c2calls = data["c2calls"]
    c2emails = data["c2emails"]
    c2deals = data["c2deals"]
    call_props = data["call_props"]
    email_props = data["email_props"]
    deal_props = data["deal_props"]

    has_range = bool(from_d or to_d)
    per_owner: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"contacts": 0, "calls": 0, "emails": 0, "connected_contacts": set(), "opportunities": 0}
    )

    for cid, oid in contact_owner.items():
        if oid not in SMB_OWNER_IDS:
            continue
        per_owner[oid]["contacts"] += 1
        connected_hit = False

        for call_id in c2calls.get(cid, []):
            p = call_props.get(call_id) or {}
            d = _activity_date(p)
            if has_range and not _in_range(d, from_d, to_d):
                continue
            # only count outbound calls as "calls made". If direction missing, count it.
            direction = (p.get("hs_call_direction") or "").upper()
            if direction in ("", "OUTBOUND"):
                per_owner[oid]["calls"] += 1
            if (p.get("hs_call_disposition") or "") == CONNECTED_DISPOSITION_ID:
                connected_hit = True

        for email_id in c2emails.get(cid, []):
            p = email_props.get(email_id) or {}
            d = _activity_date(p)
            if has_range and not _in_range(d, from_d, to_d):
                continue
            direction = (p.get("hs_email_direction") or "").upper()
            if direction in ("EMAIL", "FORWARDED_EMAIL", ""):
                per_owner[oid]["emails"] += 1
            if direction == "INCOMING_EMAIL":
                connected_hit = True

        if connected_hit:
            per_owner[oid]["connected_contacts"].add(cid)

        for deal_id in c2deals.get(cid, []):
            p = deal_props.get(deal_id) or {}
            d = _deal_created_date(p)
            if has_range and not _in_range(d, from_d, to_d):
                continue
            per_owner[oid]["opportunities"] += 1

    rows = []
    for name, oid in REPS:
        agg = per_owner.get(oid, {"contacts": 0, "calls": 0, "emails": 0, "connected_contacts": set(), "opportunities": 0})
        rows.append(
            {
                "owner_id": oid,
                "rep": name,
                "contacts": agg["contacts"],
                "calls": agg["calls"],
                "emails": agg["emails"],
                "connected": len(agg["connected_contacts"]),
                "opportunities": agg["opportunities"],
            }
        )
    rows.sort(key=lambda r: r["calls"] + r["emails"], reverse=True)
    return rows


def _list_opportunities(data: dict, from_d: date | None, to_d: date | None) -> list[dict]:
    """Return deals associated with the qualifying contacts, joined with
    rep name + the lead source(s) of the originating contact(s). Optionally
    filter by createdate inside the range."""
    contact_owner = data["contact_owner"]
    contact_lead_source = data.get("contact_lead_source", {})
    c2deals = data["c2deals"]
    deal_props = data["deal_props"]
    has_range = bool(from_d or to_d)

    # Reverse the contact->deals map so we can find every contact a deal
    # came from (a deal can be linked to several of our qualifying contacts).
    deal_to_contacts: dict[str, list[str]] = defaultdict(list)
    for cid, deal_ids in c2deals.items():
        if contact_owner.get(cid) not in SMB_OWNER_IDS:
            continue
        for deal_id in deal_ids:
            deal_to_contacts[deal_id].append(cid)

    seen: set[str] = set()
    out: list[dict] = []
    for cid, oid in contact_owner.items():
        if oid not in SMB_OWNER_IDS:
            continue
        rep_name = REP_NAME_BY_OWNER.get(oid, "Unknown")
        for deal_id in c2deals.get(cid, []):
            if deal_id in seen:
                continue
            seen.add(deal_id)
            p = deal_props.get(deal_id) or {}
            created = _deal_created_date(p)
            if has_range and not _in_range(created, from_d, to_d):
                continue
            # collect the distinct lead sources of the contacts this deal is tied to
            sources_set: list[str] = []
            for other_cid in deal_to_contacts.get(deal_id, [cid]):
                ls = contact_lead_source.get(other_cid) or ""
                if ls and ls not in sources_set:
                    sources_set.append(ls)
            out.append(
                {
                    "deal_id": deal_id,
                    "rep": rep_name,
                    "owner_id": oid,
                    "dealname": p.get("dealname") or "(no name)",
                    "amount": p.get("amount"),
                    "dealstage": p.get("dealstage"),
                    "closedate": _ms_to_date(p.get("closedate")).isoformat() if _ms_to_date(p.get("closedate")) else None,
                    "createdate": created.isoformat() if created else None,
                    "contact_id": cid,
                    "lead_source": ", ".join(sources_set) if sources_set else "",
                }
            )
    out.sort(key=lambda d: (d["createdate"] or "", d["rep"]), reverse=True)
    return out


# ---------- Background job machinery ----------


def _new_job() -> str:
    jid = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[jid] = {"status": "running", "message": "Starting ...", "error": None}
    return jid


def _set_job(jid: str, **fields):
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid].update(fields)


def _start_refresh_job(view: str) -> str:
    """Refresh one primary view, or both if view='all_outbound'."""
    jid = _new_job()

    def worker():
        def progress(msg: str):
            _set_job(jid, message=msg)

        try:
            views_to_run = list(PRIMARY_VIEWS) if view == VIEW_ALL_OUTBOUND else [view]
            for i, v in enumerate(views_to_run, 1):
                if len(views_to_run) > 1:
                    progress(f"[{i}/{len(views_to_run)}] Pulling {v} ...")
                data = _fetch_all(v, progress)
                CACHES[v]["data"] = data
                CACHES[v]["fetched_at"] = time.time()
            _set_job(jid, status="done", message="Ready.")
        except requests.HTTPError as e:
            detail = ""
            try:
                detail = e.response.text[:400] if e.response is not None else ""
            except Exception:
                pass
            _set_job(jid, status="error", error=f"HTTP {e.response.status_code if e.response is not None else '?'}: {detail or str(e)}")
        except Exception as e:
            _set_job(jid, status="error", error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jid


# ---------- Flask routes ----------

app = Flask(
    __name__,
    static_folder=str(_bundle_dir() / "static"),
    template_folder=str(_bundle_dir() / "templates"),
)


def require_auth(f):
    """HTTP Basic Auth gate. No-op when APP_PASSWORD is empty (local dev)."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not APP_PASSWORD:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != APP_USERNAME or auth.password != APP_PASSWORD:
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Rep Dashboard"'},
            )
        return f(*args, **kwargs)
    return wrapped


# ---------- Background warmup (used in production via WARMUP_ON_STARTUP=true) ----------

_warmup_started = False
_warmup_lock = threading.Lock()


def _warmup_in_background() -> None:
    """Kick off a non-blocking fetch of both primary views so users hitting
    the deployed app see warm data instead of waiting for the slow pull.
    Safe to call repeatedly; only runs once per process."""
    global _warmup_started
    if not HUBSPOT_TOKEN:
        return
    with _warmup_lock:
        if _warmup_started:
            return
        _warmup_started = True

    def worker():
        try:
            for v in PRIMARY_VIEWS:
                if CACHES[v]["data"] is not None:
                    continue
                print(f"[warmup] Pulling {v} ...", flush=True)
                data = _fetch_all(v, lambda m: print(f"[warmup] [{v}] {m}", flush=True))
                CACHES[v]["data"] = data
                CACHES[v]["fetched_at"] = time.time()
            print("[warmup] Done. Cache is warm.", flush=True)
        except Exception as e:
            print(f"[warmup] FAILED: {e}", flush=True)

    threading.Thread(target=worker, daemon=True).start()


@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/api/status")
@require_auth
def api_status():
    views_info: dict[str, dict[str, Any]] = {}
    for v in PRIMARY_VIEWS:
        c = CACHES[v]
        views_info[v] = {
            "has_cache": c["data"] is not None,
            "fetched_at": c["fetched_at"],
            "cache_age_sec": int(time.time() - c["fetched_at"]) if c["fetched_at"] else None,
        }
    # All Outbound = both primary caches available
    both_ready = all(CACHES[v]["data"] is not None for v in PRIMARY_VIEWS)
    oldest_fetched = min((CACHES[v]["fetched_at"] for v in PRIMARY_VIEWS), default=0.0) if both_ready else 0.0
    views_info[VIEW_ALL_OUTBOUND] = {
        "has_cache": both_ready,
        "fetched_at": oldest_fetched,
        "cache_age_sec": int(time.time() - oldest_fetched) if both_ready and oldest_fetched else None,
    }
    return jsonify(
        {
            "has_token": bool(HUBSPOT_TOKEN),
            "views": views_info,
            "lead_sources": LEAD_SOURCE_VALUES,
            "lead_sources_exclude": LEAD_SOURCE_EXCLUDE,
            "smb_team_id": SMB_TEAM_ID,
            "reps": [{"name": n, "owner_id": o} for n, o in REPS],
            "retry_stats": dict(_RETRY_STATS),
        }
    )


@app.route("/api/refresh", methods=["POST"])
@require_auth
def api_refresh():
    if not HUBSPOT_TOKEN:
        return jsonify({"error": "HUBSPOT_TOKEN not set in .env"}), 400
    view = _view_arg()
    jid = _start_refresh_job(view)
    return jsonify({"job_id": jid, "view": view})


@app.route("/api/job/<jid>")
@require_auth
def api_job(jid):
    with JOBS_LOCK:
        job = JOBS.get(jid)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _read_range_args() -> tuple[date | None, date | None]:
    """Accept either ?from=YYYY-MM-DD&to=YYYY-MM-DD or legacy ?date=YYYY-MM-DD."""
    legacy = _parse_date(request.args.get("date"))
    if legacy:
        return legacy, legacy
    return _parse_date(request.args.get("from")), _parse_date(request.args.get("to"))


@app.route("/api/dashboard")
@require_auth
def api_dashboard():
    if not HUBSPOT_TOKEN:
        return jsonify({"error": "HUBSPOT_TOKEN not set in .env"}), 400
    view = _view_arg()
    data, fetched_at = _data_for_view(view)
    if data is None:
        return jsonify({"error": f"no data yet for view '{view}', POST /api/refresh?view={view} first"}), 409
    from_d, to_d = _read_range_args()
    rows = _aggregate(data, from_d, to_d)
    totals = {
        "contacts": sum(r["contacts"] for r in rows),
        "calls": sum(r["calls"] for r in rows),
        "emails": sum(r["emails"] for r in rows),
        "connected": sum(r["connected"] for r in rows),
        "opportunities": sum(r["opportunities"] for r in rows),
    }
    return jsonify(
        {
            "view": view,
            "rows": rows,
            "totals": totals,
            "from": from_d.isoformat() if from_d else None,
            "to": to_d.isoformat() if to_d else None,
            "fetched_at": fetched_at,
        }
    )


@app.route("/api/opportunities")
@require_auth
def api_opportunities():
    if not HUBSPOT_TOKEN:
        return jsonify({"error": "HUBSPOT_TOKEN not set in .env"}), 400
    view = _view_arg()
    data, _ = _data_for_view(view)
    if data is None:
        return jsonify({"error": f"no data yet for view '{view}', POST /api/refresh?view={view} first"}), 409
    from_d, to_d = _read_range_args()
    owner_id = request.args.get("owner_id")
    deals = _list_opportunities(data, from_d, to_d)
    if owner_id:
        deals = [d for d in deals if d["owner_id"] == owner_id]
    return jsonify(
        {
            "view": view,
            "deals": deals,
            "count": len(deals),
            "from": from_d.isoformat() if from_d else None,
            "to": to_d.isoformat() if to_d else None,
            "owner_id": owner_id,
        }
    )


@app.route("/healthz")
def healthz():
    """Unauthenticated liveness probe for hosting platforms (Render etc.)."""
    return jsonify({"ok": True, "has_token": bool(HUBSPOT_TOKEN)})


# Trigger background warmup when running under gunicorn / production.
if WARMUP_ON_STARTUP:
    _warmup_in_background()


if __name__ == "__main__":
    if not HUBSPOT_TOKEN:
        print("WARNING: HUBSPOT_TOKEN not set. Put it in .env next to app.py.")
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="127.0.0.1", port=port, debug=False)
