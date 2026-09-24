# Person-Hours View — Design Spec

Date: 2026-09-23
Status: implemented on branch `claude/person-hours-api-cost-view-278fe4` (2026-09-23). This document describes the code as built, including changes made during review.

## Goal

Add an alternative to the "Est. API Cost" card that shows how much human work the dashboard's Claude Code sessions represent, measured in person-hours.

**Definition.** Person-hours = the estimated time a competent professional with the appropriate skills would need to produce the same outcomes without AI assistance. This is the definition used in Anthropic's "Estimating AI productivity gains from Claude conversations" (Nov 2025).

## Decisions

| Question | Decision |
|---|---|
| What the number means | Human-equivalent effort (not agent time, not the user's own time) |
| How it's estimated | Claude judges each session-day; unjudged session-days get a provisional estimate |
| Judge model | Sonnet (`--model sonnet`, currently `claude-sonnet-5`) |
| Scheduled runs | Shown on a separate line, excluded from the headline |
| Layout | `$ \| h` toggle on the existing cost card |
| Trigger | Automatic background judging, one-time 90-day backfill |
| Scope | Claude Code sessions only (not Desktop Cowork) |
| Dollar value of labor | No; hours and work-weeks only |

## Evidence behind the decisions

### What the logs support (last 30 days, measured 2026-09-23)

- 141 sessions, 801 human prompts, 101–143 active hours depending on idle cutoff (2 vs 15 min).
- 21,459 tool calls, 17,223 of them Bash. 58% of the Bash calls came from subagents and five sessions made 6,200 of them. Raw action counts mostly measure polling loops.
- ~42K lines written, but 15.7K were HTML and 10K markdown; 8.2K were `.py`. Lines-of-code models (COCOMO etc.) are not usable.

### Pilot: 10 sessions, three methods

B = active hours × 5. Haiku = `claude-haiku-4-5`. Sonnet = `claude-sonnet-5`. Judged values are "likely (low–high)".

| Session | Active h | B | Haiku | Sonnet |
|---|---|---|---|---|
| agentic-trading build (multi-day) | 15.9 | 80 | 125 (90–155) | 320 (220–420) |
| Mesonet camera pipeline | 11.5 | 57 | 45 (35–60) | 55 (30–90) |
| Integration bot phase 1 | 6.5 | 33 | 75 (60–95) | 72 (45–110) |
| dad-precip-research | 6.0 | 30 | 26 (18–35) | 28 (18–40) |
| MRCC outage | 3.0 | 15 | 12 (8–16) | 8 (5–13) |
| PR 539 review + fix | 2.6 | 13 | 12 (9–16) | 14 (8–22) |
| Production backup CronJob | 1.9 | 10 | 9 (6–13) | 14 (8–22) |
| griddedmaps investigation | 1.3 | 7 | 7 (5–9) | 9 (6–14) |
| Climate Calendar fix | 0.9 | 5 | 4.5 (3.5–6) | 6 (4–9) |
| Daily security review (scheduled) | 0.7 | 3.5 | 20 (14–28) | 7 (5–10) |
| **Total** | **50.5** | **253** | **336** | **533** |

Findings:

- For ordinary sessions all three methods agree within ~1.5×. Median judged leverage was 4.8× (Haiku) and 6.7× (Sonnet).
- The methods diverge on large builds and on the scheduled report, and those sessions drive the totals.
- Whole-session judging fails on long sessions: Haiku judged only the last piece of the 103-hour trading session while Sonnet judged the whole system. This is why the unit of judgment is the session-day.
- Giving the judge active time anchored its rationales, so the judge input omits it.
- Memory files and scratchpad drafts appeared as "work"; they are filtered.

### Accuracy

No ground truth exists for these sessions. The published validation (Sonnet 4.5 on 1,000 JIRA tasks with real completion times) found Spearman ρ=0.44 against actual time, vs ρ=0.50 for human estimators, with compressed estimates (short tasks overestimated, long tasks underestimated). Single-session estimates can be off by 2–3×; the monthly total should be read as rough, about ±2×. METR's 2025 randomized trial (experienced developers 19% slower with AI while believing they were 20% faster) is the reason the card says "estimated" and not "saved".

## Architecture

```
JSONL ──ingest──▶ messages (unchanged)
messages ──▶ which session-days are ready?
             (idle ≥ 30 min, and not yet judged or has new messages since)
         ──▶ person_hours.py reads that day's events from JSONL
         ──▶ claude -p (Sonnet) ──▶ person_hour_estimates ──▶ /api/hours ──▶ card
```

### Components

| File | Change |
|---|---|
| `person_hours.py` (new) | Session-day summarizer, judge call, output validation, pending discovery, `skip_reason()`, CLI entry point. No FastAPI imports. |
| `ingest.py` | `CREATE TABLE IF NOT EXISTS person_hour_estimates` in `init_db()`. No parsing changes. |
| `db.py` | `person_hours(days)`, per-session hours for `session_list()`, `session_hours(session_id)`, active-hours query. `detect_other_pct()` treats judge calls as local activity. |
| `app.py` | Background worker (5-minute `threading.Timer`, same pattern as `_periodic_ingest`), `/api/hours`, `/api/session/{id}/hours`, `person_hours` fields on `/api/sessions`. |
| `templates/index.html`, `static/dashboard.js`, `static/style.css` | Toggle, hours view, session-list column, drill-down block. |
| `environment.yml`, `tests/` (new) | `pytest` and unit tests. |
| `CLAUDE.md` | Document the module, table, endpoints, and settings. |

### Unit of judgment

One estimate per `(session_id, date)`, where `date` is the local date, the same convention as `messages.date`. Events are assigned to a date by converting their UTC timestamps to local time, as `ingest.parse_timestamp()` does. For day 2+ of a session, the judge input includes the one-line summary from that session's most recent earlier row that has one.

## Data model

```sql
CREATE TABLE IF NOT EXISTS person_hour_estimates (
    session_id       TEXT NOT NULL,
    date             TEXT NOT NULL,     -- local date, same convention as messages.date
    status           TEXT NOT NULL,     -- pending | done | error
    is_scheduled     INTEGER DEFAULT 0, -- session's first human prompt starts with <scheduled-task
    hours_low        REAL,
    hours_likely     REAL,
    hours_high       REAL,
    summary          TEXT,
    role             TEXT,
    rationale        TEXT,
    judged_through   TEXT,              -- max messages.timestamp covered by the last successful judgment
    model            TEXT,              -- actual model id from the CLI's modelUsage
    prompt_version   INTEGER,
    attempts         INTEGER DEFAULT 0, -- failed attempts since the last success
    error            TEXT,
    last_attempt_at  TEXT,              -- local time; drives the hourly cap and "Other" attribution
    judge_in_tokens  INTEGER,
    judge_out_tokens INTEGER,
    judge_cost_usd   REAL,
    PRIMARY KEY (session_id, date)
);
```

A row that is re-queued because new messages arrived keeps its previous `hours_*` values until the new judgment replaces them.

## The judge

### Discovery and readiness

Each worker tick:

1. For Claude Code session-days in the last `BACKFILL_DAYS` with no row, whose latest `messages.timestamp` is at least `IDLE_MINUTES` old and whose session has a transcript in the index, insert a `pending` row. Set `is_scheduled` by reading the session's first human prompt from its main JSONL. Sessions without a transcript aren't queued and are picked up if one appears. All transcript reads happen before any write, so no write lock is held during file I/O.
2. For `done` rows whose session-day now has a `messages.timestamp` later than `judged_through` and is idle again, set `status = 'pending'`.
3. Rows that failed with `no_source` get a fresh start (`pending`, 0 attempts) when their transcript is back in the index.
4. `pending` or `error` rows whose session-day no longer has any messages are deleted. Re-ingesting a resumed session can move a day's messages to another session id.
5. Judge `pending` rows and `error` rows with `attempts < 3`, newest date first, skipping any row attempted in the last `RETRY_AFTER_MINUTES` (60). Rows are claimed first: one `BEGIN IMMEDIATE` transaction re-checks the hourly cap and stamps `last_attempt_at` on the rows it takes. A backfill CLI and the server's worker therefore never judge the same row, and calls in flight count toward the cap.

### Locating the logs

The main file is `<root>/*/<session_id>.jsonl` for each root in `ingest.get_project_dirs()`. Subagent transcripts are every `agent-*.jsonl` under `<root>/<project>/<session_id>/subagents/`, including workflow agents one level deeper in `subagents/workflows/wf_*/` (on this machine, 933 of 1,085 subagent transcripts). A workflow's `journal.jsonl` and the `*.meta.json` files are not transcripts. The summarizer reads the files directly, so it does not depend on the ingest covering subagent logs.

### Summary given to the judge

Built from that day's events only. Capped at `SUMMARY_MAX_CHARS` (24,000); pilot summaries were 6–12K characters.

- Project display name (from `messages.project`), and "day N of this session" when N > 1, with the previous day's summary line.
- User requests, in order: all if 20 or fewer, otherwise the first 14 and last 5 with an omission marker. Each is clipped to 600 characters.
  - Requests include prompts typed while Claude was working, which Claude Code stores as `queued_command` attachments with `commandMode: "prompt"` (237 prompts on 87 of 293 recent session-days).
  - Slash commands appear as `/name args`. Built-in session commands (`/model`, `/exit`, `/compact` and similar) are dropped.
  - Stripped: `<system-reminder>`, `<command-*>`, `<local-command-*>`, `<task-notification>`, `<bash-stdout>` and `<bash-stderr>` blocks, and a leading desktop marker such as `<!-- attach -->`.
  - Not requests: tool results, `isMeta` entries, compaction summaries (`isCompactSummary`, which recap earlier work) and `[Request interrupted by user…]` markers.
- Files created or edited with +/− line counts from `structuredPatch` (edits, including Write overwrites) and `content` (Write `create`), top 40 by size, one entry per file however its path was spelled. Excluded: paths under `~/.claude/projects/` (memory), `~/.claude/plans/` (plan-mode files), the system temp directory and any `AppData/Local/Temp/` (scratchpads), and `/tmp/`. Worktree paths under `.claude/worktrees/` are kept.
- Tool-call counts for the main thread and for subagents.
- Shell command descriptions (the Bash tool's `description` input), first 40 distinct.
- The last 3 assistant text messages, each clipped to 1,200 characters.
- **Not included:** active time or wall-clock span.

### Prompt

System prompt (`PROMPT_VERSION = 1`):

```
You estimate how much human professional effort a piece of completed work represents.

You will receive a structured record of Claude Code work: the user's requests, the files created or edited (with line counts), tool usage, the shell commands that were run, and the assistant's final messages. This may be one day of a longer session; estimate only the work in this record. A line starting "Earlier in this session:" is context from a previous day, not work to count.

Estimate how many hours a competent professional with the appropriate skills (for example a software engineer, systems administrator, or research analyst who knows this kind of work but has no AI tools) would need to accomplish the same outcomes without AI assistance.

Include: understanding the request, investigation and research, writing and editing code or documents, testing and debugging, and verifying results.
Exclude: time spent waiting, dead ends caused only by the AI's own mistakes, and work that was clearly thrown away. Judge generated files by what they accomplish, not by their line count: boilerplate, generated reports and scratch files take a person far less time per line than core logic.

Respond with only a JSON object, no prose and no code fences:
{"summary": "<one sentence: what was accomplished>", "role": "<professional role>", "hours_low": <number>, "hours_likely": <number>, "hours_high": <number>, "rationale": "<two or three sentences>"}
```

### Call

```
claude -p --safe-mode --model sonnet --no-session-persistence --tools ""
          --system-prompt <prompt> --output-format json
```

- The summary goes on stdin. Timeout 300 s. Working directory `data/`.
- `--safe-mode` keeps the user's CLAUDE.md, plugins, hooks and MCP servers out of the judge's context (the pilot's smoke test showed 163 input tokens for a one-line prompt). `--no-session-persistence` keeps judge calls out of `~/.claude/projects/`, so the dashboard never ingests its own judge sessions. `--bare` is not usable because it requires an API key.
- CLI path: `shutil.which("claude")`, falling back to `~/.local/bin/claude.exe`. Task Scheduler's `PATH` may not include the user's.
- From the JSON envelope (the last line that parses, if the CLI printed anything before it), store `result` (parsed), `usage` tokens, `total_cost_usd`, and the model with the most output tokens in `modelUsage`. An `is_error` envelope is recorded as `cli: <result or subtype> (HTTP <api_error_status>)`, and `stop_reason: "refusal"` as `refusal`.
- npm `claude.cmd`/`.bat` shims are skipped, because `cmd.exe` would cut the multi-line system prompt at its first newline. The working directory is created if missing.
- The judge inherits the server's environment. If `ANTHROPIC_API_KEY` is set there, judge calls bill to the API instead of the subscription.

### Validation

Take the first `{...}` span in `result` that parses as a JSON object, ignoring code fences and prose around it (at most 20 candidate spans are tried). Raw newlines inside strings are tolerated. Then require:

- `hours_low`, `hours_likely`, `hours_high` are JSON numbers (not booleans or strings) with `0 < low ≤ likely ≤ high ≤ 500`
- `summary` is a non-empty string; `role` and `rationale` are strings

Summary, role and rationale are clipped to 400, 80 and 1,200 characters. Validation never raises: anything else, including absurd numbers or deeply nested JSON, counts as a failed attempt, as do timeouts, non-zero exits and refusals.

### Worker

A 5-minute daemon `threading.Timer` in `app.py` (first tick 60 s after startup), guarded by a lock so ticks never overlap. It reschedules in an outer `finally`, like `_periodic_ingest`: under the launcher, stdout is a strict cp1252 file, so even logging a failure can raise. `PERSON_HOURS_WORKER=off` disables it.

A tick skips everything while a full ingest runs, because a first-run ingest may not have created the tables yet. The 90-second periodic ingest runs alongside safely under WAL and the worker's 30 s busy timeout. Otherwise the tick always runs discovery, so provisional figures know which days are scheduled runs even while paused. It then evaluates `skip_reason(...)`, a pure function in `person_hours.py`. The first match wins:

| Reason | Condition |
|---|---|
| `unavailable` | CLI not found |
| `auth` | `_auth_dead` is set and the credentials file hasn't changed since (a re-login clears it), or there are no credentials |
| `ingest` | a full ingest is running |
| `quota` | last known 5-hour quota ≥ `QUOTA_PAUSE_PCT` (80). Unknown quota doesn't pause. The reading is refreshed only by browser polls, so it can be stale when no tab is open. |
| `token` | the OAuth access token expires within `TOKEN_MIN_SECONDS` (2,100 s): 10 minutes plus a whole tick's worth of calls. Before gating, the server asks its own `_refresh_oauth_token()` to refresh, so the CLI never has to. That avoids two processes refreshing the shared token at once. |
| `rate` | `MAX_CALLS_PER_HOUR` (20) attempts in the last hour, counted from `last_attempt_at`. It shows as running, not paused. |
| `failing` | see the breaker below |

Otherwise the tick judges up to `min(MAX_PER_TICK (10), remaining hourly budget)` session-days with `MAX_CONCURRENCY` (2) worker threads. Each call writes its row immediately.

**Breaker.** Within a batch, after `STOP_AFTER_FAILURES` (3) failed calls in a row, the rest of the batch is left "unclaimed". Those rows wait out the hourly backoff without losing an attempt. Across ticks, once 3 calls in a row have failed, the worker judges at most one session-day an hour, with reason `failing` in between, until a call succeeds. A broken CLI or login therefore can't burn through the backlog's retries.

### Failures

- A failed call sets `status = 'error'`, increments `attempts`, and records `error` and `last_attempt_at`. It's retried an hour later while `attempts < 3`; after that it stays `error` and the session-day shows its provisional estimate. A success resets `attempts` to 0.
- Nothing to judge is recorded as a final `error` with `attempts = 3`, and no call is made:
  - `no_source`: the transcript is gone. Revived if it reappears.
  - `no_messages`: the day's messages moved to another session.
  - `no_events`: the transcript has nothing on that date.

  Their claim stamp is cleared, so they don't count toward the hourly cap or "Other" usage.
- An unexpected exception while judging a row is recorded as a failed attempt (`crash: …`) before it's logged.
- Failures never raise out of the worker.
- Connections go through `_db()`, which sets a 30 s busy timeout and rolls back before closing (the repo's `ingest._open_db` convention).

### "Other" usage attribution

Judge calls consume quota but leave no session log, so `detect_other_pct()` would attribute them to "Other". It will treat a snapshot interval as having local activity if it contains messages **or** a judge attempt (`last_attempt_at` in the interval).

### Manual CLI

```
python person_hours.py --backfill 90 [--limit N] [--dry-run] [--retry-failed]
```

- The CLI applies the hourly cap and the claim, so it's safe to run while the server's worker is on. It skips the other gates (quota, auth, token, ingest).
- It prints an estimated duration first and a closing summary at the end: judged this run, still waiting, in the retry wait, failed for good, and nothing to judge.
- It stops (exit 1) after 3 failed calls in a row and prints the last error.
- It rebuilds the transcript index on every pass.
- `--dry-run` prints the summaries that would be sent without calling Claude. It still queues session-days.
- `--retry-failed` re-queues rows that failed for good, except those that had nothing to judge.
- Ctrl+C returns 130, but only after the in-flight judge calls finish.

## Aggregation

`db.person_hours(days=30)` covers Claude Code (`source = 'claude-code'`) session-days with `date >= since`.

- **Hours for a session-day:** `hours_likely` if not null; otherwise provisional = active hours × leverage multiplier.
- **Active hours** from `messages` timestamps: the sum of gaps between consecutive messages in the session-day, each gap capped at 5 minutes (SQLite `LAG` window function).
- **Leverage multiplier:** the median of `hours_likely / active_hours` over judged session-days in the last 90 days with active hours ≥ 0.1, computed separately for interactive and scheduled rows. With fewer than 10 samples in a group, it uses `DEFAULT_LEVERAGE` (5.0). `db._leverage` caches the result until the set of `done` estimates changes. Recomputing costs a 90-day window query (~130 ms), which would otherwise slow every session-list load.
- **Scheduled vs interactive:** from `is_scheduled`. Session-days with no row yet (still active) count as interactive and provisional.
- **Card figures:** interactive hours (headline), scheduled hours and run count (separate line), interactive active hours, leverage = interactive hours ÷ interactive active hours, work-weeks = interactive hours ÷ 40, and interactive hours by project.

## API

### `GET /api/hours?days=30`

```json
{
  "days": 30,
  "interactive": {"hours": 612.0, "judged_hours": 580.0, "provisional_hours": 32.0,
                  "session_days": 99, "provisional_days": 4},
  "scheduled": {"hours": 360.0, "runs": 53},
  "active_hours": 95.3,
  "leverage": 6.4,
  "work_weeks": 15.3,
  "by_project": [{"project": "geddes", "hours": 240.0}],
  "model": "claude-sonnet-5",
  "worker": {"state": "running", "reason": null, "pending": 12, "errors": 1}
}
```

`worker.state` is one of `running`, `idle`, `paused` or `unavailable`. `reason` is set only when paused: `auth`, `ingest`, `quota`, `token`, `failing`, or `disabled` (worker switched off). `pending` counts rows waiting to be judged, including those in the retry wait. `errors` counts rows that failed for good.

`/api/hours`, `/api/sessions` and `/api/session/{id}/hours` are plain `def` endpoints, so FastAPI runs their SQLite work in its threadpool rather than on the event loop.

### `GET /api/sessions`

Each session gains `person_hours` (sum over its session-days, judged or provisional) and `hours_status`: `done`, `provisional`, or `partial`. Desktop sessions get `null`.

### `GET /api/session/{id}/hours` (new)

A list of per-day rows: `date`, `status`, `hours_low`, `hours_likely`, `hours_high`, `summary`, `role`, `rationale`, or a provisional value for days not yet judged. `/api/session/{id}` is unchanged.

## UI

### Cost card toggle

- Two buttons, `$` and `h`, in the card header with `aria-pressed`. The title and icon switch between "Est. API Cost" and "Person-hours". The period pill stays "30 days".
- The last choice is stored in `localStorage`, with reads and writes wrapped in try/catch; the default is `$`.
- While the hours view is visible it refreshes with the existing 60-second chart refresh.

### Hours view

```
Person-hours                              [$|h]
30 days
~612 h
≈ 15 work-weeks · 6.4× your 95 active h
geddes                                    240 h
www                                       150 h
agentic-trading                           120 h
other                                     102 h
────────────────────────────────────────────────
+ 53 scheduled runs                       360 h
4 session-days provisional
Estimated by Claude Sonnet: time a competent professional
would need without AI. Claude Code sessions only. Rough, ±2×.
```

The project list shows the top 3 plus "other" (grouped server-side in `db.hours_summary`). The status line shows the first that applies, and is rewritten only when its text changes, since it's a live region:

1. "Claude CLI not found, showing provisional estimates"
2. "Paused: …" with the reason: 5-hour quota ≥ 80%, sign in to Claude Code, login token refreshing, session files being parsed, recent estimate calls failed (retrying hourly), or estimates turned off on this server
3. "Estimating · N left"
4. "N session-days provisional"

### Recent Sessions

An hours figure beside the token count, e.g. `6 h`. It's italic and in the secondary color with a `~` prefix when provisional or partial, and empty for Desktop sessions. The column is hidden below 500 px, where it squeezed the session name to a few characters.

### Drill-down panel

Below the meta line, one entry per session-day:

```
Sep 22 · ~6 h (4–9) · DevOps engineer
Diagnosed a duplicate 'Selected Month' bug …
Rationale text in muted small type.
```

Provisional days read "Sep 23 · ~3 h · not yet estimated".

### Accessibility

New UI meets WCAG 2.1 AA:
- The toggle is keyboard-operable with visible focus and `aria-pressed`.
- Its accessible names include the visible "$" and "h" (label in name).
- The unpressed button uses `--text-secondary`.
- The status line is in an `aria-live="polite"` region. The existing `--text-muted` color (35% opacity cream on the dark card) is below 4.5:1, so new informational text (status line, footnote, rationale, provisional hours) uses `--text-secondary` or another color that reaches 4.5:1. Restyling existing muted text is out of scope.

## Settings

Constants at the top of `person_hours.py`:

| Constant | Value |
|---|---|
| `JUDGE_MODEL` | `"sonnet"` |
| `IDLE_MINUTES` | 30 |
| `QUOTA_PAUSE_PCT` | 80 |
| `MAX_CALLS_PER_HOUR` | 20 |
| `MAX_CONCURRENCY` | 2 |
| `MAX_PER_TICK` | 10 |
| `TICK_SECONDS` | 300 |
| `BACKFILL_DAYS` | 90 |
| `PROMPT_VERSION` | 1 |
| `SUMMARY_MAX_CHARS` | 24000 |
| `MAX_ATTEMPTS` | 3 |
| `RETRY_AFTER_MINUTES` | 60 |
| `STOP_AFTER_FAILURES` | 3 |
| `MAX_HOURS` | 500 |
| `CALL_TIMEOUT_S` | 300 |
| `TOKEN_MIN_SECONDS` | 600 + (10 // 2) × 300 = 2100 |

The aggregation constants live in `db.py`: `DEFAULT_LEVERAGE` (5.0), `MIN_SAMPLES_FOR_LEVERAGE` (10), `LEVERAGE_LOOKBACK_DAYS` (90), `ACTIVE_GAP_CAP_S` (300) and `HOURS_PER_WORK_WEEK` (40).

Changing `PROMPT_VERSION` does not re-judge existing rows. To re-judge, delete rows (all, or by date range).

## Expected quota use

Derived from 8 days of quota snapshots against logged usage repriced at current API rates: about $2.50 of API-equivalent usage per 1% of the 5-hour quota, and $9–12 per 1% of the 7-day quota. A judge call costs $0.024–0.05 (the pilot's Sonnet calls averaged 4,629 input and 1,467 output tokens; the CLI reported $0.05).

| | Calls | 5-hour quota | 7-day quota |
|---|---|---|---|
| Backfill (once, ~390 session-days) | 20/hour for ~20 h | ≤ ~2% of any window | 1–2% |
| Ongoing (~5 session-days/day) | ~35/week | < 0.1% per window | 0.1–0.2% per week |

These lean high: the snapshots include usage the logs can't see (claude.ai, phone), and the conversion assumes quota is charged in proportion to API price across models.

## Testing

`pytest` added to `environment.yml`. Tests in `tests/` (113 at completion). Two autouse fixtures in `tests/conftest.py` keep tests isolated. One gives every test its own temp DB. The other fails any test that reaches the real `subprocess.run`, so no test can spend quota. The CLI is always faked. Covered areas include:

- **Summarizer** against small fixture JSONL files: local-date filtering across midnight, request extraction and tag stripping, memory/temp file filtering, worktree paths kept, subagent files included, scheduled detection, truncation.
- **Validation:** valid JSON, fenced JSON, prose around JSON, missing fields, `low > likely`, values over 500, refusal text.
- **Aggregation** on an in-memory SQLite DB: active-hours gap cap, median leverage and its fallback, the scheduled/interactive split, re-queued rows keeping old values, project breakdown.
- **`skip_reason()`:** each reason, and their precedence.

### Manual verification

Following the worktree workflow (:8080 serves the main checkout):

1. Copy the live DB into the worktree's `data/` with the sqlite3 backup API, from a `?mode=ro` connection.
2. `python person_hours.py --backfill 90 --limit 3 --dry-run` and read the summaries.
3. `python person_hours.py --backfill 90 --limit 5` for real calls. Check the stored rows, and that no new directories appear under `~/.claude/projects/`.
4. Exercise the worker path in-process: call `person_hours.run_tick(...)` from a script with `MAX_PER_TICK` lowered. Don't run a second server that shares the real credentials: two servers refreshing the same OAuth refresh token can race and kill the login.
5. Check the UI on a second instance on :8888 started with `USERPROFILE=<empty scratch home>` and `PERSON_HOURS_WORKER=off`.
6. After merge, restart the `ClaudeUsageDashboard` scheduled task and confirm the listener on :8080.

Done on 2026-09-23 with 7 real calls: $0.012–0.037 each at API rates, 1.6–3.2K input tokens, no transcripts written, and plausible estimates.

## Out of scope

- Desktop Cowork sessions. They were under 1% of output tokens in the last 30 days, are mostly recurring jobs, and their audit logs lack edit diffs.
- A dollar value for the labor.
- A 7/30/90 selector on the card (the 90-day backfill makes adding one later cheap).
- Letting the user enter their own estimate for a session to calibrate the judge.
- Resumed or forked sessions. Their transcript files repeat earlier entries (same `uuid`, original timestamps), so the same work can be judged under two sessions. In recent data this affects about 21 of 293 session-days. Fixing it needs a cross-file map of which session owns each entry; left for a follow-up.

## Known limitations

- **Deleted transcripts.** Session-days whose transcripts Claude Code has already deleted can't be judged and stay provisional. In the first backfill that was about 100 of ~400 session-days in the 90-day window.
- **Resumed or forked sessions** (see Out of scope) can have shared work judged under both sessions.
- **Backfill order.** It runs newest-first, so a multi-day session's later days are often judged before its earlier ones and miss the "Earlier in this session" context. Live operation judges days in order.
- **Timeouts.** A timed-out judge call kills the CLI process but not any child processes it started (Windows).
- **Stale quota reading.** The quota gate uses the last reading a browser poll fetched.
- **Unclaimed rows.** In a failing batch, they keep their claim stamp for an hour, so during an outage `calls_last_hour` and "Other" attribution count a few calls that never happened. This is conservative.
- **Scheduled runs** count as interactive until the worker queues them. That covers today's runs and anything outside the backfill window.
- **Early token refresh.** The server refreshes the shared OAuth token about 35 minutes before expiry on every tick, whether or not anything is waiting to be judged.

## Related issues found during design

Both were fixed on `develop` in separate PRs before this branch was rebased onto it:

- **Subagent logs weren't ingested** (PR #1). Active hours from `messages` now include subagent activity. The summarizer reads subagent files directly either way.
- **Stale model pricing in `db.py`** (PR #2). Person-hours doesn't use these prices.
- **Worktree project names.** Sessions in git worktrees get names like `Projects / www /  / claude-worktrees-…`, which splits the by-project list. This is a pre-existing ingest behavior, tracked as a separate task.
