"""
Query helpers for the usage SQLite database.
"""
import re
import sqlite3
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
    """Return per-session summary, most recent first."""
    rows = _query("""
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
    """, (_since_date(days),))
    return [dict(r) for r in rows]


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
