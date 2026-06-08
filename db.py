"""
Query helpers for the usage SQLite database.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "usage.db"

# API pricing per 1M tokens (input_price, output_price, cache_create_mult, cache_read_mult)
# Cache creation tiers (5m and 1h ephemeral) are both charged at 1.25x input price.
# Cache read is charged at 0.10x input price.
# Family rates are the source of truth; the exact-ID dict below holds overrides only.
OPUS_PRICING =   (15.00, 75.00, 1.25, 0.10)
SONNET_PRICING = (3.00,  15.00, 1.25, 0.10)
HAIKU_PRICING =  (0.25,  1.25,  1.25, 0.10)

MODEL_PRICING = {
    "claude-opus-4-6":           OPUS_PRICING,
    "claude-opus-4-5":           OPUS_PRICING,
    "claude-opus-4-5-20251101":  OPUS_PRICING,
    "claude-sonnet-4-6":         SONNET_PRICING,
    "claude-sonnet-4-5":         SONNET_PRICING,
    "claude-haiku-4-5":          HAIKU_PRICING,
    "claude-haiku-4-5-20251001": HAIKU_PRICING,
}
DEFAULT_PRICING = SONNET_PRICING


def price_for_model(model: str):
    """Return (input, output, cache_create_mult, cache_read_mult) for a model.

    Exact-ID overrides in MODEL_PRICING win; otherwise fall back to the model
    family (opus/sonnet/haiku) so new versions are never silently mis-tiered;
    otherwise DEFAULT_PRICING.
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    m = (model or "").lower()
    if "opus" in m:
        return OPUS_PRICING
    if "sonnet" in m:
        return SONNET_PRICING
    if "haiku" in m:
        return HAIKU_PRICING
    return DEFAULT_PRICING


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _since_date(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')


def daily_tokens(days: int = 90) -> list[dict]:
    """Return daily token totals, newest first."""
    conn = get_conn()
    rows = conn.execute("""
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
    """, (_since_date(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def by_project(days: int = 90) -> list[dict]:
    """Return token totals grouped by project, sorted descending."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT project,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               COUNT(DISTINCT session_id) as session_count,
               COUNT(*) as message_count
        FROM messages
        WHERE date >= ?
        GROUP BY project
        ORDER BY total_tokens DESC
        LIMIT 15
    """, (_since_date(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def by_model(days: int = 90) -> list[dict]:
    """Return token totals grouped by model."""
    conn = get_conn()
    rows = conn.execute("""
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
    """, (_since_date(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def session_heatmap(days: int = 90) -> list[dict]:
    """Return heatmap data: day_of_week x hour with token counts."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT day_of_week, hour,
               COUNT(DISTINCT session_id) as session_count,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens
        FROM messages
        WHERE date >= ?
        GROUP BY day_of_week, hour
    """, (_since_date(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def session_list(days: int = 30) -> list[dict]:
    """Return per-session summary, most recent first."""
    conn = get_conn()
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
    """, (_since_date(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def session_detail(session_id: str) -> list[dict]:
    """Return per-message breakdown for a session."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT timestamp, model,
               input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens,
               input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens as total_tokens,
               cache_5m_tokens, cache_1h_tokens,
               entrypoint, speed, git_branch, web_search_count, web_fetch_count
        FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, (session_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recent_rate(hours: int = 3) -> dict:
    """Return tokens per hour over the last N hours for forecasting."""
    conn = get_conn()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    row = conn.execute("""
        SELECT SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               COUNT(DISTINCT session_id) as sessions
        FROM messages
        WHERE timestamp >= ?
    """, (since,)).fetchone()
    conn.close()
    total = row['total_tokens'] or 0
    return {
        'tokens_per_hour': total / hours,
        'total_tokens': total,
        'hours': hours,
        'sessions': row['sessions'] or 0,
    }


def estimate_cost(days: int = 30) -> dict:
    """Estimate API cost based on token counts and model pricing."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT model,
               SUM(input_tokens) as input_tokens,
               SUM(cache_creation_tokens) as cache_creation_tokens,
               SUM(cache_read_tokens) as cache_read_tokens,
               SUM(output_tokens) as output_tokens,
               SUM(cache_5m_tokens) as cache_5m_tokens,
               SUM(cache_1h_tokens) as cache_1h_tokens
        FROM messages
        WHERE date >= ?
        GROUP BY model
    """, (_since_date(days),)).fetchall()
    conn.close()

    total_cost = 0.0
    breakdown = []

    for row in rows:
        model = row['model']
        pricing = price_for_model(model)
        input_price, output_price, cache_create_mult, cache_read_mult = pricing

        # Use tier-specific cache counts when available, fall back to aggregated column
        cache_5m = row['cache_5m_tokens'] or 0
        cache_1h = row['cache_1h_tokens'] or 0
        if cache_5m + cache_1h > 0:
            # Use tier-specific counts — both tiers charged at the same 1.25x rate
            cache_create_cost = (cache_5m + cache_1h) / 1_000_000 * input_price * cache_create_mult
        else:
            # Pre-migration data: use aggregated column
            cache_create_cost = row['cache_creation_tokens'] / 1_000_000 * input_price * cache_create_mult

        cost = (
            (row['input_tokens'] / 1_000_000) * input_price +
            cache_create_cost +
            (row['cache_read_tokens'] / 1_000_000) * input_price * cache_read_mult +
            (row['output_tokens'] / 1_000_000) * output_price
        )
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
    conn = get_conn()

    # SQLite time bucket expression
    if bucket_minutes == 60:
        bucket_expr = "strftime('%Y-%m-%dT%H:00:00', timestamp)"
    else:
        bucket_expr = (
            f"strftime('%Y-%m-%dT%H:', timestamp) || "
            f"printf('%02d:00', (CAST(strftime('%M', timestamp) AS INTEGER) / {bucket_minutes}) * {bucket_minutes})"
        )

    if group_by == 'token_type':
        rows = conn.execute(f"""
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
        """, (window_start, window_end) * 4).fetchall()
    elif group_by == 'model':
        rows = conn.execute(f"""
            SELECT {bucket_expr} as time,
                   model as grp,
                   SUM(input_tokens) as input_tokens,
                   SUM(cache_creation_tokens) as cache_creation_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(output_tokens) as output_tokens
            FROM messages
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time, model
            ORDER BY time
        """, (window_start, window_end)).fetchall()
        conn.close()
        results = []
        for r in rows:
            model = r['grp']
            inp_price, out_price, cache_create_mult, cache_read_mult = price_for_model(model)
            cost = (
                (r['input_tokens'] or 0) / 1_000_000 * inp_price +
                (r['cache_creation_tokens'] or 0) / 1_000_000 * inp_price * cache_create_mult +
                (r['cache_read_tokens'] or 0) / 1_000_000 * inp_price * cache_read_mult +
                (r['output_tokens'] or 0) / 1_000_000 * out_price
            )
            results.append({'time': r['time'], 'group': model, 'tokens': cost})
        return results
    elif group_by == 'project':
        rows = conn.execute(f"""
            SELECT {bucket_expr} as time,
                   project as grp,
                   SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as tokens
            FROM messages
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time, project
            ORDER BY time, tokens DESC
        """, (window_start, window_end)).fetchall()
    else:
        # Total only
        rows = conn.execute(f"""
            SELECT {bucket_expr} as time,
                   'total' as grp,
                   SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as tokens
            FROM messages
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY time
            ORDER BY time
        """, (window_start, window_end)).fetchall()

    conn.close()
    return [{'time': r['time'], 'group': r['grp'], 'tokens': r['tokens'] or 0} for r in rows]


def detect_other_pct(window_start: str, window_end: str, window_type: str) -> dict:
    """Detect external/other usage by finding quota increases during periods with no local activity.

    Compares consecutive quota snapshots within the window. When quota % increased but no local
    messages were recorded in that interval, the increase is attributed to other sources.

    Returns dict with keys:
        other_pct: float — quota % attributable to external/unknown sources
        has_snapshots: bool — whether enough snapshot data was available
    """
    conn = get_conn()
    pct_col = 'five_hour_pct' if window_type == '5h' else 'seven_day_pct'
    rows = conn.execute(
        f"SELECT timestamp, {pct_col} as pct FROM quota_snapshots "
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (window_start, window_end)
    ).fetchall()
    conn.close()

    if len(rows) < 2:
        return {"other_pct": 0.0, "has_snapshots": False}

    other_pct = 0.0
    conn = get_conn()
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
    conn.close()
    return {"other_pct": other_pct, "has_snapshots": True}


def entrypoint_stats(days: int = 30) -> dict:
    """Return counts of sessions by entrypoint."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT entrypoint, COUNT(DISTINCT session_id) as session_count
        FROM messages
        WHERE date >= ?
        GROUP BY entrypoint
        ORDER BY session_count DESC
    """, (_since_date(days),)).fetchall()
    conn.close()
    return {r['entrypoint'] or 'unknown': r['session_count'] for r in rows}


def by_source(days: int = 90) -> list[dict]:
    """Return token totals grouped by source (claude-code vs claude-desktop)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT COALESCE(source, 'claude-code') as source,
               SUM(input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens) as total_tokens,
               COUNT(DISTINCT session_id) as session_count,
               COUNT(*) as message_count
        FROM messages
        WHERE date >= ?
        GROUP BY COALESCE(source, 'claude-code')
        ORDER BY total_tokens DESC
    """, (_since_date(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_stats() -> dict:
    """Return basic DB stats."""
    if not DB_PATH.exists():
        return {'exists': False}
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as count, MIN(date) as oldest, MAX(date) as newest FROM messages").fetchone()
    meta = conn.execute("SELECT COUNT(*) as count FROM ingest_meta").fetchone()
    conn.close()
    return {
        'exists': True,
        'message_count': row['count'],
        'oldest_date': row['oldest'],
        'newest_date': row['newest'],
        'files_tracked': meta['count'],
    }
