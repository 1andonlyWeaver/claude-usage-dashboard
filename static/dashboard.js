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

const ARC_TOTAL      = 330;   // total arc length (out of 440 circumference for 270° arc)
const DAY_LABELS     = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

// Chart.js global defaults
Chart.defaults.color = 'rgba(245,237,228,0.5)';
Chart.defaults.borderColor = 'rgba(255,245,235,0.07)';
Chart.defaults.font.family = "'DM Sans', sans-serif";

// ─── State ───────────────────────────────────────────────────
let dailyChart = null, projectChart = null, modelChart = null, sessionDetailChart = null;
let currentDailyDays = 30;
let currentSessionDays = 7;
let rateData = null;

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
    loadDaily(currentDailyDays),
    loadProjects(),
    loadModels(),
    loadHeatmap(),
    loadSessions(currentSessionDays),
    loadCost(),
    loadRate(),
  ]);
}

// ─── Quota polling ───────────────────────────────────────────
function startQuotaPolling() {
  fetchQuota();
  setInterval(fetchQuota, 5000);
  setInterval(updateCountdowns, 1000);
}

let quotaState = { five: null, seven: null };

async function fetchQuota() {
  try {
    const data = await apiFetch('/api/quota');
    if (data.error) return;
    quotaState = {
      five: { pct: data.five_hour_pct, resetsAt: new Date(data.five_hour_resets_at) },
      seven: { pct: data.seven_day_pct, resetsAt: new Date(data.seven_day_resets_at) },
    };
    updateGauge('5h', quotaState.five.pct);
    updateGauge('7d', quotaState.seven.pct);
    updateForecasts();
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

function updateCountdowns() {
  if (quotaState.five) {
    document.getElementById('countdown5h').textContent = 'Resets ' + formatCountdown(quotaState.five.resetsAt);
  }
  if (quotaState.seven) {
    document.getElementById('countdown7d').textContent = 'Resets ' + formatCountdown(quotaState.seven.resetsAt);
  }
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

function formatCountdown(date) {
  const ms = date - new Date();
  if (ms <= 0) return 'now';
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d > 1) return `in ${d}d ${h % 24}h`;
  if (h > 0) return `in ${h}h ${m % 60}m`;
  return `in ${m}m`;
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

// ─── Daily chart ─────────────────────────────────────────────
async function loadDaily(days) {
  const data = await apiFetch('/api/daily?days=' + days);
  const labels = data.map(r => r.date);
  const datasets = [
    { label: 'Input',         data: data.map(r => r.input_tokens),          backgroundColor: hexAlpha(CLAUDE_ORANGE, 0.55), borderColor: CLAUDE_ORANGE,   borderWidth: 1.5, fill: true },
    { label: 'Cache Create',  data: data.map(r => r.cache_creation_tokens),  backgroundColor: hexAlpha(CLAUDE_AMBER, 0.4),  borderColor: CLAUDE_AMBER,    borderWidth: 1.5, fill: true },
    { label: 'Cache Read',    data: data.map(r => r.cache_read_tokens),       backgroundColor: hexAlpha('#7B9E8A', 0.4),     borderColor: '#7B9E8A',       borderWidth: 1.5, fill: true },
    { label: 'Output',        data: data.map(r => r.output_tokens),           backgroundColor: hexAlpha(CLAUDE_CREAM, 0.25), borderColor: CLAUDE_CREAM,    borderWidth: 1.5, fill: true },
  ];

  if (dailyChart) { dailyChart.destroy(); }
  const ctx = document.getElementById('dailyChart').getContext('2d');
  dailyChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', align: 'end', labels: { boxWidth: 10, boxHeight: 10, padding: 16, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`,
            footer: items => ` Total: ${fmt(items.reduce((s, i) => s + i.parsed.y, 0))}`,
          }
        }
      },
      scales: {
        x: { stacked: true, ticks: { maxTicksLimit: 12, font: { size: 10, family: "'DM Mono'" } }, grid: { display: false } },
        y: { stacked: true, ticks: { callback: v => fmtShort(v), font: { size: 10 } }, grid: { color: 'rgba(255,245,235,0.04)' } },
      }
    }
  });
}

function setDailyDays(days, btn) {
  currentDailyDays = days;
  document.querySelectorAll('.day-selector .day-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadDaily(days);
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
async function loadHeatmap() {
  const data = await apiFetch('/api/heatmap?days=90');

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

    row.innerHTML = `
      ${dot}
      <div class="session-info">
        <div class="session-project">${escHtml(s.project)}</div>
        <div class="session-time">${startDt.toLocaleDateString()} ${startDt.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})} · ${durationMin}m · ${s.message_count} msgs</div>
      </div>
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
