# EMA Projection for Window Time-Series Charts

## Problem

The 5-hour and 7-day window charts (`buildWindowChart()` in `static/dashboard.js`) generate x-axis buckets for the full quota window and fill future buckets with zeros. This causes the line to drop to zero after the present moment, which is misleading and makes it hard to distinguish past data from future emptiness.

## Solution

Add three visual elements to both chart views (cumulative and rate):

1. **"Now" dot** — a glowing dot marking the present moment on the data line
2. **EMA projection line** — a dashed line in blue-gray extending from "now" for a fixed interval (1h for 5h chart, 1d for 7d chart), fading from 50% to 0% opacity
3. **Variance-based confidence band** — a shaded region around the projection that widens over time (reflecting real data variance) and fades to 0% opacity, also in blue-gray

After the projection interval, the chart is blank — x-axis continues to the window end but nothing is drawn.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| "Now" marker | Glowing dot only (no vertical line) | Sufficient without adding visual clutter |
| Projection color | Blue-gray `#7B9EB8` | Distinct from orange (`#E07A5F`) actual data fill |
| Confidence band | Variance-based (σ from EMA residuals) | Lightweight calculation, reflects real uncertainty |
| Fade | Strong: 50% → 0% opacity | Emphasizes near-term confidence, fully disappears at projection end |
| Projection length | Fixed: 1h (5h chart), 1d (7d chart) | Near-term forecast only, not full remaining window |
| Stacked mode | Projection on aggregate total only | Avoids clutter from per-group projections |
| EMA alpha | 0.3 (hardcoded) | Responsive to recent changes without being noisy |
| "Now" detection | Timestamp-based (`bucket_time <= Date.now()`) | Reliable even during zero-usage periods |
| Chart views | Both cumulative (% quota) and rate (tokens/min) | Consistent experience across views |
| Beyond projection | Blank space, x-axis continues | Shows remaining window time without misleading data |

## File Modified

- `static/dashboard.js` — `buildWindowChart()` function (lines 308–481)

No backend/API changes required. All computation is frontend-only on data already returned by `/api/window`.

## Implementation Details

### 1. Detect "now" index

Compare each bucket timestamp in `allTimes` against `Date.now()`. The now-index is the last bucket where `timestamp <= now`.

### 2. Compute EMA and variance (single pass over `[0..nowIndex]`)

For each group's data (or the aggregate total in stacked mode):

```
ema[0] = data[0]
for i = 1 to nowIndex:
    ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]
    sumSquaredResiduals += (data[i] - ema[i])²
σ = sqrt(sumSquaredResiduals / nowIndex)
```

### 3. Project forward from nowIndex

Compute the projection end index based on the fixed interval:
- 5h chart (5-min buckets): `projectionBuckets = 60 / 5 = 12` (1 hour)
- 7d chart (60-min buckets): `projectionBuckets = 1440 / 60 = 24` (1 day)
- Clamp to not exceed the window end

**Cumulative view**: Continue with the slope of the last EMA segment.
```
slope = ema[nowIndex] - ema[nowIndex - 1]   (or 0 if nowIndex == 0)
projected[step] = ema[nowIndex] + slope * step
```

**Rate view**: Hold the last EMA value flat.
```
projected[step] = ema[nowIndex]
```

### 4. Confidence band

```
upper[step] = projected[step] + 1.5 * σ * sqrt(step)
lower[step] = projected[step] - 1.5 * σ * sqrt(step)
```

Clamp lower to 0. For cumulative view, clamp upper to 100%.

### 5. Chart.js dataset changes

**Truncate actual data**: Set all values after `nowIndex` to `null` so Chart.js stops drawing.

**"Now" dot**: On the actual data dataset(s), set `pointRadius` to an array: 0 for all points except `nowIndex` where it's 5. Set `pointBackgroundColor` and `pointBorderColor` arrays similarly, with a glow effect at `nowIndex`.

**Projection line** (new dataset):
- Data: `null` for `[0..nowIndex-1]`, actual EMA value at `nowIndex` (to connect with the dot), then projected values for `[nowIndex+1..nowIndex+projectionBuckets]`, then `null` for the rest
- `borderColor`: canvas linear gradient from `rgba(123,158,184,0.5)` to `rgba(123,158,184,0)` spanning nowIndex to projection end
- `borderDash: [6, 4]`
- `pointRadius: 0`
- `fill: false`

**Upper confidence bound** (new dataset):
- Data: same null pattern, upper band values in projection zone
- `backgroundColor`: canvas gradient from `rgba(123,158,184,0.12)` to `rgba(123,158,184,0)`
- `borderWidth: 0`
- `fill: '+1'` (fills down to the lower bound dataset)
- `pointRadius: 0`

**Lower confidence bound** (new dataset):
- Data: same null pattern, lower band values in projection zone
- `borderWidth: 0`
- `fill: false`
- `pointRadius: 0`

### 6. Edge case: insufficient data

If `nowIndex < 2` (fewer than 2 data points), skip the EMA projection entirely — just show the actual data with the now-dot. There's not enough history to compute a meaningful trend or variance.

### 7. Existing features preserved

- Pace reference line (cumulative view) — unchanged
- Moving average smoothing (rate view via `_movingAverage`) — unchanged
- Stacked fills, legend filtering, tooltip formatting — unchanged
- Color schemes for groups — unchanged

## Verification

1. Start the server: `conda activate claude-usage-dashboard && python app.py --port 8080`
2. Open `http://127.0.0.1:8080/` in browser
3. Check the 5h window chart:
   - Solid orange line with fill stops at "now" (no zero-fill beyond)
   - Glowing dot at "now"
   - Blue-gray dashed projection line extends ~1 hour from "now", fading to invisible
   - Blue-gray confidence band widens and fades around the projection
   - X-axis continues to window end with blank space after projection
4. Check the 7d window chart: same behavior, projection extends ~1 day
5. Toggle between cumulative and rate views — both should show projection
6. Toggle stacked modes (token_type, project, model) — projection should appear on aggregate total
7. Verify tooltips still work correctly on both actual data and projection regions
