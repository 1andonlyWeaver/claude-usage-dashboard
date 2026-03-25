"""
FastAPI server for Claude Code usage dashboard.
"""
import os
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import JSONResponse

import db

BASE_DIR = Path(__file__).parent
QUOTA_FILE = Path(os.path.expanduser("~")) / "AppData" / "Local" / "Temp" / "claude-statusline-quota-weaverjc.json"

app = FastAPI(title="Claude Usage Dashboard")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Ingest state
_ingest_lock = threading.Lock()
_ingest_status = {"running": False, "progress": 0, "total": 0, "done": False, "error": None}


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


def _read_quota_data() -> dict | None:
    """Read quota JSON file, return the data dict or None on failure."""
    if not QUOTA_FILE.exists():
        return None
    try:
        raw = json.loads(QUOTA_FILE.read_text())
        return raw.get("data", raw)
    except Exception:
        return None


@app.on_event("startup")
async def startup():
    """Kick off ingest if DB is missing or stale."""
    stats = db.db_stats()
    if not stats.get("exists") or stats.get("message_count", 0) == 0:
        thread = threading.Thread(target=_run_ingest_background, daemon=True)
        thread.start()
    else:
        _ingest_status["done"] = True


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/quota")
async def get_quota():
    """Read the live quota JSON file."""
    data = _read_quota_data()
    if data is None:
        return JSONResponse({"error": "Quota file not found", "five_hour_pct": 0, "seven_day_pct": 0})
    return data


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

    The window boundaries are derived from the quota reset times in the quota file.
    Timestamps are converted to local time to match the DB's timestamp column.

    Returns:
        window_start, window_end: local-time ISO strings
        quota_pct: current percentage of quota used
        bucket_minutes: bucket size used
        buckets: list of {time, group, tokens}
    """
    quota = _read_quota_data()

    # Determine window boundaries
    now_local = datetime.now()
    if quota and type == "5h" and quota.get("five_hour_resets_at"):
        try:
            resets_utc = datetime.fromisoformat(quota["five_hour_resets_at"])
            resets_local = resets_utc.astimezone().replace(tzinfo=None)
            window_end = resets_local
            window_start = resets_local - timedelta(hours=5)
            quota_pct = quota.get("five_hour_pct", 0)
        except Exception:
            window_end = now_local
            window_start = now_local - timedelta(hours=5)
            quota_pct = 0
    elif quota and type == "7d" and quota.get("seven_day_resets_at"):
        try:
            resets_utc = datetime.fromisoformat(quota["seven_day_resets_at"])
            resets_local = resets_utc.astimezone().replace(tzinfo=None)
            window_end = resets_local
            window_start = resets_local - timedelta(days=7)
            quota_pct = quota.get("seven_day_pct", 0)
        except Exception:
            window_end = now_local
            window_start = now_local - timedelta(days=7)
            quota_pct = 0
    else:
        # Fallback: no quota file
        window_end = now_local
        window_start = now_local - (timedelta(hours=5) if type == "5h" else timedelta(days=7))
        quota_pct = 0

    bucket_minutes = 5 if type == "5h" else 60
    group_by_param = None if group_by == "none" else group_by

    ws = window_start.strftime('%Y-%m-%dT%H:%M:%S')
    we = window_end.strftime('%Y-%m-%dT%H:%M:%S')

    buckets = db.window_tokens(ws, we, bucket_minutes, group_by_param)

    return {
        "window_start": ws,
        "window_end": we,
        "quota_pct": quota_pct,
        "bucket_minutes": bucket_minutes,
        "buckets": buckets,
    }


if __name__ == "__main__":
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("app:app", host="127.0.0.1", port=args.port, reload=False)
