# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A FastAPI + vanilla JS dashboard for monitoring Claude Code token usage, costs, and quota limits. It parses JSONL session logs from `~/.claude/projects/`, stores metrics in SQLite, and serves a single-page app with real-time charts and quota gauges.

## Setup & Running

```bash
# Create environment (first time)
conda env create -f environment.yml

# Run the server
conda activate claude-usage-dashboard && python app.py
# Serves at http://127.0.0.1:8000/
```

Manual ingest only (without starting the server):
```bash
conda activate claude-usage-dashboard && python ingest.py
```

There is no test suite.

## Architecture

```
app.py       FastAPI server — 13 API endpoints, background ingest thread, serves templates/
db.py        SQLite query layer — pricing constants, token aggregations, cost calculations
ingest.py    ETL pipeline — scans ~/.claude/projects/**/*.jsonl, deduplicates, writes to SQLite
templates/   Jinja2 HTML (single index.html)
static/      dashboard.js (Chart.js, quota polling), style.css (glassmorphism dark theme)
data/        usage.db — auto-created on first run; not committed
```

**Data flow**: JSONL session files → `ingest.py` → `data/usage.db` → `db.py` queries → FastAPI endpoints → `dashboard.js` charts

**Ingest behavior**: On startup, a background thread runs ingest automatically if the DB is missing or empty. The `/api/refresh` endpoint triggers a full re-ingest. File metadata (`ingest_meta` table) is used to skip unchanged files.

**Quota source**: Read from `~/AppData/Local/Temp/claude-statusline-quota-weaverjc.json` (written by the Claude Code status line tool). Frontend polls `/api/quota` every 5 seconds.

## Key Paths (Runtime)

| Path | Purpose |
|------|---------|
| `~/.claude/projects/` | Claude session JSONL files (read-only) |
| `data/usage.db` | SQLite database (auto-created) |
| `~/AppData/Local/Temp/claude-statusline-quota-weaverjc.json` | Live quota data |

## Database Schema

```sql
messages (id, msg_id UNIQUE, timestamp, date, hour, day_of_week,
          session_id, project, model,
          input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens)

ingest_meta (file_path PRIMARY KEY, file_size, last_modified)
```

## Model Pricing (hardcoded in db.py and ingest.py)

- Opus: $15/$75 per 1M input/output tokens
- Sonnet: $3/$15 per 1M input/output tokens
- Haiku: $0.25/$1.25 per 1M input/output tokens
- Cache: 1.25× create, 0.10× read

When updating pricing, change it in **both** `db.py` and `ingest.py`.

## API Endpoints

`/api/quota`, `/api/ingest-status`, `/api/refresh` (POST), `/api/daily`, `/api/projects`, `/api/models`, `/api/heatmap`, `/api/sessions`, `/api/session/{id}`, `/api/rate`, `/api/cost`, `/api/stats`
