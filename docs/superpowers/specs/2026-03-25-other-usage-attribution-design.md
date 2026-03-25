# Other/External Usage Attribution — Design Spec

## Problem

The dashboard ingests JSONL session logs only from the local machine. However, the Anthropic quota API reports total usage across all machines. The cumulative chart currently normalizes local tokens to fill the entire reported quota %, making it appear that all usage came from local projects — even when significant usage occurred on other computers.

## Solution

Store periodic quota % snapshots, calibrate the relationship between local cost and quota %, and show the unaccounted gap as an "Other / External" band in cumulative charts.

## Design

### 1. Quota Snapshot Storage

New SQLite table:

```sql
quota_snapshots (
    timestamp TEXT PRIMARY KEY,  -- local time (same convention as messages table)
    five_hour_pct REAL,
    seven_day_pct REAL
)
```

- **Write frequency**: One row per ~60 seconds, throttled inside the existing quota fetch path.
- **Cleanup**: Delete rows older than 8 days on each write.
- **Migration**: `CREATE TABLE IF NOT EXISTS` in existing `_migrate_db()`.

### 2. Calibration: Local Cost → Quota %

Goal: determine `cost_per_pct` — how many dollars of local cost correspond to 1% of quota.

Method:
1. For each consecutive pair of snapshots within the current window, compute:
   - `quota_delta = pct_at_t2 - pct_at_t1`
   - `local_cost_delta` = total cost of local messages between t1 and t2 (using MODEL_PRICING)
2. Filter to observations where `quota_delta > 0` and `local_cost_delta > 0`.
3. Compute `cost_per_pct = local_cost_delta / quota_delta` for each observation.
4. Use the **weighted median** (weighted by `quota_delta` magnitude) as the calibration factor.

The weighted median is robust to mixed-machine periods: when other-machine usage coincides, observed `cost_per_pct` drops (denominator inflated), pulling below the true value. Pure-local periods give accurate values. The median naturally selects the correct cluster.

**Minimum data threshold**: Require at least 5 valid observations before considering calibration reliable. Below this, fall back to current behavior.

### 3. Cumulative Chart Changes

**When calibrated:**

For each time bucket in the window:
- `local_cost` = sum of local message costs in that bucket
- `local_pct` = `local_cost / cost_per_pct` (what % of quota this local cost represents)
- In stacked mode (project/model), each group's cost is converted to quota % using the same factor
- `other_pct` in each bucket = `quota_delta_in_bucket - sum(local_pct_in_bucket)`, floored at 0

The cumulative chart stacks local groups + "Other" to reach the actual total quota %.

**"Other" visual treatment:**
- Distinct muted color (gray, ~50% opacity or hatched pattern)
- Legend label: "Other / External"
- Tooltip shows the percentage value

**When NOT calibrated (fallback):**
- Current behavior: local tokens scaled to fill quota %
- No "Other" band shown
- Optional info tooltip: "Collecting calibration data for external usage detection"

### 4. Rate View

No changes. Rate view shows local tokens/min which is accurate as-is. "Other" usage has no per-bucket granularity in rate view.

### 5. EMA Projection

When calibrated, the EMA projection should be based on the total quota % curve (from snapshots) rather than only local data. This gives a more accurate projection of when quota will be exhausted.

### 6. API Changes

**`/api/quota` side-effect:** Each call writes a quota snapshot row (throttled to 1 per 60 seconds). No change to the response schema.

**`/api/window` response additions:**
- `calibrated: boolean` — whether calibration data is sufficient
- `cost_per_pct: number | null` — the calibration factor (for frontend use)
- When `group_by` is `project` or `model`, bucket data includes an `"Other"` group

**Recommended**: Compute calibration and "Other" in the **backend** (`db.py`), so the frontend receives ready-to-render data. This keeps `dashboard.js` simpler and avoids duplicating cost calculations.

### 7. Edge Cases

| Scenario | Behavior |
|---|---|
| Fresh deploy, no snapshots | Fall back to current normalization, no "Other" |
| Single machine only | "Other" ≈ 0%, local fills quota correctly |
| Dashboard offline during usage | Gaps in snapshots; quota jumps appear as "Other" |
| Quota resets mid-window | Snapshots capture the drop; calibration uses deltas |
| `quota_pct = 0` | Nothing to attribute, no "Other" shown |

### 8. Files to Modify

| File | Changes |
|---|---|
| `db.py` | New `quota_snapshots` table creation, snapshot write function, snapshot query function, calibration computation, modified `window_tokens()` to include "Other" |
| `app.py` | Snapshot write side-effect in quota fetch path (throttled), pass calibration data through `/api/window` |
| `ingest.py` | Add `quota_snapshots` table to `_migrate_db()` |
| `static/dashboard.js` | Modified `buildWindowChart()` to render "Other" band, updated normalization logic, EMA based on total quota curve |
| `static/style.css` | Color/style for "Other" band (if needed beyond Chart.js config) |
