# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A FastAPI + vanilla JS dashboard for monitoring Claude Code token usage, costs, and quota limits. It parses JSONL session logs from `~/.claude/projects/`, stores metrics in SQLite, and serves a single-page app with real-time charts and quota gauges.

## Setup & Running

```bash
# Create environment (first time)
conda env create -f environment.yml

# Run the server (avoid port 8000 — often in use)
conda activate claude-usage-dashboard && python app.py --port 8080
# Serves at http://127.0.0.1:8080/
```

Manual ingest only (without starting the server):
```bash
conda activate claude-usage-dashboard && python ingest.py
```

Run the tests. Each test gets its own temp DB, and an autouse guard fails any test that would spawn the real `claude` CLI:
```bash
conda activate claude-usage-dashboard && python -m pytest
```

## Architecture

```
app.py       FastAPI server — 16 API endpoints, background ingest and person-hours threads, serves templates/
db.py        SQLite query layer — pricing constants, token aggregations, cost calculations, person-hours figures
ingest.py    ETL pipeline — scans ~/.claude/projects/**/*.jsonl, deduplicates, writes to SQLite
person_hours.py  Person-hours judge — one-day transcript summaries, `claude -p` (Sonnet) calls, queue, worker gating, CLI
tests/       pytest suite
templates/   Jinja2 HTML (single index.html)
static/      dashboard.js (Chart.js, quota polling), style.css (glassmorphism dark theme)
data/        usage.db — auto-created on first run; not committed
scripts/     launcher.py (Task Scheduler entry point — port check, spawns server, stays alive so TS tracks it)
             register-task.ps1 (one-time setup), start-dashboard.bat (legacy manual launcher)
logs/        dashboard.log — server output when run via Task Scheduler; not committed
```

**Data flow**: JSONL session files (top-level + subagent transcripts) → `ingest.py` → `data/usage.db` → `db.py` queries → FastAPI endpoints → `dashboard.js` charts

**Files ingested** per `~/.claude/projects/<project>/` dir (Windows root plus the WSL root from `get_project_dirs()`):
- `*.jsonl` — top-level session transcripts
- `*/subagents/**/*.jsonl` — subagent transcripts newer CLI versions write beside the session: `<session-id>/subagents/agent-*.jsonl` (Task/Agent tool) and `<session-id>/subagents/workflows/wf_*/agent-*.jsonl` (workflow agents). The same glob also picks up `workflows/wf_*/journal.jsonl`, which has no assistant/usage lines and inserts nothing.

**Ingest behavior**: On startup, a background thread runs ingest automatically if the DB is missing or empty. The `/api/refresh` endpoint triggers a full re-ingest. File metadata (`ingest_meta` table) is used to skip unchanged files.

**Quota source**: Fetched from the Anthropic OAuth usage API (`https://api.anthropic.com/api/oauth/usage`) using the token in `~/.claude/.credentials.json`. Response is cached 360s in-memory and on disk at `data/quota_cache.json`. Frontend polls `/api/quota` every 5 seconds.

## Key Paths (Runtime)

| Path | Purpose |
|------|---------|
| `~/.claude/projects/` | Claude session JSONL files, including `<session-id>/subagents/` transcripts (read-only) |
| `data/usage.db` | SQLite database (auto-created) |
| `~/.claude/.credentials.json` | OAuth token for quota API (read-only) |
| `data/quota_cache.json` | Disk cache of last known quota data (auto-created) |
| `claude` CLI (`PATH` or `~/.local/bin/claude.exe`) | Run headless by the person-hours judge |

## Database Schema

```sql
messages (id, msg_id UNIQUE, timestamp, date, hour, day_of_week,
          session_id, project, model,
          input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens,
          cache_5m_tokens, cache_1h_tokens,
          entrypoint, speed, git_branch,
          web_search_count, web_fetch_count,
          source)

ingest_meta (file_path PRIMARY KEY, file_size, last_modified)

person_hour_estimates (session_id, date,            -- PRIMARY KEY (session_id, date)
                       status, is_scheduled,        -- status: pending | done | error
                       hours_low, hours_likely, hours_high, summary, role, rationale,
                       judged_through, model, prompt_version, attempts, error, last_attempt_at,
                       judge_in_tokens, judge_out_tokens, judge_cost_usd)
```

`timestamp` stores **local time** (no timezone). All window/range queries use local-time boundaries to match.

## Model Pricing (hardcoded in db.py and ingest.py)

- Opus: $15/$75 per 1M input/output tokens
- Sonnet: $3/$15 per 1M input/output tokens
- Haiku: $0.25/$1.25 per 1M input/output tokens
- Cache: 1.25× create, 0.10× read

When updating pricing, change it in **both** `db.py` and `ingest.py`.

## Server Restart

Changes to `app.py`, `db.py`, or `ingest.py` require a server restart to take effect (FastAPI loads these once at startup). After making any such change, automatically flag that a restart is needed and offer to restart the server.

**When running under Task Scheduler (normal):**
```powershell
Stop-ScheduledTask  -TaskName ClaudeUsageDashboard   # stops launcher; its job object kills the server too
Start-ScheduledTask -TaskName ClaudeUsageDashboard   # launcher re-spawns the server
# Verify it actually bound the port — task State can read "Ready" transiently while the
# launcher is still starting uvicorn, so check the listener rather than the task State:
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
```

`launcher.py` binds the server to a Windows Job Object (`KILL_ON_JOB_CLOSE`), so stopping the task reliably tears the server down instead of orphaning it on port 8080. If you ever still find a stale listener after a stop (e.g. one started by an older launcher build), free the port manually before starting again:
```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

**When running manually:**
The server runs on port 8080; the process can be identified via `netstat -ano | grep :8080` and killed by PID before relaunching with `conda activate claude-usage-dashboard && python app.py --port 8080`.

## Person-hours

The cost card's `$ | h` toggle shows estimated person-hours: how long a competent professional would need, without AI, to do each Claude Code session-day's work. Design: `docs/superpowers/specs/2026-09-23-person-hours-view-design.md`.

- **Worker** (`app._hours_tick`, every 5 min, first run 60 s after start): queues session-days that have been idle ≥ 30 min and still have a transcript, then judges up to 10 per tick, 2 at a time and ≤ 20 calls per rolling hour, with `claude -p --safe-mode --model sonnet --no-session-persistence --tools ""`. Results go to `person_hour_estimates`.
- **It pauses** when:
  - the CLI is missing
  - auth is dead (it notices a re-login by itself)
  - a full ingest is running
  - the 5-hour quota is ≥ 80%. It uses the last known reading, which can be stale when no browser tab is open.
  - the OAuth token has less than ~35 min left. It first asks `_refresh_oauth_token()` to refresh early, so the CLI never has to.
- **Breaker**: after 3 failed calls in a row, the rest of that batch is left unclaimed; those rows wait an hour without losing an attempt. The worker then judges at most one session-day an hour (reason `failing`, shown as paused) until a call succeeds.
- **Provisional figures**: unjudged session-days show active hours × the median judged leverage. The default of 5× applies until a group (interactive or scheduled) has 10 judged days with ≥ 0.1 active hours in the last 90 days. `db._leverage` caches the median until judged estimates change.
- **Scheduled runs** (sessions whose first prompt starts with `<scheduled-task`) get their own line, not the headline. Until the worker queues a run, it counts as interactive.
- **Quota**: judge calls use subscription quota, roughly 0.1–0.2% of the weekly quota once the backfill is done. They write no transcript, so `detect_other_pct()` counts an interval with a judge call as local activity.
- **Settings** are constants at the top of `person_hours.py`; the leverage constants are in `db.py`. `PERSON_HOURS_WORKER=off` disables the worker, e.g. for a test server.
- **Manual backfill**: `python person_hours.py --backfill 90 [--limit N] [--dry-run] [--retry-failed]`.
  - Keeps the hourly cap but skips the other gates (quota, auth, token, ingest).
  - Stops (exit 1) after 3 failed calls in a row.
  - Safe to run while the server's worker is on, because rows are claimed before they're judged.
  - `--dry-run` still queues rows.
  - Ctrl+C waits for the in-flight judge calls to finish.
  - `--retry-failed` re-queues rows whose calls failed for good. It leaves alone rows that had nothing to judge (no transcript, no messages, nothing on that date).
- **Re-judging**: delete rows from `person_hour_estimates`. Changing `PROMPT_VERSION` alone doesn't re-judge anything.
- **Known limits**:
  - Session-days whose transcripts Claude Code has already deleted can't be judged; they stay provisional.
  - Resumed or forked sessions repeat earlier entries, so work they share can be judged under both sessions.
  - The backfill runs newest-first, so a multi-day session's later days often miss the "Earlier in this session" context.
  - A timed-out judge call doesn't kill child processes the CLI started.
  - If `ANTHROPIC_API_KEY` is set in the server's environment, judge calls bill to the API rather than the subscription.
  - The spec's "Known limitations" section has the full list.

## Gotchas

- **Tests never touch `data/usage.db`.** An autouse fixture in `tests/conftest.py` points `db.DB_PATH` and `ingest.DB_PATH` at a per-test temp file.
- **Force re-ingest required** after schema migrations or `extract_project_name` changes — unchanged files are skipped otherwise. Use `POST /api/refresh?force=true` or delete `ingest_meta` rows manually.
- **Subagent messages keep the parent's `session_id`.** Subagent transcript lines carry the parent session's `sessionId` (with `isSidechain: true`), and ingest stores them as-is. Session lists and drill-down therefore include subagent tokens, and every `COUNT(DISTINCT session_id)` metric counts a session once no matter how many agents it spawned. Nothing records which rows are sidechain; add a column if you ever need to split them out. Dedup is by `msg_id` (the API message id), and subagent ids don't overlap with the parent's (verified 2026-09-23: 0 shared ids across ~18.6k subagent messages, and no top-level file contains inlined `isSidechain` messages). Before this was added, subagent usage was missing entirely, which undercounted 30-day output tokens by about half and made `detect_other_pct` attribute subagent quota use to "other".
- **Forked/resumed sessions share msg_ids.** A fork copies earlier messages into a new top-level file under a new `sessionId`, and `INSERT OR REPLACE` gives each shared `msg_id` to whichever file was ingested last. A force re-ingest can therefore move rows between the two sessions. Token totals are unaffected.
- **Project name resolution** uses filesystem greedy-match: `C--Users-weaverjc-Projects-march-madness` → resolves by checking real directories on disk, so project names only resolve correctly on the machine where the paths exist.
- **Schema migration** is handled automatically by `_migrate_db()` in `ingest.py` via `PRAGMA table_info` + `ALTER TABLE`. New columns default to 0/empty for pre-migration rows.
- **Auto-start**: Registered in Windows Task Scheduler as `ClaudeUsageDashboard`. `launcher.py` is the entry point — it checks whether port 8080 is already in use, then spawns the server and waits (keeping the task in "Running" state so TS can enforce RestartCount and IgnoreNew). The server is assigned to a `KILL_ON_JOB_CLOSE` Windows Job Object so it dies with the launcher; otherwise `Stop-ScheduledTask` would kill only the launcher and orphan the server on the port. To re-register on a new machine, run `powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1`.

## API Endpoints

`/api/quota`, `/api/ingest-status`, `/api/refresh` (POST), `/api/daily`, `/api/projects`, `/api/models`, `/api/heatmap`, `/api/sessions`, `/api/session/{id}`, `/api/session/{id}/hours`, `/api/rate`, `/api/cost`, `/api/hours`, `/api/sources`, `/api/stats`, `/api/window`

`/api/hours?days=30` returns person-hours for the cost card's `h` view: interactive hours (judged + provisional), scheduled runs, active hours, leverage, hours by project, the judge model, and the `worker` state. `/api/session/{id}/hours` returns per-day person-hours for the drill-down panel. `/api/sessions` rows carry `person_hours` and `hours_status` (`done` / `provisional` / `partial`; null for Desktop sessions).

`/api/window?type=5h|7d&group_by=none|token_type|project|model` — token buckets within the current quota window (5-min or 60-min buckets).

`/api/refresh?force=true` — clears `ingest_meta` and re-processes all files. Use after schema migrations or project name changes.
