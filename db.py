"""
Query helpers for the usage SQLite database.
"""
import re
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "usage.db"

# First-party API pricing, per https://platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-09-23). Tuples are (input_price, output_price, cache_read_mult), prices
# per 1M tokens. Cache reads are 0.1x input unless noted.
FABLE_5_1_PRICING = (10.00, 50.00, 0.025)  # cache reads $0.25
FABLE_5_PRICING   = (10.00, 50.00, 0.10)
OPUS_5_5_PRICING  = (4.00,  20.00, 0.05)   # cache reads $0.20
OPUS_4_5_PRICING  = (5.00,  25.00, 0.10)   # Opus 4.5 through Opus 5
OPUS_4_PRICING    = (15.00, 75.00, 0.10)   # Opus 4 / 4.1
SONNET_5_PRICING  = (2.00,  10.00, 0.10)
SONNET_4_PRICING  = (3.00,  15.00, 0.10)   # Sonnet 4 / 4.5 / 4.6
HAIKU_4_5_PRICING = (1.00,  5.00,  0.10)

# Cache writes cost the same multiple of input on every model.
CACHE_5M_WRITE_MULT = 1.25
CACHE_1H_WRITE_MULT = 2.0

# Per family, (min_version, pricing) tiers, newest first. A model gets the first tier
# its version reaches, so a newer unreleased version inherits the newest known rate.
FAMILY_PRICING = {
    "fable":  [((5, 1), FABLE_5_1_PRICING), ((5, 0), FABLE_5_PRICING)],
    "opus":   [((5, 5), OPUS_5_5_PRICING), ((4, 5), OPUS_4_5_PRICING), ((4, 0), OPUS_4_PRICING)],
    "sonnet": [((5, 0), SONNET_5_PRICING), ((4, 0), SONNET_4_PRICING)],
    "haiku":  [((4, 5), HAIKU_4_5_PRICING)],
}

# Exact model IDs whose price doesn't follow their family's version tiers. Checked first.
MODEL_PRICING = {}
DEFAULT_PRICING = SONNET_5_PRICING

# "claude-opus-4-5-20251101" -> opus, 4, 5. The 1-2 digit limit keeps a snapshot date
# from being read as a version ("claude-opus-4-20250514" is Opus 4.0).
_MODEL_ID_RE = re.compile(
    r"(?P<family>fable|opus|sonnet|haiku)"
    r"(?:-(?P<major>\d{1,2})(?!\d)(?:-(?P<minor>\d{1,2})(?!\d))?)?"
)


def price_for_model(model: str):
    """Return (input, output, cache_read_mult) for a model.

    Exact-ID overrides in MODEL_PRICING win; otherwise the family and version parsed
    from the ID pick a tier from FAMILY_PRICING (an ID with no parseable version gets
    the family's newest tier); otherwise DEFAULT_PRICING.
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    m = _MODEL_ID_RE.search((model or "").lower())
    if not m:
        return DEFAULT_PRICING
    tiers = FAMILY_PRICING[m['family']]
    version = (int(m['major']), int(m['minor'] or 0)) if m['major'] else None
    for min_version, pricing in tiers:
        if version is None or version >= min_version:
            return pricing
    return tiers[-1][1]


# Token sums needed to price a group of messages. Rows ingested before Claude Code
# logged the 5m/1h split carry only cache_creation_tokens; that remainder is charged
# at the 5-minute rate, the default cache TTL.
_COST_COLUMNS = """
    SUM(input_tokens) as input_tokens,
    SUM(cache_5m_tokens + MAX(cache_creation_tokens - cache_5m_tokens - cache_1h_tokens, 0)) as cache_5m_tokens,
    SUM(cache_1h_tokens) as cache_1h_tokens,
    SUM(cache_read_tokens) as cache_read_tokens,
    SUM(output_tokens) as output_tokens
"""


def _group_cost(model: str, row) -> float:
    """Dollar cost at API rates of a row selected with _COST_COLUMNS."""
    input_price, output_price, cache_read_mult = price_for_model(model)
    return (
        (row['input_tokens'] or 0) * input_price +
        (row['cache_5m_tokens'] or 0) * input_price * CACHE_5M_WRITE_MULT +
        (row['cache_1h_tokens'] or 0) * input_price * CACHE_1H_WRITE_MULT +
        (row['cache_read_tokens'] or 0) * input_price * cache_read_mult +
        (row['output_tokens'] or 0) * output_price
    ) / 1_000_000


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _query(sql: str, params=()) -> list[sqlite3.Row]:
    """Run one query and return all its rows, closing the connection even on error.

    An unclosed connection is only freed by the cyclic GC (it references itself through
    its statement cache), so close explicitly rather than rely on going out of scope.
    """
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchall()


def _since_date(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')


def daily_tokens(days: int = 90) -> list[dict]:
    """Return daily token totals, newest first."""
    rows = _query("""
        SELECT date,
               SUM(input_tokens) as input_tokens,
               SUM(cache_creation_tokens) as cache_creation_tokens,
               SUM(cache_read_tokens) as cache_read_tokens,
               SUM(output_tokens) as output_tokens,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens
        FROM messages
        WHERE date >= ?
        GROUP BY date
        ORDER BY date ASC
    """, (_since_date(days),))
    return [dict(r) for r in rows]


def by_project(days: int = 90) -> list[dict]:
    """Return token totals grouped by project, sorted descending."""
    rows = _query("""
        SELECT project,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               COUNT(DISTINCT session_id) as session_count,
               COUNT(*) as message_count
        FROM messages
        WHERE date >= ?
        GROUP BY project
        ORDER BY total_tokens DESC
        LIMIT 15
    """, (_since_date(days),))
    return [dict(r) for r in rows]


def by_model(days: int = 90) -> list[dict]:
    """Return token totals grouped by model."""
    rows = _query("""
        SELECT model,
               SUM(input_tokens) as input_tokens,
               SUM(cache_creation_tokens) as cache_creation_tokens,
               SUM(cache_read_tokens) as cache_read_tokens,
               SUM(output_tokens) as output_tokens,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               COUNT(DISTINCT session_id) as session_count
        FROM messages
        WHERE date >= ?
        GROUP BY model
        ORDER BY total_tokens DESC
    """, (_since_date(days),))
    return [dict(r) for r in rows]


def session_heatmap(days: int = 90) -> list[dict]:
    """Return heatmap data: day_of_week x hour with token counts."""
    rows = _query("""
        SELECT day_of_week, hour,
               COUNT(DISTINCT session_id) as session_count,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens
        FROM messages
        WHERE date >= ?
        GROUP BY day_of_week, hour
    """, (_since_date(days),))
    return [dict(r) for r in rows]


def session_list(days: int = 30) -> list[dict]:
    """Return per-session summary, most recent first, with person-hours for Claude Code sessions."""
    since = _since_date(days)
    with closing(get_conn()) as conn:
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
    out = []
    for r in rows:
        d = dict(r)
        d["person_hours"], d["hours_status"] = hours.get(d["session_id"], (None, None))
        out.append(d)
    return out


def session_detail(session_id: str) -> list[dict]:
    """Return per-message breakdown for a session."""
    rows = _query("""
        SELECT timestamp, model,
               input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens,
               input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens as total_tokens,
               cache_5m_tokens, cache_1h_tokens,
               entrypoint, speed, git_branch, web_search_count, web_fetch_count
        FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, (session_id,))
    return [dict(r) for r in rows]


def recent_rate(hours: int = 3) -> dict:
    """Return tokens per hour over the last N hours for forecasting."""
    # messages.timestamp is naive local time, so the cutoff must be too.
    since = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S')
    row = _query("""
        SELECT SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               COUNT(DISTINCT session_id) as sessions
        FROM messages
        WHERE timestamp >= ?
    """, (since,))[0]
    total = row['total_tokens'] or 0
    return {
        'tokens_per_hour': total / hours,
        'total_tokens': total,
        'hours': hours,
        'sessions': row['sessions'] or 0,
    }


def estimate_cost(days: int = 30) -> dict:
    """Estimate API cost based on token counts and model pricing."""
    rows = _query(f"""
        SELECT model, {_COST_COLUMNS}
        FROM messages
        WHERE date >= ?
        GROUP BY model
    """, (_since_date(days),))

    total_cost = 0.0
    breakdown = []

    for row in rows:
        model = row['model']
        cost = _group_cost(model, row)
        total_cost += cost
        breakdown.append({'model': model, 'cost': round(cost, 2)})

    return {
        'total_cost': round(total_cost, 2),
        'breakdown': sorted(breakdown, key=lambda x: -x['cost']),
        'days': days,
    }


def window_tokens(window_start: str, window_end: str, bucket_minutes: int,
                  group_by: str = None) -> list[dict]:
    """Return token counts in time buckets within a window.

    Args:
        window_start: Local-time ISO string (no tz) matching DB timestamp format
        window_end: Local-time ISO string (no tz)
        bucket_minutes: Bucket size in minutes (e.g. 5 or 60)
        group_by: None | 'token_type' | 'project' | 'model'

    Returns list of {time, group, tokens} dicts.
    """
    # SQLite time bucket expression
    if bucket_minutes == 60:
        bucket_expr = "strftime('%Y-%m-%dT%H:00:00', timestamp)"
    else:
        bucket_expr = (
            f"strftime('%Y-%m-%dT%H:', timestamp) || "
            f"printf('%02d:00', (CAST(strftime('%M', timestamp) AS INTEGER) / {bucket_minutes}) * {bucket_minutes})"
        )

    if group_by == 'token_type':
        rows = _query(f"""
            SELECT {bucket_expr} as time,
                   'input' as grp,
                   SUM(input_tokens) as tokens
            FROM messages WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time
            UNION ALL
            SELECT {bucket_expr} as time,
                   'cache_create' as grp,
                   SUM(cache_creation_tokens) as tokens
            FROM messages WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time
            UNION ALL
            SELECT {bucket_expr} as time,
                   'cache_read' as grp,
                   SUM(cache_read_tokens) as tokens
            FROM messages WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time
            UNION ALL
            SELECT {bucket_expr} as time,
                   'output' as grp,
                   SUM(output_tokens) as tokens
            FROM messages WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time
            ORDER BY time, grp
        """, (window_start, window_end) * 4)
    elif group_by == 'model':
        rows = _query(f"""
            SELECT {bucket_expr} as time,
                   model as grp,
                   {_COST_COLUMNS}
            FROM messages
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time, model
            ORDER BY time
        """, (window_start, window_end))
        return [{'time': r['time'], 'group': r['grp'], 'tokens': _group_cost(r['grp'], r)}
                for r in rows]
    elif group_by == 'project':
        rows = _query(f"""
            SELECT {bucket_expr} as time,
                   project as grp,
                   SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as tokens
            FROM messages
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time, project
            ORDER BY time, tokens DESC
        """, (window_start, window_end))
    else:
        # Total only
        rows = _query(f"""
            SELECT {bucket_expr} as time,
                   'total' as grp,
                   SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as tokens
            FROM messages
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time
            ORDER BY time
        """, (window_start, window_end))

    return [{'time': r['time'], 'group': r['grp'], 'tokens': r['tokens'] or 0} for r in rows]


def _judge_calls_between(conn, start: str, end: str) -> int:
    """Person-hours judge calls use quota but leave no session log to ingest."""
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM person_hour_estimates WHERE last_attempt_at >= ? AND last_attempt_at < ?",
            (start, end)).fetchone()[0]
    except sqlite3.OperationalError:  # table not created yet
        return 0


def detect_other_pct(window_start: str, window_end: str, window_type: str) -> dict:
    """Detect external/other usage by finding quota increases during periods with no local activity.

    Compares consecutive quota snapshots within the window. When quota % increased but no local
    messages were recorded in that interval, the increase is attributed to other sources.

    Returns dict with keys:
        other_pct: float — quota % attributable to external/unknown sources
        has_snapshots: bool — whether enough snapshot data was available
    """
    pct_col = 'five_hour_pct' if window_type == '5h' else 'seven_day_pct'
    rows = _query(
        f"SELECT timestamp, {pct_col} as pct FROM quota_snapshots "
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (window_start, window_end)
    )

    if len(rows) < 2:
        return {"other_pct": 0.0, "has_snapshots": False}

    other_pct = 0.0
    with closing(get_conn()) as conn:
        for i in range(len(rows) - 1):
            t1, pct1 = rows[i]['timestamp'], rows[i]['pct']
            t2, pct2 = rows[i + 1]['timestamp'], rows[i + 1]['pct']
            quota_delta = (pct2 or 0) - (pct1 or 0)
            if quota_delta <= 0.1:
                continue
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp >= ? AND timestamp < ?",
                (t1, t2)
            ).fetchone()[0]
            if count == 0:
                count = _judge_calls_between(conn, t1, t2)
            if count == 0:
                other_pct += quota_delta
    return {"other_pct": other_pct, "has_snapshots": True}


def entrypoint_stats(days: int = 30) -> dict:
    """Return counts of sessions by entrypoint."""
    rows = _query("""
        SELECT entrypoint, COUNT(DISTINCT session_id) as session_count
        FROM messages
        WHERE date >= ?
        GROUP BY entrypoint
        ORDER BY session_count DESC
    """, (_since_date(days),))
    return {r['entrypoint'] or 'unknown': r['session_count'] for r in rows}


def by_source(days: int = 90) -> list[dict]:
    """Return token totals grouped by source (claude-code vs claude-desktop)."""
    rows = _query("""
        SELECT COALESCE(source, 'claude-code') as source,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               COUNT(DISTINCT session_id) as session_count,
               COUNT(*) as message_count
        FROM messages
        WHERE date >= ?
        GROUP BY COALESCE(source, 'claude-code')
        ORDER BY total_tokens DESC
    """, (_since_date(days),))
    return [dict(r) for r in rows]


def db_stats() -> dict:
    """Return basic DB stats."""
    if not DB_PATH.exists():
        return {'exists': False}
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT COUNT(*) as count, MIN(date) as oldest, MAX(date) as newest FROM messages").fetchone()
        meta = conn.execute("SELECT COUNT(*) as count FROM ingest_meta").fetchone()
    return {
        'exists': True,
        'message_count': row['count'],
        'oldest_date': row['oldest'],
        'newest_date': row['newest'],
        'files_tracked': meta['count'],
    }


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


_leverage_cache: dict = {"key": None, "value": None}


def _leverage(conn) -> dict:
    """Leverage over the last 90 days, recomputed only when judged estimates change.

    It needs a 90-day window query (~130 ms on real data); caching it keeps the session list,
    which loads on every page view, as fast as it was before person-hours.
    """
    count, latest = conn.execute(
        "SELECT COUNT(*), MAX(last_attempt_at) FROM person_hour_estimates WHERE status = 'done'"
    ).fetchone()
    key = (str(DB_PATH), _since_date(LEVERAGE_LOOKBACK_DAYS), count, latest)
    if _leverage_cache["key"] != key:
        rows = _session_day_rows(conn, _since_date(LEVERAGE_LOOKBACK_DAYS))
        _leverage_cache.update(key=key, value=leverage_from_rows(rows))
    return _leverage_cache["value"]


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
    with closing(get_conn()) as conn:
        rows = _session_day_rows(conn, _since_date(days))
        leverage = _leverage(conn)
        latest = conn.execute(
            "SELECT model FROM person_hour_estimates WHERE status = 'done' AND model IS NOT NULL"
            " ORDER BY last_attempt_at DESC LIMIT 1").fetchone()
    out = hours_summary(rows, leverage)
    out["days"] = days
    out["model"] = latest["model"] if latest else None
    return out


def _session_hours_map(conn, since: str) -> dict:
    """session_id -> (person_hours, 'done' | 'provisional' | 'partial') for days since `since`."""
    leverage = _leverage(conn)
    acc: dict[str, list] = {}
    for r in _session_day_rows(conn, since):
        hours, judged = _day_hours(r, leverage)
        entry = acc.setdefault(r["session_id"], [0.0, 0, 0])
        entry[0] += hours
        entry[1] += judged
        entry[2] += 1
    return {sid: (round(h, 1), "done" if nj == n else "provisional" if nj == 0 else "partial")
            for sid, (h, nj, n) in acc.items()}


def session_hours(session_id: str) -> list[dict]:
    """Per-day person-hours for one session (the drill-down panel)."""
    with closing(get_conn()) as conn:
        leverage = _leverage(conn)
        rows = _session_day_rows(conn, "0000-00-00", session_id=session_id)
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
