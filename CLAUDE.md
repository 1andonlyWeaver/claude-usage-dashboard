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

There is no test suite.

## Architecture

```
app.py       FastAPI server — 14 API endpoints, background ingest thread, serves templates/
db.py        SQLite query layer — pricing constants, token aggregations, cost calculations
ingest.py    ETL pipeline — scans ~/.claude/projects/**/*.jsonl, deduplicates, writes to SQLite
templates/   Jinja2 HTML (single index.html)
static/      dashboard.js (Chart.js, quota polling), style.css (glassmorphism dark theme)
data/        usage.db — auto-created on first run; not committed
scripts/     start-dashboard.bat (Task Scheduler launch), register-task.ps1 (one-time setup)
logs/        dashboard.log — server output when run via Task Scheduler; not committed
```

**Data flow**: JSONL session files → `ingest.py` → `data/usage.db` → `db.py` queries → FastAPI endpoints → `dashboard.js` charts

**Ingest behavior**: On startup, a background thread runs ingest automatically if the DB is missing or empty. The `/api/refresh` endpoint triggers a full re-ingest. File metadata (`ingest_meta` table) is used to skip unchanged files.

**Quota source**: Fetched from the Anthropic OAuth usage API (`https://api.anthropic.com/api/oauth/usage`) using the token in `~/.claude/.credentials.json`. Response is cached 360s in-memory and on disk at `data/quota_cache.json`. Frontend polls `/api/quota` every 5 seconds.

## Key Paths (Runtime)

| Path | Purpose |
|------|---------|
| `~/.claude/projects/` | Claude session JSONL files (read-only) |
| `data/usage.db` | SQLite database (auto-created) |
| `~/.claude/.credentials.json` | OAuth token for quota API (read-only) |
| `data/quota_cache.json` | Disk cache of last known quota data (auto-created) |

## Database Schema

```sql
messages (id, msg_id UNIQUE, timestamp, date, hour, day_of_week,
          session_id, project, model,
          input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens,
          cache_5m_tokens, cache_1h_tokens,
          entrypoint, speed, git_branch,
          web_search_count, web_fetch_count)

ingest_meta (file_path PRIMARY KEY, file_size, last_modified)
```

`timestamp` stores **local time** (no timezone). All window/range queries use local-time boundaries to match.

## Model Pricing (hardcoded in db.py and ingest.py)

- Opus: $15/$75 per 1M input/output tokens
- Sonnet: $3/$15 per 1M input/output tokens
- Haiku: $0.25/$1.25 per 1M input/output tokens
- Cache: 1.25× create, 0.10× read

When updating pricing, change it in **both** `db.py` and `ingest.py`.

## Gotchas

- **Force re-ingest required** after schema migrations or `extract_project_name` changes — unchanged files are skipped otherwise. Use `POST /api/refresh?force=true` or delete `ingest_meta` rows manually.
- **Project name resolution** uses filesystem greedy-match: `C--Users-weaverjc-Projects-march-madness` → resolves by checking real directories on disk, so project names only resolve correctly on the machine where the paths exist.
- **Schema migration** is handled automatically by `_migrate_db()` in `ingest.py` via `PRAGMA table_info` + `ALTER TABLE`. New columns default to 0/empty for pre-migration rows.
- **Auto-start**: Registered in Windows Task Scheduler as `ClaudeUsageDashboard`. To re-register on a new machine, run `powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1`.

## API Endpoints

`/api/quota`, `/api/ingest-status`, `/api/refresh` (POST), `/api/daily`, `/api/projects`, `/api/models`, `/api/heatmap`, `/api/sessions`, `/api/session/{id}`, `/api/rate`, `/api/cost`, `/api/stats`, `/api/window`

`/api/window?type=5h|7d&group_by=none|token_type|project|model` — token buckets within the current quota window (5-min or 60-min buckets).

`/api/refresh?force=true` — clears `ingest_meta` and re-processes all files. Use after schema migrations or project name changes.
