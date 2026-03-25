"""
Query helpers for the usage SQLite database.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "usage.db"

# API pricing per 1M tokens (input, output, cache_creation_multiplier, cache_read_multiplier)
MODEL_PRICING = {
    "claude-opus-4-6":           (15.00, 75.00, 1.25, 0.10),
    "claude-opus-4-5":           (15.00, 75.00, 1.25, 0.10),
    "claude-opus-4-5-20251101":  (15.00, 75.00, 1.25, 0.10),
    "claude-sonnet-4-6":         (3.00,  15.00, 1.25, 0.10),
    "claude-sonnet-4-5":         (3.00,  15.00, 1.25, 0.10),
    "claude-haiku-4-5":          (0.25,  1.25,  1.25, 0.10),
    "claude-haiku-4-5-20251001": (0.25,  1.25,  1.25, 0.10),
}
DEFAULT_PRICING = (3.00, 15.00, 1.25, 0.10)


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
               date
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
               input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens as total_tokens
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
               SUM(output_tokens) as output_tokens
        FROM messages
        WHERE date >= ?
        GROUP BY model
    """, (_since_date(days),)).fetchall()
    conn.close()

    total_cost = 0.0
    breakdown = []

    for row in rows:
        model = row['model']
        pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
        input_price, output_price, cache_create_mult, cache_read_mult = pricing

        # Cost per token type (prices are per 1M)
        cost = (
            (row['input_tokens'] / 1_000_000) * input_price +
            (row['cache_creation_tokens'] / 1_000_000) * input_price * cache_create_mult +
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
