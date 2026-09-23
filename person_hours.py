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
INTERRUPTED_PREFIX = "[Request interrupted by user"
_SLASH_RE = re.compile(
    r"<command-name>(.*?)</command-name>\s*"
    r"(?:<command-message>.*?</command-message>\s*)?"
    r"(?:<command-args>(.*?)</command-args>)?",
    re.S,
)
# Session housekeeping, not work: dropped from the prompts the judge sees.
_BUILT_IN_COMMANDS = {
    "/add-dir", "/agents", "/clear", "/compact", "/config", "/context", "/cost", "/doctor",
    "/effort", "/exit", "/fast", "/help", "/hooks", "/ide", "/login", "/logout", "/mcp",
    "/memory", "/model", "/permissions", "/plugin", "/reload-plugins", "/resume", "/rewind",
    "/sandbox", "/status", "/statusline", "/theme", "/usage", "/vim",
}
_TAG_RE = re.compile(
    r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+|task-notification"
    r"|bash-stdout|bash-stderr)\b[^>]*>.*?</\1>",
    re.S,
)
# The desktop app prefixes some prompts with a marker such as <!-- attach --> or <!-- reply -->.
_MARKER_RE = re.compile(r"^\s*<!--\s*[\w-]+\b[^>]*?-->")


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
    """A session's subagent transcripts, including workflow agents one level deeper.

    Layout: <project>/<session_id>/subagents/agent-*.jsonl and
    .../subagents/workflows/wf_*/agent-*.jsonl. A workflow's journal.jsonl is
    bookkeeping, not a transcript, so only agent-*.jsonl files count.
    """
    return sorted((main.parent / main.stem / "subagents").rglob("agent-*.jsonl"))


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
    """Raw text of a prompt the user typed.

    None for tool results, meta, sidechain and compaction-summary entries (a compaction
    summary recaps earlier work, which would otherwise be credited twice), interrupt
    markers, and malformed entries.
    """
    if (obj.get("type") != "user" or obj.get("isSidechain") or obj.get("isMeta")
            or obj.get("isCompactSummary")):
        return None
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = "\n".join(b.get("text") or "" for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return None
    content = content.strip()
    if not content or content.startswith(INTERRUPTED_PREFIX):
        return None
    return content


def _slash_command(match) -> str:
    name = match.group(1).strip()
    if name in _BUILT_IN_COMMANDS:
        return ""
    return " ".join(p for p in (name, (match.group(2) or "").strip()) if p)


def clean_prompt(text: str) -> str:
    """Keep slash commands as "/name args"; drop other harness-injected blocks."""
    return _MARKER_RE.sub("", _TAG_RE.sub("", _SLASH_RE.sub(_slash_command, text))).strip()


def queued_prompt(obj: dict):
    """Text the user typed while Claude was busy (a queued_command attachment), or None."""
    att = obj.get("attachment")
    if obj.get("type") != "attachment" or not isinstance(att, dict):
        return None
    if att.get("type") != "queued_command" or att.get("commandMode") != "prompt":
        return None
    prompt = att.get("prompt")
    if isinstance(prompt, list):
        prompt = "\n".join(b.get("text") or "" for b in prompt
                           if isinstance(b, dict) and b.get("type") == "text")
    return (prompt.strip() or None) if isinstance(prompt, str) else None


def is_scheduled_session(main: Path) -> bool:
    """True when the session's first real prompt is a scheduled-task invocation."""
    for obj in _iter_json(main):
        raw = human_text(obj)
        prompt = clean_prompt(raw) if raw else ""
        if prompt:
            return prompt.startswith(SCHEDULED_PREFIX)
    return False


# ─── Summarizing one session-day ─────────────────────────────
_TEMP_PREFIX = tempfile.gettempdir().replace("\\", "/").lower().rstrip("/") + "/"


def _excluded(path: str) -> bool:
    """Memory, plan-mode and scratchpad files aren't deliverables. Worktrees (.claude/worktrees) are."""
    p = path.replace("\\", "/").lower()
    return ("/.claude/projects/" in p or "/.claude/plans/" in p or "/appdata/local/temp/" in p
            or p.startswith(_TEMP_PREFIX) or p.startswith("/tmp/"))


def _add_change(changes: dict, result) -> None:
    """Fold one Edit/Write toolUseResult into {path: [added, removed, kind]}."""
    if not isinstance(result, dict) or not result.get("filePath") or _excluded(result["filePath"]):
        return
    path = result["filePath"].replace("\\", "/")  # one key per file however the path was spelled
    if result.get("structuredPatch"):
        entry = changes.setdefault(path, [0, 0, "edit"])
        for hunk in result["structuredPatch"]:
            for line in hunk.get("lines", []):
                if line.startswith("+"):
                    entry[0] += 1
                elif line.startswith("-"):
                    entry[1] += 1
    elif result.get("type") == "create" and isinstance(result.get("content"), str):
        entry = changes.setdefault(path, [0, 0, "new"])
        entry[0] += len(result["content"].splitlines())
        entry[2] = "new"


def _add_assistant(s: dict, obj: dict, is_sub: bool) -> None:
    """Count tool calls, collect Bash descriptions and main-thread text from one assistant entry."""
    for block in (obj.get("message") or {}).get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = block.get("name", "?")
            (s["tools_sub"] if is_sub else s["tools_main"])[name] += 1
            desc = (block.get("input") or {}).get("description")
            if name == "Bash" and not is_sub and desc and desc not in s["bash_desc"]:
                s["bash_desc"].append(desc)
        elif block.get("type") == "text" and not is_sub and (block.get("text") or "").strip():
            s["finals"].append(block["text"].strip())


def summarize_day(main: Path, date: str, subagents=None):
    """Collect one local date's work from a session's main and subagent transcripts.

    Returns None when nothing in the transcripts happened on `date`.
    """
    subagents = subagent_files(main) if subagents is None else subagents
    s = {"prompts": [], "finals": [], "bash_desc": [], "tools_main": Counter(),
         "tools_sub": Counter(), "changes": {}, "subagents": 0}
    events = 0
    for path in [main, *subagents]:
        is_sub = path != main
        seen = 0
        for obj in _iter_json(path):
            ts = obj.get("timestamp")
            if not ts or parse_timestamp(ts)[1] != date:
                continue
            seen += 1
            if obj.get("type") == "assistant":
                _add_assistant(s, obj, is_sub)
                continue
            if obj.get("type") == "user":
                _add_change(s["changes"], obj.get("toolUseResult"))
            raw = None if is_sub else (human_text(obj) or queued_prompt(obj))
            prompt = clean_prompt(raw) if raw else ""
            if prompt:
                s["prompts"].append(prompt)
        events += seen
        if is_sub and seen:
            s["subagents"] += 1
    return s if events else None


def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= n else s[: n - 1] + "…"


def render_summary(s: dict, project: str, day_number: int = 1, prev_summary=None,
                   max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Plain-text record of a session-day for the judge. Deliberately omits any timing."""
    out = [f"Project: {project}"]
    if day_number > 1:
        out.append(f"This is day {day_number} of a longer session.")
        if prev_summary:
            out.append(f"Earlier in this session: {prev_summary}")
    if s["subagents"]:
        out.append(f"Subagent transcripts this day: {s['subagents']}")
    out.append("")

    p = s["prompts"]
    out.append(f"USER REQUESTS ({len(p)} total, in order):")
    if len(p) <= 20:
        out += [f"  {i}. {_clip(t, 600)}" for i, t in enumerate(p, 1)]
    else:
        out += [f"  {i}. {_clip(t, 600)}" for i, t in enumerate(p[:14], 1)]
        out.append(f"  [... {len(p) - 19} more requests omitted ...]")
        out += [f"  {i}. {_clip(t, 600)}" for i, t in enumerate(p[-5:], len(p) - 4)]
    out.append("")

    changes = sorted(s["changes"].items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
    out.append(f"FILES CREATED OR EDITED ({len(changes)}):")
    out += [f"  {kind:4s} +{added} -{removed}  {path}"
            for path, (added, removed, kind) in changes[:40]]
    if len(changes) > 40:
        out.append(f"  [... {len(changes) - 40} more files ...]")
    out.append("")

    out.append("TOOL CALLS (main thread): "
               + (", ".join(f"{k}×{v}" for k, v in s["tools_main"].most_common(15)) or "none"))
    if s["tools_sub"]:
        out.append("TOOL CALLS (subagents): "
                   + ", ".join(f"{k}×{v}" for k, v in s["tools_sub"].most_common(10)))
    out.append("")

    bd = s["bash_desc"]
    out.append(f"SHELL COMMANDS RUN ({len(bd)} distinct, first 40):")
    out += [f"  - {_clip(d, 120)}" for d in bd[:40]]
    out.append("")

    out.append("ASSISTANT'S FINAL MESSAGES (last 3):")
    out += [f"  > {_clip(f, 1200)}" for f in s["finals"][-3:]]
    rendered = "\n".join(out)
    return rendered if len(rendered) <= max_chars else rendered[:max_chars] + "\n[truncated]"
