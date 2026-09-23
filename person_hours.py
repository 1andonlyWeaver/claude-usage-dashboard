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
RETRY_AFTER_MINUTES = 60   # a failed session-day is retried at most once per hour
MAX_HOURS = 500            # sanity cap on one session-day's estimate
CALL_TIMEOUT_S = 300
# Never start a tick on an OAuth token that could expire before the tick's last call ends.
TOKEN_MIN_SECONDS = 600 + (MAX_PER_TICK // MAX_CONCURRENCY) * CALL_TIMEOUT_S
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


# ─── The judge ───────────────────────────────────────────────
SYSTEM_PROMPT = """You estimate how much human professional effort a piece of completed work represents.

You will receive a structured record of Claude Code work: the user's requests, the files created or edited (with line counts), tool usage, the shell commands that were run, and the assistant's final messages. This may be one day of a longer session; estimate only the work in this record. A line starting "Earlier in this session:" is context from a previous day, not work to count.

Estimate how many hours a competent professional with the appropriate skills (for example a software engineer, systems administrator, or research analyst who knows this kind of work but has no AI tools) would need to accomplish the same outcomes without AI assistance.

Include: understanding the request, investigation and research, writing and editing code or documents, testing and debugging, and verifying results.
Exclude: time spent waiting, dead ends caused only by the AI's own mistakes, and work that was clearly thrown away. Judge generated files by what they accomplish, not by their line count: boilerplate, generated reports and scratch files take a person far less time per line than core logic.

Respond with only a JSON object, no prose and no code fences:
{"summary": "<one sentence: what was accomplished>", "role": "<professional role>", "hours_low": <number>, "hours_likely": <number>, "hours_high": <number>, "rationale": "<two or three sentences>"}"""


MAX_SUMMARY_CHARS = 400
MAX_ROLE_CHARS = 80
MAX_RATIONALE_CHARS = 1200
_DECODER = json.JSONDecoder(strict=False)  # tolerate raw newlines inside strings


def _first_json_object(text: str):
    """The first JSON object in `text`, ignoring fences and prose around it; None if none parses."""
    starts = [i for i, ch in enumerate(text) if ch == "{"][:20]  # replies are short; bound the work
    for i in starts:
        try:
            obj, _ = _DECODER.raw_decode(text, i)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_estimate(text: str):
    """(estimate, None) from the judge's reply, or (None, reason) when it isn't usable. Never raises."""
    text = text or ""
    obj = _first_json_object(text)
    if obj is None:
        return None, "invalid JSON" if "{" in text else "no JSON object in response"
    hours = [obj.get(k) for k in ("hours_low", "hours_likely", "hours_high")]
    if not all(_is_number(h) for h in hours):
        return None, "missing or non-numeric hours"
    low, likely, high = hours  # compared before float() so huge integers can't overflow
    if not (0 < low <= likely <= high <= MAX_HOURS):
        return None, f"hours out of order or out of range: {low}/{likely}/{high}"[:200]
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, "missing summary"
    role = obj.get("role") if isinstance(obj.get("role"), str) else ""
    rationale = obj.get("rationale") if isinstance(obj.get("rationale"), str) else ""
    return {"summary": _clip(summary.strip(), MAX_SUMMARY_CHARS),
            "role": _clip(role.strip(), MAX_ROLE_CHARS),
            "hours_low": float(low), "hours_likely": float(likely), "hours_high": float(high),
            "rationale": _clip(rationale.strip(), MAX_RATIONALE_CHARS)}, None


# ─── Calling the judge via CLI ───────────────────────────────
def find_claude_cli():
    """Path to the claude CLI. Task Scheduler's PATH may lack ~/.local/bin, so check it too.

    npm's claude.cmd/.bat shims are skipped: cmd.exe would cut the multi-line system prompt
    at its first newline.
    """
    found = shutil.which("claude")
    if found and not found.lower().endswith((".cmd", ".bat")):
        return found
    for name in ("claude.exe", "claude"):
        candidate = Path.home() / ".local" / "bin" / name
        if candidate.exists():
            return str(candidate)
    return None


def _parse_envelope(stdout):
    """The CLI's JSON result object, tolerating stray lines printed before it; None if absent."""
    text = (stdout or "").strip()
    for candidate in [text, *reversed(text.splitlines())]:
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def call_judge(summary_text: str, cli: str, runner=None) -> dict:
    """Run one headless judge call.

    Returns {"ok": True, "estimate": {...}, "in_tokens", "out_tokens", "cost_usd", "model"}
    or {"ok": False, "error": "...", ...usage when the CLI reported it}.
    --safe-mode keeps the user's CLAUDE.md, plugins, hooks and MCP servers out of the
    judge's context; --no-session-persistence keeps the call out of ~/.claude/projects,
    so the dashboard never ingests its own judge sessions.
    """
    runner = runner or subprocess.run
    workdir = db.DB_PATH.parent  # the CLI needs an existing cwd; a missing one fails as WinError 267
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [cli, "-p", "--safe-mode", "--model", JUDGE_MODEL, "--no-session-persistence",
           "--tools", "", "--system-prompt", SYSTEM_PROMPT, "--output-format", "json"]
    try:
        proc = runner(cmd, input=summary_text, capture_output=True, encoding="utf-8",
                      errors="replace", timeout=CALL_TIMEOUT_S, cwd=str(workdir),
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except OSError as ex:
        return {"ok": False, "error": f"launch: {ex}"}
    envelope = _parse_envelope(proc.stdout)
    if envelope is None:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return {"ok": False, "error": f"exit {proc.returncode}: {detail}"}
    usage = envelope.get("usage") or {}
    meta = {
        "in_tokens": sum(usage.get(k) or 0 for k in
                         ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "out_tokens": usage.get("output_tokens") or 0,
        "cost_usd": envelope.get("total_cost_usd"),
        "model": next(iter(envelope.get("modelUsage") or {}), None),
    }
    if envelope.get("is_error"):
        detail = str(envelope.get("result") or envelope.get("subtype") or "error")[:200]
        status = envelope.get("api_error_status")
        return {"ok": False, "error": f"cli: {detail}" + (f" (HTTP {status})" if status else ""),
                **meta}
    if envelope.get("stop_reason") == "refusal":
        return {"ok": False, "error": "refusal", **meta}
    estimate, error = parse_estimate(envelope.get("result") or "")
    if error:
        return {"ok": False, "error": error, **meta}
    return {"ok": True, "estimate": estimate, **meta}


# ─── Queue ───────────────────────────────────────────────────
def _ts(dt: datetime) -> str:
    """Local-time ISO string in the messages.timestamp format."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def discover(conn, now: datetime, index: dict, backfill_days: int = BACKFILL_DAYS) -> int:
    """Queue quiet session-days: new ones, and judged ones with messages newer than the judgment.

    Session-days whose transcript isn't in `index` are skipped (it may be deleted, or its root
    briefly unreachable) and get picked up if it appears. Transcripts are read before anything
    is written, so no write lock is held during file I/O. Returns how many were queued.
    """
    since = (now - timedelta(days=backfill_days)).strftime("%Y-%m-%d")
    quiet_before = _ts(now - timedelta(minutes=IDLE_MINUTES))
    new = conn.execute("""
        SELECT m.session_id, m.date
        FROM messages m
        LEFT JOIN person_hour_estimates e ON e.session_id = m.session_id AND e.date = m.date
        WHERE COALESCE(m.source, 'claude-code') = 'claude-code'
          AND m.date >= ? AND e.session_id IS NULL
        GROUP BY m.session_id, m.date
        HAVING MAX(m.timestamp) <= ?
    """, (since, quiet_before)).fetchall()
    scheduled = {sid: is_scheduled_session(index[sid])
                 for sid in {row["session_id"] for row in new} if sid in index}
    queued = [(row["session_id"], row["date"], int(scheduled[row["session_id"]]))
              for row in new if row["session_id"] in scheduled]
    conn.executemany(
        "INSERT OR IGNORE INTO person_hour_estimates (session_id, date, status, is_scheduled)"
        " VALUES (?, ?, 'pending', ?)", queued)
    conn.execute("""
        UPDATE person_hour_estimates SET status = 'pending'
        WHERE status = 'done' AND date >= ?
          AND judged_through < (SELECT MAX(m.timestamp) FROM messages m
                                WHERE m.session_id = person_hour_estimates.session_id
                                  AND m.date = person_hour_estimates.date)
          AND (SELECT MAX(m.timestamp) FROM messages m
               WHERE m.session_id = person_hour_estimates.session_id
                 AND m.date = person_hour_estimates.date) <= ?
    """, (since, quiet_before))
    # Re-ingesting a resumed session can move a day's messages to another session id.
    conn.execute("""
        DELETE FROM person_hour_estimates
        WHERE status IN ('pending', 'error')
          AND NOT EXISTS (SELECT 1 FROM messages m
                          WHERE m.session_id = person_hour_estimates.session_id
                            AND m.date = person_hour_estimates.date)
    """)
    conn.commit()
    return len(queued)


def pending_rows(conn, limit: int, now: datetime | None = None) -> list:
    """Session-days waiting for a judgment, newest date first.

    A day attempted within the last RETRY_AFTER_MINUTES waits (a failed one is retried up to
    MAX_ATTEMPTS times), so each row is attempted at most once per rolling hour and
    calls_last_hour counts attempts exactly.
    """
    retry_before = _ts((now or datetime.now()) - timedelta(minutes=RETRY_AFTER_MINUTES))
    return conn.execute("""
        SELECT session_id, date FROM person_hour_estimates
        WHERE (status = 'pending' OR (status = 'error' AND attempts < ?))
          AND (last_attempt_at IS NULL OR last_attempt_at <= ?)
        ORDER BY date DESC, session_id
        LIMIT ?
    """, (MAX_ATTEMPTS, retry_before, limit)).fetchall()


def calls_last_hour(conn, now: datetime) -> int:
    """Judge calls started in the last hour, for the rolling hourly cap."""
    return conn.execute(
        "SELECT COUNT(*) FROM person_hour_estimates WHERE last_attempt_at >= ?",
        (_ts(now - timedelta(hours=1)),)).fetchone()[0]


def queue_counts(conn) -> dict:
    """{"pending": waiting or retryable, "errors": failed for good}."""
    row = conn.execute("""
        SELECT SUM(CASE WHEN status = 'pending' OR (status = 'error' AND attempts < ?) THEN 1 ELSE 0 END),
               SUM(CASE WHEN status = 'error' AND attempts >= ? THEN 1 ELSE 0 END)
        FROM person_hour_estimates
    """, (MAX_ATTEMPTS, MAX_ATTEMPTS)).fetchone()
    return {"pending": row[0] or 0, "errors": row[1] or 0}


# ─── Judging ─────────────────────────────────────────────────
def _day_context(conn, session_id: str, date: str) -> dict:
    """Project name, day number, previous day's summary and last message time for a session-day."""
    row = conn.execute(
        "SELECT MAX(project) AS project, MAX(timestamp) AS last_ts FROM messages"
        " WHERE session_id = ? AND date = ?", (session_id, date)).fetchone()
    day_number = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM messages WHERE session_id = ? AND date <= ?",
        (session_id, date)).fetchone()[0]
    prev = conn.execute(
        "SELECT summary FROM person_hour_estimates WHERE session_id = ? AND date < ?"
        " AND summary IS NOT NULL ORDER BY date DESC LIMIT 1", (session_id, date)).fetchone()
    return {"project": row["project"] or "Unknown", "judged_through": row["last_ts"],
            "day_number": max(day_number, 1), "prev_summary": prev["summary"] if prev else None}


def build_request(conn, session_id: str, date: str, index: dict):
    """(summary_text, context, problem) for a session-day.

    problem is None, or 'no_source' / 'no_messages' / 'no_events' when there's nothing to judge.
    """
    main = index.get(session_id)
    if main is None:
        return None, None, "no_source"
    ctx = _day_context(conn, session_id, date)
    if ctx["judged_through"] is None:  # the day's messages moved to another session id
        return None, ctx, "no_messages"
    day = summarize_day(main, date)
    if day is None:
        return None, ctx, "no_events"
    return render_summary(day, ctx["project"], ctx["day_number"], ctx["prev_summary"]), ctx, None


def _record_success(conn, session_id, date, result, judged_through, attempted_at):
    est = result["estimate"]
    conn.execute("""
        UPDATE person_hour_estimates SET status = 'done',
            hours_low = ?, hours_likely = ?, hours_high = ?, summary = ?, role = ?, rationale = ?,
            judged_through = ?, model = ?, prompt_version = ?, attempts = 0, error = NULL,
            last_attempt_at = ?, judge_in_tokens = ?, judge_out_tokens = ?, judge_cost_usd = ?
        WHERE session_id = ? AND date = ?
    """, (est["hours_low"], est["hours_likely"], est["hours_high"], est["summary"], est["role"],
          est["rationale"], judged_through, result.get("model"), PROMPT_VERSION, attempted_at,
          result.get("in_tokens"), result.get("out_tokens"), result.get("cost_usd"),
          session_id, date))
    conn.commit()


def _record_failure(conn, session_id, date, error, attempted_at=None, meta=None, final=False):
    """Mark a failed attempt. `final` failures (no transcript) are never retried."""
    meta = meta or {}
    conn.execute("""
        UPDATE person_hour_estimates SET status = 'error',
            attempts = CASE WHEN ? THEN ? ELSE attempts + 1 END,
            error = ?, last_attempt_at = COALESCE(?, last_attempt_at),
            judge_in_tokens = COALESCE(?, judge_in_tokens),
            judge_out_tokens = COALESCE(?, judge_out_tokens),
            judge_cost_usd = COALESCE(?, judge_cost_usd)
        WHERE session_id = ? AND date = ?
    """, (int(final), MAX_ATTEMPTS, error, attempted_at, meta.get("in_tokens"),
          meta.get("out_tokens"), meta.get("cost_usd"), session_id, date))
    conn.commit()


def _connect():
    """A DB connection that waits out ingest writes instead of failing (and losing a paid result)."""
    conn = db.get_conn()
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def judge_session_day(session_id, date, index, cli, runner=None, now_fn=datetime.now) -> str:
    """Judge one queued session-day and store the outcome. Returns 'done' or 'error'."""
    conn = _connect()
    try:
        text, ctx, problem = build_request(conn, session_id, date, index)
        if problem:
            _record_failure(conn, session_id, date, problem, final=True)
            return "error"
        result = call_judge(text, cli, runner=runner)
        attempted_at = _ts(now_fn())
        if result["ok"]:
            _record_success(conn, session_id, date, result, ctx["judged_through"], attempted_at)
            return "done"
        _record_failure(conn, session_id, date, result["error"], attempted_at, meta=result)
        return "error"
    finally:
        conn.close()


def _record_crash(session_id, date, ex, now_fn) -> None:
    """Count an unexpected exception as a failed attempt, so it backs off and hits the cap."""
    try:
        conn = _connect()
        try:
            _record_failure(conn, session_id, date, f"crash: {ex}"[:200], _ts(now_fn()))
        finally:
            conn.close()
    except Exception as inner:  # the DB itself is unavailable; the next tick will retry
        print(f"[hours {datetime.now():%Y-%m-%d %H:%M:%S}] could not record crash: {inner}")


def judge_pending(limit: int, cli: str, index: dict, runner=None, now_fn=datetime.now) -> dict:
    """Judge up to `limit` queued session-days, newest first, MAX_CONCURRENCY at a time.

    Returns outcome counts, e.g. {"done": 2, "error": 1}.
    """
    if limit <= 0:
        return {}
    conn = _connect()
    try:
        # Claim the rows (stamp them as started) in one write transaction, so another process
        # running the backfill can't take them too and in-flight calls count toward the cap.
        conn.execute("BEGIN IMMEDIATE")
        # Re-check the hourly cap inside the lock: another process may have just claimed rows.
        limit = min(limit, MAX_CALLS_PER_HOUR - calls_last_hour(conn, now_fn()))
        rows = [(r["session_id"], r["date"]) for r in pending_rows(conn, max(limit, 0), now_fn())]
        started = _ts(now_fn())
        conn.executemany(
            "UPDATE person_hour_estimates SET last_attempt_at = ? WHERE session_id = ? AND date = ?",
            [(started, sid, date) for sid, date in rows])
        conn.commit()
    finally:
        conn.close()

    def one(row):
        try:
            return judge_session_day(row[0], row[1], index, cli, runner, now_fn)
        except Exception as ex:  # one bad session-day must not stop the batch
            print(f"[hours {datetime.now():%Y-%m-%d %H:%M:%S}] {row[0]} {row[1]}: {ex}")
            _record_crash(row[0], row[1], ex, now_fn)
            return "error"

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        return dict(Counter(pool.map(one, rows)))


# ─── Worker ──────────────────────────────────────────────────
_worker = {"reason": None, "last_tick": None}


def skip_reason(*, cli_found, auth_dead, ingest_running, five_hour_pct, token_seconds_left,
                calls_last_hour):
    """Why this tick shouldn't call the judge, or None to go ahead. First match wins."""
    if not cli_found:
        return "unavailable"
    if auth_dead or token_seconds_left is None:  # before quota: a stale quota % hides "sign in"
        return "auth"
    if ingest_running:
        return "ingest"
    if five_hour_pct is not None and five_hour_pct >= QUOTA_PAUSE_PCT:
        return "quota"
    if token_seconds_left < TOKEN_MIN_SECONDS:
        return "token"
    if calls_last_hour >= MAX_CALLS_PER_HOUR:
        return "rate"
    return None


def set_worker_reason(reason, now: datetime) -> None:
    _worker["reason"] = reason
    _worker["last_tick"] = _ts(now)


def worker_status(counts: dict) -> dict:
    """Worker state for /api/hours: unavailable, paused (with reason), running or idle.

    Hitting the hourly cap isn't a pause: the backlog keeps draining at the capped rate.
    """
    reason = _worker["reason"]
    if reason == "unavailable":
        state = "unavailable"
    elif reason not in (None, "rate"):
        state = "paused"
    else:
        state = "running" if counts["pending"] else "idle"
    return {"state": state, "reason": reason if state == "paused" else None, **counts}


def run_tick(now: datetime, gates: dict, runner=None, now_fn=datetime.now) -> dict:
    """One worker pass: queue quiet session-days, then judge within the hourly budget.

    `gates` carries the server's state: auth_dead, ingest_running, five_hour_pct and
    token_seconds_left. Queuing still happens while paused, so provisional numbers know
    which session-days are scheduled runs.
    """
    if gates["ingest_running"]:  # tables may be mid-rebuild on a first run; try next tick
        set_worker_reason("ingest", now)
        return {"skipped": "ingest"}
    cli = find_claude_cli()
    index = index_sessions()
    conn = _connect()
    try:
        discover(conn, now, index)
        calls = calls_last_hour(conn, now)
    finally:
        conn.close()
    reason = skip_reason(cli_found=cli is not None, calls_last_hour=calls, **gates)
    set_worker_reason(reason, now)
    if reason:
        return {"skipped": reason}
    return judge_pending(min(MAX_PER_TICK, MAX_CALLS_PER_HOUR - calls), cli, index, runner, now_fn)
