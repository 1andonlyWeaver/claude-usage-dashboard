"""
FastAPI server for Claude Code usage dashboard.
"""
import asyncio
import json
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import JSONResponse

import db

BASE_DIR = Path(__file__).parent
CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_API_BETA_HEADER = "oauth-2025-04-20"
CACHE_MAX_AGE = 180       # seconds before re-fetching
CACHE_MIN_RETRY = 300     # minimum seconds between failed attempts (5 min)
CACHE_MAX_RETRY = 3600    # maximum retry backoff (1 hour)
QUOTA_CACHE_FILE = Path(__file__).parent / "data" / "quota_cache.json"
QUOTA_CACHE_MAX_STALE = 600  # seconds: accept disk-cached data up to 10 min old on startup

app = FastAPI(title="Claude Usage Dashboard")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Ingest state
_ingest_lock = threading.Lock()
_ingest_status = {"running": False, "progress": 0, "total": 0, "done": False, "error": None}

# Usage API cache: holds the last successful API response + metadata
_usage_cache: dict = {
    "data": None,        # parsed response dict or None
    "fetched_at": 0.0,   # monotonic time of last successful fetch
    "retry_after": 0.0,  # monotonic time before which we should not retry
    "error": None,       # last error string if data is None
    "fail_count": 0,     # consecutive failure count for exponential backoff
}

# Throttle for quota snapshot writes (max 1 per 60 seconds)
_last_snapshot_time = 0.0


def _maybe_write_snapshot(five_pct: float, seven_pct: float) -> None:
    """Write a quota snapshot row if 60s have elapsed since the last write."""
    global _last_snapshot_time
    now = time.monotonic()
    if now - _last_snapshot_time < 60:
        return
    _last_snapshot_time = now
    try:
        ts = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        cutoff = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%dT%H:%M:%S')
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO quota_snapshots (timestamp, five_hour_pct, seven_day_pct) VALUES (?, ?, ?)",
            (ts, five_pct, seven_pct),
        )
        conn.execute("DELETE FROM quota_snapshots WHERE timestamp < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# Seed in-memory cache from disk on startup so restarts don't lose last known quota
try:
    _saved = json.loads(QUOTA_CACHE_FILE.read_text())
    if time.time() - _saved.get("time", 0) < QUOTA_CACHE_MAX_STALE:
        _usage_cache["data"] = _saved["data"]
        # Mark as stale so the next request will refresh, but non-None so fallback works
        _usage_cache["fetched_at"] = time.monotonic() - CACHE_MAX_AGE
except Exception:
    pass


def _run_ingest_background(force: bool = False):
    from ingest import run_ingest
    global _ingest_status
    _ingest_status["running"] = True
    _ingest_status["error"] = None

    def progress_cb(i, total, path):
        _ingest_status["progress"] = i + 1
        _ingest_status["total"] = total

    try:
        stats = run_ingest(progress_callback=progress_cb, force=force)
        _ingest_status["done"] = True
        _ingest_status["stats"] = stats
    except Exception as e:
        _ingest_status["error"] = str(e)
    finally:
        _ingest_status["running"] = False


def _periodic_ingest():
    """Run an incremental ingest quietly in the background, then reschedule."""
    from ingest import run_ingest
    try:
        if not _ingest_status.get("running"):
            run_ingest(force=False)
    except Exception:
        pass
    t = threading.Timer(90, _periodic_ingest)
    t.daemon = True
    t.start()


def _read_oauth_token() -> str | None:
    """Extract the OAuth access token from ~/.claude/.credentials.json."""
    try:
        raw = json.loads(CREDENTIALS_FILE.read_text())
        return raw.get("claudeAiOauth", {}).get("accessToken") or None
    except Exception:
        return None


def _fetch_usage_sync() -> dict:
    """Call the Anthropic usage API synchronously. Returns a result dict:
    On success: {"ok": True, "data": {...}, "retry_after": None}
    On rate-limit: {"ok": False, "error": "rate-limited", "retry_after": <seconds>}
    On other failure: {"ok": False, "error": "<message>", "retry_after": None}
    """
    token = _read_oauth_token()
    if not token:
        return {"ok": False, "error": "no-credentials", "retry_after": None}

    req = urllib.request.Request(
        USAGE_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": USAGE_API_BETA_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            rate_headers = {k: resp.headers[k] for k in resp.headers if 'ratelimit' in k.lower() or 'retry-after' in k.lower()}
            if rate_headers:
                print(f"[quota] rate-limit headers: {rate_headers}")
            return {"ok": True, "data": data, "retry_after": None, "rate_headers": rate_headers}
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after_raw = e.headers.get("Retry-After", "")
            try:
                retry_secs = int(retry_after_raw)
            except (ValueError, TypeError):
                retry_secs = 300
            return {"ok": False, "error": "rate-limited", "retry_after": retry_secs}
        return {"ok": False, "error": f"http-{e.code}", "retry_after": None}
    except Exception as ex:
        return {"ok": False, "error": str(ex), "retry_after": None}


async def _get_usage_data() -> dict:
    """Return cached usage data, refreshing from the API when the cache is stale.

    Returns a dict with keys: five_hour_pct, five_hour_resets_at,
    seven_day_pct, seven_day_resets_at, plus optional extra_usage_* fields.
    On error, includes an "error" key.
    """
    now = time.monotonic()
    cache = _usage_cache

    # Return in-memory cache if still fresh
    if cache["data"] and (now - cache["fetched_at"]) < CACHE_MAX_AGE:
        return cache["data"]

    # Respect rate-limit backoff
    if now < cache["retry_after"]:
        if cache["data"]:
            return cache["data"]
        return {"error": cache["error"] or "rate-limited",
                "five_hour_pct": 0, "seven_day_pct": 0}

    # Fetch in a thread so we don't block the event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _fetch_usage_sync)

    if result["ok"]:
        raw = result["data"]
        five = raw.get("five_hour") or {}
        seven = raw.get("seven_day") or {}
        extra = raw.get("extra_usage") or {}
        parsed = {
            "five_hour_pct": five.get("utilization", 0),
            "five_hour_resets_at": five.get("resets_at"),
            "seven_day_pct": seven.get("utilization", 0),
            "seven_day_resets_at": seven.get("resets_at"),
            "extra_usage_enabled": extra.get("is_enabled", False),
            "extra_usage_limit": extra.get("monthly_limit"),
            "extra_usage_used": extra.get("used_credits"),
            "extra_usage_utilization": extra.get("utilization"),
        }
        cache["data"] = parsed
        cache["fetched_at"] = now
        cache["retry_after"] = 0.0
        cache["error"] = None
        cache["fail_count"] = 0
        try:
            QUOTA_CACHE_FILE.write_text(json.dumps({"data": parsed, "time": time.time()}))
        except Exception:
            pass
        _maybe_write_snapshot(parsed["five_hour_pct"], parsed["seven_day_pct"])
        return parsed
    else:
        # Exponential backoff: double the wait per consecutive failure, capped at CACHE_MAX_RETRY
        cache["fail_count"] = cache.get("fail_count", 0) + 1
        api_retry = result.get("retry_after") or 0
        backoff = min(CACHE_MIN_RETRY * (2 ** (cache["fail_count"] - 1)), CACHE_MAX_RETRY)
        retry_secs = max(api_retry, backoff)
        cache["retry_after"] = now + retry_secs
        print(f"[quota] fetch failed ({result['error']}), fail #{cache['fail_count']}, retry in {retry_secs}s")
        cache["error"] = result["error"]
        # Return stale data if available, otherwise an error shell
        if cache["data"]:
            return {**cache["data"], "error": result["error"]}
        return {"error": result["error"], "five_hour_pct": 0, "seven_day_pct": 0}


@app.on_event("startup")
async def startup():
    """Kick off ingest if DB is missing or stale, then schedule periodic ingest."""
    stats = db.db_stats()
    if not stats.get("exists") or stats.get("message_count", 0) == 0:
        thread = threading.Thread(target=_run_ingest_background, daemon=True)
        thread.start()
    else:
        _ingest_status["done"] = True
    t = threading.Timer(90, _periodic_ingest)
    t.daemon = True
    t.start()


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/quota")
async def get_quota():
    """Fetch live quota data from the Anthropic usage API."""
    return await _get_usage_data()


@app.get("/api/ingest-status")
async def ingest_status():
    return _ingest_status


@app.post("/api/refresh")
async def refresh(force: bool = Query(default=False)):
    """Trigger a re-ingest of JSONL files.

    Use ?force=true to clear ingest_meta and re-process all files (needed after
    schema migrations or project name changes).
    """
    if _ingest_status.get("running"):
        return {"message": "Ingest already running"}
    thread = threading.Thread(target=lambda: _run_ingest_background(force=force), daemon=True)
    thread.start()
    return {"message": "Ingest started", "force": force}


@app.get("/api/daily")
async def daily(days: int = 90):
    return db.daily_tokens(days)


@app.get("/api/projects")
async def projects(days: int = 90):
    return db.by_project(days)


@app.get("/api/models")
async def models(days: int = 90):
    return db.by_model(days)


@app.get("/api/heatmap")
async def heatmap(days: int = 90):
    return db.session_heatmap(days)


@app.get("/api/sessions")
async def sessions(days: int = 30):
    return db.session_list(days)


@app.get("/api/session/{session_id}")
async def session_detail(session_id: str):
    data = db.session_detail(session_id)
    if not data:
        raise HTTPException(404, "Session not found")
    return data


@app.get("/api/rate")
async def rate(hours: int = 3):
    return db.recent_rate(hours)


@app.get("/api/cost")
async def cost(days: int = 30):
    return db.estimate_cost(days)


@app.get("/api/stats")
async def stats():
    return db.db_stats()


@app.get("/api/window")
async def window(
    type: str = Query(default="5h", pattern="^(5h|7d)$"),
    group_by: str = Query(default="none", pattern="^(none|token_type|project|model)$"),
):
    """Return token data bucketed within the current quota window.

    The window boundaries are derived from the Anthropic usage API reset times.
    Timestamps are converted to local time to match the DB's timestamp column.

    Returns:
        window_start, window_end: local-time ISO strings
        quota_pct: current percentage of quota used
        bucket_minutes: bucket size used
        buckets: list of {time, group, tokens}
    """
    quota = await _get_usage_data()

    # If quota API returned an error, try the disk cache for window boundaries
    if quota.get("error") and not quota.get("five_hour_resets_at"):
        try:
            disk = json.loads(QUOTA_CACHE_FILE.read_text()).get("data", {})
            # Merge cached resets_at and pct values the live response is missing
            for key in ("five_hour_resets_at", "seven_day_resets_at",
                        "five_hour_pct", "seven_day_pct"):
                if key not in quota or quota[key] is None:
                    quota[key] = disk.get(key)
        except Exception:
            pass

    now_local = datetime.now()
    if type == "5h" and quota.get("five_hour_resets_at"):
        try:
            resets_utc = datetime.fromisoformat(quota["five_hour_resets_at"])
            resets_local = resets_utc.astimezone().replace(tzinfo=None)
            window_end = resets_local
            window_start = resets_local - timedelta(hours=5)
            quota_pct = quota.get("five_hour_pct", 0)
        except Exception:
            window_end = now_local
            window_start = now_local - timedelta(hours=5)
            quota_pct = quota.get("five_hour_pct") or 0
    elif type == "7d" and quota.get("seven_day_resets_at"):
        try:
            resets_utc = datetime.fromisoformat(quota["seven_day_resets_at"])
            resets_local = resets_utc.astimezone().replace(tzinfo=None)
            window_end = resets_local
            window_start = resets_local - timedelta(days=7)
            quota_pct = quota.get("seven_day_pct", 0)
        except Exception:
            window_end = now_local
            window_start = now_local - timedelta(days=7)
            quota_pct = quota.get("seven_day_pct") or 0
    else:
        window_end = now_local
        window_start = now_local - (timedelta(hours=5) if type == "5h" else timedelta(days=7))
        pct_key = "five_hour_pct" if type == "5h" else "seven_day_pct"
        quota_pct = quota.get(pct_key) or 0

    bucket_minutes = 5 if type == "5h" else 60
    group_by_param = None if group_by == "none" else group_by

    ws = window_start.strftime('%Y-%m-%dT%H:%M:%S')
    we = window_end.strftime('%Y-%m-%dT%H:%M:%S')

    buckets = db.window_tokens(ws, we, bucket_minutes, group_by_param)

    calibration = db.calibrate_cost_per_pct(ws, we, type)
    other_buckets = None
    if calibration["calibrated"] and calibration.get("cost_per_pct"):
        other_buckets = db.compute_other_series(
            ws, we, type, bucket_minutes, calibration["cost_per_pct"], quota_pct
        )

    return {
        "window_start": ws,
        "window_end": we,
        "quota_pct": quota_pct,
        "bucket_minutes": bucket_minutes,
        "buckets": buckets,
        "value_type": "cost" if group_by_param == "model" else "tokens",
        "calibrated": calibration["calibrated"],
        "other_buckets": other_buckets,
    }


if __name__ == "__main__":
    import sys
    import uvicorn
    import argparse

    # When launched via pythonw.exe, stdout/stderr are None — redirect to log file
    if sys.stdout is None or sys.stderr is None:
        log_dir = BASE_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = open(log_dir / "dashboard.log", "a", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run("app:app", host="127.0.0.1", port=args.port, reload=False)
