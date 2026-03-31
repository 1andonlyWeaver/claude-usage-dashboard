/* ============================================================
   Claude Code Usage Dashboard — Frontend Logic
   ============================================================ */

'use strict';

// ─── Constants ───────────────────────────────────────────────
const CLAUDE_ORANGE  = '#E07A5F';
const CLAUDE_AMBER   = '#C9A96E';
const CLAUDE_CREAM   = 'rgba(250,240,230,0.7)';
const CLAUDE_RED     = '#D95F5F';
const CLAUDE_PURPLE  = '#9B7FC8';

const ARC_TOTAL      = 377;   // total arc length for 270° arc on r=80 (circumference≈503, arc=377, gap=126)
const DAY_LABELS     = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

// Chart.js global defaults
Chart.defaults.color = 'rgba(245,237,228,0.5)';
Chart.defaults.borderColor = 'rgba(255,245,235,0.07)';
Chart.defaults.font.family = "'DM Sans', sans-serif";

// ─── State ───────────────────────────────────────────────────
let chart5h = null, chart7d = null, projectChart = null, modelChart = null, sessionDetailChart = null;
let currentSessionDays = 7;
let _windowRefreshInterval = null;
let rateData = null;
let windowView = 'rate';    // 'rate' | 'cumulative'
let windowStack = 'none';   // 'none' | 'token_type' | 'project' | 'model'
let chartFullscreen = null;
let fsTab = '5h';            // '5h' | '7d'
let exceedanceState = { '5h': null, '7d': null };
let fsView = 'rate';
let fsStack = 'none';

// Extended palette for project/model stacking
const STACK_PALETTE = [
  '#E07A5F','#C9A96E','#7B9E8A','#6B8CBA','#BA6B8C',
  '#8CBA6B','#BA8C6B','#6BBA8C','#8C6BBA','#BA9E6B',
];

// ─── Init ────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('keydown', e => {
    if (e.key === 'r' || e.key === 'R') { if (!e.target.matches('input,textarea')) triggerRefresh(); }
    if (e.key === 'Escape') closePanel();
  });
  initAll();
  startQuotaPolling();
  checkIngestStatus();
});

async function initAll() {
  await Promise.all([
    loadWindowCharts(),
    loadProjects(),
    loadModels(),
    loadHeatmap(),
    loadSessions(currentSessionDays),
    loadCost(),
    loadRate(),
  ]);
  // Auto-refresh window charts every 60s (clear any prior interval to avoid stacking)
  if (_windowRefreshInterval) clearInterval(_windowRefreshInterval);
  _windowRefreshInterval = setInterval(loadWindowCharts, 60000);
}

// ─── Quota polling ───────────────────────────────────────────
let _quotaPollInterval = null;
const QUOTA_POLL_NORMAL = 5000;
const QUOTA_POLL_ERROR  = 30000;

function _setQuotaPollRate(ms) {
  if (_quotaPollInterval) clearInterval(_quotaPollInterval);
  _quotaPollInterval = setInterval(fetchQuota, ms);
}

function startQuotaPolling() {
  fetchQuota();
  _setQuotaPollRate(QUOTA_POLL_NORMAL);
  setInterval(updateCountdowns, 1000);
}

let quotaState = { five: null, seven: null };

async function fetchQuota() {
  try {
    const data = await apiFetch('/api/quota');
    const hasError = !!data.error;
    document.getElementById('gauge5h').classList.toggle('stale', hasError);
    document.getElementById('gauge7d').classList.toggle('stale', hasError);

    if (hasError) {
      document.getElementById('lastUpdated').textContent = 'Quota unavailable: ' + data.error;
      _setQuotaPollRate(QUOTA_POLL_ERROR);
      return;
    }
    _setQuotaPollRate(QUOTA_POLL_NORMAL);

    quotaState = {
      five: { pct: data.five_hour_pct, resetsAt: new Date(data.five_hour_resets_at) },
      seven: { pct: data.seven_day_pct, resetsAt: new Date(data.seven_day_resets_at) },
    };
    updateGauge('5h', quotaState.five.pct);
    updateGauge('7d', quotaState.seven.pct);
    updateIdealTick('5h', quotaState.five.resetsAt, 5 * 3600000);
    updateIdealTick('7d', quotaState.seven.resetsAt, 7 * 24 * 3600000);
    updateForecasts();
    updateExtraUsage(data);
    document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('lastUpdated').textContent = 'Error fetching quota';
  }
}

function updateGauge(id, pct) {
  const arc = document.getElementById('arc' + id);
  const pctEl = document.getElementById('pct' + id);
  if (!arc || !pctEl) return;

  const offset = ARC_TOTAL - (ARC_TOTAL * Math.min(pct, 100) / 100);
  arc.style.strokeDashoffset = offset;

  // Color transition
  let color;
  if (pct >= 80) {
    color = CLAUDE_RED;
    arc.classList.add('danger');
  } else if (pct >= 50) {
    // Interpolate orange → amber
    const t = (pct - 50) / 30;
    color = lerpColor(CLAUDE_ORANGE, CLAUDE_AMBER, t);
    arc.classList.remove('danger');
  } else {
    color = CLAUDE_ORANGE;
    arc.classList.remove('danger');
  }

  // Update gradient stops
  const gaugeNum = id === '5h' ? '5h' : '7d';
  const grad = document.getElementById('grad' + gaugeNum);
  if (grad) {
    grad.children[0].setAttribute('stop-color', color);
    grad.children[1].setAttribute('stop-color', pct >= 80 ? '#FF4444' : CLAUDE_AMBER);
  }

  pctEl.textContent = Math.round(pct) + '%';
}

function updateIdealTick(id, resetsAt, windowMs) {
  const tick = document.getElementById('tick' + id);
  if (!tick) return;
  const now = Date.now();
  const elapsed = windowMs - (resetsAt - now);
  const idealPct = Math.max(0, Math.min(100, elapsed / windowMs * 100));
  // 270° arc starting at 135° (SVG coords, center 100,100, r=80)
  const angleDeg = 135 + (idealPct / 100 * 270);
  const angleRad = angleDeg * (Math.PI / 180);
  const cos = Math.cos(angleRad), sin = Math.sin(angleRad);
  tick.setAttribute('x1', (100 + 72 * cos).toFixed(2));
  tick.setAttribute('y1', (100 + 72 * sin).toFixed(2));
  tick.setAttribute('x2', (100 + 88 * cos).toFixed(2));
  tick.setAttribute('y2', (100 + 88 * sin).toFixed(2));
}

function updateCountdowns() {
  if (quotaState.five) {
    document.getElementById('countdown5h').textContent = 'Resets ' + formatCountdown(quotaState.five.resetsAt);
    updateIdealTick('5h', quotaState.five.resetsAt, 5 * 3600000);
  }
  if (quotaState.seven) {
    document.getElementById('countdown7d').textContent = 'Resets ' + formatCountdown(quotaState.seven.resetsAt);
    updateIdealTick('7d', quotaState.seven.resetsAt, 7 * 24 * 3600000);
  }
  updateExceedanceWarnings();
}

function updateForecasts() {
  if (!rateData || !quotaState.five) return;
  const tph = rateData.tokens_per_hour;
  if (tph <= 0) return;

  // Simple linear forecast: how long until 100%?
  [
    { id: '5h', q: quotaState.five, windowHours: 5 },
    { id: '7d', q: quotaState.seven, windowHours: 168 },
  ].forEach(({ id, q }) => {
    const el = document.getElementById('forecast' + id);
    if (!el || !q) return;
    const pctRemaining = 100 - q.pct;
    if (pctRemaining <= 0) {
      el.textContent = 'Limit reached';
      el.style.color = CLAUDE_RED;
      return;
    }
    // Estimate: if current rate applies, how long till cap?
    // pct/hr = (tokens/hr) / (totalCapTokens/100) — but we don't know cap in tokens
    // Use: remaining_pct / (current_rate_pct_per_hr)
    // current_rate_pct_per_hr ≈ (current_pct / elapsed_time_since_start)
    // Simpler: use proportional estimate from pct consumption and time to reset
    const now = new Date();
    const resetMs = q.resetsAt - now;
    if (resetMs <= 0) {
      el.textContent = '';
      return;
    }
    const windowHours = resetMs / 3600000;
    const pctPerHour = q.pct / Math.max(1, (q.resetsAt - now - resetMs + resetMs) / 3600000);

    // Better: use actual token rate
    // If tokens_per_hour = X, and current pct = P, then rate = X tokens/hr
    // Remaining = (100-P)% of cap. At X tokens/hr, time = remaining_tokens / X
    // remaining_tokens ≈ (remaining_pct / pct) * current_tokens_in_window
    if (q.pct > 0 && tph > 0) {
      const currentTokensEstimate = (rateData.total_tokens / Math.min(rateData.hours, 3));
      const hoursToFull = (pctRemaining / q.pct) * (rateData.hours);
      if (hoursToFull > windowHours) {
        el.textContent = 'On track — won\'t cap';
        el.style.color = 'rgba(245,237,228,0.4)';
      } else if (hoursToFull < 0.5) {
        el.textContent = '⚠ ~' + Math.round(hoursToFull * 60) + 'm to limit';
        el.style.color = CLAUDE_RED;
      } else if (hoursToFull < 2) {
        el.textContent = '⚠ ~' + hoursToFull.toFixed(1) + 'h to limit';
        el.style.color = CLAUDE_AMBER;
      } else {
        el.textContent = '~' + hoursToFull.toFixed(1) + 'h at current pace';
        el.style.color = 'rgba(245,237,228,0.4)';
      }
    }
  });
}

function updateExtraUsage(data) {
  const el = document.getElementById('extraUsage');
  if (!el) return;
  const enabled = data.extra_usage_enabled;
  if (enabled == null) { el.style.display = 'none'; return; }
  el.style.display = '';
  if (!enabled) {
    el.style.display = 'none';
    return;
  }
  const limit = data.extra_usage_limit != null ? '$' + (data.extra_usage_limit / 100).toFixed(2) : '—';
  const used  = data.extra_usage_used  != null ? '$' + (data.extra_usage_used  / 100).toFixed(2) : '—';
  const pct   = data.extra_usage_utilization != null ? ' (' + Math.round(data.extra_usage_utilization) + '%)' : '';
  el.textContent = `Overuse: ${used} / ${limit}${pct}`;
}

function formatCountdown(date) {
  const ms = date - new Date();
  if (ms <= 0 && ms > -15000) return 'now';
  if (ms <= -15000) return 'passed';
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d >= 1) return `in ${d}d ${h % 24}h`;
  if (h >= 1) return `in ${h}h ${m % 60}m`;
  if (m >= 1) return `in ${m}m`;
  return `in ${m}m ${s % 60}s`;
}

// ─── Ingest status ───────────────────────────────────────────
function checkIngestStatus() {
  const check = async () => {
    const status = await apiFetch('/api/ingest-status');
    const banner = document.getElementById('ingestBanner');
    const bar = document.getElementById('ingestProgressBar');
    const msg = document.getElementById('ingestMsg');

    if (status.running) {
      banner.style.display = 'flex';
      const pct = status.total > 0 ? (status.progress / status.total * 100) : 0;
      bar.style.width = pct + '%';
      msg.textContent = `Parsing session files… ${status.progress}/${status.total}`;
      setTimeout(check, 800);
    } else if (!status.done && !status.error) {
      setTimeout(check, 1000);
    } else {
      banner.style.display = 'none';
      if (status.done) initAll();
    }
  };
  check();
}

async function triggerRefresh() {
  await apiFetch('/api/refresh', { method: 'POST' });
  checkIngestStatus();
}

// ─── Window charts (5h / 7d session) ─────────────────────────
function setWindowView(view, btn) {
  windowView = view;
  document.querySelectorAll('.view-selector .view-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadWindowCharts();
}

function setWindowStack(stack, btn) {
  windowStack = stack;
  document.querySelectorAll('.stack-selector .stack-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadWindowCharts();
}

// ─── Fullscreen chart overlay ─────────────────────────────────
function _syncFsButtons() {
  const tabMap = { '5h': 'fsTab5h', '7d': 'fsTab7d' };
  const viewMap = { 'rate': 'fsViewRate', 'cumulative': 'fsViewCum' };
  const stackMap = { 'none': 'fsStackNone', 'token_type': 'fsStackToken', 'project': 'fsStackProj', 'model': 'fsStackModel' };
  document.querySelectorAll('.chart-fs-tabs .view-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.chart-fs-header .view-selector .view-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.chart-fs-header .stack-selector .stack-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(tabMap[fsTab])?.classList.add('active');
  document.getElementById(viewMap[fsView])?.classList.add('active');
  document.getElementById(stackMap[fsStack])?.classList.add('active');
}

function openChartFullscreen(tab) {
  fsTab = tab;
  fsView = windowView;
  fsStack = windowStack;
  _syncFsButtons();
  document.getElementById('chartFsOverlay').classList.add('open');
  loadFullscreenChart();
}

function closeChartFullscreen() {
  document.getElementById('chartFsOverlay').classList.remove('open');
  if (chartFullscreen) { chartFullscreen.destroy(); chartFullscreen = null; }
}

function setFsTab(tab, btn) {
  fsTab = tab;
  document.querySelectorAll('.chart-fs-tabs .view-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadFullscreenChart();
}

function setFsView(view, btn) {
  fsView = view;
  document.querySelectorAll('.chart-fs-header .view-selector .view-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadFullscreenChart();
}

function setFsStack(stack, btn) {
  fsStack = stack;
  document.querySelectorAll('.chart-fs-header .stack-selector .stack-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadFullscreenChart();
}

async function loadFullscreenChart() {
  try {
    const gb = fsStack === 'none' ? 'none' : fsStack;
    const data = await apiFetch('/api/window?type=' + fsTab + '&group_by=' + gb);
    if (chartFullscreen) { chartFullscreen.destroy(); chartFullscreen = null; }
    // Temporarily swap global view/stack state for buildWindowChart, then restore
    const savedView = windowView, savedStack = windowStack;
    windowView = fsView;
    windowStack = fsStack;
    chartFullscreen = buildWindowChart(data, 'chartFullscreen', fsTab === '5h' ? 3 : 9);
    windowView = savedView;
    windowStack = savedStack;
  } catch (e) {
    console.warn('Fullscreen chart load failed:', e);
  }
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.getElementById('chartFsOverlay').classList.contains('open')) {
    closeChartFullscreen();
  }
});

async function loadWindowCharts() {
  try {
    const gb = windowStack === 'none' ? 'none' : windowStack;
    const [d5h, d7d] = await Promise.all([
      apiFetch('/api/window?type=5h&group_by=' + gb),
      apiFetch('/api/window?type=7d&group_by=' + gb),
    ]);
    // Compute exceedance predictions before building charts
    exceedanceState['5h'] = computeExceedance(d5h);
    exceedanceState['7d'] = computeExceedance(d7d);
    updateExceedanceWarnings();
    // Destroy old charts, then build new ones from fetched data
    if (chart5h) { chart5h.destroy(); chart5h = null; }
    if (chart7d) { chart7d.destroy(); chart7d = null; }
    chart5h = buildWindowChart(d5h, 'chart5h', 3);
    chart7d = buildWindowChart(d7d, 'chart7d', 9);
  } catch (e) {
    // Fetch failed — leave existing charts intact rather than showing blank canvas
    console.warn('Window chart refresh failed, keeping previous charts:', e);
  }
}

function _movingAverage(arr, halfWin) {
  return arr.map((_, i) => {
    const s = Math.max(0, i - halfWin), e = Math.min(arr.length - 1, i + halfWin);
    const slice = arr.slice(s, e + 1);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  });
}

// ─── EMA helper (module-scoped so computeExceedance can reuse it) ─────────────
function _computeEma(series, nowIdx) {
  if (nowIdx < 2) return null;
  const alpha = 0.3;
  const ema = [series[0]];
  let sumSqRes = 0;
  for (let i = 1; i <= nowIdx; i++) {
    ema[i] = alpha * series[i] + (1 - alpha) * ema[i - 1];
    sumSqRes += (series[i] - ema[i]) ** 2;
  }
  const sigma = Math.sqrt(sumSqRes / nowIdx);
  return { ema, sigma, projBuckets: 0 };  // projBuckets set by caller
}

// Compute when cumulative usage is projected to reach 100% based on EMA slope.
// Returns { exceedTime: Date } if cap is predicted before window_end, or null otherwise.
function computeExceedance(data) {
  if (!data) return null;
  const { window_start, window_end, quota_pct, bucket_minutes, buckets } = data;
  if (!quota_pct || quota_pct <= 0) return null;  // quota API unavailable

  const startMs = new Date(window_start).getTime();
  const endMs   = new Date(window_end).getTime();
  const bucketMs = bucket_minutes * 60000;
  const allTimes = [];
  for (let t = startMs; t < endMs; t += bucketMs) allTimes.push(t);

  const nowMs = Date.now();
  const nowIndex = allTimes.reduce((acc, t, i) => t <= nowMs ? i : acc, -1);
  if (nowIndex < 2) return null;

  // Aggregate all groups into a single raw series
  const lookup = {};
  for (const b of buckets) {
    const k = new Date(b.time).getTime();
    lookup[k] = (lookup[k] || 0) + b.tokens;
  }
  const aggRaw = allTimes.map(t => lookup[t] || 0);
  const totalTokens = aggRaw.reduce((a, b) => a + b, 0);
  if (totalTokens === 0) return null;

  // Normalize to quota % — same formula as cumulative chart
  const norm = quota_pct / totalTokens;
  let running = 0;
  const cumSeries = aggRaw.map(v => { running += v; return running * norm; });

  const emaResult = _computeEma(cumSeries, nowIndex);
  if (!emaResult) return null;

  const { ema } = emaResult;
  const slope = nowIndex > 0 ? ema[nowIndex] - ema[nowIndex - 1] : 0;
  if (slope <= 0) return null;  // usage declining, won't cap

  const actualAtNow = cumSeries[nowIndex];
  if (actualAtNow >= 100) return null;  // already at limit

  const stepsToHundred = (100 - actualAtNow) / slope;
  const exceedMs = allTimes[nowIndex] + stepsToHundred * bucketMs;

  if (exceedMs >= endMs) return null;  // won't cap before window resets
  return { exceedTime: new Date(exceedMs) };
}

function updateExceedanceWarnings() {
  [
    { id: '5h', state: exceedanceState['5h'], quota: quotaState.five },
    { id: '7d', state: exceedanceState['7d'], quota: quotaState.seven },
  ].forEach(({ id, state, quota }) => {
    const el = document.getElementById('exceedance' + id);
    if (!el) return;

    if (!state || !state.exceedTime) {
      el.style.display = 'none';
      el.classList.remove('urgent');
      return;
    }

    const msUntil = state.exceedTime - Date.now();
    if (msUntil < 0) {
      // Projection already passed — chart data will update on next refresh
      el.style.display = 'none';
      el.classList.remove('urgent');
      return;
    }

    let timeStr;
    if (id === '7d') {
      timeStr = state.exceedTime.toLocaleDateString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
    } else {
      timeStr = state.exceedTime.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }
    el.textContent = 'Projected to cap ~' + timeStr;
    el.style.display = '';

    if (msUntil < 30 * 60000) {
      el.style.color = CLAUDE_RED;
      el.classList.add('urgent');
    } else {
      el.style.color = CLAUDE_AMBER;
      el.classList.remove('urgent');
    }
  });
}

function buildWindowChart(data, canvasId, maHalf) {
  const { window_start, window_end, quota_pct, bucket_minutes, buckets, value_type } = data;

  // Generate all time labels for the window
  const startMs = new Date(window_start).getTime();
  const endMs   = new Date(window_end).getTime();
  const bucketMs = bucket_minutes * 60000;
  const allTimes = [];
  for (let t = startMs; t < endMs; t += bucketMs) allTimes.push(t);

  // "Now" detection: last bucket whose timestamp <= current time
  const nowMs = Date.now();
  const nowIndex = allTimes.reduce((acc, t, i) => t <= nowMs ? i : acc, -1);

  // Collect unique groups preserving order
  const groupOrder = [];
  const seen = new Set();
  for (const b of buckets) {
    if (!seen.has(b.group)) { groupOrder.push(b.group); seen.add(b.group); }
  }
  if (groupOrder.length === 0) groupOrder.push('total');

  // Build lookup: time_label -> group -> tokens
  const lookup = {};
  for (const b of buckets) {
    const k = new Date(b.time).getTime();
    if (!lookup[k]) lookup[k] = {};
    lookup[k][b.group] = (lookup[k][b.group] || 0) + b.tokens;
  }

  // Fill arrays: allTimes x groupOrder
  const rawByGroup = {};
  for (const g of groupOrder) rawByGroup[g] = allTimes.map(t => (lookup[t] && lookup[t][g]) || 0);

  const totalTokensInWindow = groupOrder.reduce(
    (sum, g) => sum + rawByGroup[g].reduce((a, b) => a + b, 0), 0
  );

  const stacked = windowStack !== 'none';
  const isCumulative = windowView === 'cumulative';

  // X-axis labels
  const xLabels = allTimes.map(t => {
    const d = new Date(t);
    if (bucket_minutes < 60) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    // 60-min buckets (7d chart): show weekday + date + hour
    return d.toLocaleDateString([], { weekday: 'short', month: 'numeric', day: 'numeric' });
  });

  // Group colors
  const tokenTypeColors = {
    input: CLAUDE_ORANGE, cache_create: CLAUDE_AMBER,
    cache_read: '#7B9E8A', output: CLAUDE_CREAM,
    total: CLAUDE_ORANGE,
  };

  function groupColor(g, i) {
    if (windowStack === 'token_type' || windowStack === 'none') return tokenTypeColors[g] || STACK_PALETTE[i % STACK_PALETTE.length];
    return STACK_PALETTE[i % STACK_PALETTE.length];
  }

  let datasets;
  // When quota_pct is unavailable (API error), fall back to raw cumulative tokens
  const quotaAvailable = quota_pct > 0;

  if (isCumulative) {
    // Determine effective normalization factor (local quota % per token)
    // If quota_pct is 0 (API unreachable), use norm=1 so charts show raw token totals
    // instead of multiplying everything by zero and flatlining.
    const effectiveNorm = (quotaAvailable && totalTokensInWindow > 0)
        ? quota_pct / totalTokensInWindow
        : (totalTokensInWindow > 0 ? 1 : 0);

    // Running sum per group, normalized to quota contribution; truncated at nowIndex
    const norm = effectiveNorm;
    datasets = groupOrder.map((g, i) => {
      const color = groupColor(g, i);
      let running = 0;
      const cumData = rawByGroup[g].map((v, idx) => {
        running += v;
        return idx <= nowIndex ? running * norm : null;
      });
      return {
        label: g,
        data: cumData,
        backgroundColor: hexAlpha(color.startsWith('rgba') ? CLAUDE_ORANGE : color, stacked ? 0.4 : 0.3),
        borderColor: color,
        borderWidth: 1.5,
        fill: stacked ? (i === 0 ? 'origin' : '-1') : false,
        tension: 0.3,
        pointRadius: 0,
      };
    });

    // Reference line: linear 0% → 100% across window
    const refData = allTimes.map((t, i) => (i / (allTimes.length - 1 || 1)) * 100);
    datasets.push({
      label: '— pace',
      data: refData,
      borderColor: 'rgba(255,245,235,0.25)',
      borderDash: [5, 4],
      borderWidth: 1.5,
      fill: false,
      tension: 0,
      pointRadius: 0,
      order: -1,
      yAxisID: 'y2',
    });
  } else {
    // Rate view: tokens/min with moving average; truncated at nowIndex
    datasets = groupOrder.map((g, i) => {
      const color = groupColor(g, i);
      const raw = rawByGroup[g].map(v => v / bucket_minutes);
      const smoothed = _movingAverage(raw, maHalf).map((v, idx) => idx <= nowIndex ? v : null);
      return {
        label: g,
        data: smoothed,
        backgroundColor: hexAlpha(color.startsWith('rgba') ? CLAUDE_ORANGE : color, stacked ? 0.5 : 0.3),
        borderColor: color,
        borderWidth: 1.5,
        fill: stacked ? (i === 0 ? 'origin' : '-1') : false,
        tension: 0.3,
        pointRadius: 0,
      };
    });
  }

  // ── EMA Projection ───────────────────────────────────────────
  // Aggregate total across all groups for EMA input
  const aggRaw = allTimes.map((_, i) => groupOrder.reduce((s, g) => s + rawByGroup[g][i], 0));
  const emaNorm = (quotaAvailable && totalTokensInWindow > 0) ? quota_pct / totalTokensInWindow : (totalTokensInWindow > 0 ? 1 : 0);

  let emaInput;
  if (isCumulative) {
    let running = 0;
    emaInput = aggRaw.map(v => { running += v; return running * emaNorm; });
  } else {
    emaInput = _movingAverage(aggRaw.map(v => v / bucket_minutes), maHalf);
  }

  function computeEmaLocal(series, nowIdx) {
    const result = _computeEma(series, nowIdx);
    if (!result) return null;
    // Override projBuckets using bucket_minutes from this chart's closure
    result.projBuckets = Math.min(
      bucket_minutes < 60 ? Math.round(60 / bucket_minutes) : 24,
      series.length - 1 - nowIdx
    );
    return result;
  }

  const emaResult = nowIndex >= 0 ? computeEmaLocal(emaInput, nowIndex) : null;

  if (emaResult) {
    const { ema, sigma, projBuckets } = emaResult;
    const projEndIndex = nowIndex + projBuckets;
    const n = allTimes.length;
    const slope = nowIndex > 0 ? ema[nowIndex] - ema[nowIndex - 1] : 0;

    // Build projection data arrays — anchor at actual value, not EMA (EMA lags)
    const actualAtNow = emaInput[nowIndex];
    const projLineData = allTimes.map((_, i) => {
      const step = i - nowIndex;
      if (step < 0 || step > projBuckets) return null;
      if (step === 0) return actualAtNow;
      return isCumulative
        ? Math.min(Math.max(actualAtNow + slope * step, 0), 100)
        : Math.max(actualAtNow, 0);
    });

    const projUpperData = allTimes.map((_, i) => {
      const step = i - nowIndex;
      if (step < 0 || step > projBuckets) return null;
      const base = projLineData[i];
      const band = sigma * 1.5 * Math.sqrt(step);  // 0 at step=0, fans out
      return isCumulative ? Math.min(base + band, 100) : base + band;
    });

    const projLowerData = allTimes.map((_, i) => {
      const step = i - nowIndex;
      if (step < 0 || step > projBuckets) return null;
      const base = projLineData[i];
      const band = sigma * 1.5 * Math.sqrt(step);  // 0 at step=0, fans out
      return isCumulative ? Math.max(base - band, actualAtNow) : Math.max(base - band, 0);
    });

    // Gradient factories using scriptable context (chart area available after layout)
    const PROJ_RGB = '123,158,184';
    const makeGrad = (startA, endA) => (ctx) => {
      const { chart } = ctx;
      const { chartArea } = chart;
      if (!chartArea) return `rgba(${PROJ_RGB},${startA})`;
      const pct0 = nowIndex / Math.max(n - 1, 1);
      const pct1 = projEndIndex / Math.max(n - 1, 1);
      const xStart = chartArea.left + pct0 * chartArea.width;
      const xEnd   = chartArea.left + pct1 * chartArea.width;
      const g = chart.ctx.createLinearGradient(xStart, 0, xEnd, 0);
      g.addColorStop(0, `rgba(${PROJ_RGB},${startA})`);
      g.addColorStop(1, `rgba(${PROJ_RGB},${endA})`);
      return g;
    };

    // "Now" glowing dot — single point at the aggregate value at nowIndex
    datasets.push({
      label: 'now-dot',
      data: allTimes.map((_, i) => i === nowIndex ? emaInput[nowIndex] : null),
      borderColor: 'rgba(250,240,230,0.7)',
      backgroundColor: CLAUDE_ORANGE,
      borderWidth: 1.5,
      fill: false,
      tension: 0,
      pointRadius: allTimes.map((_, i) => i === nowIndex ? 5 : 0),
      pointHoverRadius: allTimes.map((_, i) => i === nowIndex ? 5 : 0),
      pointBackgroundColor: CLAUDE_ORANGE,
      pointBorderColor: 'rgba(250,240,230,0.7)',
      pointBorderWidth: 1.5,
      stack: 'proj-dot',
    });

    // Confidence band: upper fills down to lower (each in own stack group to avoid y-accumulation)
    datasets.push({
      label: 'proj-upper',
      data: projUpperData,
      borderWidth: 0,
      borderColor: 'transparent',
      backgroundColor: makeGrad(0.12, 0),
      fill: '+1',
      pointRadius: 0,
      tension: 0.3,
      stack: 'proj-u',
    });
    datasets.push({
      label: 'proj-lower',
      data: projLowerData,
      borderWidth: 0,
      borderColor: 'transparent',
      backgroundColor: 'transparent',
      fill: false,
      pointRadius: 0,
      tension: 0.3,
      stack: 'proj-l',
    });

    // Projection line (drawn on top, starts at ema[nowIndex] to connect with now-dot)
    datasets.push({
      label: 'projection',
      data: projLineData,
      borderColor: makeGrad(0.55, 0),
      borderWidth: 1.5,
      borderDash: [6, 4],
      fill: false,
      pointRadius: 0,
      tension: 0.3,
      stack: 'proj-c',
    });
  }

  const isCostWeighted = value_type === 'cost';
  const yLabel = isCumulative
      ? (quotaAvailable ? '% of quota' : 'tokens (quota unavailable)')
      : (isCostWeighted ? '$ / min' : 'tokens / min');
  const yMax = (isCumulative && quotaAvailable) ? 100 : undefined;
  const projLabels = new Set(['projection', 'proj-upper', 'proj-lower', 'now-dot']);

  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  return new Chart(ctx, {
    type: 'line',
    data: { labels: xLabels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top', align: 'end',
          labels: { boxWidth: 8, boxHeight: 8, padding: 12, font: { size: 10 },
            filter: item => {
              if (projLabels.has(item.text)) return false;
              return item.text !== '— pace' || isCumulative;
            },
          },
        },
        tooltip: {
          callbacks: {
            title: (items) => {
              const t = allTimes[items[0].dataIndex];
              const d = new Date(t);
              if (bucket_minutes >= 60) {
                return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
                  + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
              }
              return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            },
            label: ctx => {
              const lbl = ctx.dataset.label;
              if (lbl === 'proj-upper' || lbl === 'proj-lower' || lbl === 'now-dot') return null;
              if (lbl === '— pace') return ` Pace: ${ctx.parsed.y.toFixed(1)}%`;
              const cumFmt = v => (isCumulative && quotaAvailable) ? `${v.toFixed(1)}%` : fmtShort(v);
              const rateFmt = v => isCostWeighted ? `$${v.toFixed(4)}/min` : fmtShort(v) + '/min';
              if (lbl === 'projection') {
                const val = isCumulative ? cumFmt(ctx.parsed.y) : rateFmt(ctx.parsed.y);
                return ` ~ projected: ${val}`;
              }
              const val = isCumulative ? cumFmt(ctx.parsed.y) : rateFmt(ctx.parsed.y);
              return ` ${lbl}: ${val}`;
            },
          }
        }
      },
      scales: {
        x: {
          stacked,
          ticks: { maxTicksLimit: bucket_minutes < 60 ? 10 : 7, font: { size: 9, family: "'DM Mono'" } },
          grid: { display: false },
        },
        y: {
          stacked,
          max: yMax,
          min: 0,
          ticks: {
            callback: v => (isCumulative && quotaAvailable) ? v + '%' : isCostWeighted ? `$${v.toFixed(3)}` : fmtShort(v),
            font: { size: 10 },
          },
          grid: { color: 'rgba(255,245,235,0.04)' },
          title: { display: true, text: yLabel, color: 'rgba(245,237,228,0.3)', font: { size: 9 } },
        },
        y2: {
          display: false,
          min: 0,
          max: 100,
          stacked: false,
        },
      }
    }
  });
}

// ─── Projects chart ──────────────────────────────────────────
async function loadProjects() {
  const data = await apiFetch('/api/projects?days=90');
  if (projectChart) projectChart.destroy();
  const ctx = document.getElementById('projectChart').getContext('2d');
  const labels = data.map(r => r.project);
  const values = data.map(r => r.total_tokens);
  const maxVal = Math.max(...values, 1);

  projectChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: values.map((v, i) => hexAlpha(CLAUDE_ORANGE, 0.25 + 0.6 * (v / maxVal))),
        borderColor: values.map((v, i) => hexAlpha(CLAUDE_ORANGE, 0.5 + 0.5 * (v / maxVal))),
        borderWidth: 1,
        borderRadius: 4,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: ctx => ` ${fmt(ctx.parsed.x)} tokens` }
        }
      },
      scales: {
        x: { ticks: { callback: v => fmtShort(v), font: { size: 10 } }, grid: { color: 'rgba(255,245,235,0.04)' } },
        y: { ticks: { font: { size: 10, family: "'DM Mono'" } }, grid: { display: false } },
      }
    }
  });
}

// ─── Models chart ────────────────────────────────────────────
async function loadModels() {
  const data = await apiFetch('/api/models?days=90');
  if (modelChart) modelChart.destroy();
  const ctx = document.getElementById('modelChart').getContext('2d');

  const labels = data.map(r => shortModelName(r.model));
  const values = data.map(r => r.total_tokens);
  const colors = data.map(r => modelColor(r.model));

  modelChart = new Chart(ctx, {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderColor: 'rgba(17,16,16,0.5)', borderWidth: 2, hoverOffset: 8 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, padding: 10, font: { size: 10 } } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${fmt(ctx.parsed)} tokens (${Math.round(100 * ctx.parsed / values.reduce((a,b)=>a+b,0))}%)`,
          }
        }
      },
    }
  });
}

function shortModelName(model) {
  if (!model) return 'Unknown';
  if (model.includes('opus')) return 'Opus';
  if (model.includes('haiku')) return 'Haiku';
  if (model.includes('sonnet')) return 'Sonnet';
  return model.split('-').slice(-2).join('-');
}

function modelColor(model) {
  if (!model) return CLAUDE_CREAM;
  if (model.includes('opus'))   return CLAUDE_PURPLE;
  if (model.includes('haiku'))  return CLAUDE_AMBER;
  if (model.includes('sonnet')) return CLAUDE_ORANGE;
  return CLAUDE_CREAM;
}

// ─── Heatmap ─────────────────────────────────────────────────
function setHeatmapDays(days, btn) {
  document.querySelectorAll('#heatmapDaySelector .day-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadHeatmap(days);
}

async function loadHeatmap(days = 90) {
  const data = await apiFetch('/api/heatmap?days=' + days);

  // Build lookup: dow -> hour -> tokens
  const grid = {};
  let maxTokens = 0;
  for (const cell of data) {
    const { day_of_week, hour, total_tokens } = cell;
    if (!grid[day_of_week]) grid[day_of_week] = {};
    grid[day_of_week][hour] = total_tokens;
    if (total_tokens > maxTokens) maxTokens = total_tokens;
  }

  const wrap = document.getElementById('heatmapWrap');
  const gridEl = document.createElement('div');
  gridEl.className = 'heatmap-grid';

  // Corner empty cell
  const corner = document.createElement('div');
  gridEl.appendChild(corner);

  // Hour labels (0-23, show every 4)
  for (let h = 0; h < 24; h++) {
    const lbl = document.createElement('div');
    lbl.className = 'heatmap-hour-label';
    lbl.textContent = h % 4 === 0 ? (h === 0 ? '12a' : h < 12 ? h+'a' : h === 12 ? '12p' : (h-12)+'p') : '';
    gridEl.appendChild(lbl);
  }

  // Day rows
  for (let dow = 0; dow < 7; dow++) {
    const dayLbl = document.createElement('div');
    dayLbl.className = 'heatmap-day-label';
    dayLbl.textContent = DAY_LABELS[dow];
    gridEl.appendChild(dayLbl);

    for (let h = 0; h < 24; h++) {
      const tokens = (grid[dow] && grid[dow][h]) || 0;
      const intensity = maxTokens > 0 ? tokens / maxTokens : 0;
      const cell = document.createElement('div');
      cell.className = 'heatmap-cell';
      cell.style.background = heatmapColor(intensity);
      if (tokens > 0) {
        cell.title = `${DAY_LABELS[dow]} ${formatHour(h)} — ${fmt(tokens)} tokens`;
      }
      gridEl.appendChild(cell);
    }
  }

  wrap.innerHTML = '';
  wrap.appendChild(gridEl);
}

function heatmapColor(t) {
  if (t <= 0) return 'rgba(255,245,235,0.05)';
  // Map 0→1 through orange palette
  const alpha = 0.12 + 0.7 * t;
  const r = Math.round(224 + (217 - 224) * t);
  const g = Math.round(122 + (95 - 122) * t);
  const b = Math.round(95 + (95 - 95) * t);
  return `rgba(${r},${g},${b},${alpha.toFixed(2)})`;
}

function formatHour(h) {
  if (h === 0) return '12am';
  if (h < 12) return h + 'am';
  if (h === 12) return '12pm';
  return (h - 12) + 'pm';
}

// ─── Sessions ────────────────────────────────────────────────
async function loadSessions(days) {
  const data = await apiFetch('/api/sessions?days=' + days);
  const list = document.getElementById('sessionList');
  list.innerHTML = '';

  if (!data.length) {
    list.innerHTML = '<p style="color:var(--text-muted);font-size:0.8rem;padding:16px 0">No sessions found</p>';
    return;
  }

  for (const s of data) {
    const row = document.createElement('div');
    row.className = 'session-row';
    row.onclick = () => openPanel(s);

    const modelClass = s.model.includes('opus') ? 'opus' : s.model.includes('haiku') ? 'haiku' : '';
    const dot = `<div class="session-dot ${modelClass}"></div>`;

    const startDt = new Date(s.start_time);
    const endDt = new Date(s.end_time);
    const durationMin = Math.max(1, Math.round((endDt - startDt) / 60000));

    const sourceBadge = s.source === 'claude-desktop'
      ? '<span class="session-badge badge-desktop">Desktop</span>'
      : '';
    const entrypointBadge = s.entrypoint && s.entrypoint.includes('vscode')
      ? '<span class="session-badge badge-vscode">VS Code</span>'
      : s.entrypoint && !s.entrypoint.includes('vscode') && s.entrypoint !== 'desktop' && s.entrypoint !== ''
        ? '<span class="session-badge badge-cli">CLI</span>'
        : '';
    const speedBadge = s.speed === 'fast'
      ? '<span class="session-badge badge-fast">fast</span>'
      : '';

    row.innerHTML = `
      ${dot}
      <div class="session-info">
        <div class="session-project">${escHtml(s.project)}</div>
        <div class="session-time">${startDt.toLocaleDateString()} ${startDt.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})} · ${durationMin}m · ${s.message_count} msgs${s.git_branch ? ' · ' + escHtml(s.git_branch) : ''}</div>
      </div>
      <div class="session-badges">${sourceBadge}${entrypointBadge}${speedBadge}</div>
      <div class="session-tokens">${fmtShort(s.total_tokens)}</div>
      <div class="session-model ${modelClass ? modelClass+'-model' : ''}">${shortModelName(s.model)}</div>
    `;
    list.appendChild(row);
  }
}

function setSessionDays(days, btn) {
  currentSessionDays = days;
  document.querySelectorAll('.session-filter .day-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadSessions(days);
}

// ─── Session detail panel ─────────────────────────────────────
async function openPanel(session) {
  const msgs = await apiFetch('/api/session/' + session.session_id);

  document.getElementById('panelTitle').textContent = session.project;
  const startDt = new Date(session.start_time);
  document.getElementById('panelMeta').textContent =
    `${startDt.toLocaleString()} · ${session.message_count} messages · ${fmt(session.total_tokens)} tokens · ${shortModelName(session.model)}`;

  if (sessionDetailChart) sessionDetailChart.destroy();
  const ctx = document.getElementById('sessionDetailChart').getContext('2d');
  const labels = msgs.map((_, i) => `Msg ${i+1}`);

  sessionDetailChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Input',        data: msgs.map(m => m.input_tokens),          backgroundColor: hexAlpha(CLAUDE_ORANGE, 0.6), borderRadius: 2 },
        { label: 'Cache Create', data: msgs.map(m => m.cache_creation_tokens), backgroundColor: hexAlpha(CLAUDE_AMBER, 0.5),  borderRadius: 2 },
        { label: 'Cache Read',   data: msgs.map(m => m.cache_read_tokens),     backgroundColor: hexAlpha('#7B9E8A', 0.5),     borderRadius: 2 },
        { label: 'Output',       data: msgs.map(m => m.output_tokens),         backgroundColor: hexAlpha(CLAUDE_CREAM, 0.4),  borderRadius: 2 },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 8, boxHeight: 8, padding: 12, font: { size: 10 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}` } }
      },
      scales: {
        x: { stacked: true, ticks: { font: { size: 9 } }, grid: { display: false } },
        y: { stacked: true, ticks: { callback: v => fmtShort(v), font: { size: 10 } }, grid: { color: 'rgba(255,245,235,0.04)' } },
      }
    }
  });

  document.getElementById('sessionPanel').classList.add('open');
  document.getElementById('panelOverlay').classList.add('open');
}

function closePanel() {
  document.getElementById('sessionPanel').classList.remove('open');
  document.getElementById('panelOverlay').classList.remove('open');
}

// ─── Cost ────────────────────────────────────────────────────
async function loadCost() {
  const data = await apiFetch('/api/cost?days=30');
  document.getElementById('costPeriod').textContent = data.days + ' days';
  document.getElementById('costAmount').textContent = '$' + data.total_cost.toFixed(2);

  const breakdown = document.getElementById('costBreakdown');
  breakdown.innerHTML = '';
  for (const row of data.breakdown) {
    const div = document.createElement('div');
    div.className = 'cost-row';
    div.innerHTML = `
      <span class="cost-model">${shortModelName(row.model)}</span>
      <span class="cost-model-val">$${row.cost.toFixed(2)}</span>
    `;
    breakdown.appendChild(div);
  }
}

// ─── Rate ────────────────────────────────────────────────────
async function loadRate() {
  rateData = await apiFetch('/api/rate?hours=3');
}

// ─── Utilities ───────────────────────────────────────────────
async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
}

function fmt(n) {
  if (n == null || isNaN(n)) return '0';
  return Math.round(n).toLocaleString();
}

function fmtShort(n) {
  if (n == null || isNaN(n)) return '0';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(0) + 'k';
  return String(Math.round(n));
}

function hexAlpha(hex, alpha) {
  // Convert hex color to rgba
  const r = parseInt(hex.slice(1,3), 16);
  const g = parseInt(hex.slice(3,5), 16);
  const b = parseInt(hex.slice(5,7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function lerpColor(a, b, t) {
  const ra = parseInt(a.slice(1,3),16), ga = parseInt(a.slice(3,5),16), ba = parseInt(a.slice(5,7),16);
  const rb = parseInt(b.slice(1,3),16), gb = parseInt(b.slice(3,5),16), bb = parseInt(b.slice(5,7),16);
  const r = Math.round(ra + (rb-ra)*t);
  const g = Math.round(ga + (gb-ga)*t);
  const bv = Math.round(ba + (bb-ba)*t);
  return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${bv.toString(16).padStart(2,'0')}`;
}

function escHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
