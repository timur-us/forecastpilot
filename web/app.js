/* ForecastPilot web UI — vanilla JS, no build step.
 * Talks to the API on Azure; GitHub Pages serves this file so the page is up
 * even while the container behind the API is still waking up.
 */
'use strict';

const API_BASE = 'https://forecastpilot.mangotree-f644cfcc.germanywestcentral.azurecontainerapps.io';
const HISTORY_POINTS = 60;
const HEALTH_POLL_MS = 1500;
const HEALTH_TIMEOUT_MS = 90000;
const FORECAST_TIMEOUT_MS = 120000;

/* ?api=http://127.0.0.1:8000 overrides the API for local development only. */
function apiBase() {
  try {
    const here = location.hostname;
    if (here !== '127.0.0.1' && here !== 'localhost') return API_BASE;
    const raw = new URLSearchParams(location.search).get('api');
    if (!raw) return API_BASE;
    const u = new URL(raw);
    const local = u.protocol === 'http:' && (u.hostname === '127.0.0.1' || u.hostname === 'localhost');
    return local ? u.origin : API_BASE;
  } catch (_) {
    return API_BASE;
  }
}
const API = apiBase();

const $ = (id) => document.getElementById(id);
const STATES = ['idle', 'waking', '429', 'result'];
/* Two phases the page can actually observe: the health probe, then the
 * one request that does everything else. */
const PHASE_1 = 'Starting the container';
function phase2Label(ticker, withCommentary) {
  const work = withCommentary
    ? 'loading prices, fitting the model, writing and checking the commentary'
    : 'loading prices and fitting the model';
  return `Working on ${ticker} · one request: ${work}`;
}

const form = {
  ticker: $('ticker'),
  horizon: $('horizon'),
  commentary: $('commentary'),
  lang: 'en',
};
let run = null; // the in-flight request: {controller, timer, startedAt, ...}
let lastResult = null;

/* ---------- state switching ---------- */
function show(state) {
  for (const s of STATES) $('state-' + s).hidden = s !== state;
  window.scrollTo({ top: 0, behavior: 'auto' });
  if (state === 'idle') $('ticker').focus({ preventScroll: true });
  else $('state-' + state).focus({ preventScroll: true });
}
function announce(text) { $('live').textContent = text; }

/* ---------- form ---------- */
for (const b of document.querySelectorAll('.fp-seg button')) {
  b.addEventListener('click', () => {
    form.lang = b.dataset.lang;
    for (const o of document.querySelectorAll('.fp-seg button')) o.setAttribute('aria-pressed', String(o === b));
  });
}
$('form').addEventListener('submit', (e) => { e.preventDefault(); start(form.commentary.checked); });
$('cancel').addEventListener('click', () => { abort(); show('idle'); });
$('back').addEventListener('click', () => show('idle'));
$('new-forecast').addEventListener('click', () => show('idle'));
$('without-commentary').addEventListener('click', () => { form.commentary.checked = false; start(false); });

function showError(title, text) {
  $('error-title').textContent = title;
  $('error-text').textContent = text;
  $('error').hidden = false;
  show('idle');
}
function clearError() { $('error').hidden = true; }

/* ---------- request flow: poll /health, then /forecast ---------- */
function abort() {
  if (!run) return;
  run.controller.abort();
  clearInterval(run.timer);
  run = null;
}

async function start(withCommentary) {
  clearError();
  const ticker = form.ticker.value.trim().toUpperCase();
  if (!/^[A-Z0-9.^=-]{1,16}$/.test(ticker)) {
    showError('Check the ticker.', 'Use a Yahoo Finance symbol such as NVDA, SAP.DE or ^GDAXI.');
    return;
  }
  form.ticker.value = ticker;
  const horizon = Number(form.horizon.value);
  const lang = form.lang;
  abort();
  const controller = new AbortController();
  run = {
    controller,
    startedAt: performance.now(),
    requestSentAt: null,
    withCommentary,
    timer: null,
  };
  $('waking-tag').textContent = `${ticker} · ${horizon} trading days`;
  $('phase2-label').textContent = phase2Label(ticker, withCommentary);
  renderWaking(ticker);
  run.timer = setInterval(() => renderWaking(ticker), 250);
  show('waking');
  announce(`Working on ${ticker}.`);

  try {
    await waitForHealth(controller.signal);
    if (controller.signal.aborted) return;
    run.requestSentAt = performance.now();
    const params = new URLSearchParams({ horizon: String(horizon), commentary: String(withCommentary), language: lang });
    const res = await fetchWithTimeout(`${API}/forecast/${encodeURIComponent(ticker)}?${params}`, controller.signal, FORECAST_TIMEOUT_MS);
    if (controller.signal.aborted) return;
    const headers = res.headers;
    let body = null;
    try { body = await res.json(); } catch (_) { body = null; }
    clearInterval(run.timer);
    run = null;
    if (res.status === 429) { renderRateLimited(headers, withCommentary); return; }
    if (!res.ok) { renderHttpError(res.status, body, withCommentary); return; }
    lastResult = { body, headers: pickHeaders(headers), ticker, horizon, lang, withCommentary, receivedAt: new Date() };
    renderResult(lastResult);
    show('result');
    announce(`Forecast for ${ticker} is ready.`);
  } catch (err) {
    if (controller.signal.aborted) return;
    clearInterval(run && run.timer);
    run = null;
    if (err && err.name === 'HealthTimeout') {
      showError('The service did not wake up in time.', 'Please try again in a minute.');
    } else {
      showError('Could not reach the API.', 'Check your connection and try again. If the problem persists the service may be down.');
    }
  }
}

async function fetchWithTimeout(url, signal, ms) {
  const timeout = new AbortController();
  const t = setTimeout(() => timeout.abort(), ms);
  const onAbort = () => timeout.abort();
  signal.addEventListener('abort', onAbort, { once: true });
  try {
    return await fetch(url, { signal: timeout.signal, headers: { Accept: 'application/json' } });
  } finally {
    clearTimeout(t);
    signal.removeEventListener('abort', onAbort);
  }
}

async function waitForHealth(signal) {
  const deadline = performance.now() + HEALTH_TIMEOUT_MS;
  while (!signal.aborted) {
    try {
      const r = await fetchWithTimeout(`${API}/health`, signal, 8000);
      if (r.ok) return;
    } catch (_) { /* container still starting */ }
    if (performance.now() > deadline) { const e = new Error('health timeout'); e.name = 'HealthTimeout'; throw e; }
    await new Promise((resolve) => setTimeout(resolve, HEALTH_POLL_MS));
  }
}

/* Phase 1 is the health probe; phase 2 is the single forecast request.
 * Nothing in between can be observed, so nothing is simulated. */
function renderWaking(ticker) {
  if (!run) return;
  const elapsed = (performance.now() - run.startedAt) / 1000;
  const phase = run.requestSentAt === null ? 0 : 1;
  document.querySelectorAll('#steps .fp-step').forEach((el, i) => {
    const state = i < phase ? 'done' : i === phase ? 'active' : 'pending';
    el.dataset.state = state;
    el.querySelector('.fp-step-sr').textContent = state === 'done' ? 'done' : state === 'active' ? 'in progress' : 'pending';
  });
  $('meter').querySelectorAll('span').forEach((s, i) => {
    s.classList.toggle('on', i < phase);
    s.classList.toggle('active', i === phase);
  });
  $('progress').value = phase;
  $('waking-title').textContent = phase === 0 ? 'Waking the service…' : `Working on ${ticker}…`;
  $('waking-hint').textContent = phase === 0
    ? 'The container was asleep to save cost. Starting it takes about 20 seconds; this page shows what happens while you wait.'
    : 'One request loads the prices, fits the model and, if asked, writes and checks the commentary. The page has nothing to show until it returns.';
  const label = phase === 0 ? PHASE_1 : `Working on ${ticker}`;
  $('progress-caption').textContent = `Phase ${phase + 1} of 2 · ${label} · ${Math.floor(elapsed)} s`;
}

/* ---------- errors ---------- */
function renderHttpError(status, body, withCommentary) {
  const detail = body && typeof body.detail === 'string' ? body.detail : '';
  if (status === 404) return showError('Unknown ticker.', detail || 'No market data was found for that symbol.');
  if (status === 422) {
    const msg = detail || (body && Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join('; ') : '') || 'The request was rejected.';
    return showError('Not enough data.', msg);
  }
  // The API explains commentary failures in `detail`; an ingress 502/503 has none.
  if (withCommentary && detail && status === 503) return showError('Commentary is not configured on the server.', detail);
  if (withCommentary && detail && status === 502) return showError('The commentary could not be generated.', `${detail} — the forecast itself is unaffected; try again or switch commentary off.`);
  if (status >= 500) return showError(`The API answered with HTTP ${status}.`, detail || 'Try again in a minute.');
  return showError(`The API answered with HTTP ${status}.`, detail || '');
}

function pickHeaders(h) {
  return {
    limit: num(h.get('x-ratelimit-limit')),
    remaining: num(h.get('x-ratelimit-remaining')),
    reset: num(h.get('x-ratelimit-reset')),
    retryAfter: num(h.get('retry-after')),
  };
}
function num(v) { const n = v === null ? NaN : Number(v); return Number.isFinite(n) ? n : null; }

function renderRateLimited(headers, withCommentary) {
  const h = pickHeaders(headers);
  const limit = h.limit ?? (withCommentary ? 5 : 30);
  const kind = withCommentary ? 'commentary' : 'forecast';
  $('limit-title').textContent = `You have used your ${limit} ${kind} requests for this hour`;
  const big = $('quota-big');
  big.replaceChildren(...Array.from({ length: Math.min(limit, 10) }, () => document.createElement('span')));
  big.hidden = limit > 10;
  const minutes = resetMinutes(h);
  const resets = minutes === null ? 'within the hour' : minutes <= 1 ? 'in about a minute' : `in ${minutes} minutes`;
  $('limit-text').textContent = withCommentary
    ? `Each commentary is a paid call to Claude, so it is limited to ${limit} per hour per visitor. Plain forecasts are limited to 30 per hour. Your limit resets ${resets}.`
    : `Plain forecasts are limited to ${limit} per hour per visitor. Your limit resets ${resets}.`;
  $('without-commentary').hidden = !withCommentary;
  show('429');
  announce(`Hourly limit reached. It resets ${resets}.`);
}
function resetMinutes(h) {
  let seconds = null;
  if (h.reset !== null) seconds = h.reset - Date.now() / 1000;
  if (seconds === null && h.retryAfter !== null) seconds = h.retryAfter;
  if (seconds === null) return null;
  return Math.max(0, Math.ceil(seconds / 60));
}

/* ---------- result ---------- */
const fmt2 = (v) => Number(v).toFixed(2);
const fmtPct = (v) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)} %`;
const finite = (v) => (v === null || v === undefined || v === '' ? NaN : Number(v));

function renderResult(r) {
  const b = r.body;
  const c = b.commentary;
  const hist = Array.isArray(b.history) ? b.history : [];
  const fc = b.forecast;
  const m = b.metrics || {};
  const last = finite(b.last_close);
  const end = finite(fc[fc.length - 1]);

  $('r-ticker').textContent = b.ticker;
  $('r-horizon').textContent = String(b.horizon_days);
  $('r-lang').textContent = c ? (r.lang === 'de' ? 'German' : 'English') : 'off';
  $('r-lang-in').hidden = !c;
  const gen = c && c.generated_at ? new Date(c.generated_at) : r.receivedAt;
  $('r-generated').textContent = fmtUtc(gen);

  $('k-last').textContent = fmt2(last);
  $('k-h').textContent = String(b.horizon_days);
  $('k-fc').textContent = fmt2(end);
  $('k-pct').textContent = fmtPct((end / last - 1) * 100);

  // Short series (fewer than twice the horizon) come back without a backtest.
  const maeModel = finite(m.mae_model), maeNaive = finite(m.mae_naive), mape = finite(m.mape_model_pct);
  const hasBacktest = Number.isFinite(maeModel) && Number.isFinite(maeNaive);
  const beats = hasBacktest && m.beats_naive === true;
  $('k-mae').textContent = hasBacktest ? fmt2(maeModel) : '–';
  $('k-naive').textContent = hasBacktest ? fmt2(maeNaive) : '–';
  $('bar').hidden = !hasBacktest;
  if (hasBacktest) {
    const scale = Math.max(maeModel, maeNaive) * 1.25 || 1;
    const modelPct = (maeModel / scale) * 100, naivePct = (maeNaive / scale) * 100;
    const fill = $('bar-fill'), excess = $('bar-excess');
    fill.style.width = `${(beats ? modelPct : naivePct).toFixed(2)}%`;
    fill.classList.toggle('miss', !beats);
    excess.hidden = beats;
    if (!beats) { excess.style.left = `${naivePct.toFixed(2)}%`; excess.style.width = `${Math.max(0, modelPct - naivePct).toFixed(2)}%`; }
    $('bar-mark').style.left = `${naivePct.toFixed(2)}%`;
  }
  const v = $('verdict');
  v.classList.toggle('ok', beats); v.classList.toggle('warn', hasBacktest && !beats); v.classList.toggle('none', !hasBacktest);
  $('verdict-title').textContent = !hasBacktest ? 'No backtest for this horizon' : beats ? 'Beats the naive baseline' : 'Does not beat the naive baseline';
  const margin = hasBacktest ? Math.abs(maeNaive - maeModel) / maeNaive : 0;
  $('verdict-sub').textContent = !hasBacktest
    ? 'Too little history to hold this many days out; the forecast is shown without a verdict.'
    : beats
      ? (margin < 0.1 ? 'Narrow margin: treat with care.' : 'Lower backtest error than the naive baseline.')
      : 'Treat this forecast as a weak signal.';

  renderChart(hist, fc, last, b.horizon_days);
  $('c-h').textContent = String(b.horizon_days);
  $('x-left').textContent = `${hist.length || HISTORY_POINTS} trading days ago`;
  $('x-right').textContent = `+${b.horizon_days} d`;
  $('note-backtest').hidden = !hasBacktest;
  $('note-nobacktest').hidden = hasBacktest;
  $('n-h').textContent = String(m.backtest_horizon ?? b.horizon_days);
  $('n-mape').textContent = Number.isFinite(mape) ? `${mape.toFixed(2)} %` : '–';

  renderCommentary(c, r.lang, b.ticker);
  renderHood(c, r.headers, r.withCommentary);
}

function fmtUtc(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
}

/* Chart: inline SVG, viewBox 0 0 1000 240, non-scaling strokes; labels are
 * HTML overlays positioned in percent so the SVG can stretch. */
function renderChart(hist, fc, last, horizon) {
  const histVals = hist.map((p) => Number(p.close));
  const series = histVals.length ? histVals : [last];
  const all = series.concat(fc.map(Number));
  const n = series.length + fc.length;
  const lo0 = Math.min(...all), hi0 = Math.max(...all);
  const pad = Math.max((hi0 - lo0) * 0.08, Math.abs(hi0) * 0.002, 1e-6);
  const ticks = niceTicks(lo0 >= 0 ? Math.max(0, lo0 - pad) : lo0 - pad, hi0 + pad, 8);
  const lo = ticks.lo, hi = ticks.hi;
  const yTop = 16, yBot = 240;
  const X = (i) => (i * 1000) / (n - 1);
  const Y = (v) => yBot - ((v - lo) / (hi - lo)) * (yBot - yTop);
  const seg = (arr, offset) => arr.map((v, i) => (i === 0 ? 'M' : 'L') + X(offset + i).toFixed(1) + ' ' + Y(v).toFixed(1)).join(' ');

  const grid = $('grid');
  grid.replaceChildren();
  const yaxis = $('yaxis');
  yaxis.replaceChildren();
  for (const t of ticks.values) {
    const y = Y(t);
    if (y < 4 || y > 236) continue;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('class', 'fp-grid');
    line.setAttribute('x1', '0'); line.setAttribute('x2', '1000');
    line.setAttribute('y1', y.toFixed(1)); line.setAttribute('y2', y.toFixed(1));
    grid.appendChild(line);
    const label = document.createElement('span');
    label.textContent = ticks.format(t);
    label.style.top = `${y.toFixed(1)}px`;
    yaxis.appendChild(label);
  }
  $('hist-path').setAttribute('d', seg(series, 0));
  $('fc-path').setAttribute('d', seg([series[series.length - 1]].concat(fc.map(Number)), series.length - 1));
  const todayX = X(series.length - 1), todayY = Y(series[series.length - 1]), endY = Y(Number(fc[fc.length - 1]));
  const tl = $('today-line');
  tl.setAttribute('x1', todayX.toFixed(1)); tl.setAttribute('x2', todayX.toFixed(1));
  const todayPct = todayX / 10;
  $('today-label').style.left = `calc(${todayPct.toFixed(2)}% + 6px)`;
  const lastTag = $('last-tag');
  lastTag.textContent = fmt2(last);
  lastTag.style.top = `${(todayY - 10).toFixed(1)}px`;
  lastTag.style.right = `calc(${(100 - todayPct).toFixed(2)}% + 10px)`;
  const dot = $('today-dot');
  dot.style.left = `${todayPct.toFixed(2)}%`; dot.style.top = `${todayY.toFixed(1)}px`;
  $('fc-dot').style.top = `${endY.toFixed(1)}px`;
  const fcTag = $('fc-tag');
  fcTag.textContent = fmt2(Number(fc[fc.length - 1]));
  fcTag.style.top = `${(endY - 10).toFixed(1)}px`;
}

function niceTicks(lo, hi, count) {
  const span = hi - lo || 1;
  const rough = span / (count - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rough) || 10 * mag;
  const values = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) values.push(Number(v.toFixed(6)));
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  return { lo, hi, values, format: (v) => v.toFixed(decimals) };
}

/* Commentary is model output: escaped first, then only **bold** and
 * paragraphs are turned into markup; numbers get the mono treatment. */
const HEADING_RE = /^\*\*([^*]+)\*\*\s*$/;
const DISCLAIMERS = ['Educational analysis — not investment advice.', 'Bildungsanalyse — keine Anlageberatung.'];

function renderCommentary(c, lang, ticker) {
  const box = $('comm-text');
  box.replaceChildren();
  $('comm').hidden = !c;
  box.lang = lang === 'de' ? 'de' : 'en';
  $('disclaimer').lang = box.lang;
  $('model-tag').textContent = c ? `${lang === 'de' ? 'German' : 'English'} · ${c.model || ''}` : '';
  $('callout').hidden = !(c && c.truncated);
  if (!c) return;
  const paras = String(c.text || '').replace(/\r\n/g, '\n').trim().split(/\n{2,}/);
  const closing = paras.length > 1 ? DISCLAIMERS.find((d) => paras[paras.length - 1].trim().replace(/\*/g, '') === d) : null;
  if (closing) paras.pop();
  $('disclaimer').textContent = closing || (lang === 'de' ? DISCLAIMERS[1] : DISCLAIMERS[0]);
  try {
    renderSections(box, paras, ticker, Boolean(c.truncated));
  } catch (_) {
    // Never lose the text over a rendering detail: fall back to plain paragraphs.
    box.replaceChildren(...paras.map((para) => { const p = document.createElement('p'); p.textContent = para.replace(/\*\*/g, ''); return p; }));
  }
}

function renderSections(box, paras, ticker, truncated) {
  const sections = [];
  let pendingHeading = null;
  for (const para of paras) {
    const lines = para.split('\n');
    const first = lines[0].match(HEADING_RE);
    if (first && lines.length === 1) { pendingHeading = first[1]; continue; }
    let heading = pendingHeading, bodyLines = lines;
    pendingHeading = null;
    if (!heading && first) { heading = first[1]; bodyLines = lines.slice(1); }
    sections.push({ heading, body: bodyLines.join(' ') });
  }
  if (pendingHeading) sections.push({ heading: pendingHeading, body: '' });
  sections.forEach((sec, idx) => {
    const section = document.createElement('section');
    if (sec.heading) {
      const h = document.createElement('h4');
      h.textContent = sec.heading;
      section.appendChild(h);
    }
    const p = document.createElement('p');
    appendInline(p, sec.body, ticker);
    if (truncated && idx === sections.length - 1) {
      const cut = document.createElement('span');
      cut.className = 'fp-cut'; cut.setAttribute('aria-hidden', 'true');
      p.appendChild(cut);
    }
    section.appendChild(p);
    box.appendChild(section);
  });
}

/* text → text nodes, <strong> for **…**, mono spans for numbers/tokens. */
function appendInline(parent, text, ticker) {
  const parts = text.split(/(\*\*[^*]+\*\*)/);
  for (const part of parts) {
    const bold = part.match(/^\*\*([^*]+)\*\*$/);
    if (bold) {
      const s = document.createElement('strong');
      appendMono(s, bold[1], ticker);
      parent.appendChild(s);
    } else {
      appendMono(parent, part, ticker);
    }
  }
}
function appendMono(parent, text, ticker) {
  const esc = String(ticker || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const alts = esc ? [`(?<![A-Za-z0-9])${esc}(?![A-Za-z0-9])`] : [];
  alts.push('beats_naive', '\\bTrue\\b', '\\bFalse\\b', '(?<![A-Za-z])[+-]?\\d[\\d.,]*\\d%?', '(?<![A-Za-z])\\d%?');
  const re = new RegExp(`(${alts.join('|')})`, 'g');
  let i = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m[0].length === 0) { re.lastIndex++; continue; }
    if (m.index > i) parent.appendChild(document.createTextNode(text.slice(i, m.index)));
    const span = document.createElement('span');
    span.className = 'fp-num';
    span.textContent = m[0];
    parent.appendChild(span);
    i = m.index + m[0].length;
  }
  if (i < text.length) parent.appendChild(document.createTextNode(text.slice(i)));
}

function renderHood(c, h, withCommentary) {
  const hasComm = Boolean(c);
  for (const id of ['check', 'dt-tokens', 'dd-tokens', 'dt-cost', 'dd-cost', 'dt-stop', 'dd-stop', 'dt-trunc', 'dd-trunc', 'dt-cached', 'dd-cached']) $(id).hidden = !hasComm;
  if (hasComm) {
    const ev = c.eval || {};
    const checked = Number(ev.numbers_checked) || 0, matched = Number(ev.numbers_matched) || 0;
    const passed = ev.passed === true;
    $('check').classList.toggle('warn', !passed);
    $('check-big').textContent = `${matched} / ${checked}`;
    $('check-chip-text').textContent = passed ? 'all numbers traced' : `${checked - matched} unmatched`;
    const meter = $('check-meter');
    meter.replaceChildren(...Array.from({ length: Math.min(checked, 40) }, (_, i) => {
      const s = document.createElement('span');
      if (i >= matched) s.className = 'off';
      return s;
    }));
    const unmatched = Array.isArray(ev.unmatched) ? ev.unmatched : [];
    $('check-note').textContent = passed
      ? 'Every number in the text was found in the forecast, its metrics, or values derived from them. Unmatched numbers would be listed here.'
      : `Not found in the forecast or its metrics: ${unmatched.join(', ')}. Read those figures with care.`;
    $('dd-tokens').textContent = `${c.input_tokens} in · ${c.output_tokens} out`;
    $('dd-cost').textContent = `$${Number(c.cost_usd).toFixed(4)}`;
    $('dd-stop').textContent = c.stop_reason || '–';
    $('dd-stop').classList.toggle('warn', Boolean(c.stop_reason) && c.stop_reason !== 'end_turn');
    $('dd-trunc').textContent = c.truncated ? 'yes' : 'no';
    $('dd-trunc').classList.toggle('warn', Boolean(c.truncated));
    $('dd-cached').textContent = c.cached ? 'yes' : 'no';
  }
  const limit = h.limit, remaining = h.remaining;
  const segs = $('quota-segs');
  if (limit !== null && limit <= 10) {
    segs.hidden = false;
    segs.replaceChildren(...Array.from({ length: limit }, (_, i) => {
      const s = document.createElement('span');
      if (remaining !== null && i < remaining) s.className = 'on';
      return s;
    }));
  } else {
    segs.hidden = true;
  }
  $('quota-text').textContent = limit === null ? 'not reported' : `${remaining ?? '?'} of ${limit} left this hour`;
  $('hood-note').textContent = hasComm
    ? 'Commentary is cached until midnight UTC, keyed by the numbers it cites. A price move during the day yields fresh text.'
    : 'Commentary was off for this request. Switch it on to see the numbers-match check, tokens and cost.';
  $('disclaimer').hidden = !hasComm;
}

show('idle');
