"""
FastAPI server for Claude Code usage dashboard.
"""
import os
import json
import threading
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, BackgroundTasks, HTTPException
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


def _run_ingest_background():
    from ingest import run_ingest
    global _ingest_status
    _ingest_status["running"] = True
    _ingest_status["error"] = None

    def progress_cb(i, total, path):
        _ingest_status["progress"] = i + 1
        _ingest_status["total"] = total

    try:
        stats = run_ingest(progress_callback=progress_cb)
        _ingest_status["done"] = True
        _ingest_status["stats"] = stats
    except Exception as e:
        _ingest_status["error"] = str(e)
    finally:
        _ingest_status["running"] = False


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
    if not QUOTA_FILE.exists():
        return JSONResponse({"error": "Quota file not found", "five_hour_pct": 0, "seven_day_pct": 0})
    try:
        data = json.loads(QUOTA_FILE.read_text())
        # File has {"timestamp": ..., "data": {...}} structure
        if "data" in data:
            return data["data"]
        return data
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ingest-status")
async def ingest_status():
    return _ingest_status


@app.post("/api/refresh")
async def refresh():
    """Trigger a re-ingest of JSONL files."""
    if _ingest_status.get("running"):
        return {"message": "Ingest already running"}
    thread = threading.Thread(target=_run_ingest_background, daemon=True)
    thread.start()
    return {"message": "Ingest started"}


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


if __name__ == "__main__":
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("app:app", host="127.0.0.1", port=args.port, reload=False)
