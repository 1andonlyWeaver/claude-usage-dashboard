"""
Person-hours estimates for Claude Code session-days.

Claude, run through the `claude` CLI in print mode, judges how long a competent
professional would need to produce each session-day's outcomes without AI. Results
live in the person_hour_estimates table; db.py turns them into the card's figures.

Design: docs/superpowers/specs/2026-09-23-person-hours-view-design.md
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import db
from ingest import get_project_dirs, init_db, parse_timestamp

# ─── Settings ────────────────────────────────────────────────
JUDGE_MODEL = "sonnet"
IDLE_MINUTES = 30          # a session-day is judged once it has been quiet this long
QUOTA_PAUSE_PCT = 80       # skip ticks while the 5-hour quota is at or above this
MAX_CALLS_PER_HOUR = 20    # judge calls per rolling hour, backfill and live alike
MAX_CONCURRENCY = 2
MAX_PER_TICK = 10
TICK_SECONDS = 300
BACKFILL_DAYS = 90
PROMPT_VERSION = 1
SUMMARY_MAX_CHARS = 24000
MAX_ATTEMPTS = 3
MAX_HOURS = 500            # sanity cap on one session-day's estimate
CALL_TIMEOUT_S = 300
TOKEN_MIN_SECONDS = 600    # never start a call on an OAuth token with less life left than this
# DEFAULT_LEVERAGE and MIN_SAMPLES_FOR_LEVERAGE live in db.py, which does the aggregation.

SCHEDULED_PREFIX = "<scheduled-task"
_TAG_RE = re.compile(
    r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+|task-notification)\b[^>]*>.*?</\1>",
    re.S,
)


# ─── Reading session logs ────────────────────────────────────
def index_sessions(roots=None) -> dict:
    """Map session_id -> main transcript path across every ~/.claude/projects root."""
    index = {}
    for root in get_project_dirs() if roots is None else roots:
        try:
            for path in root.glob("*/*.jsonl"):
                index.setdefault(path.stem, path)
        except OSError:
            continue
    return index


def subagent_files(main: Path) -> list:
    """A session's subagent transcripts: <project>/<session_id>/subagents/*.jsonl."""
    return sorted((main.parent / main.stem / "subagents").glob("*.jsonl"))


def _iter_json(path: Path):
    """Parsed JSON objects from a JSONL file, skipping bad lines; nothing if the file is gone."""
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def human_text(obj: dict):
    """Raw text of a prompt the user typed; None for tool results, meta and sidechain entries."""
    if obj.get("type") != "user" or obj.get("isSidechain") or obj.get("isMeta"):
        return None
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = "\n".join(b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return None
    return content.strip() or None


def clean_prompt(text: str) -> str:
    """Remove harness-injected blocks (system reminders, slash-command wrappers)."""
    return _TAG_RE.sub("", text).strip()


def is_scheduled_session(main: Path) -> bool:
    """True when the session's first real prompt is a scheduled-task invocation."""
    for obj in _iter_json(main):
        raw = human_text(obj)
        prompt = clean_prompt(raw) if raw else ""
        if prompt:
            return prompt.startswith(SCHEDULED_PREFIX)
    return False
