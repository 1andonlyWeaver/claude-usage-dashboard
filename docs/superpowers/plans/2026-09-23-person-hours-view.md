# Person-Hours View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `$ | h` toggle to the Est. API Cost card that shows estimated person-hours for Claude Code sessions, judged per session-day by Claude Sonnet through the `claude` CLI.

**Architecture:** A new `person_hours.py` module reads session transcripts, builds a one-day summary, calls `claude -p` headless, validates the JSON estimate, and stores it in a new `person_hour_estimates` table. A background worker in `app.py` runs it every 5 minutes behind quota, auth and rate gates. `db.py` aggregates judged and provisional hours for the card, the session list, and the drill-down panel.

**Tech Stack:** Python 3.12 (conda env `claude-usage-dashboard`), FastAPI, SQLite 3.52, vanilla JS + Chart.js, pytest (new).

**Spec:** `docs/superpowers/specs/2026-09-23-person-hours-view-design.md`

## Progress (paused 2026-09-23)

- **Done:** Tasks 1–10, each reviewed for spec compliance and code quality; 86 tests pass. The code in `person_hours.py` and `tests/` is authoritative. Reviews changed Tasks 3–10 beyond the code blocks below; the spec was updated to match.
- **Still open:** re-review of the Task 10 fix commit `75e220d` (fresh index each pass, stop after an all-failed pass, closing summary). It was stopped before reporting. Re-run it before Task 11.
- **Next:** Task 11. Carry these changes into later tasks:
  - `pending_rows(conn, limit, now)` takes `now`.
  - `judge_session_day` returns `skipped` for no-call problems.
  - `discover` skips sessions that aren't in the transcript index.
  - Rows are claimed before judging, so the backfill CLI is safe alongside the worker. Update Task 16's CLAUDE.md text, which says otherwise.
- **Known limitations to report at the end:**
  - Resumed or forked sessions are double-counted (a follow-up task).
  - Claude Code deletes old transcripts, so older session-days stay provisional.
  - A timeout kills the CLI but not its child processes on Windows.
  - Newest-first backfill often lacks the previous day's context.

---

## Conventions

- Work in the worktree root: `C:\Users\weaverjc\Projects\Personal\claude-usage-dashboard\.claude\worktrees\person-hours-api-cost-view-278fe4`.
- Commands are Git Bash. Every `python` command needs the conda env. In the steps below, **`$ENV`** stands for this literal prefix:
  `eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard &&`
  So `$ENV python -m pytest tests/test_x.py -v` means run the prefix, then `python -m pytest tests/test_x.py -v`, in one command.
- Commit messages: plain imperative subject. **No `Co-Authored-By` or other AI attribution trailers** (user rule).
- Never stage `.claude/`, `data/`, or `logs/`. `CLAUDE.md` is tracked in this repo but the user must approve each commit that touches it (Task 16).
- Two other sessions are editing this repo in parallel on their own branches: subagent-log ingest (`ingest.py` `run_ingest`) and model pricing (`db.py` price table, `estimate_cost`, `window_tokens`). This plan avoids those regions; expect small merge conflicts in `CLAUDE.md` only.

## Deviations from the spec (intentional)

1. `DEFAULT_LEVERAGE` and `MIN_SAMPLES_FOR_LEVERAGE` live in `db.py`, which does the aggregation. `person_hours.py` imports `db`, so the reverse import would be circular.
2. `/api/hours` → `worker` has `state`, `reason`, `pending`, `errors`. The spec's `backfill_remaining` is dropped because it equals `pending`.
3. A worker tick does nothing at all while an ingest runs (no discovery either), because a first-run ingest may not have created tables yet.
4. When the OAuth token has under 10 minutes left, the worker asks the dashboard's existing `_refresh_oauth_token()` to refresh it before gating. Without this, a dashboard with no browser tab open would never refresh the token and the worker would stall on `token`.
5. Env var `PERSON_HOURS_WORKER=off` disables the background worker. Used during verification so a test server doesn't start judging on its own.

## File structure

| File | Responsibility |
|---|---|
| `person_hours.py` (create) | Transcript reading, one-day summaries, judge call and validation, queue (discover / pending / record), worker gating (`skip_reason`, `run_tick`), CLI |
| `db.py` (modify) | Session-day rows with active hours, leverage, `person_hours()`, `session_hours()`, hours on `session_list()`, judge calls in `detect_other_pct()` |
| `ingest.py` (modify) | `person_hour_estimates` table in `init_db()` |
| `app.py` (modify) | Schema check at startup, worker timer, `/api/hours`, `/api/session/{id}/hours` |
| `templates/index.html`, `static/dashboard.js`, `static/style.css` (modify) | Toggle, hours view, session-list hours, drill-down entries |
| `environment.yml` (modify), `pytest.ini` (create) | pytest |
| `tests/conftest.py`, `tests/helpers.py` (create) | Temp-DB fixture; transcript and CLI-output builders |
| `tests/test_*.py` (create) | One file per area |
| `CLAUDE.md` (modify) | Docs |

---

### Task 1: Test infrastructure

**Files:**
- Modify: `environment.yml`
- Create: `pytest.ini`, `tests/conftest.py`, `tests/helpers.py`, `tests/test_fixtures.py`

- [ ] **Step 1: Add pytest to the environment file**

Replace the contents of `environment.yml` with:

```yaml
name: claude-usage-dashboard
channels:
  - conda-forge
  - defaults
dependencies:
  - python=3.12
  - pip
  - pytest
  - pip:
    - fastapi
    - uvicorn[standard]
    - jinja2
    - orjson
```

- [ ] **Step 2: Install pytest into the existing env**

Run: `eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda install -n claude-usage-dashboard -c conda-forge pytest -y`
Expected: ends with `done`. Then `$ENV python -m pytest --version` prints `pytest 8.x` or later.

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
pythonpath = . tests
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
"""Shared fixtures: a throwaway usage DB with the full schema."""
import pytest

import db
import ingest


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """An empty usage DB with the full schema; db.py and ingest.py both point at it."""
    path = tmp_path / "usage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(ingest, "DB_PATH", path)
    c = db.get_conn()
    ingest.init_db(c)
    yield c
    c.close()
```

- [ ] **Step 5: Create `tests/helpers.py`**

```python
"""Builders for Claude Code transcripts, DB rows and claude CLI output used across tests."""
import json
import subprocess
from datetime import datetime, timezone


def utc(local: str) -> str:
    """Local wall-clock 'YYYY-MM-DDTHH:MM:SS' -> the UTC 'Z' form Claude Code writes."""
    return datetime.fromisoformat(local).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def user(local_ts, content, **extra):
    """A user entry; content is a string or a list of content blocks."""
    return {"type": "user", "timestamp": utc(local_ts), "sessionId": "s1",
            "message": {"role": "user", "content": content}, **extra}


def assistant(local_ts, *blocks, **extra):
    return {"type": "assistant", "timestamp": utc(local_ts), "sessionId": "s1",
            "message": {"id": f"msg-{local_ts}", "role": "assistant", "model": "claude-sonnet-5",
                        "content": list(blocks), "usage": {"input_tokens": 1, "output_tokens": 1}},
            **extra}


def text(t):
    return {"type": "text", "text": t}


def tool_use(name, **inputs):
    return {"type": "tool_use", "id": f"tu-{name}", "name": name, "input": inputs}


def tool_result(local_ts, result, **extra):
    """A tool-result entry carrying Claude Code's structured toolUseResult payload."""
    return {"type": "user", "timestamp": utc(local_ts), "sessionId": "s1",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
            "toolUseResult": result, **extra}


def edit_result(path, added, removed):
    lines = [" context"] + ["+new"] * added + ["-old"] * removed
    return {"filePath": path, "structuredPatch": [
        {"oldStart": 1, "oldLines": removed + 1, "newStart": 1, "newLines": added + 1, "lines": lines}]}


def create_result(path, n_lines):
    return {"type": "create", "filePath": path,
            "content": "\n".join(f"line {i}" for i in range(n_lines))}


def add_message(conn, session_id, local_ts, *, project="proj", source="claude-code"):
    """Insert an assistant-message row the way ingest.py does (local timestamp and date)."""
    dt = datetime.fromisoformat(local_ts)
    conn.execute(
        "INSERT INTO messages (msg_id, timestamp, date, hour, day_of_week, session_id, project, model,"
        " input_tokens, output_tokens, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'claude-sonnet-5', 1, 1, ?)",
        (f"{session_id}@{local_ts}", local_ts, dt.strftime("%Y-%m-%d"), dt.hour, dt.weekday(),
         session_id, project, source))
    conn.commit()


GOOD_ESTIMATE = {"summary": "Fixed the duplicate month bug.", "role": "Software engineer",
                 "hours_low": 2, "hours_likely": 3, "hours_high": 5,
                 "rationale": "Tracing plus testing."}


def envelope(result, *, is_error=False, model="claude-sonnet-5"):
    """What `claude -p --output-format json` prints."""
    if isinstance(result, dict):
        result = json.dumps(result)
    return json.dumps({
        "type": "result", "is_error": is_error, "result": result, "total_cost_usd": 0.05,
        "usage": {"input_tokens": 4000, "cache_creation_input_tokens": 600,
                  "cache_read_input_tokens": 0, "output_tokens": 1500},
        "modelUsage": {model: {"inputTokens": 4600, "outputTokens": 1500}},
    })


class FakeRun:
    """Stand-in for subprocess.run that records each call and returns canned output."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.stdout, self.returncode, self.stderr, self.raises = stdout, returncode, stderr, raises
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def queued_session(conn, tmp_path, sid, now):
    """A one-day session with a transcript and a messages row, already queued by discover()."""
    import person_hours
    main = write_jsonl(tmp_path / "p" / f"{sid}.jsonl", [
        user("2026-09-20T09:00:00", "Fix the calendar"),
        assistant("2026-09-20T09:05:00", text("Fixed.")),
    ])
    add_message(conn, sid, "2026-09-20T09:05:00", project="Projects / www")
    person_hours.discover(conn, now, index={sid: main})
    return {sid: main}
```

- [ ] **Step 6: Write a smoke test for the fixture**

Create `tests/test_fixtures.py`:

```python
from helpers import add_message


def test_conn_fixture_creates_schema(conn):
    add_message(conn, "s1", "2026-09-20T10:00:00")
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
```

- [ ] **Step 7: Run it**

Run: `$ENV python -m pytest tests/test_fixtures.py -v`
Expected: `1 passed`.

- [ ] **Step 8: Commit**

```bash
git add environment.yml pytest.ini tests/conftest.py tests/helpers.py tests/test_fixtures.py
git commit -m "Add pytest with a temp-DB fixture and transcript builders"
```

---

### Task 2: `person_hour_estimates` table

**Files:**
- Modify: `ingest.py:82-127` (`init_db`)
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema.py`:

```python
EXPECTED_COLUMNS = {
    "session_id", "date", "status", "is_scheduled", "hours_low", "hours_likely", "hours_high",
    "summary", "role", "rationale", "judged_through", "model", "prompt_version", "attempts",
    "error", "last_attempt_at", "judge_in_tokens", "judge_out_tokens", "judge_cost_usd",
}


def test_init_db_creates_person_hour_estimates(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(person_hour_estimates)")}
    assert cols == EXPECTED_COLUMNS


def test_primary_key_is_session_and_date(conn):
    info = conn.execute("PRAGMA table_info(person_hour_estimates)").fetchall()
    pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
    assert pk == ["session_id", "date"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `$ENV python -m pytest tests/test_schema.py -v`
Expected: FAIL (`assert set() == {...}`: the table doesn't exist).

- [ ] **Step 3: Add the table to `init_db`**

In `ingest.py`, inside the `conn.executescript("""...""")` in `init_db`, after the `quota_snapshots` table and before the closing `"""`, add:

```sql
        CREATE TABLE IF NOT EXISTS person_hour_estimates (
            session_id       TEXT NOT NULL,
            date             TEXT NOT NULL,
            status           TEXT NOT NULL,
            is_scheduled     INTEGER DEFAULT 0,
            hours_low        REAL,
            hours_likely     REAL,
            hours_high       REAL,
            summary          TEXT,
            role             TEXT,
            rationale        TEXT,
            judged_through   TEXT,
            model            TEXT,
            prompt_version   INTEGER,
            attempts         INTEGER DEFAULT 0,
            error            TEXT,
            last_attempt_at  TEXT,
            judge_in_tokens  INTEGER,
            judge_out_tokens INTEGER,
            judge_cost_usd   REAL,
            PRIMARY KEY (session_id, date)
        );
```

- [ ] **Step 4: Run it to verify it passes**

Run: `$ENV python -m pytest tests/test_schema.py -v`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add ingest.py tests/test_schema.py
git commit -m "Add person_hour_estimates table"
```

---

### Task 3: Reading transcripts (`person_hours.py` scaffold)

**Files:**
- Create: `person_hours.py`
- Test: `tests/test_person_hours_logs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_logs.py`:

```python
import person_hours as ph
from helpers import assistant, text, tool_result, user, write_jsonl

T = "2026-09-20T10:00:00"


def test_index_sessions_maps_ids_to_main_files(tmp_path):
    a = write_jsonl(tmp_path / "root1" / "C--proj-a" / "s1.jsonl", [])
    b = write_jsonl(tmp_path / "root2" / "-home-me-proj" / "s2.jsonl", [])
    write_jsonl(tmp_path / "root1" / "C--proj-a" / "s1" / "subagents" / "agent-x.jsonl", [])
    index = ph.index_sessions([tmp_path / "root1", tmp_path / "root2", tmp_path / "missing"])
    assert index == {"s1": a, "s2": b}


def test_subagent_files(tmp_path):
    main = write_jsonl(tmp_path / "p" / "s1.jsonl", [])
    sub = write_jsonl(tmp_path / "p" / "s1" / "subagents" / "agent-1.jsonl", [])
    assert ph.subagent_files(main) == [sub]


def test_human_text_accepts_typed_prompts_only():
    assert ph.human_text(user(T, "hello")) == "hello"
    assert ph.human_text(user(T, [text("a"), text("b")])) == "a\nb"
    assert ph.human_text(tool_result(T, {})) is None
    assert ph.human_text(user(T, "x", isMeta=True)) is None
    assert ph.human_text(user(T, "x", isSidechain=True)) is None
    assert ph.human_text(assistant(T, text("hi"))) is None


def test_clean_prompt_strips_harness_blocks():
    raw = "<system-reminder>ignore\nme</system-reminder>Fix the bug<command-name>/x</command-name>"
    assert ph.clean_prompt(raw) == "Fix the bug"


def test_is_scheduled_session(tmp_path):
    scheduled = write_jsonl(tmp_path / "a.jsonl", [
        user(T, '<system-reminder>r</system-reminder><scheduled-task name="daily">go</scheduled-task>')])
    normal = write_jsonl(tmp_path / "b.jsonl", [
        user(T, "<system-reminder>only a reminder</system-reminder>"),
        user(T, "Please fix the calendar"),
        user(T, "<scheduled-task name='x'>later</scheduled-task>")])
    assert ph.is_scheduled_session(scheduled) is True
    assert ph.is_scheduled_session(normal) is False
    assert ph.is_scheduled_session(tmp_path / "missing.jsonl") is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_logs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'person_hours'`.

- [ ] **Step 3: Create `person_hours.py`**

```python
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_logs.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add person_hours.py tests/test_person_hours_logs.py
git commit -m "Add person_hours transcript reading helpers"
```

---

### Task 4: One-day summary

**Files:**
- Modify: `person_hours.py` (append a section)
- Test: `tests/test_person_hours_summary.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_summary.py`:

```python
from collections import Counter

import person_hours as ph
from helpers import (assistant, create_result, edit_result, text, tool_result, tool_use, user,
                     write_jsonl)

DAY = "2026-09-20"
SITE_JS = "C:/Users/me/Projects/www/.claude/worktrees/fix/site.js"
README = "C:/Users/me/Projects/www/README.md"
LIB = "C:/Users/me/Projects/www/lib.py"


def _session(tmp_path):
    main = write_jsonl(tmp_path / "proj" / "s1.jsonl", [
        user("2026-09-19T23:50:00", "Yesterday's request"),
        user("2026-09-20T09:00:00", "<system-reminder>x</system-reminder>Fix the calendar"),
        assistant("2026-09-20T09:01:00",
                  tool_use("Bash", command="ls", description="List files"),
                  tool_use("Bash", command="ls", description="List files"),
                  tool_use("Read", file_path="a.js")),
        tool_result("2026-09-20T09:02:00", edit_result(SITE_JS, 5, 2)),
        tool_result("2026-09-20T09:03:00",
                    create_result(r"C:\Users\me\.claude\projects\C--www\memory\note.md", 40)),
        tool_result("2026-09-20T09:04:00", create_result(ph._TEMP_PREFIX + "claude/scratch/pr.md", 30)),
        tool_result("2026-09-20T09:05:00", create_result(README, 12)),
        assistant("2026-09-20T09:06:00", text("Done: fixed it.")),
        user("2026-09-21T00:10:00", "Tomorrow's request"),
    ])
    write_jsonl(tmp_path / "proj" / "s1" / "subagents" / "agent-a.jsonl", [
        assistant("2026-09-20T09:02:30", tool_use("Grep", pattern="x"), text("sub text"),
                  isSidechain=True),
        tool_result("2026-09-20T09:02:40", create_result(LIB, 20), isSidechain=True),
    ])
    return main


def _empty(**over):
    base = {"prompts": [], "finals": [], "bash_desc": [], "tools_main": Counter(),
            "tools_sub": Counter(), "changes": {}, "subagents": 0}
    return {**base, **over}


def test_summarize_day_keeps_only_that_local_date(tmp_path):
    assert ph.summarize_day(_session(tmp_path), DAY)["prompts"] == ["Fix the calendar"]


def test_summarize_day_returns_none_without_events(tmp_path):
    assert ph.summarize_day(_session(tmp_path), "2026-09-25") is None


def test_summarize_day_counts_changes_and_skips_memory_and_scratch(tmp_path):
    assert ph.summarize_day(_session(tmp_path), DAY)["changes"] == {
        SITE_JS: [5, 2, "edit"],
        README: [12, 0, "new"],
        LIB: [20, 0, "new"],
    }


def test_summarize_day_tools_commands_finals_and_subagents(tmp_path):
    s = ph.summarize_day(_session(tmp_path), DAY)
    assert s["tools_main"] == {"Bash": 2, "Read": 1}
    assert s["tools_sub"] == {"Grep": 1}
    assert s["bash_desc"] == ["List files"]
    assert s["finals"] == ["Done: fixed it."]
    assert s["subagents"] == 1


def test_render_summary_has_context_and_no_timing(tmp_path):
    out = ph.render_summary(ph.summarize_day(_session(tmp_path), DAY), "Projects / www",
                            day_number=2, prev_summary="Set up the branch.")
    assert "Project: Projects / www" in out
    assert "day 2 of a longer session" in out
    assert "Earlier in this session: Set up the branch." in out
    assert "1. Fix the calendar" in out
    assert f"edit +5 -2  {SITE_JS}" in out
    assert "Bash×2" in out and "Grep×1" in out
    assert "- List files" in out
    assert "> Done: fixed it." in out
    assert "active back-and-forth" not in out and "wall-clock" not in out


def test_render_summary_elides_middle_requests():
    out = ph.render_summary(_empty(prompts=[f"req {i}" for i in range(1, 26)]), "p")
    assert "14. req 14" in out and "15. req 15" not in out
    assert "[... 6 more requests omitted ...]" in out
    assert "21. req 21" in out and "25. req 25" in out


def test_render_summary_caps_length():
    s = _empty(prompts=["x" * 600] * 20, finals=["y" * 1200] * 3)
    out = ph.render_summary(s, "p", max_chars=2000)
    assert out.endswith("[truncated]")
    assert len(out) <= 2000 + len("\n[truncated]")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_summary.py -v`
Expected: FAIL with `AttributeError: module 'person_hours' has no attribute '_TEMP_PREFIX'`.

- [ ] **Step 3: Append the summary section to `person_hours.py`**

```python
# ─── Summarizing one session-day ─────────────────────────────
_TEMP_PREFIX = tempfile.gettempdir().replace("\\", "/").lower().rstrip("/") + "/"


def _excluded(path: str) -> bool:
    """Memory files and scratchpads aren't deliverables. Worktrees (.claude/worktrees) are."""
    p = path.replace("\\", "/").lower()
    return "/.claude/projects/" in p or p.startswith(_TEMP_PREFIX) or p.startswith("/tmp/")


def _add_change(changes: dict, result) -> None:
    """Fold one Edit/Write toolUseResult into {path: [added, removed, kind]}."""
    if not isinstance(result, dict) or not result.get("filePath") or _excluded(result["filePath"]):
        return
    path = result["filePath"]
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
        entry[0] += result["content"].count("\n") + 1
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
            elif obj.get("type") == "user":
                raw = None if is_sub else human_text(obj)
                prompt = clean_prompt(raw) if raw else ""
                if prompt:
                    s["prompts"].append(prompt)
                _add_change(s["changes"], obj.get("toolUseResult"))
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_summary.py -v`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add person_hours.py tests/test_person_hours_summary.py
git commit -m "Summarize one session-day of a Claude Code transcript for the judge"
```

---

### Task 5: Judge prompt and output validation

**Files:**
- Modify: `person_hours.py` (append)
- Test: `tests/test_person_hours_parse.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_parse.py`:

```python
import json

import pytest

import person_hours as ph
from helpers import GOOD_ESTIMATE


def test_system_prompt_mentions_partial_sessions():
    assert "This may be one day of a longer session" in ph.SYSTEM_PROMPT


def test_parse_estimate_accepts_plain_json():
    est, err = ph.parse_estimate(json.dumps(GOOD_ESTIMATE))
    assert err is None
    assert est == {"summary": "Fixed the duplicate month bug.", "role": "Software engineer",
                   "hours_low": 2.0, "hours_likely": 3.0, "hours_high": 5.0,
                   "rationale": "Tracing plus testing."}


@pytest.mark.parametrize("wrapper", ["```json\nEST\n```", "Here you go:\nEST\nThanks"])
def test_parse_estimate_tolerates_fences_and_prose(wrapper):
    est, err = ph.parse_estimate(wrapper.replace("EST", json.dumps(GOOD_ESTIMATE)))
    assert err is None and est["hours_likely"] == 3.0


@pytest.mark.parametrize("change, expected", [
    ({"hours_low": 4}, "out of order"),
    ({"hours_high": 600}, "out of order"),
    ({"hours_low": 0}, "out of order"),
    ({"hours_likely": "three"}, "non-numeric"),
    ({"summary": " "}, "summary"),
])
def test_parse_estimate_rejects_bad_values(change, expected):
    est, err = ph.parse_estimate(json.dumps({**GOOD_ESTIMATE, **change}))
    assert est is None and expected in err


def test_parse_estimate_rejects_missing_fields_and_non_json():
    missing = {k: v for k, v in GOOD_ESTIMATE.items() if k != "hours_high"}
    assert "non-numeric" in ph.parse_estimate(json.dumps(missing))[1]
    assert ph.parse_estimate("I can't help with that.")[1] == "no JSON object in response"
    assert ph.parse_estimate("{not json}")[1] == "invalid JSON"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_parse.py -v`
Expected: FAIL with `AttributeError: module 'person_hours' has no attribute 'SYSTEM_PROMPT'`.

- [ ] **Step 3: Append the prompt and parser to `person_hours.py`**

```python
# ─── The judge ───────────────────────────────────────────────
SYSTEM_PROMPT = """You estimate how much human professional effort a piece of completed work represents.

You will receive a structured record of Claude Code work: the user's requests, the files created or edited (with line counts), tool usage, the shell commands that were run, and the assistant's final messages. This may be one day of a longer session; estimate only the work in this record.

Estimate how many hours a competent professional with the appropriate skills (for example a software engineer, systems administrator, or research analyst who knows this kind of work but has no AI tools) would need to accomplish the same outcomes without AI assistance.

Include: understanding the request, investigation and research, writing and editing code or documents, testing and debugging, and verifying results.
Exclude: time spent waiting, dead ends caused only by the AI's own mistakes, and work that was clearly thrown away. Judge generated files by what they accomplish, not by their line count: boilerplate, generated reports and scratch files take a person far less time per line than core logic.

Respond with only a JSON object, no prose and no code fences:
{"summary": "<one sentence: what was accomplished>", "role": "<professional role>", "hours_low": <number>, "hours_likely": <number>, "hours_high": <number>, "rationale": "<two or three sentences>"}"""


def parse_estimate(text: str):
    """(estimate, None) from the judge's reply, or (None, reason) when it isn't usable."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None, "no JSON object in response"
    try:
        obj = json.loads(match.group(0))
    except ValueError:
        return None, "invalid JSON"
    if not isinstance(obj, dict):
        return None, "invalid JSON"
    try:
        low, likely, high = (float(obj[k]) for k in ("hours_low", "hours_likely", "hours_high"))
    except (KeyError, TypeError, ValueError):
        return None, "missing or non-numeric hours"
    if not (0 < low <= likely <= high <= MAX_HOURS):
        return None, f"hours out of order or out of range: {low}/{likely}/{high}"
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, "missing summary"
    role = obj.get("role") if isinstance(obj.get("role"), str) else ""
    rationale = obj.get("rationale") if isinstance(obj.get("rationale"), str) else ""
    return {"summary": summary.strip(), "role": role.strip(), "hours_low": low,
            "hours_likely": likely, "hours_high": high, "rationale": rationale.strip()}, None
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_parse.py -v`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add person_hours.py tests/test_person_hours_parse.py
git commit -m "Add judge prompt and estimate validation"
```

---

### Task 6: Calling the `claude` CLI

**Files:**
- Modify: `person_hours.py` (append)
- Test: `tests/test_person_hours_call.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_call.py`:

```python
import subprocess

import person_hours as ph
from helpers import GOOD_ESTIMATE, FakeRun, envelope


def test_call_judge_success_uses_headless_isolated_flags():
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    res = ph.call_judge("SUMMARY", "claude.exe", runner=run)
    assert res["ok"] and res["estimate"]["hours_likely"] == 3.0
    assert res["in_tokens"] == 4600 and res["out_tokens"] == 1500
    assert res["cost_usd"] == 0.05 and res["model"] == "claude-sonnet-5"
    cmd, kw = run.calls[0]
    assert cmd[:2] == ["claude.exe", "-p"]
    assert "--safe-mode" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--system-prompt") + 1] == ph.SYSTEM_PROMPT
    assert kw["input"] == "SUMMARY" and kw["timeout"] == ph.CALL_TIMEOUT_S


def test_call_judge_reports_timeout():
    run = FakeRun(raises=subprocess.TimeoutExpired("claude", 300))
    assert ph.call_judge("S", "claude", runner=run) == {"ok": False, "error": "timeout"}


def test_call_judge_reports_launch_failure():
    res = ph.call_judge("S", "claude", runner=FakeRun(raises=FileNotFoundError("nope")))
    assert not res["ok"] and res["error"].startswith("launch:")


def test_call_judge_reports_non_json_output():
    res = ph.call_judge("S", "claude", runner=FakeRun(stdout="Error: not logged in", returncode=1))
    assert not res["ok"] and res["error"] == "exit 1: Error: not logged in"


def test_call_judge_reports_cli_errors_and_unusable_estimates():
    err = ph.call_judge("S", "claude",
                        runner=FakeRun(stdout=envelope("Credit balance too low", is_error=True)))
    assert not err["ok"] and err["error"].startswith("cli:") and err["cost_usd"] == 0.05
    bad = ph.call_judge("S", "claude", runner=FakeRun(stdout=envelope("I won't estimate this.")))
    assert not bad["ok"] and bad["error"] == "no JSON object in response"


def test_find_claude_cli_falls_back_to_local_bin(tmp_path, monkeypatch):
    monkeypatch.setattr(ph.shutil, "which", lambda name: None)
    monkeypatch.setattr(ph.Path, "home", lambda: tmp_path)
    assert ph.find_claude_cli() is None
    exe = tmp_path / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert ph.find_claude_cli() == str(exe)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_call.py -v`
Expected: FAIL with `AttributeError: module 'person_hours' has no attribute 'call_judge'`.

- [ ] **Step 3: Append to `person_hours.py`**

```python
def find_claude_cli():
    """Path to the claude CLI. Task Scheduler's PATH may lack ~/.local/bin, so check it too."""
    found = shutil.which("claude")
    if found:
        return found
    for name in ("claude.exe", "claude"):
        candidate = Path.home() / ".local" / "bin" / name
        if candidate.exists():
            return str(candidate)
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
    cmd = [cli, "-p", "--safe-mode", "--model", JUDGE_MODEL, "--no-session-persistence",
           "--tools", "", "--system-prompt", SYSTEM_PROMPT, "--output-format", "json"]
    try:
        proc = runner(cmd, input=summary_text, capture_output=True, encoding="utf-8",
                      errors="replace", timeout=CALL_TIMEOUT_S, cwd=str(db.DB_PATH.parent),
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except OSError as ex:
        return {"ok": False, "error": f"launch: {ex}"}
    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        envelope = None
    if not isinstance(envelope, dict):
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
        return {"ok": False, "error": f"cli: {str(envelope.get('result'))[:200]}", **meta}
    estimate, error = parse_estimate(envelope.get("result") or "")
    if error:
        return {"ok": False, "error": error, **meta}
    return {"ok": True, "estimate": estimate, **meta}
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_call.py -v`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add person_hours.py tests/test_person_hours_call.py
git commit -m "Call the claude CLI headless for person-hours estimates"
```

---

### Task 7: Queue — discovery, pending rows, rate counting

**Files:**
- Modify: `person_hours.py` (append)
- Test: `tests/test_person_hours_queue.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_queue.py`:

```python
from datetime import datetime

import person_hours as ph
from helpers import add_message, user, write_jsonl

NOW = datetime(2026, 9, 20, 12, 0, 0)


def _rows(conn):
    return {(r["session_id"], r["date"]): dict(r)
            for r in conn.execute("SELECT * FROM person_hour_estimates")}


def test_discover_queues_quiet_claude_code_session_days_only(conn):
    add_message(conn, "quiet", "2026-09-20T11:00:00")
    add_message(conn, "busy", "2026-09-20T11:45:00")                      # 15 min ago
    add_message(conn, "desk", "2026-09-20T09:00:00", source="claude-desktop")
    add_message(conn, "old", "2026-06-01T09:00:00")                       # outside 90 days
    assert ph.discover(conn, NOW, index={}) == 1
    rows = _rows(conn)
    assert list(rows) == [("quiet", "2026-09-20")]
    assert rows[("quiet", "2026-09-20")]["status"] == "pending"
    assert rows[("quiet", "2026-09-20")]["is_scheduled"] == 0


def test_discover_marks_every_day_of_a_scheduled_session(conn, tmp_path):
    main = write_jsonl(tmp_path / "p" / "sched.jsonl", [
        user("2026-09-19T06:00:00", '<scheduled-task name="daily">go</scheduled-task>')])
    add_message(conn, "sched", "2026-09-19T06:01:00")
    add_message(conn, "sched", "2026-09-20T06:01:00")
    ph.discover(conn, NOW, index={"sched": main})
    assert [r["is_scheduled"] for r in _rows(conn).values()] == [1, 1]


def test_discover_requeues_judged_days_with_new_messages(conn):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    ph.discover(conn, NOW, index={})
    conn.execute("UPDATE person_hour_estimates SET status = 'done', hours_likely = 3,"
                 " judged_through = '2026-09-20T09:00:00'")
    conn.commit()
    ph.discover(conn, NOW, index={})
    assert _rows(conn)[("s1", "2026-09-20")]["status"] == "done"
    add_message(conn, "s1", "2026-09-20T10:00:00")
    ph.discover(conn, NOW, index={})
    row = _rows(conn)[("s1", "2026-09-20")]
    assert row["status"] == "pending" and row["hours_likely"] == 3


def test_pending_rows_newest_first_with_limited_retries(conn):
    conn.executemany(
        "INSERT INTO person_hour_estimates (session_id, date, status, attempts) VALUES (?, ?, ?, ?)",
        [("a", "2026-09-18", "pending", 0), ("b", "2026-09-20", "pending", 0),
         ("c", "2026-09-19", "error", 2), ("d", "2026-09-19", "error", 3),
         ("e", "2026-09-19", "done", 0)])
    conn.commit()
    assert [r["session_id"] for r in ph.pending_rows(conn, 10)] == ["b", "c", "a"]
    assert len(ph.pending_rows(conn, 2)) == 2
    assert ph.queue_counts(conn) == {"pending": 3, "errors": 1}


def test_calls_last_hour_counts_recent_attempts(conn):
    conn.executemany(
        "INSERT INTO person_hour_estimates (session_id, date, status, last_attempt_at)"
        " VALUES (?, '2026-09-20', 'done', ?)",
        [("a", "2026-09-20T11:30:00"), ("b", "2026-09-20T10:30:00"), ("c", None)])
    conn.commit()
    assert ph.calls_last_hour(conn, NOW) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_queue.py -v`
Expected: FAIL with `AttributeError: module 'person_hours' has no attribute 'discover'`.

- [ ] **Step 3: Append the queue section to `person_hours.py`**

```python
# ─── Queue ───────────────────────────────────────────────────
def _ts(dt: datetime) -> str:
    """Local-time ISO string in the messages.timestamp format."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def discover(conn, now: datetime, index: dict, backfill_days: int = BACKFILL_DAYS) -> int:
    """Queue quiet session-days: new ones, and judged ones with messages newer than the judgment.

    Returns how many new session-days were queued.
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
    scheduled = {}
    for row in new:
        sid = row["session_id"]
        if sid not in scheduled:
            main = index.get(sid)
            scheduled[sid] = bool(main) and is_scheduled_session(main)
        conn.execute(
            "INSERT OR IGNORE INTO person_hour_estimates (session_id, date, status, is_scheduled)"
            " VALUES (?, ?, 'pending', ?)", (sid, row["date"], int(scheduled[sid])))
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
    conn.commit()
    return len(new)


def pending_rows(conn, limit: int) -> list:
    """Session-days waiting for a judgment (or a retry), newest date first."""
    return conn.execute("""
        SELECT session_id, date FROM person_hour_estimates
        WHERE status = 'pending' OR (status = 'error' AND attempts < ?)
        ORDER BY date DESC, session_id
        LIMIT ?
    """, (MAX_ATTEMPTS, limit)).fetchall()


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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_queue.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add person_hours.py tests/test_person_hours_queue.py
git commit -m "Queue quiet session-days for person-hours judging"
```

---

### Task 8: Judging queued session-days

**Files:**
- Modify: `person_hours.py` (append)
- Test: `tests/test_person_hours_judge.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_judge.py`:

```python
from datetime import datetime

import person_hours as ph
from helpers import (GOOD_ESTIMATE, FakeRun, add_message, envelope, queued_session, user,
                     write_jsonl)

NOW = datetime(2026, 9, 20, 12, 0, 0)


def _row(conn, sid, date="2026-09-20"):
    return dict(conn.execute("SELECT * FROM person_hour_estimates WHERE session_id = ? AND date = ?",
                             (sid, date)).fetchone())


def _judge(index, runner, sid="s1", date="2026-09-20"):
    return ph.judge_session_day(sid, date, index, "claude", runner=runner, now_fn=lambda: NOW)


def test_judge_session_day_stores_the_estimate(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    assert _judge(index, run) == "done"
    row = _row(conn, "s1")
    assert row["status"] == "done" and row["hours_likely"] == 3.0
    assert row["summary"] == "Fixed the duplicate month bug." and row["role"] == "Software engineer"
    assert row["judged_through"] == "2026-09-20T09:05:00"
    assert row["model"] == "claude-sonnet-5" and row["prompt_version"] == ph.PROMPT_VERSION
    assert row["attempts"] == 0 and row["last_attempt_at"] == "2026-09-20T12:00:00"
    assert row["judge_in_tokens"] == 4600 and row["judge_cost_usd"] == 0.05
    assert "Project: Projects / www" in run.calls[0][1]["input"]


def test_judge_sends_previous_day_summary(conn, tmp_path):
    main = write_jsonl(tmp_path / "p" / "s1.jsonl", [
        user("2026-09-19T09:00:00", "Start"), user("2026-09-20T09:00:00", "Continue")])
    add_message(conn, "s1", "2026-09-19T09:05:00")
    add_message(conn, "s1", "2026-09-20T09:05:00")
    ph.discover(conn, NOW, index={"s1": main})
    conn.execute("UPDATE person_hour_estimates SET status = 'done', summary = 'Set up the branch.'"
                 " WHERE date = '2026-09-19'")
    conn.commit()
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    _judge({"s1": main}, run)
    sent = run.calls[0][1]["input"]
    assert "day 2 of a longer session" in sent
    assert "Earlier in this session: Set up the branch." in sent


def test_failures_count_attempts_and_stop_after_three(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    run = FakeRun(stdout=envelope("no estimate here"))
    for n in (1, 2, 3):
        assert _judge(index, run) == "error"
        assert _row(conn, "s1")["attempts"] == n
    assert ph.pending_rows(conn, 10) == []
    row = _row(conn, "s1")
    assert row["status"] == "error" and row["error"] == "no JSON object in response"


def test_missing_transcript_fails_for_good_without_a_call(conn):
    add_message(conn, "gone", "2026-09-20T09:00:00")
    ph.discover(conn, NOW, index={})
    run = FakeRun()
    assert _judge({}, run, sid="gone") == "error"
    row = _row(conn, "gone")
    assert row["error"] == "no_source" and row["attempts"] == ph.MAX_ATTEMPTS
    assert row["last_attempt_at"] is None and run.calls == []


def test_success_after_failure_resets_attempts(conn, tmp_path):
    index = queued_session(conn, tmp_path, "s1", NOW)
    _judge(index, FakeRun(stdout="junk"))
    assert _row(conn, "s1")["error"] == "exit 0: junk"
    _judge(index, FakeRun(stdout=envelope(GOOD_ESTIMATE)))
    row = _row(conn, "s1")
    assert row["status"] == "done" and row["attempts"] == 0 and row["error"] is None


def test_judge_pending_processes_up_to_limit(conn, tmp_path):
    index = {}
    for sid in ("a", "b", "c"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    out = ph.judge_pending(2, "claude", index, runner=FakeRun(stdout=envelope(GOOD_ESTIMATE)),
                           now_fn=lambda: NOW)
    assert out == {"done": 2}
    assert ph.queue_counts(conn)["pending"] == 1
    assert ph.judge_pending(0, "claude", index) == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_judge.py -v`
Expected: FAIL with `AttributeError: module 'person_hours' has no attribute 'judge_session_day'`.

- [ ] **Step 3: Append the judging section to `person_hours.py`**

```python
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
    """(summary_text, context, problem) for a session-day; problem is 'no_source' or 'no_events'."""
    main = index.get(session_id)
    if main is None:
        return None, None, "no_source"
    ctx = _day_context(conn, session_id, date)
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


def judge_session_day(session_id, date, index, cli, runner=None, now_fn=datetime.now) -> str:
    """Judge one queued session-day and store the outcome. Returns 'done' or 'error'."""
    conn = db.get_conn()
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


def judge_pending(limit: int, cli: str, index: dict, runner=None, now_fn=datetime.now) -> dict:
    """Judge up to `limit` queued session-days, newest first, MAX_CONCURRENCY at a time.

    Returns outcome counts, e.g. {"done": 2, "error": 1}.
    """
    if limit <= 0:
        return {}
    conn = db.get_conn()
    try:
        rows = [(r["session_id"], r["date"]) for r in pending_rows(conn, limit)]
    finally:
        conn.close()

    def one(row):
        try:
            return judge_session_day(row[0], row[1], index, cli, runner, now_fn)
        except Exception as ex:  # one bad session-day must not stop the batch
            print(f"[hours {datetime.now():%Y-%m-%d %H:%M:%S}] {row[0]} {row[1]}: {ex}")
            return "error"

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        return dict(Counter(pool.map(one, rows)))
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_judge.py -v`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add person_hours.py tests/test_person_hours_judge.py
git commit -m "Judge queued session-days and record outcomes"
```

---

### Task 9: Worker gating and ticks

**Files:**
- Modify: `person_hours.py` (append)
- Test: `tests/test_person_hours_worker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_worker.py`:

```python
from datetime import datetime

import pytest

import person_hours as ph
from helpers import GOOD_ESTIMATE, FakeRun, add_message, envelope, queued_session

NOW = datetime(2026, 9, 20, 12, 0, 0)
GATES = {"auth_dead": False, "ingest_running": False, "five_hour_pct": 10.0,
         "token_seconds_left": 3600}
BASE = {**GATES, "cli_found": True, "calls_last_hour": 0}


@pytest.fixture(autouse=True)
def reset_worker():
    ph._worker.update(reason=None, last_tick=None)
    yield
    ph._worker.update(reason=None, last_tick=None)


@pytest.mark.parametrize("change, reason", [
    ({}, None),
    ({"cli_found": False, "auth_dead": True}, "unavailable"),
    ({"auth_dead": True, "ingest_running": True}, "auth"),
    ({"ingest_running": True, "five_hour_pct": 95}, "ingest"),
    ({"five_hour_pct": 80}, "quota"),
    ({"five_hour_pct": None}, None),
    ({"token_seconds_left": None}, "auth"),
    ({"token_seconds_left": 599}, "token"),
    ({"calls_last_hour": 20}, "rate"),
])
def test_skip_reason(change, reason):
    assert ph.skip_reason(**{**BASE, **change}) == reason


def test_worker_status_states():
    ph.set_worker_reason("quota", NOW)
    assert ph.worker_status({"pending": 4, "errors": 0}) == {
        "state": "paused", "reason": "quota", "pending": 4, "errors": 0}
    ph.set_worker_reason("unavailable", NOW)
    assert ph.worker_status({"pending": 4, "errors": 0})["state"] == "unavailable"
    ph.set_worker_reason("rate", NOW)
    assert ph.worker_status({"pending": 4, "errors": 1}) == {
        "state": "running", "reason": None, "pending": 4, "errors": 1}
    ph.set_worker_reason(None, NOW)
    assert ph.worker_status({"pending": 0, "errors": 0})["state"] == "idle"


def test_run_tick_judges_within_the_hourly_budget(conn, tmp_path, monkeypatch):
    index = {}
    for sid in ("a", "b", "c"):
        index.update(queued_session(conn, tmp_path, sid, NOW))
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "MAX_CALLS_PER_HOUR", 2)
    run = FakeRun(stdout=envelope(GOOD_ESTIMATE))
    assert ph.run_tick(NOW, GATES, runner=run, now_fn=lambda: NOW) == {"done": 2}
    assert ph.run_tick(NOW, GATES, runner=run, now_fn=lambda: NOW) == {"skipped": "rate"}


def test_run_tick_does_nothing_while_ingesting(monkeypatch):
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: pytest.fail("scanned during ingest"))
    assert ph.run_tick(NOW, {**GATES, "ingest_running": True}) == {"skipped": "ingest"}
    assert ph._worker["reason"] == "ingest"


def test_run_tick_still_queues_when_paused(conn, monkeypatch):
    add_message(conn, "s1", "2026-09-20T09:00:00")
    monkeypatch.setattr(ph, "find_claude_cli", lambda: None)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: {})
    assert ph.run_tick(NOW, GATES) == {"skipped": "unavailable"}
    assert ph.queue_counts(conn)["pending"] == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_worker.py -v`
Expected: FAIL with `AttributeError: module 'person_hours' has no attribute '_worker'`.

- [ ] **Step 3: Append the worker section to `person_hours.py`**

```python
# ─── Worker ──────────────────────────────────────────────────
_worker = {"reason": None, "last_tick": None}
_PAUSE_REASONS = ("auth", "ingest", "quota", "token")


def skip_reason(*, cli_found, auth_dead, ingest_running, five_hour_pct, token_seconds_left,
                calls_last_hour):
    """Why this tick shouldn't call the judge, or None to go ahead. First match wins."""
    if not cli_found:
        return "unavailable"
    if auth_dead:
        return "auth"
    if ingest_running:
        return "ingest"
    if five_hour_pct is not None and five_hour_pct >= QUOTA_PAUSE_PCT:
        return "quota"
    if token_seconds_left is None:
        return "auth"
    if token_seconds_left < TOKEN_MIN_SECONDS:
        return "token"
    if calls_last_hour >= MAX_CALLS_PER_HOUR:
        return "rate"
    return None


def set_worker_reason(reason, now: datetime) -> None:
    _worker["reason"] = reason
    _worker["last_tick"] = _ts(now)


def worker_status(counts: dict) -> dict:
    """Worker state for /api/hours: unavailable, paused (with reason), running or idle."""
    reason = _worker["reason"]
    if reason == "unavailable":
        state = "unavailable"
    elif reason in _PAUSE_REASONS:
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
    conn = db.get_conn()
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_worker.py -v`
Expected: `13 passed`.

- [ ] **Step 5: Commit**

```bash
git add person_hours.py tests/test_person_hours_worker.py
git commit -m "Add person-hours worker gating and tick"
```

---

### Task 10: Command-line backfill

**Files:**
- Modify: `person_hours.py` (append)
- Test: `tests/test_person_hours_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_hours_cli.py`:

```python
from datetime import datetime

import pytest

import person_hours as ph
from helpers import GOOD_ESTIMATE, FakeRun, envelope, queued_session

NOW = datetime(2026, 9, 20, 12, 0, 0)


def test_dry_run_prints_summaries_without_calling(conn, tmp_path, monkeypatch, capsys):
    index = queued_session(conn, tmp_path, "s1", NOW)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: pytest.fail("dry run looked for the CLI"))
    assert ph.main(["--dry-run", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "=== s1 2026-09-20 ===" in out and "Fix the calendar" in out
    assert ph.queue_counts(conn)["pending"] == 1


def test_cli_judges_pending_session_days(conn, tmp_path, monkeypatch, capsys):
    index = queued_session(conn, tmp_path, "s1", NOW)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: "claude")
    monkeypatch.setattr(ph.subprocess, "run", FakeRun(stdout=envelope(GOOD_ESTIMATE)))
    assert ph.main(["--limit", "1"]) == 0
    assert ph.queue_counts(conn) == {"pending": 0, "errors": 0}
    assert "1/1" in capsys.readouterr().out


def test_cli_without_claude_exits_1(conn, tmp_path, monkeypatch):
    index = queued_session(conn, tmp_path, "s1", NOW)
    monkeypatch.setattr(ph, "index_sessions", lambda roots=None: index)
    monkeypatch.setattr(ph, "find_claude_cli", lambda: None)
    assert ph.main([]) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_person_hours_cli.py -v`
Expected: FAIL with `AttributeError: module 'person_hours' has no attribute 'main'`.

- [ ] **Step 3: Append the CLI to `person_hours.py`**

```python
# ─── Command line ────────────────────────────────────────────
def main(argv=None) -> int:
    """Queue and judge session-days by hand. Skips the quota pause; keeps the hourly cap."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # summaries contain × and …
    ap = argparse.ArgumentParser(description="Estimate person-hours for Claude Code session-days.")
    ap.add_argument("--backfill", type=int, default=BACKFILL_DAYS, metavar="DAYS",
                    help=f"queue session-days from the last DAYS days (default {BACKFILL_DAYS})")
    ap.add_argument("--limit", type=int, help="judge at most this many session-days")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be sent to Claude without calling it")
    args = ap.parse_args(argv)

    index = index_sessions()
    conn = db.get_conn()
    try:
        init_db(conn)
        queued = discover(conn, datetime.now(), index, backfill_days=args.backfill)
        waiting = queue_counts(conn)["pending"]
        total = waiting if args.limit is None else min(waiting, args.limit)
        print(f"Queued {queued} new session-days; {waiting} waiting to be judged.")
        if args.dry_run:
            for row in pending_rows(conn, total):
                text, _, problem = build_request(conn, row["session_id"], row["date"], index)
                print(f"\n=== {row['session_id']} {row['date']} ===")
                print(text if text else f"(skipped: {problem})")
            return 0
    finally:
        conn.close()

    cli = find_claude_cli()
    if cli is None:
        print("claude CLI not found on PATH or in ~/.local/bin", file=sys.stderr)
        return 1
    print(f"Judging {total} session-days, at most {MAX_CALLS_PER_HOUR} calls per hour. "
          "The 5-hour quota pause does not apply here.")
    attempted = 0
    while attempted < total:
        conn = db.get_conn()
        try:
            budget = MAX_CALLS_PER_HOUR - calls_last_hour(conn, datetime.now())
        finally:
            conn.close()
        if budget <= 0:
            time.sleep(60)
            continue
        outcome = judge_pending(min(budget, MAX_PER_TICK, total - attempted), cli, index)
        if not outcome:
            break
        attempted += sum(outcome.values())
        print(f"  {attempted}/{total} {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run them to verify they pass**

Run: `$ENV python -m pytest tests/test_person_hours_cli.py -v`
Expected: `3 passed`.

- [ ] **Step 5: Run the whole suite**

Run: `$ENV python -m pytest -q`
Expected: `58 passed`.

- [ ] **Step 6: Commit**

```bash
git add person_hours.py tests/test_person_hours_cli.py
git commit -m "Add person_hours command-line backfill with dry run"
```

---

### Task 11: Aggregation in `db.py`

**Files:**
- Modify: `db.py` (imports at top; `session_list` at lines 135-159; append a section at the end)
- Test: `tests/test_db_hours.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_hours.py`:

```python
from datetime import date, timedelta

import db
from helpers import add_message


def _day(n):
    return (date.today() - timedelta(days=n)).isoformat()


def _estimate(conn, sid, day, hours, *, scheduled=0, status="done", summary=None):
    conn.execute(
        "INSERT OR REPLACE INTO person_hour_estimates (session_id, date, status, is_scheduled,"
        " hours_low, hours_likely, hours_high, summary, role, rationale, model, last_attempt_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Engineer', 'Why.', 'claude-sonnet-5', ?)",
        (sid, day, status, scheduled, hours / 2, hours, hours * 2, summary, f"{day}T12:00:00"))
    conn.commit()


def _row(active, hours=None, scheduled=0, project="p"):
    return {"session_id": "x", "date": "2026-09-20", "project": project, "active_hours": active,
            "is_scheduled": scheduled, "hours_likely": hours}


def test_session_day_rows_caps_idle_gaps(conn):
    for ts in ("2026-09-20T10:00:00", "2026-09-20T10:02:00", "2026-09-20T10:30:00"):
        add_message(conn, "s1", ts)
    [row] = db._session_day_rows(conn, "2026-09-01")
    assert abs(row["active_hours"] - (120 + 300) / 3600) < 1e-6
    assert row["hours_likely"] is None and row["is_scheduled"] == 0 and row["project"] == "proj"


def test_leverage_falls_back_until_enough_samples():
    rows = [_row(1.0, 6.0) for _ in range(9)]
    assert db.leverage_from_rows(rows) == {"interactive": db.DEFAULT_LEVERAGE,
                                           "scheduled": db.DEFAULT_LEVERAGE}
    rows += [_row(1.0, 8.0), _row(0.05, 99.0)]  # 10th usable sample; tiny active time ignored
    assert db.leverage_from_rows(rows)["interactive"] == 6.0


def test_hours_summary_splits_scheduled_and_marks_provisional():
    leverage = {"interactive": 4.0, "scheduled": 10.0}
    rows = [_row(1.0, 6.0, project="a"), _row(0.5, None, project="b"),
            _row(0.2, 3.0, scheduled=1), _row(0.1, None, scheduled=1)]
    out = db.hours_summary(rows, leverage)
    assert out["interactive"] == {"hours": 8.0, "judged_hours": 6.0, "provisional_hours": 2.0,
                                  "session_days": 2, "provisional_days": 1}
    assert out["scheduled"] == {"hours": 4.0, "runs": 2}
    assert out["active_hours"] == 1.5 and out["leverage"] == 5.3 and out["work_weeks"] == 0.2
    assert out["by_project"] == [{"project": "a", "hours": 6.0}, {"project": "b", "hours": 2.0}]


def test_hours_summary_groups_small_projects_into_other():
    rows = [_row(1.0, h, project=p)
            for p, h in (("a", 9.0), ("b", 7.0), ("c", 5.0), ("d", 2.0), ("e", 1.0))]
    out = db.hours_summary(rows, {"interactive": 5.0, "scheduled": 5.0})
    assert out["by_project"][-1] == {"project": "other", "hours": 3.0}
    assert [p["project"] for p in out["by_project"]] == ["a", "b", "c", "other"]


def test_hours_summary_without_active_time_has_no_leverage():
    assert db.hours_summary([], {"interactive": 5.0, "scheduled": 5.0})["leverage"] is None


def test_person_hours(conn):
    for sid, n in (("s1", 1), ("s2", 2)):
        add_message(conn, sid, f"{_day(n)}T10:00:00")
        add_message(conn, sid, f"{_day(n)}T10:05:00")
    add_message(conn, "old", f"{_day(40)}T10:00:00")
    add_message(conn, "desk", f"{_day(1)}T10:00:00", source="claude-desktop")
    _estimate(conn, "s1", _day(1), 6.0)
    out = db.person_hours(30)
    assert out["days"] == 30 and out["model"] == "claude-sonnet-5"
    assert out["interactive"]["session_days"] == 2 and out["interactive"]["provisional_days"] == 1
    assert out["interactive"]["hours"] == round(6.0 + (5 / 60) * db.DEFAULT_LEVERAGE, 1)


def test_session_list_includes_person_hours(conn):
    add_message(conn, "s1", f"{_day(1)}T10:00:00")
    add_message(conn, "s1", f"{_day(1)}T10:05:00")
    add_message(conn, "s1", f"{_day(0)}T09:00:00")  # second day, not judged yet
    add_message(conn, "desk", f"{_day(1)}T10:00:00", source="claude-desktop")
    _estimate(conn, "s1", _day(1), 6.0)
    by_id = {s["session_id"]: s for s in db.session_list(7)}
    assert by_id["s1"]["person_hours"] == 6.0 and by_id["s1"]["hours_status"] == "partial"
    assert by_id["desk"]["person_hours"] is None and by_id["desk"]["hours_status"] is None


def test_session_hours_lists_each_day(conn):
    add_message(conn, "s1", f"{_day(1)}T10:00:00")
    add_message(conn, "s1", f"{_day(0)}T09:00:00")
    add_message(conn, "s1", f"{_day(0)}T09:06:00")
    _estimate(conn, "s1", _day(1), 6.0, summary="Did the thing.")
    days = db.session_hours("s1")
    assert [d["date"] for d in days] == [_day(1), _day(0)]
    assert days[0]["status"] == "done" and days[0]["summary"] == "Did the thing."
    assert days[0]["hours_likely"] == 6.0 and days[0]["provisional_hours"] is None
    assert days[1]["status"] == "provisional"
    assert days[1]["provisional_hours"] == round(0.1 * db.DEFAULT_LEVERAGE, 1)
    assert db.session_hours("nope") == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `$ENV python -m pytest tests/test_db_hours.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute '_session_day_rows'`.

- [ ] **Step 3: Add `import statistics` to `db.py`**

At the top of `db.py`, change:

```python
import sqlite3
from datetime import datetime, timedelta, timezone
```

to:

```python
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 4: Append the person-hours section to the end of `db.py`**

```python
# ─── Person-hours ────────────────────────────────────────────
# Estimates are written by person_hours.py; these queries turn them into card figures.
ACTIVE_GAP_CAP_S = 300          # gaps between messages longer than this count as idle
DEFAULT_LEVERAGE = 5.0          # hours per active hour until enough session-days are judged
MIN_SAMPLES_FOR_LEVERAGE = 10
LEVERAGE_LOOKBACK_DAYS = 90
HOURS_PER_WORK_WEEK = 40


def _session_day_rows(conn, since: str, session_id: str | None = None) -> list[dict]:
    """One row per Claude Code session-day since `since`: active hours, project, any estimate."""
    session_filter = "AND session_id = ?" if session_id else ""
    params = [since] + ([session_id] if session_id else [])
    rows = conn.execute(f"""
        WITH ordered AS (
            SELECT session_id, date, project, timestamp,
                   LAG(timestamp) OVER (PARTITION BY session_id, date ORDER BY timestamp) AS prev_ts
            FROM messages
            WHERE COALESCE(source, 'claude-code') = 'claude-code' AND date >= ? {session_filter}
        ),
        days AS (
            SELECT session_id, date, MAX(project) AS project,
                   SUM(MIN(COALESCE((julianday(timestamp) - julianday(prev_ts)) * 86400.0, 0),
                           {ACTIVE_GAP_CAP_S})) / 3600.0 AS active_hours
            FROM ordered
            GROUP BY session_id, date
        )
        SELECT d.session_id, d.date, d.project, d.active_hours,
               e.status, COALESCE(e.is_scheduled, 0) AS is_scheduled,
               e.hours_low, e.hours_likely, e.hours_high, e.summary, e.role, e.rationale
        FROM days d
        LEFT JOIN person_hour_estimates e ON e.session_id = d.session_id AND e.date = d.date
        ORDER BY d.date, d.session_id
    """, params).fetchall()
    return [dict(r) for r in rows]


def leverage_from_rows(rows) -> dict:
    """Median judged hours per active hour, separately for interactive and scheduled days."""
    samples = {"interactive": [], "scheduled": []}
    for r in rows:
        if r["hours_likely"] is not None and (r["active_hours"] or 0) >= 0.1:
            group = "scheduled" if r["is_scheduled"] else "interactive"
            samples[group].append(r["hours_likely"] / r["active_hours"])
    return {group: statistics.median(v) if len(v) >= MIN_SAMPLES_FOR_LEVERAGE else DEFAULT_LEVERAGE
            for group, v in samples.items()}


def _day_hours(r, leverage) -> tuple[float, bool]:
    """(hours, judged) for a session-day: the judge's figure, else active hours x leverage.

    A re-queued day keeps its last judged figure until the new judgment replaces it.
    """
    if r["hours_likely"] is not None:
        return r["hours_likely"], True
    group = "scheduled" if r["is_scheduled"] else "interactive"
    return (r["active_hours"] or 0) * leverage[group], False


def hours_summary(rows, leverage) -> dict:
    """Card figures from session-day rows: interactive headline, scheduled line, projects."""
    inter = {"hours": 0.0, "judged_hours": 0.0, "provisional_hours": 0.0,
             "session_days": 0, "provisional_days": 0}
    sched = {"hours": 0.0, "runs": 0}
    active = 0.0
    projects: dict[str, float] = {}
    for r in rows:
        hours, judged = _day_hours(r, leverage)
        if r["is_scheduled"]:
            sched["hours"] += hours
            sched["runs"] += 1
            continue
        inter["hours"] += hours
        inter["judged_hours" if judged else "provisional_hours"] += hours
        inter["session_days"] += 1
        inter["provisional_days"] += 0 if judged else 1
        active += r["active_hours"] or 0
        projects[r["project"]] = projects.get(r["project"], 0.0) + hours
    ranked = sorted(projects.items(), key=lambda kv: -kv[1])
    if len(ranked) > 4:
        ranked = ranked[:3] + [("other", sum(h for _, h in ranked[3:]))]
    return {
        "interactive": {k: round(v, 1) if isinstance(v, float) else v for k, v in inter.items()},
        "scheduled": {"hours": round(sched["hours"], 1), "runs": sched["runs"]},
        "active_hours": round(active, 1),
        "leverage": round(inter["hours"] / active, 1) if active else None,
        "work_weeks": round(inter["hours"] / HOURS_PER_WORK_WEEK, 1),
        "by_project": [{"project": p, "hours": round(h, 1)} for p, h in ranked],
    }


def person_hours(days: int = 30) -> dict:
    """Figures for the cost card's person-hours view."""
    conn = get_conn()
    try:
        rows = _session_day_rows(conn, _since_date(max(days, LEVERAGE_LOOKBACK_DAYS)))
        latest = conn.execute(
            "SELECT model FROM person_hour_estimates WHERE status = 'done' AND model IS NOT NULL"
            " ORDER BY last_attempt_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    lookback = _since_date(LEVERAGE_LOOKBACK_DAYS)
    leverage = leverage_from_rows([r for r in rows if r["date"] >= lookback])
    since = _since_date(days)
    out = hours_summary([r for r in rows if r["date"] >= since], leverage)
    out["days"] = days
    out["model"] = latest["model"] if latest else None
    return out


def _session_hours_map(conn, since: str) -> dict:
    """session_id -> (person_hours, 'done' | 'provisional' | 'partial') for days since `since`."""
    lookback = _since_date(LEVERAGE_LOOKBACK_DAYS)
    rows = _session_day_rows(conn, min(since, lookback))
    leverage = leverage_from_rows([r for r in rows if r["date"] >= lookback])
    acc: dict[str, list] = {}
    for r in rows:
        if r["date"] < since:
            continue
        hours, judged = _day_hours(r, leverage)
        entry = acc.setdefault(r["session_id"], [0.0, 0, 0])
        entry[0] += hours
        entry[1] += judged
        entry[2] += 1
    return {sid: (round(h, 1), "done" if nj == n else "provisional" if nj == 0 else "partial")
            for sid, (h, nj, n) in acc.items()}


def session_hours(session_id: str) -> list[dict]:
    """Per-day person-hours for one session (the drill-down panel)."""
    conn = get_conn()
    try:
        leverage = leverage_from_rows(_session_day_rows(conn, _since_date(LEVERAGE_LOOKBACK_DAYS)))
        rows = _session_day_rows(conn, "0000-00-00", session_id=session_id)
    finally:
        conn.close()
    out = []
    for r in rows:
        hours, judged = _day_hours(r, leverage)
        out.append({
            "date": r["date"], "status": "done" if judged else "provisional",
            "hours_low": r["hours_low"], "hours_likely": r["hours_likely"],
            "hours_high": r["hours_high"],
            "provisional_hours": None if judged else round(hours, 1),
            "summary": r["summary"], "role": r["role"], "rationale": r["rationale"],
        })
    return out
```

- [ ] **Step 5: Add hours to `session_list`**

Replace the whole `session_list` function in `db.py` with:

```python
def session_list(days: int = 30) -> list[dict]:
    """Return per-session summary, most recent first, with person-hours for Claude Code sessions."""
    conn = get_conn()
    since = _since_date(days)
    rows = conn.execute("""
        SELECT session_id,
               project,
               model,
               MIN(timestamp) as start_time,
               MAX(timestamp) as end_time,
               COUNT(*) as message_count,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               SUM(output_tokens) as output_tokens,
               date,
               MAX(entrypoint) as entrypoint,
               MAX(speed) as speed,
               MAX(git_branch) as git_branch,
               MAX(source) as source
        FROM messages
        WHERE date >= ?
        GROUP BY session_id
        ORDER BY start_time DESC
        LIMIT 200
    """, (since,)).fetchall()
    hours = _session_hours_map(conn, since)
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["person_hours"], d["hours_status"] = hours.get(d["session_id"], (None, None))
        out.append(d)
    return out
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `$ENV python -m pytest tests/test_db_hours.py -v`
Expected: `8 passed`.

- [ ] **Step 7: Commit**

```bash
git add db.py tests/test_db_hours.py
git commit -m "Aggregate judged and provisional person-hours in db.py"
```

---

### Task 12: Count judge calls as local activity

**Files:**
- Modify: `db.py` (`detect_other_pct`, around lines 352-389)
- Test: `tests/test_db_other_pct.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_other_pct.py`:

```python
import db


def test_judge_calls_count_as_local_activity(conn):
    conn.executemany("INSERT INTO quota_snapshots VALUES (?, ?, ?)", [
        ("2026-09-20T10:00:00", 10, 5), ("2026-09-20T10:05:00", 12, 5),
        ("2026-09-20T10:10:00", 15, 5)])
    conn.execute("INSERT INTO person_hour_estimates (session_id, date, status, last_attempt_at)"
                 " VALUES ('s', '2026-09-20', 'done', '2026-09-20T10:07:00')")
    conn.commit()
    out = db.detect_other_pct("2026-09-20T09:00:00", "2026-09-20T11:00:00", "5h")
    # 10:00-10:05 (+2) had no local activity; 10:05-10:10 (+3) had a judge call.
    assert out == {"other_pct": 2.0, "has_snapshots": True}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `$ENV python -m pytest tests/test_db_other_pct.py -v`
Expected: FAIL: `other_pct` is `5.0`.

- [ ] **Step 3: Implement**

In `db.py`, add this function directly above `def detect_other_pct`:

```python
def _judge_calls_between(conn, start: str, end: str) -> int:
    """Person-hours judge calls use quota but leave no session log to ingest."""
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM person_hour_estimates WHERE last_attempt_at >= ? AND last_attempt_at < ?",
            (start, end)).fetchone()[0]
    except sqlite3.OperationalError:  # table not created yet
        return 0
```

Then in `detect_other_pct`, replace:

```python
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp >= ? AND timestamp < ?",
            (t1, t2)
        ).fetchone()[0]
        if count == 0:
            other_pct += quota_delta
```

with:

```python
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp >= ? AND timestamp < ?",
            (t1, t2)
        ).fetchone()[0]
        if count == 0:
            count = _judge_calls_between(conn, t1, t2)
        if count == 0:
            other_pct += quota_delta
```

- [ ] **Step 4: Run it to verify it passes, then the suite**

Run: `$ENV python -m pytest tests/test_db_other_pct.py -v && $ENV python -m pytest -q`
Expected: `1 passed`, then all tests pass.

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db_other_pct.py
git commit -m "Don't attribute person-hours judge calls to Other usage"
```

---

### Task 13: Wire the worker and endpoints into `app.py`

**Files:**
- Modify: `app.py` (imports lines 4-20; new helpers before `@app.on_event("startup")` at line 519; `startup()`; new endpoints after `/api/session/{session_id}` at line 620)

No unit tests: `app.py` has none and importing it starts nothing testable in isolation. Verified by running a server in Step 6.

- [ ] **Step 1: Imports and the worker switch**

In `app.py`, change:

```python
import asyncio
import json
import threading
```

to:

```python
import asyncio
import json
import os
import threading
```

and change:

```python
import db
```

to:

```python
import db
import person_hours
```

After the `_auth_dead_creds_sig = None ...` line (line 70), add:

```python

# Person-hours judge worker. PERSON_HOURS_WORKER=off disables it (e.g. for a test server).
HOURS_WORKER_ENABLED = os.environ.get("PERSON_HOURS_WORKER", "on").lower() not in ("0", "off", "false")
_hours_lock = threading.Lock()
```

- [ ] **Step 2: Worker helpers**

Directly above `@app.on_event("startup")`, add:

```python
def _cached_five_hour_pct() -> float | None:
    """Last known 5-hour quota %, or None when unknown or from a window that has reset."""
    data = _usage_cache.get("data")
    if data:
        return _bound_stale_quota(data, time.monotonic() - _usage_cache["fetched_at"]).get("five_hour_pct")
    disk = _read_disk_cache_data()
    return disk.get("five_hour_pct") if disk else None


def _token_seconds_left() -> float | None:
    """Seconds until the stored OAuth access token expires, or None without credentials."""
    oauth = (_read_credentials() or {}).get("claudeAiOauth") or {}
    if not oauth.get("accessToken"):
        return None
    return (oauth.get("expiresAt") or 0) / 1000 - time.time()


def _hours_tick():
    """Judge queued session-days for the person-hours view, then reschedule."""
    if _hours_lock.acquire(blocking=False):
        try:
            left = _token_seconds_left()
            if left is not None and left < person_hours.TOKEN_MIN_SECONDS:
                # Refresh here, in the process that already owns token refresh, so the CLI
                # never starts a call on a token it would have to refresh itself.
                _refresh_oauth_token()
                left = _token_seconds_left()
            person_hours.run_tick(datetime.now(), {
                "auth_dead": _auth_dead,
                "ingest_running": bool(_ingest_status.get("running")),
                "five_hour_pct": _cached_five_hour_pct(),
                "token_seconds_left": left,
            })
        except Exception as ex:
            print(f"[hours {datetime.now():%Y-%m-%d %H:%M:%S}] tick failed: {ex}")
        finally:
            _hours_lock.release()
    t = threading.Timer(person_hours.TICK_SECONDS, _hours_tick)
    t.daemon = True
    t.start()
```

- [ ] **Step 3: Startup — schema check and first tick**

Replace the whole `startup()` function with:

```python
@app.on_event("startup")
async def startup():
    """Kick off ingest if DB is missing or stale, then schedule periodic ingest and judging."""
    stats = db.db_stats()
    if not stats.get("exists") or stats.get("message_count", 0) == 0:
        thread = threading.Thread(target=_run_ingest_background, daemon=True)
        thread.start()
    else:
        _ingest_status["done"] = True
    if stats.get("exists"):
        # Existing DBs get newer tables (person_hour_estimates) before any request needs them.
        from ingest import get_db, init_db
        conn = get_db()
        try:
            init_db(conn)
        finally:
            conn.close()
    # Pre-refresh the OAuth token so the first /api/quota poll doesn't pay the latency
    # (or fail with 401 when the token expired while the machine was off).
    threading.Thread(target=_read_oauth_token, daemon=True).start()
    t = threading.Timer(90, _periodic_ingest)
    t.daemon = True
    t.start()
    if HOURS_WORKER_ENABLED:
        h = threading.Timer(60, _hours_tick)
        h.daemon = True
        h.start()
```

- [ ] **Step 4: Endpoints**

After the `session_detail` endpoint (the one ending `return data` under `@app.get("/api/session/{session_id}")`), add:

```python


@app.get("/api/session/{session_id}/hours")
async def session_hours(session_id: str):
    """Per-day person-hours for one session (empty for Desktop sessions)."""
    return db.session_hours(session_id)


@app.get("/api/hours")
async def hours(days: int = 30):
    """Person-hours for the cost card's hours view, plus the judge worker's state."""
    data = db.person_hours(days)
    conn = db.get_conn()
    try:
        counts = person_hours.queue_counts(conn)
    finally:
        conn.close()
    worker = person_hours.worker_status(counts)
    if not HOURS_WORKER_ENABLED:
        worker = {**worker, "state": "paused", "reason": "disabled"}
    data["worker"] = worker
    return data
```

- [ ] **Step 5: Run the test suite (import check)**

Run: `$ENV python -c "import app; print('ok')" && $ENV python -m pytest -q`
Expected: `ok`, then all tests pass.

- [ ] **Step 6: Verify the endpoints on a test server (worker off)**

1. Copy the live DB safely (SQLite backup API; the :8080 server may be writing):

```bash
mkdir -p data && $ENV python -c "import sqlite3; s = sqlite3.connect(r'C:\Users\weaverjc\Projects\Personal\claude-usage-dashboard\data\usage.db'); d = sqlite3.connect('data/usage.db'); s.backup(d); d.close(); s.close()" && cp "C:/Users/weaverjc/Projects/Personal/claude-usage-dashboard/data/quota_cache.json" data/ 2>/dev/null; ls -la data
```

2. Start the server in the background (Bash tool with `run_in_background: true`):

```bash
eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && PERSON_HOURS_WORKER=off python app.py --port 8888
```

3. Check the endpoints once it's listening:

```bash
curl -s "http://127.0.0.1:8888/api/hours?days=30" | head -c 800; echo; curl -s "http://127.0.0.1:8888/api/sessions?days=7" | head -c 600; echo
```

Expected: `/api/hours` returns JSON with `interactive`, `scheduled`, `active_hours`, `leverage`, `work_weeks`, `by_project`, `days: 30`, `model: null`, and `worker.state == "paused"`, `worker.reason == "disabled"`. All interactive hours are provisional at this point (active hours × 5). Sessions include `person_hours` and `hours_status: "provisional"` for Claude Code sessions and `null` for Desktop ones. Take a `session_id` from the list and check:

```bash
curl -s "http://127.0.0.1:8888/api/session/<session_id>/hours"
```

Expected: a list of per-day rows with `status: "provisional"` and `provisional_hours`.

4. Leave the server running for Tasks 14–15.

- [ ] **Step 7: Commit**

```bash
git add app.py
git commit -m "Run the person-hours worker and serve /api/hours"
```

---

### Task 14: Cost card toggle and hours view

**Files:**
- Modify: `templates/index.html:209-219` (cost card)
- Modify: `static/style.css` (after `.cost-note`, around line 439)
- Modify: `static/dashboard.js` (`DOMContentLoaded` handler lines 52-59, `initAll` lines 62-75, cost section around line 1314)

- [ ] **Step 1: Replace the cost card markup**

In `templates/index.html`, replace the block from `<!-- Cost panel -->` through its closing `</div>` (lines 209-219) with:

```html
        <!-- Cost / person-hours panel -->
        <div class="card cost-card" id="costCard">
          <div class="cost-header">
            <span class="cost-icon" id="costIcon" aria-hidden="true">✦</span>
            <span class="cost-title" id="costTitle">Est. API Cost</span>
            <span class="cost-period" id="costPeriod">30 days</span>
            <div class="day-selector" role="group" aria-label="Show API cost or person-hours">
              <button type="button" class="day-btn unit-btn active" id="unitCost" aria-pressed="true"
                      onclick="setCostUnit('cost')" title="Estimated API cost">$</button>
              <button type="button" class="day-btn unit-btn" id="unitHours" aria-pressed="false"
                      onclick="setCostUnit('hours')" title="Person-hours">h</button>
            </div>
          </div>
          <div class="cost-view" id="costView">
            <div class="cost-amount" id="costAmount">—</div>
            <div class="cost-breakdown" id="costBreakdown"></div>
            <p class="cost-note">At published API prices. You're on a subscription — this is for context.</p>
          </div>
          <div class="cost-view" id="hoursView" hidden>
            <div class="cost-amount" id="hoursAmount">—</div>
            <div class="hours-sub" id="hoursSub"></div>
            <div class="cost-breakdown" id="hoursBreakdown"></div>
            <div class="hours-scheduled" id="hoursScheduled"></div>
            <p class="hours-status" id="hoursStatus" aria-live="polite"></p>
            <p class="hours-note">Estimated by Claude <span id="hoursModel">Sonnet</span>: time a competent professional would need without AI. Claude Code sessions only. Rough, ±2×.</p>
          </div>
        </div>
```

- [ ] **Step 2: Styles**

In `static/style.css`, directly after the `.cost-note { ... }` rule, add:

```css
/* ─── Cost card: $ / person-hours views ─── */
.cost-view {
  display: flex;
  flex-direction: column;
  flex: 1;
}
.cost-view[hidden] { display: none; }
.unit-btn {
  font-family: var(--font-mono);
  padding: 3px 9px;
}
.unit-btn:focus-visible {
  outline: 2px solid var(--claude-orange);
  outline-offset: 2px;
}
.hours-sub {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin: -8px 0 12px;
}
.hours-scheduled {
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: var(--text-secondary);
  border-top: 1px solid var(--border-subtle);
  margin-top: 10px;
  padding-top: 8px;
}
.hours-scheduled:empty { display: none; }
.hours-status {
  font-size: 0.7rem;
  color: var(--text-secondary);
  margin-top: 8px;
}
.hours-status:empty { display: none; }
.hours-note {
  font-size: 0.68rem;
  color: var(--text-secondary);
  margin-top: 8px;
  line-height: 1.5;
}
```

- [ ] **Step 3: JS — restore the saved view on load and refresh it every minute**

In `static/dashboard.js`, in the `DOMContentLoaded` handler, change:

```js
  initAll();
  startQuotaPolling();
```

to:

```js
  setCostUnit(savedCostUnit());
  initAll();
  startQuotaPolling();
```

In `initAll`, change:

```js
  _windowRefreshInterval = setInterval(loadWindowCharts, 60000);
```

to:

```js
  _windowRefreshInterval = setInterval(() => {
    loadWindowCharts();
    if (costUnit === 'hours') loadHours();
  }, 60000);
```

- [ ] **Step 4: JS — the toggle and the hours view**

In `static/dashboard.js`, directly after the `loadCost` function, add:

```js
// ─── Person-hours view ───────────────────────────────────────
const COST_UNIT_KEY = 'costCardUnit';
let costUnit = 'cost';   // 'cost' | 'hours'

function savedCostUnit() {
  try { return localStorage.getItem(COST_UNIT_KEY) === 'hours' ? 'hours' : 'cost'; }
  catch { return 'cost'; }
}

function setCostUnit(unit) {
  costUnit = unit;
  try { localStorage.setItem(COST_UNIT_KEY, unit); } catch { /* storage blocked: in-page choice still applies */ }
  const hours = unit === 'hours';
  for (const [id, on] of [['unitCost', !hours], ['unitHours', hours]]) {
    const btn = document.getElementById(id);
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', String(on));
  }
  document.getElementById('costView').hidden = hours;
  document.getElementById('hoursView').hidden = !hours;
  document.getElementById('costTitle').textContent = hours ? 'Person-hours' : 'Est. API Cost';
  document.getElementById('costIcon').textContent = hours ? '◷' : '✦';
  if (hours) loadHours();
}

const HOURS_PAUSE_TEXT = {
  quota: 'Paused: 5-hour quota ≥ 80%',
  auth: 'Paused: sign in to Claude Code to resume',
  token: 'Paused until the login token refreshes',
  ingest: 'Paused while session files are parsed',
  disabled: 'Paused: estimates are turned off on this server',
};

function fmtHoursNum(h) {
  return h >= 10 ? Math.round(h).toLocaleString() : h.toFixed(1);
}

function fmtHours(h) {
  return h == null ? '—' : fmtHoursNum(h) + ' h';
}

function hoursStatusText(data) {
  const w = data.worker;
  if (w.state === 'unavailable') return 'Claude CLI not found, showing provisional estimates';
  if (w.state === 'paused') return HOURS_PAUSE_TEXT[w.reason] || 'Paused';
  if (w.pending > 0) return `Backfilling · ${w.pending} left`;
  const p = data.interactive.provisional_days;
  return p > 0 ? `${p} session-day${p === 1 ? '' : 's'} provisional` : '';
}

async function loadHours() {
  let data;
  try {
    data = await apiFetch('/api/hours?days=30');
  } catch (e) {
    document.getElementById('hoursStatus').textContent = "Couldn't load person-hours.";
    return;
  }
  document.getElementById('hoursAmount').textContent = '~' + fmtHours(data.interactive.hours);
  const sub = [`≈ ${data.work_weeks.toFixed(1)} work-weeks`];
  if (data.leverage != null) sub.push(`${data.leverage.toFixed(1)}× your ${fmtHours(data.active_hours)} active`);
  document.getElementById('hoursSub').textContent = sub.join(' · ');

  const list = document.getElementById('hoursBreakdown');
  list.innerHTML = '';
  for (const p of data.by_project) {
    const row = document.createElement('div');
    row.className = 'cost-row';
    const name = document.createElement('span');
    name.className = 'cost-model';
    name.textContent = p.project;
    name.title = p.project;
    const val = document.createElement('span');
    val.className = 'cost-model-val';
    val.textContent = fmtHours(p.hours);
    row.append(name, val);
    list.appendChild(row);
  }

  const s = data.scheduled;
  const sched = document.getElementById('hoursScheduled');
  sched.textContent = '';
  if (s.runs) {
    const label = document.createElement('span');
    label.textContent = `+ ${s.runs} scheduled run${s.runs === 1 ? '' : 's'}`;
    const val = document.createElement('span');
    val.textContent = fmtHours(s.hours);
    sched.append(label, val);
  }
  document.getElementById('hoursStatus').textContent = hoursStatusText(data);
  document.getElementById('hoursModel').textContent = data.model ? shortModelName(data.model) : 'Sonnet';
}
```

- [ ] **Step 5: Verify in the browser**

With the :8888 server from Task 13 still running (no restart needed for template/JS/CSS):

1. Navigate the built-in browser (`mcp__Claude_Browser__navigate`) to `http://127.0.0.1:8888/`.
2. Find and click the `h` button (`mcp__Claude_Browser__find` "Person-hours"). Expect: title "Person-hours", icon ◷, a `~N h` headline, "≈ X work-weeks · Y× your Z h active", up to 4 project rows, a "+ N scheduled runs" line if any, status "Paused: estimates are turned off on this server", and the footnote.
3. Reload the page. Expect the hours view to stay selected (localStorage).
4. Click `$`. Expect the original cost view, unchanged.
5. Keyboard: Tab to the toggle and press Space/Enter. Expect a visible orange focus ring and the view to switch; `aria-pressed` flips (check with `mcp__Claude_Browser__read_page`).
6. `mcp__Claude_Browser__read_console_messages` with `onlyErrors: true`. Expect none.
7. Resize to mobile (`mcp__Claude_Browser__resize_window` preset `mobile`), screenshot the card, confirm the header doesn't overflow, then reset with preset `desktop`.

- [ ] **Step 6: Commit**

```bash
git add templates/index.html static/style.css static/dashboard.js
git commit -m "Add \$/h toggle and person-hours view to the cost card"
```

---

### Task 15: Hours in Recent Sessions and the drill-down panel

**Files:**
- Modify: `static/dashboard.js` (`loadSessions` lines 1212-1258, `openPanel` line 1268)
- Modify: `templates/index.html` (session panel, lines 255-268)
- Modify: `static/style.css` (`.session-row` line 618-620; after `.session-tokens`; after `.session-panel-meta`)

- [ ] **Step 1: Session list cell**

In `loadSessions` in `static/dashboard.js`, directly before `row.innerHTML = \``, add:

```js
    const hoursCell = s.person_hours == null ? ''
      : s.hours_status === 'done'
        ? `<span title="Estimated person-hours">${fmtHours(s.person_hours)}</span>`
        : `<span class="provisional" title="Provisional: not yet estimated by Claude">~${fmtHours(s.person_hours)}</span>`;
```

and in the `row.innerHTML` template, change:

```js
      <div class="session-badges">${sourceBadge}${entrypointBadge}${speedBadge}</div>
      <div class="session-tokens">${fmtShort(s.total_tokens)}</div>
```

to:

```js
      <div class="session-badges">${sourceBadge}${entrypointBadge}${speedBadge}</div>
      <div class="session-hours">${hoursCell}</div>
      <div class="session-tokens">${fmtShort(s.total_tokens)}</div>
```

- [ ] **Step 2: Session list styles**

In `static/style.css`, in `.session-row`, change:

```css
  grid-template-columns: auto 1fr auto auto auto;
```

to:

```css
  grid-template-columns: auto 1fr auto auto auto auto;
```

and after the `.session-tokens { ... }` rule, add:

```css
.session-hours {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--text-primary);
  text-align: right;
  white-space: nowrap;
}
.session-hours .provisional {
  color: var(--text-secondary);
  font-style: italic;
}
```

- [ ] **Step 3: Panel markup**

In `templates/index.html`, inside `.session-panel-inner`, between the closing `</div>` of `.session-panel-header` and `<div class="chart-wrap">`, add:

```html
        <div class="panel-hours" id="panelHours" aria-live="polite"></div>
```

- [ ] **Step 4: Panel styles**

In `static/style.css`, after the `.session-panel-meta { ... }` rule, add:

```css
.panel-hours {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
}
.panel-hours:empty { display: none; }
.panel-hours-day {
  border-left: 2px solid rgba(224,122,95,0.35);
  padding-left: 10px;
}
.panel-hours-head {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-primary);
  margin-bottom: 4px;
}
.panel-hours-summary {
  font-size: 0.8rem;
  color: var(--text-primary);
  line-height: 1.5;
}
.panel-hours-rationale {
  font-size: 0.72rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin-top: 4px;
}
```

- [ ] **Step 5: Panel JS**

In `openPanel` in `static/dashboard.js`, directly after the statement that sets `panelMeta`'s `textContent` (ends with `shortModelName(session.model)}\`;`), add:

```js
  loadPanelHours(session.session_id);
```

Then directly after the `openPanel` function, add:

```js
let _panelHoursSeq = 0;

async function loadPanelHours(sessionId) {
  const seq = ++_panelHoursSeq;
  const el = document.getElementById('panelHours');
  el.textContent = '';
  let days;
  try {
    days = await apiFetch('/api/session/' + encodeURIComponent(sessionId) + '/hours');
  } catch (e) {
    return;
  }
  if (seq !== _panelHoursSeq) return;  // another session was opened meanwhile
  for (const d of days) {
    const label = new Date(d.date + 'T00:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' });
    const item = document.createElement('div');
    item.className = 'panel-hours-day';
    const head = document.createElement('div');
    head.className = 'panel-hours-head';
    item.append(head);
    if (d.status === 'done') {
      head.textContent = `${label} · ~${fmtHours(d.hours_likely)} (${fmtHoursNum(d.hours_low)}–${fmtHoursNum(d.hours_high)})`
        + (d.role ? ` · ${d.role}` : '');
      for (const [cls, txt] of [['panel-hours-summary', d.summary], ['panel-hours-rationale', d.rationale]]) {
        if (!txt) continue;
        const p = document.createElement('p');
        p.className = cls;
        p.textContent = txt;
        item.append(p);
      }
    } else {
      head.textContent = `${label} · ~${fmtHours(d.provisional_hours)} · not yet estimated`;
    }
    el.append(item);
  }
}
```

- [ ] **Step 6: Verify in the browser**

On `http://127.0.0.1:8888/` (reload):

1. Recent Sessions rows show an hours figure before the token count: `~N h` in italic secondary color for Claude Code sessions, blank for Desktop sessions.
2. Click a Claude Code session row. Expect one entry per day, e.g. "Sep 22 · ~3.4 h · not yet estimated", above the chart.
3. Click a Desktop session. Expect no hours block.
4. Open two sessions in quick succession; the panel shows only the second one's days.
5. Console has no errors.

- [ ] **Step 7: Commit**

```bash
git add templates/index.html static/style.css static/dashboard.js
git commit -m "Show person-hours in Recent Sessions and the session panel"
```

---

### Task 16: Document the feature in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Edit `CLAUDE.md`**

1. In "Setup & Running", replace `There is no test suite.` with:

````markdown
Run the tests:
```bash
conda activate claude-usage-dashboard && python -m pytest
```
````

2. In the Architecture code block, add these two lines after the `ingest.py` line:

```
person_hours.py  Person-hours judge — one-day transcript summaries, `claude -p` (Sonnet) calls, queue, worker gating, CLI
tests/       pytest suite (temp DB per test; the claude CLI is always faked)
```

3. In "Database Schema", after the `ingest_meta` line, add:

```sql
person_hour_estimates (session_id, date, status, is_scheduled,
                       hours_low, hours_likely, hours_high, summary, role, rationale,
                       judged_through, model, prompt_version, attempts, error, last_attempt_at,
                       judge_in_tokens, judge_out_tokens, judge_cost_usd)   -- PK (session_id, date)
```

4. In "API Endpoints", add `/api/hours`, `/api/session/{id}/hours` to the list, and after the `/api/refresh` paragraph add:

```markdown
`/api/hours?days=30` — person-hours for the cost card's `h` view: interactive hours (judged + provisional), scheduled runs, active hours, leverage, by-project, and `worker` state.
```

5. Add a new section before "## Gotchas":

```markdown
## Person-hours

The cost card's `$ | h` toggle shows estimated person-hours: how long a competent professional would need to do each Claude Code session-day's work without AI. Design: `docs/superpowers/specs/2026-09-23-person-hours-view-design.md`.

- A worker in `app.py` ticks every 5 minutes. It queues session-days idle ≥ 30 min, then judges up to 10 per tick (2 at a time, ≤ 20 calls/hour) with `claude -p --safe-mode --model sonnet --no-session-persistence --tools ""`. It pauses when the CLI is missing, auth is dead, an ingest runs, the 5-hour quota is ≥ 80%, or the OAuth token has < 10 min left (it asks `_refresh_oauth_token()` first).
- Unjudged session-days show a provisional figure: active hours × median judged leverage (5× until 10 days are judged).
- Scheduled runs (first prompt starts with `<scheduled-task`) are shown on their own line, not in the headline.
- Judge calls use subscription quota (roughly 0.1–0.2% of the weekly quota ongoing). They write no session logs, so `detect_other_pct()` counts them as local activity.
- Settings are constants at the top of `person_hours.py`; leverage constants are in `db.py`.
- `PERSON_HOURS_WORKER=off` disables the worker (use it for test servers).
- Manual backfill: `python person_hours.py --backfill 90 [--limit N] [--dry-run]`. It applies the hourly cap but not the quota pause. Don't run it while a server's worker is judging the same DB.
- To re-judge, delete rows from `person_hour_estimates`; changing `PROMPT_VERSION` alone doesn't re-judge.
```

- [ ] **Step 2: Ask before committing**

`CLAUDE.md` is tracked in this repo, but the user's global rule requires explicit approval before committing it. Show the user the diff (`git diff CLAUDE.md`) and ask. Only after a clear yes:

```bash
git add CLAUDE.md
git commit -m "Document the person-hours view"
```

If the user declines, leave the change unstaged and continue.

---

### Task 17: End-to-end verification with real judge calls

This spends a little real quota (about 5 judge calls, ~$0.25 at API rates).

- [ ] **Step 1: Stop the worker-off test server**

Find the PID listening on 8888 and stop it:

```bash
netstat -ano | grep ":8888" | grep LISTENING
```

Then `taskkill //PID <pid> //F` (double slashes in Git Bash).

- [ ] **Step 2: Note the current transcript directories**

```bash
ls ~/.claude/projects | wc -l
```

Record the number.

- [ ] **Step 3: Dry run**

```bash
$ENV python person_hours.py --backfill 90 --limit 3 --dry-run | head -120
```

Expected: "Queued N new session-days; M waiting…", then three `=== <id> <date> ===` blocks. Read them: requests are the user's words without system reminders, no memory/scratchpad files, no timing lines.

- [ ] **Step 4: Judge five session-days for real**

```bash
$ENV python person_hours.py --backfill 90 --limit 5
```

Expected: "Judging 5 session-days…" then progress lines ending `5/5 {'done': 5}` (an occasional `error` is acceptable; rerun shows it retried). Then:

```bash
$ENV python -c "import sqlite3; c = sqlite3.connect('data/usage.db'); [print(r) for r in c.execute(\"SELECT date, hours_low, hours_likely, hours_high, role, substr(summary,1,70), model, judge_cost_usd FROM person_hour_estimates WHERE status='done' ORDER BY last_attempt_at DESC LIMIT 5\")]"
```

Expected: five rows with plausible ranges, a role, a one-sentence summary, model `claude-sonnet-5`, cost around $0.02–0.05.

- [ ] **Step 5: Confirm the judge left no session logs**

```bash
ls ~/.claude/projects | wc -l
```

Expected: the same number as Step 2.

- [ ] **Step 6: Run the server with the worker on and watch one tick**

Start in the background (`run_in_background: true`):

```bash
eval "$(/c/Users/weaverjc/miniconda3/Scripts/conda.exe shell.bash hook)" && conda activate claude-usage-dashboard && python app.py --port 8888
```

Wait about 90 seconds (use Monitor with an until-loop on `curl -s http://127.0.0.1:8888/api/hours | grep -q '"state"'`, then a second check after the first tick at ~60 s). Then:

```bash
curl -s "http://127.0.0.1:8888/api/hours?days=30"
```

Expected: `worker.state` is `running` (with `pending` dropping by up to 10 per tick, capped at 20/hour) or `paused` with a real reason (e.g. `quota` if the 5-hour quota is ≥ 80%). `interactive.judged_hours` > 0 and `model` = `claude-sonnet-5`.

- [ ] **Step 7: Check the UI with real data**

In the browser at `http://127.0.0.1:8888/`: the `h` view shows "Backfilling · N left" (or a pause reason); a session judged in Step 4 shows a non-italic hours figure in Recent Sessions (if it's within 7 days), and its panel shows "~X h (low–high) · Role", the summary, and the rationale.

- [ ] **Step 8: Stop the test server and run the full suite once more**

Stop it by PID as in Step 1, then:

```bash
$ENV python -m pytest -q
```

Expected: all tests pass. `data/` stays untracked (git-ignored).

- [ ] **Step 9: Hand off**

Report results to the user (test count, the five judged rows, the pause/running state seen). Then use superpowers:finishing-a-development-branch. After merge, the :8080 server needs a restart (Task Scheduler: `Stop-ScheduledTask`/`Start-ScheduledTask -TaskName ClaudeUsageDashboard`, then confirm the listener on 8080). Its first ticks will start the 90-day backfill at 20 calls/hour.
