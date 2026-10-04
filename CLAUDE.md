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

Run the desktop app (window + tray, one copy per Windows session):
```bash
conda activate claude-usage-dashboard && python desktop.py               # window + tray on port 8765, or a free port
conda activate claude-usage-dashboard && python desktop.py --background  # tray only: what Start at login runs
conda activate claude-usage-dashboard && python desktop.py --smoke       # free port, checks /, /api/connection and a vendored file (frozen: the window's libraries too), exits 0/1
```
Don't run it from the main checkout while the scheduled :8080 server runs: both would use `data/usage.db`, and with automatic renewal on they'd race on the refresh token. Try it from a worktree with `USERPROFILE` and `CUD_DATA_DIR` pointed at a scratch home. A test instance also asks GitHub for the latest release a minute after it starts (`PERSON_HOURS_WORKER=off` doesn't stop that); set `"update_check": false` in its `settings.json` to keep it offline.

Build the exe and the installer. This needs `pip install -r packaging/requirements-build.txt` and Inno Setup 6; on this PC it's installed per user in `%LOCALAPPDATA%\Programs\Inno Setup 6`:
```bash
conda activate claude-usage-dashboard && python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec   # -> dist/ClaudeUsageDashboard/
MSYS_NO_PATHCONV=1 "$LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe" /DAppVersion=2.0.0 packaging/installer.iss              # -> dist/ClaudeUsageDashboard-Setup-2.0.0.exe
```
`MSYS_NO_PATHCONV=1` stops Git Bash from turning `/DAppVersion=...` into a path. To smoke-test the frozen exe without touching the real profile, set `USERPROFILE` and `LOCALAPPDATA` to scratch folders and run `ClaudeUsageDashboard.exe --smoke`.

The installed app and a source `desktop.py` share the `Local\ClaudeUsageDashboard` mutex but not `runtime.json`, since their data dirs differ. A source copy started while the installed one runs therefore waits 10 s, then exits 1. Quit one first.

`environment.yml` installs `requirements.txt` (the pinned runtime packages) plus the test tools. When you upgrade a runtime package, change its pin in `requirements.txt`. An env created before the desktop packages (pywebview, pystray, Pillow) were pinned needs `pip install -r requirements.txt` inside the activated env.

Manual ingest only (without starting the server):
```bash
conda activate claude-usage-dashboard && python ingest.py
```

Run the tests. Each test gets its own temp DB, settings file and credentials file, and an autouse guard fails any test that would spawn a real process (the `claude` CLI would spend quota or open a sign-in window):
```bash
conda activate claude-usage-dashboard && python -m pytest
```
`httpx2` is a pip test dependency in `environment.yml`. Starlette's `TestClient` needs it; with plain `httpx` it still works but warns. An env created before `httpx2` was added needs `pip install httpx2` (inside the activated env). The suite finishes with no warnings; treat a new one as something to fix, not filter.

## Architecture

```
app.py       FastAPI server — 24 API endpoints, local-only request guard, background ingest and person-hours threads, serves templates/
db.py        SQLite query layer — pricing constants, token aggregations, cost calculations, person-hours figures
ingest.py    ETL pipeline — scans ~/.claude/projects/**/*.jsonl, deduplicates, writes to SQLite
person_hours.py  Person-hours judge — one-day transcript summaries, `claude -p` (Sonnet) calls, queue, worker gating, CLI
auth.py      Claude Code OAuth token — reads ~/.claude/.credentials.json, renews it on request, connection status, sign-in launcher
paths.py     Where bundled files, data and logs live (repo when run from source, %LOCALAPPDATA% when frozen; CUD_DATA_DIR overrides data)
settings.py  User settings in data/settings.json: judge opt-in, automatic token renewal, the desktop app's preferred_port (8765), the daily update check and the dismissed update notice
autostart.py  Start at login for the desktop app: the HKCU Run value, and Task Manager's StartupApproved switch for it
instance.py  One desktop app per Windows session: the Local\ClaudeUsageDashboard mutex, data/runtime.json (port, pid), proxy-free calls to the app's own server
tray.py      The desktop app's tray icon (pystray): menu (with Check for updates), amber attention dot, one notification when the sign-in needs the person; polls /api/connection every 30 s
desktop.py   Desktop entry point: pywebview window (close hides to tray, Ctrl+0/plus/minus zoom), uvicorn thread on preferred_port or a free one, tray, second-launch handoff, --background, --smoke
updates.py   Update check: GitHub's latest release at most once a day (an hour after a failure), kept in data/update.json and compared with version.py
applog.py    The log file: opens logs/dashboard.log as UTF-8, rotates it at 5 MB, makes print() safe on any stdout
version.py   __version__, a bare release number (2.0.0) shown in Settings and the diagnostics
requirements.txt  Pinned runtime packages for the release build; environment.yml installs it too
README.md    For the people who run the dashboard: what it reads and sends, sign-in, the judge's cost, where data lives. LICENSE is MIT
tests/       pytest suite
templates/   Jinja2 HTML (single index.html)
static/      dashboard.js (Chart.js, quota polling), style.css (glassmorphism dark theme)
             vendor/chart.umd.js (Chart.js 4.4.4) and fonts/ (Sora, DM Sans, DM Mono, fonts.css): nothing loads from a CDN, so the page works offline
data/        usage.db — auto-created on first run; not committed
scripts/     launcher.py (Task Scheduler entry point — port check, spawns server, stays alive so TS tracks it)
             register-task.ps1 (one-time setup), start-dashboard.bat (legacy manual launcher)
packaging/   ClaudeUsageDashboard.spec (PyInstaller: onedir, windowed), build_assets.py (the exe's icon from tray.icon_image, its version resource from version.py),
             installer.iss (Inno Setup, per user), requirements-build.txt (PyInstaller pins). Not a Python package: a PyPI library is called `packaging`
.github/workflows/release.yml  On a bare-number tag: tag check, tests, build, --smoke, installer, silent install/upgrade/--smoke/uninstall, draft release. PRs touching the build run all but the tag check and release
logs/        dashboard.log — server output under Task Scheduler or pythonw, UTF-8; moved to dashboard.log.1 at startup once past 5 MB; not committed
```

**Data flow**: JSONL session files (top-level + subagent transcripts) → `ingest.py` → `data/usage.db` → `db.py` queries → FastAPI endpoints → `dashboard.js` charts

**Files ingested** per `~/.claude/projects/<project>/` dir (the Windows root plus, from `get_project_dirs()`, the root of every `/home` user in every running WSL distro):
- `*.jsonl` — top-level session transcripts
- `*/subagents/**/*.jsonl` — subagent transcripts newer CLI versions write beside the session: `<session-id>/subagents/agent-*.jsonl` (Task/Agent tool) and `<session-id>/subagents/workflows/wf_*/agent-*.jsonl` (workflow agents). The same glob also picks up `workflows/wf_*/journal.jsonl`, which has no assistant/usage lines and inserts nothing.

**Ingest behavior**: On startup, a background thread runs ingest automatically if the DB is missing or empty. The `/api/refresh` endpoint triggers a full re-ingest. File metadata (`ingest_meta` table) is used to skip unchanged files.

**Quota source**: Fetched from the Anthropic OAuth usage API (`https://api.anthropic.com/api/oauth/usage`) using the token in `~/.claude/.credentials.json`. Response is cached 360s in-memory and on disk at `data/quota_cache.json`. Frontend polls `/api/quota` every 5 seconds. The dashboard only *reads* the token by default. An expired token is reported as `token-expired` and never sent (the usage API answers expired tokens with 429, which used to look like rate limiting). The token is written back only when the user clicks **Renew now** or turns on automatic renewal in Settings (`auth.refresh_token`).

- **Connection state**: `auth.connection_status()` turns the credentials file, the CLI lookup and the last fetch into one of six states: `not-installed`, `signed-out`, `login-required`, `token-expired`, `unavailable`, `connected`. `/api/quota` responses carry it as `connection`, and the dashboard draws the banner from it. Screen-reader announcements go through `#authLive`; the settings panel's copy result goes through `#settingsLive`.
- **Backoff**: each quota poll stats the credentials file, and a change lifts any sign-in backoff at once, so a re-login shows up within about 5 s.
  - Every `_fetch_usage_sync` result carries `creds_sig`, the file signature its token was read under. An auth failure or `http-401` whose file has changed since then gets no backoff. `/api/quota` and `/api/window` poll at the same time, so another poller may already have recorded the new signature, and the lift above would never fire.
  - Renew now and `/api/refresh` go through `_refetch_quota_now()`. It clears the backoff and `fail_count` and moves `fetched_at` back just far enough to count as stale, not to 0. A failed refetch then judges the cached figures by their real age; stamped 0 they would look as old as the PC's uptime, and `_bound_stale_quota` would blank both gauges.
- **Refused credentials**: a 401 from the usage API marks the credentials refused (`auth.mark_rejected`), and the state becomes `login-required`.
  - In read-only mode the first 401 marks it. With automatic renewal on, `_fetch_usage_sync` renews once and retries, and only a 401 that survives the retry marks it. A refresh token the OAuth endpoint rejects (`invalid_grant` or 401) marks it too.
  - A renewal that can't happen (no refresh token in the file) or can't be saved also marks it, as long as the file hasn't changed. A renewal that fails in transit (network error, OAuth server error, a response with no token: `auth.last_refresh_transient()`) leaves the error at `http-401`, shown as `unavailable`, and retries after the usual backoff.
  - The refusal is keyed to the credentials file's `(mtime, size)` signature, taken before the token was read. A file change clears it, whether from a re-login or from Claude Code renewing its own token.
- **Fetch errors**: `_fetch_usage_sync` returns `rate-limited`, `http-<status>`, `network-error` or an auth status. `network-error` is a fixed code for any non-HTTP exception, because some exception messages quote the request headers. The detail goes to the log through `auth.redact()`, which masks `sk-ant-…` text. Never put a raw exception message into a response or the diagnostics.

## Key Paths (Runtime)

| Path | Purpose |
|------|---------|
| `~/.claude/projects/` | Claude session JSONL files, including `<session-id>/subagents/` transcripts (read-only) |
| `data/usage.db` | SQLite database (auto-created) |
| `~/.claude/.credentials.json` | OAuth token for the quota API. Read-only unless automatic renewal is on or Renew now is clicked |
| `data/quota_cache.json` | Disk cache of last known quota data (auto-created) |
| `data/settings.json` | User settings (auto-created on first change) |
| `data/runtime.json` | The running desktop app's port and pid, for a second launch; removed at Quit |
| `data/webview/` | The desktop window's WebView2 profile, so `localStorage` survives restarts |
| `data/update.json` | The last update check: when, the latest release number, and any error |
| `%LOCALAPPDATA%\Programs\Claude Usage Dashboard` | The installed app (Inno Setup, per user). Its data and log are in `%LOCALAPPDATA%\ClaudeUsageDashboard\data` and `\logs` |
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ClaudeUsageDashboard` | Start at login (desktop app, tray menu). Task Manager's switch is under `...\Explorer\StartupApproved\Run` |
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

Changes to `app.py`, `applog.py`, `auth.py`, `db.py`, `ingest.py`, `paths.py`, `person_hours.py`, `settings.py`, `updates.py` or `version.py` require a server restart to take effect (FastAPI loads these once at startup). Template, JS and CSS edits don't. After making any such change, automatically flag that a restart is needed and offer to restart the server.

The desktop app (`python desktop.py`) loads the same modules plus `desktop.py`, `tray.py`, `instance.py` and `autostart.py`. After changing any of them, Quit it from the tray and start it again.

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

## Releasing

Release tags are bare numbers equal to `version.__version__` (annotated, no `v`).
1. Bump `version.py` on develop. Settings, `/api/app/info`, the exe's version resource and the installer all read it.
2. Fast-forward main to develop and push it, then tag main: `git tag -a 2.1.0 -m 2.1.0 && git push origin 2.1.0`.
3. `.github/workflows/release.yml` checks the tag against `version.py`, runs the tests, builds, smoke-tests the exe, and builds the installer. It installs that silently with Start at login (checking the `Run` value), runs Setup again as an upgrade (checking Start at login stays as it was), smoke-tests the installed exe and uninstalls it. Then it creates a **draft** release with `ClaudeUsageDashboard-Setup-<version>.exe`.
4. Download the installer from the draft, try it, then publish the draft. `releases/latest` skips drafts, so installed copies see the release only once it's published.

A tag that doesn't match `version.py` fails before anything is built: delete the tag, fix it, tag again.

## Person-hours

The cost card's `$ | h` toggle shows estimated person-hours: how long a competent professional would need, without AI, to do each Claude Code session-day's work. Design: `docs/superpowers/specs/2026-09-23-person-hours-view-design.md`.

- **Worker** (`app._hours_tick`, every 5 min, first run 60 s after start): queues session-days that have been idle ≥ 30 min and still have a transcript, then judges up to 10 per tick, 2 at a time and ≤ 20 calls per rolling hour, with `claude -p --safe-mode --model sonnet --no-session-persistence --tools ""`. Results go to `person_hour_estimates`.
  - It judges only after the user turns on **Estimate person-hours** in Settings (off by default). While it's off, ticks still queue session-days, so scheduled runs are recognized, but make no calls.
  - Turning it on runs a pass at once instead of waiting for the next tick. Turning it off doesn't stop a batch already in flight (at most 10 calls); the next tick is the first to skip.
- **It pauses** when:
  - the judge is off in Settings (reason `disabled`, the default)
  - the CLI is missing
  - auth is dead (it notices a re-login by itself)
  - a full ingest is running
  - the 5-hour quota is ≥ 80%. It uses the last known reading, which can be stale when no browser tab is open.
  - the OAuth token has less than ~35 min left. With automatic renewal on, it first asks `auth.refresh_token()` to renew early, so the CLI never has to; otherwise it waits for Claude Code to renew the token.
- **Breaker**: after 3 failed calls in a row, the rest of that batch is left unclaimed; those rows wait an hour without losing an attempt. The worker then judges at most one session-day an hour (reason `failing`, shown as paused) until a call succeeds.
- **Provisional figures**: unjudged session-days show active hours × the median judged leverage. The default of 5× applies until a group (interactive or scheduled) has 10 judged days with ≥ 0.1 active hours in the last 90 days. `db._leverage` caches the median until judged estimates change.
- **Scheduled runs** (sessions whose first prompt starts with `<scheduled-task`) get their own line, not the headline. Until the worker queues a run, it counts as interactive.
- **Quota**: judge calls use subscription quota, roughly 0.1–0.2% of the weekly quota once the backfill is done. They write no transcript, so `detect_other_pct()` counts an interval with a judge call as local activity. The CLI runs without `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and `CLAUDE_CODE_USE_FOUNDRY` (`person_hours.BILLING_ENV`), so an API key or cloud setup in the server's environment doesn't take the bill. A key Claude Code gets from its own settings (`apiKeyHelper`, or an `env` block in `~/.claude/settings.json`) still would; the dashboard can't see those.
- **Settings** are constants at the top of `person_hours.py`; the leverage constants are in `db.py`. `PERSON_HOURS_WORKER=off` stops the worker entirely (no queueing either), e.g. for a test server. The user-facing toggles (`judge_enabled`, `auto_refresh_token`) live in `data/settings.json`.
- **Manual backfill**: `python person_hours.py --backfill 90 [--limit N] [--dry-run] [--retry-failed]`.
  - Keeps the hourly cap but skips the other gates (quota, auth, token, ingest) and ignores the Settings toggle, since a person ran it.
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
  - The spec's "Known limitations" section has the full list.

## Gotchas

- **Tests never touch `data/usage.db`.** An autouse fixture in `tests/conftest.py` points `db.DB_PATH` and `ingest.DB_PATH` at a per-test temp file. `tests/conftest.py` also redirects `settings.SETTINGS_PATH` and `auth.CREDENTIALS_FILE` to per-test temp files and forbids `subprocess.run` / `subprocess.Popen`. Every test also gets an in-memory `winreg` in `autostart` (`tests/fake_winreg.py`), so none can touch the real registry. `instance.RUNTIME_FILE` points at a per-test file too. `updates._urlopen` fails any test that would reach GitHub, and `updates.STATE_FILE` points at a per-test file.
- **Local-only guard.** Requests whose Host isn't `127.0.0.1`, `localhost` or `[::1]` get 403, and so do state-changing requests whose `Origin` isn't the dashboard's own. curl sends no `Origin`, so `curl -X POST http://127.0.0.1:8080/api/refresh` still works.
- **Desktop app threads.** pywebview owns the main thread. uvicorn runs in a thread, so it installs no signal handlers. pystray runs its icon in another thread, and its setup callback is the 30-second connection poll. The close button hides the window only when WinForms reports `CloseReason.UserClosing` and Quit wasn't chosen, so Windows sign-out is never held up. Calls to the app's own server go through `instance.call()`, which ignores proxies.
- **Credentials writes go through `auth.refresh_token()` only**, and it runs only when automatic renewal is on or the user clicks Renew now. Everything else reads the file.
- **Settings file.** `settings.load()` reads `data/settings.json` on every call, so a change applies on the next quota poll or judge tick without a restart. One `RLock` guards reads and writes, and `update()` tries the file replace up to 10 times over about a second on a Windows `PermissionError` (antivirus or an indexer holding the file). A missing or unreadable file, or a stored value of the wrong type, gives the default.
- **Force re-ingest required** after schema migrations or `extract_project_name` changes — unchanged files are skipped otherwise. Use `POST /api/refresh?force=true` or delete `ingest_meta` rows manually.
- **Subagent messages keep the parent's `session_id`.** Subagent transcript lines carry the parent session's `sessionId` (with `isSidechain: true`), and ingest stores them as-is. Session lists and drill-down therefore include subagent tokens, and every `COUNT(DISTINCT session_id)` metric counts a session once no matter how many agents it spawned. Nothing records which rows are sidechain; add a column if you ever need to split them out. Dedup is by `msg_id` (the API message id), and subagent ids don't overlap with the parent's (verified 2026-09-23: 0 shared ids across ~18.6k subagent messages, and no top-level file contains inlined `isSidechain` messages). Before this was added, subagent usage was missing entirely, which undercounted 30-day output tokens by about half and made `detect_other_pct` attribute subagent quota use to "other".
- **Forked/resumed sessions share msg_ids.** A fork copies earlier messages into a new top-level file under a new `sessionId`, and `INSERT OR REPLACE` gives each shared `msg_id` to whichever file was ingested last. A force re-ingest can therefore move rows between the two sessions. Token totals are unaffected.
- **Project name resolution** uses filesystem greedy-match: `C--Users-weaverjc-Projects-march-madness` → resolves by checking real directories on disk, so project names only resolve correctly on the machine where the paths exist.
- **WSL distros are scanned only while running.** `ingest.running_wsl_distros()` asks `wsl.exe --list --running --quiet`, at most every 10 seconds. Reading a stopped distro through `\\wsl.localhost` starts it; until 2026-09-30 the scan did that to Ubuntu on every 90-second pass. A stopped distro's transcripts are picked up on the first pass after it starts. A WSL session-day the judge reaches while its distro is stopped is recorded as having no transcript; `person_hours.discover()` re-queues it once the distro runs again.
- **Schema migration** is handled automatically by `_migrate_db()` in `ingest.py` via `PRAGMA table_info` + `ALTER TABLE`. New columns default to 0/empty for pre-migration rows.
- **Auto-start**: Registered in Windows Task Scheduler as `ClaudeUsageDashboard`. `launcher.py` is the entry point — it checks whether port 8080 is already in use, then spawns the server and waits (keeping the task in "Running" state so TS can enforce RestartCount and IgnoreNew). The server is assigned to a `KILL_ON_JOB_CLOSE` Windows Job Object so it dies with the launcher; otherwise `Stop-ScheduledTask` would kill only the launcher and orphan the server on the port. To re-register on a new machine, run `powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1`.

## API Endpoints

`/api/quota`, `/api/connection`, `/api/connection/login` (POST), `/api/connection/renew` (POST), `/api/settings` (GET/POST), `/api/ingest-status`, `/api/refresh` (POST), `/api/daily`, `/api/projects`, `/api/models`, `/api/heatmap`, `/api/sessions`, `/api/session/{id}`, `/api/session/{id}/hours`, `/api/rate`, `/api/cost`, `/api/hours`, `/api/sources`, `/api/stats`, `/api/window`, `/api/app/info`, `/api/app/show` (POST), `/api/update`, `/api/update/check` (POST)

`/api/hours?days=30` returns person-hours for the cost card's `h` view: interactive hours (judged + provisional), scheduled runs, active hours, leverage, hours by project, the judge model, and the `worker` state. `/api/session/{id}/hours` returns per-day person-hours for the drill-down panel. `/api/sessions` rows carry `person_hours` and `hours_status` (`done` / `provisional` / `partial`; null for Desktop sessions).

`/api/connection` returns the connection state with `title`, `detail`, `actions` (`install` / `sign-in` / `renew`), `login_running` and a token-free `diagnostics` text. `/api/connection/login` opens `claude auth login --claudeai` in a console window, one at a time (409 without the CLI, 500 with the reason if Windows won't start it). `/api/connection/renew` makes one forced token renewal. `/api/settings` reads or changes `judge_enabled`, `auto_refresh_token`, `preferred_port`, `update_check` and `dismissed_version` (400 on unknown keys or values of the wrong type). `preferred_port` is an int with no UI; the desktop app reads it at start.

`/api/app/info` names the program on the port (`name`, `version`, `desktop`, `pid`, `data_dir`, `log_dir`). `/api/app/show` brings the desktop window forward; a second launch of the desktop app calls it. It answers 409 when `app.py` runs on its own, with no window attached.

`/api/update` returns the last update check: `current`, `latest`, `available`, `notify` (available, checks on, and not the dismissed release), `url` (the release page, built from the tag), `checked_at`, `error` (`http-<code>`, `network-error`, `bad-answer`) and `enabled`. `/api/update/check` asks GitHub at once, even with `update_check` off. `app._update_tick` runs hourly and asks only when `updates.due()`.

`/api/window?type=5h|7d&group_by=none|token_type|project|model` — token buckets within the current quota window (5-min or 60-min buckets).

`/api/refresh?force=true` — clears `ingest_meta` and re-processes all files. Use after schema migrations or project name changes.
