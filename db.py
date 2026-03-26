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
               date,
               MAX(entrypoint) as entrypoint,
               MAX(speed) as speed,
               MAX(git_branch) as git_branch
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
        pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
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
        bucket_expr = "strftime('%Y-%m-%dT%H:00', timestamp)"
    else:
        bucket_expr = (
            f"strftime('%Y-%m-%dT%H:', timestamp) || "
            f"printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / {bucket_minutes}) * {bucket_minutes})"
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
            inp_price, out_price, cache_create_mult, cache_read_mult = MODEL_PRICING.get(model, DEFAULT_PRICING)
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


def _cost_sql_expr() -> str:
    """Return a SQL CASE expression computing per-row cost from token columns and model."""
    cases = []
    for model, (inp, out, cc_mult, cr_mult) in MODEL_PRICING.items():
        cases.append(
            f"WHEN model = '{model}' THEN "
            f"(input_tokens / 1000000.0 * {inp}) + "
            f"(cache_creation_tokens / 1000000.0 * {inp} * {cc_mult}) + "
            f"(cache_read_tokens / 1000000.0 * {inp} * {cr_mult}) + "
            f"(output_tokens / 1000000.0 * {out})"
        )
    default_inp, default_out, default_cc, default_cr = DEFAULT_PRICING
    default_case = (
        f"(input_tokens / 1000000.0 * {default_inp}) + "
        f"(cache_creation_tokens / 1000000.0 * {default_inp} * {default_cc}) + "
        f"(cache_read_tokens / 1000000.0 * {default_inp} * {default_cr}) + "
        f"(output_tokens / 1000000.0 * {default_out})"
    )
    return "CASE " + " ".join(cases) + f" ELSE {default_case} END"


def _weighted_median(values: list, weights: list) -> float:
    """Compute weighted median of values with corresponding weights."""
    pairs = sorted(zip(values, weights))
    total = sum(weights)
    cumulative = 0.0
    for val, w in pairs:
        cumulative += w
        if cumulative >= total / 2:
            return val
    return pairs[-1][0] if pairs else 0.0


def calibrate_cost_per_pct(window_start: str, window_end: str, window_type: str) -> dict:
    """Derive cost_per_pct calibration from quota snapshot deltas vs local cost deltas.

    Args:
        window_start: Local-time ISO string
        window_end: Local-time ISO string
        window_type: '5h' or '7d' — determines which pct column to use

    Returns:
        {"calibrated": bool, "cost_per_pct": float|None, "observations": int}
    """
    pct_col = "five_hour_pct" if window_type == "5h" else "seven_day_pct"
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT timestamp, {pct_col} as pct FROM quota_snapshots "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (window_start, window_end),
        ).fetchall()
    except Exception:
        conn.close()
        return {"calibrated": False, "cost_per_pct": None, "observations": 0}

    cost_expr = _cost_sql_expr()
    now = datetime.now()
    observations = []
    for i in range(len(rows) - 1):
        t1, p1 = rows[i]["timestamp"], rows[i]["pct"]
        t2, p2 = rows[i + 1]["timestamp"], rows[i + 1]["pct"]
        delta_pct = p2 - p1
        if delta_pct <= 0.5:  # skip noise, resets, and quota decrements
            continue
        result = conn.execute(
            f"SELECT SUM({cost_expr}) as cost FROM messages WHERE timestamp >= ? AND timestamp < ?",
            (t1, t2),
        ).fetchone()
        local_cost = result["cost"] or 0.0
        if local_cost <= 0:
            continue
        # Weight by delta_pct * recency (observations closer to now get higher weight)
        try:
            hours_ago = (now - datetime.fromisoformat(t2)).total_seconds() / 3600
        except Exception:
            hours_ago = 0.0
        recency_weight = 1.0 / (1.0 + hours_ago)
        observations.append((local_cost / delta_pct, delta_pct * recency_weight))

    conn.close()

    if len(observations) < 3:
        return {"calibrated": False, "cost_per_pct": None, "observations": len(observations)}

    values = [o[0] for o in observations]
    weights = [o[1] for o in observations]
    return {
        "calibrated": True,
        "cost_per_pct": _weighted_median(values, weights),
        "observations": len(observations),
    }


def window_cost_buckets(window_start: str, window_end: str, bucket_minutes: int) -> list:
    """Return total local cost per time bucket within a window.

    Returns list of {time, cost} dicts.
    """
    conn = get_conn()
    if bucket_minutes == 60:
        bucket_expr = "strftime('%Y-%m-%dT%H:00', timestamp)"
    else:
        bucket_expr = (
            f"strftime('%Y-%m-%dT%H:', timestamp) || "
            f"printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / {bucket_minutes}) * {bucket_minutes})"
        )
    cost_expr = _cost_sql_expr()
    rows = conn.execute(
        f"SELECT {bucket_expr} as time, SUM({cost_expr}) as cost "
        "FROM messages WHERE timestamp >= ? AND timestamp < ? "
        "GROUP BY time ORDER BY time",
        (window_start, window_end),
    ).fetchall()
    conn.close()
    return [{"time": r["time"], "cost": r["cost"] or 0.0} for r in rows]


def compute_other_series(
    window_start: str,
    window_end: str,
    window_type: str,
    bucket_minutes: int,
    cost_per_pct: float,
    quota_pct: float,
) -> list:
    """Compute the 'Other/External' usage series using snapshot interpolation.

    Returns a list of {time, pct} dicts representing the estimated external quota
    usage at each bucket boundary. Only emits points within snapshot coverage;
    buckets before the first snapshot (e.g. before server started) are omitted.

    Args:
        window_start: Local-time ISO string (window boundary)
        window_end: Local-time ISO string (window boundary)
        window_type: '5h' or '7d'
        bucket_minutes: Bucket size in minutes
        cost_per_pct: Dollars per quota percentage point (from calibration)
        quota_pct: Current live quota percentage (anchors the final snapshot point)

    Returns:
        list of {"time": str, "pct": float}
    """
    pct_col = "five_hour_pct" if window_type == "5h" else "seven_day_pct"
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT timestamp, {pct_col} as pct FROM quota_snapshots "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (window_start, window_end),
        ).fetchall()
    except Exception:
        conn.close()
        return []

    if len(rows) < 2:
        conn.close()
        return []

    # Build snapshot series anchored to the live quota_pct at "now"
    snap_times = [datetime.fromisoformat(r["timestamp"]) for r in rows]
    snap_pcts = [r["pct"] for r in rows]
    now = datetime.now()
    snap_times.append(now)
    snap_pcts.append(quota_pct)

    first_snap = snap_times[0]
    last_snap = snap_times[-1]

    # Get local cost per bucket (only buckets with messages are present)
    cost_buckets = window_cost_buckets(window_start, window_end, bucket_minutes)
    cost_lookup = {cb["time"]: cb["cost"] for cb in cost_buckets}

    # Generate all bucket start times across the window
    ws_dt = datetime.fromisoformat(window_start)
    we_dt = datetime.fromisoformat(window_end)
    step = timedelta(minutes=bucket_minutes)

    result = []
    cumulative_cost = 0.0
    bt = ws_dt
    while bt < we_dt:
        # Format bucket time to match window_cost_buckets output format
        if bucket_minutes == 60:
            bt_str = bt.strftime('%Y-%m-%dT%H:00')
        else:
            snapped_min = (bt.minute // bucket_minutes) * bucket_minutes
            bt_str = bt.strftime('%Y-%m-%dT%H:') + f'{snapped_min:02d}'

        cumulative_cost += cost_lookup.get(bt_str, 0.0)

        # Only emit for buckets within snapshot coverage
        if bt >= first_snap and bt <= last_snap:
            # Linearly interpolate total quota % between surrounding snapshots
            interp_pct = _interpolate_snapshot_pct(snap_times, snap_pcts, bt)
            local_pct = cumulative_cost / cost_per_pct
            other_pct = max(0.0, interp_pct - local_pct)
            result.append({"time": bt_str, "pct": round(other_pct, 4)})

        bt += step

    conn.close()
    return result


def _interpolate_snapshot_pct(snap_times: list, snap_pcts: list, target: datetime) -> float:
    """Linearly interpolate quota % at `target` time between snapshot data points."""
    if target <= snap_times[0]:
        return snap_pcts[0]
    if target >= snap_times[-1]:
        return snap_pcts[-1]
    for i in range(len(snap_times) - 1):
        t1, t2 = snap_times[i], snap_times[i + 1]
        if t1 <= target <= t2:
            span = (t2 - t1).total_seconds()
            if span <= 0:
                return snap_pcts[i]
            frac = (target - t1).total_seconds() / span
            return snap_pcts[i] + frac * (snap_pcts[i + 1] - snap_pcts[i])
    return snap_pcts[-1]


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
