const $ = id => document.getElementById(id);
const canvas = $('chart');
const ctx = canvas.getContext('2d');
// NSE sessions use fixed UTC+05:30, independent of the browser's locale and zone.
const istTimestamp = time => new Date(new Date(time).getTime() + 330 * 60000).toISOString();
const sessionDate = (time = new Date()) => istTimestamp(time).slice(0, 10);
const timeLabel = time => istTimestamp(time).slice(11, 16);
const number = value => Number.isFinite(value) ? value.toFixed(2) : '—';
const percent = value => Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const chartMotion = new ChartMotion();
const candleLists = new WeakMap();
const candleClocks = new WeakMap();
let tipContext = null;
const comparisonTips = new Map();
const seriesLabels = new Map();
let hoveredComparison = null;
let focusedComparison = null;
let chartHover = { primary: false, comparison: null };
let chartHitTest = null;
let chartHoverAnchor = null;
const sourceName = source => source === 'shoonya' ? 'Shoonya' : 'Yahoo Finance';
let today = sessionDate();
let selectedDate = today;
let followingToday = true;
let selectedInterval = 15;
let selectedSource = 'yahoo';
let sourceGeneration = -1;
let sourceEpoch = null;
let switchingSource = false;
let pendingSourceSwitch = null;
let sourceError = '';
let shoonyaHelpRequest = 0;
let dates = [];
let view = null;
let live = null;
let connected = false;
let loading = true;
let loadError = '';
let requestId = 0;
let requestController;
let pointer = null;
let historyNote = '';
let transition = null;
let replay = null;
let renderFrame = 0;
let palette;
let selectedComparisonGroup = 'derivatives';
let lastComparisonGroup = selectedComparisonGroup;
let lastClockState = '';
const comparisonPalette = ['comparison-line', ...Array.from({ length: 14 }, (_, index) => `comparison-line-${index + 2}`)];
const sectorSymbols = new Set(['^CNXPHARMA', '^CNXAUTO', '^CNXFMCG', '^CNXMETAL', '^CNXPSUBANK',
  '^CNXREALTY', 'NIFTY_CONSR_DURBL.NS', 'NIFTY_OIL_AND_GAS.NS', 'NIFTY_PVT_BANK.NS', '^CNXENERGY',
  'NIFTY_IND_DEFENCE.NS', '^CNXINFRA', '^CNXPSE', '^CNXCMDT', 'NIFTY_CPSE.NS']);
const comparisonGroups = { derivatives: { id: 'comparison-toggle', label: 'Derivatives' },
  sectors: { id: 'sectors-tab', label: 'NIFTY Sectors' } };

async function fetchJSON(url, fallback, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.message || (typeof fallback === 'function' ? fallback(response.status) : fallback));
  return data;
}

function readPalette() {
  const style = getComputedStyle(document.documentElement);
  palette = Object.fromEntries(['up', 'down', 'wick', 'grid', 'chart-label', 'orb-line', 'orb-label', 'orb-fill', 'orb-opening', 'marker', 'price-line', 'price-fill', 'price-ink', 'crosshair', 'crosshair-fill', 'crosshair-ink', ...comparisonPalette].map(key => [key, style.getPropertyValue(`--${key}`).trim()]));
}

function setTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const label = `Switch to ${dark ? 'light' : 'dark'} mode`;
  $('theme-toggle').setAttribute('aria-pressed', String(dark));
  $('theme-toggle').setAttribute('aria-label', label);
  $('theme-toggle').title = label;
  try { localStorage.setItem('nse-orb-theme', dark ? 'dark' : 'light'); } catch { /* Storage can be unavailable in private browsing. */ }
  readPalette();
  scheduleRender();
}
try { setTheme(localStorage.getItem('nse-orb-theme') === 'dark'); } catch { setTheme(false); }

function syncNavigation() {
  $('session-date').value = selectedDate;
  $('session-date').max = today;
  $('previous').disabled = !dates.some(date => date < selectedDate);
  $('next').disabled = !dates.some(date => date > selectedDate);
  $('today').setAttribute('aria-pressed', String(followingToday));
  $('source-select').value = selectedSource;
  $('source-select').disabled = switchingSource;
  $('interval-15m').setAttribute('aria-pressed', String(selectedInterval === 15));
  $('interval-5m').setAttribute('aria-pressed', String(selectedInterval === 5));
  $('chart-wrap').setAttribute('aria-label', `${selectedInterval}-minute candlestick chart. Press left arrow for an earlier session or right arrow for a later session.`);
  document.title = `${selectedDate} · NIFTY ORB`;
}

async function loadDates() {
  const source = selectedSource;
  try {
    const data = await fetchJSON('/api/history', 'History unavailable');
    if (source !== selectedSource || data.source_id && data.source_id !== selectedSource) return;
    historyNote = data.history_note || '';
    dates = [...new Set([...(data.dates || []), today, ...(live ? [live.date] : [])])].sort();
    syncNavigation();
  } catch {
    if (source !== selectedSource) return;
    historyNote = 'History could not be loaded. Select a date to retry.';
    dates = [...new Set([...dates, today])].sort();
  }
  $('date-nav').title = historyNote;
}

async function selectDate(date, follow = false) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date > today) return syncNavigation();
  stopReplay();
  chartMotion.reset();
  transition = null;
  selectedDate = date;
  followingToday = follow;
  pointer = null;
  loadError = '';
  const id = ++requestId;
  requestController?.abort();
  if (live?.date === date && (!live.source_id || live.source_id === selectedSource)) {
    view = live;
    loading = false;
    return scheduleRender();
  }
  view = null;
  loading = true;
  scheduleRender();
  requestController = new AbortController();
  try {
    const data = await fetchJSON(`/api/session?date=${date}`, status => `Session unavailable (${status})`, { signal: requestController.signal });
    if (id !== requestId || data.source_id && data.source_id !== selectedSource) return;
    view = live?.date === date ? live : data;
  } catch (error) {
    if (id !== requestId || error.name === 'AbortError') return;
    loadError = 'Could not load this session. Select the date again to retry.';
  } finally {
    if (id === requestId) {
      loading = false;
      scheduleRender();
    }
  }
}

async function switchSource(source) {
  if (!['yahoo', 'shoonya'].includes(source) || switchingSource) return;
  const previousSource = selectedSource, previousLive = live, previousView = view;
  const pending = { source, previousSource, generation: sourceGeneration, epoch: sourceEpoch, confirmed: false };
  resetSession();
  chartMotion.reset();
  pendingSourceSwitch = pending;
  selectedSource = source;
  switchingSource = true;
  sourceError = '';
  pointer = null;
  loading = true;
  scheduleRender();
  try {
    const data = await fetchJSON('/api/source', status => `Source switch failed (${status})`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }) });
    if (pendingSourceSwitch !== pending) return;
    // The stream may already have delivered newer quotes than the POST snapshot.
    const keepLive = pending.confirmed && live?.source_id === source
      && (live.source_epoch !== data.source_epoch || (live.source_generation ?? -1) >= (data.source_generation ?? -1));
    const accepted = keepLive ? live : data;
    selectedSource = accepted.source_id || source;
    if (accepted.source_epoch && accepted.source_epoch !== sourceEpoch) { sourceEpoch = accepted.source_epoch; sourceGeneration = -1; }
    sourceGeneration = Math.max(sourceGeneration, accepted.source_generation ?? -1);
    live = accepted;
    switchingSource = false;
    loading = false;
    if (selectedDate === live.date) view = live;
    else await selectDate(selectedDate, followingToday);
    loadDates();
  } catch (error) {
    if (pendingSourceSwitch !== pending) return;
    // Losing the HTTP acknowledgement does not undo a stream-confirmed switch.
    if (!pending.confirmed || live?.source_id !== source) {
      selectedSource = previousSource;
      live = previousLive;
      view = previousView?.date === selectedDate ? previousView : null;
      sourceError = `Could not switch to ${sourceName(source)}: ${error.message}`;
    }
    switchingSource = false;
    if (!view) await selectDate(selectedDate, followingToday);
  } finally {
    if (pendingSourceSwitch === pending) {
      pendingSourceSwitch = null;
      switchingSource = false;
      loading = false;
      scheduleRender();
    }
  }
}

function updateShoonyaHelp(data) {
  const help = $('shoonya-help');
  help.hidden = selectedSource !== 'shoonya' || !(data?.feed_status === 'error' || /auth|login|credential/i.test(data?.feed_error || sourceError));
  if (help.hidden) help.open = false;
  $('source-retry').disabled = switchingSource;
}

async function loadShoonyaHelp() {
  const id = ++shoonyaHelpRequest;
  const link = $('shoonya-login'), note = $('shoonya-login-note'), client = $('shoonya-client-id');
  link.hidden = true;
  link.removeAttribute('href');
  client.hidden = true;
  client.textContent = '';
  note.textContent = 'Loading authorization link…';
  try {
    const data = await fetchJSON('/api/shoonya/help', 'Help unavailable');
    if (id !== shoonyaHelpRequest) return;
    const url = data.login_url && new URL(data.login_url);
    if (url && url.protocol === 'https:' && url.hostname === 'api.shoonya.com' && !url.port
        && !url.username && !url.password && !url.hash && url.pathname === '/OAuthlogin/authorize/oauth'
        && url.searchParams.get('client_id') && [...url.searchParams.keys()].every(key => key === 'client_id')) {
      link.href = url.href;
      link.hidden = false;
      client.textContent = `Client ID: ${url.searchParams.get('client_id')}`;
      client.hidden = false;
      note.textContent = '';
    } else note.textContent = 'Set SHOONYA_CLIENT_ID in .env to enable the authorization link.';
  } catch {
    if (id === shoonyaHelpRequest) note.textContent = 'Authorization link unavailable. Check SHOONYA_CLIENT_ID in .env.';
  }
}

async function checkPublicIp() {
  const button = $('check-ip'), output = $('public-ip');
  button.disabled = true;
  output.textContent = 'Checking public IP…';
  try {
    const data = await fetchJSON('/api/public-ip', 'Public IP lookup unavailable. Try curl -s ifconfig.me.');
    if (typeof data.ip !== 'string' || !data.ip.trim()) throw new Error('No public IP returned. Try curl -s ifconfig.me.');
    output.textContent = `Public IP: ${data.ip.trim()} — confirm it matches Shoonya’s API IP setting.`;
  } catch (error) {
    output.textContent = error.message || 'Could not check public IP. Try curl -s ifconfig.me.';
  } finally { button.disabled = false; }
}

function moveDate(direction) {
  const date = direction < 0 ? dates.filter(d => d < selectedDate).at(-1) : dates.find(d => d > selectedDate);
  if (date) selectDate(date, date === today);
}

function getCandles(data = displayView(), interval = selectedInterval) {
  if (!data) return [];
  const closed = interval === 5 ? data?.candles_5m : data?.candles;
  const active = interval === 5 ? data?.active_candle_5m : data?.active_candle;
  const cached = candleLists.get(data)?.[interval];
  if (cached && cached.closed === closed && cached.active === active && cached.date === selectedDate) return cached.list;
  const all = [...(closed || []), ...(active ? [active] : [])];
  const list = [...new Map(all.filter(c => sessionDate(c.start_time) === selectedDate && [c.open, c.high, c.low, c.close].every(Number.isFinite)).map(c => [c.start_time, c])).values()]
    .sort((a, b) => candleClock(a).start - candleClock(b).start);
  candleLists.set(data, { ...candleLists.get(data), [interval]: { closed, active, date: selectedDate, list } });
  return list;
}

function candleClock(candle) {
  if (!candleClocks.has(candle)) candleClocks.set(candle, { start: Date.parse(candle.start_time), end: Date.parse(candle.end_time) });
  return candleClocks.get(candle);
}

function primaryInstrument(data) {
  return data?.instruments?.[data.primary_symbol || '^NSEI'] || data;
}

function comparisonInstruments(data) {
  return Object.values(data?.instruments || {}).filter(item => item.symbol !== (data.primary_symbol || '^NSEI')
    && (item.comparison_group || (sectorSymbols.has(item.symbol) ? 'sectors' : 'derivatives')) === selectedComparisonGroup);
}

const comparisonColor = index => palette[comparisonPalette[index % comparisonPalette.length]];

const comparisonQuoteDelay = instrument => Number.isFinite(instrument?.quote_delay_seconds) ? Math.max(0, instrument.quote_delay_seconds) : 0;
const comparisonDelayLabel = instrument => comparisonQuoteDelay(instrument) ? `${Number((comparisonQuoteDelay(instrument) / 60).toFixed(1))}m delayed` : 'delayed';

function comparisonProvenance(instrument, data) {
  if (!instrument.history_fallback || !instrument.history_source_id) return '';
  const name = source => source === 'yahoo' ? 'Yahoo' : sourceName(source);
  const historySource = name(instrument.history_source_id);
  const historyDelay = Number.isFinite(instrument.history_delay_seconds) && instrument.history_delay_seconds > 0
    ? ` (${Number((instrument.history_delay_seconds / 60).toFixed(1))}m delayed)` : '';
  const quoteSource = instrument.quote_source_id && name(instrument.quote_source_id);
  const price = data?.replay_time != null ? `Replay price: ${historySource}`
    : quoteSource ? `${instrument.quote_source_id === 'shoonya' ? 'Live quotes' : instrument.comparison_status === 'closed' ? 'After-hours price' : 'Price'}: ${quoteSource}` : '';
  return ` · History: ${historySource}${historyDelay}${price ? ` · ${price}` : ''}`;
}

function comparisonQuoteFresh(instrument) {
  const timestamp = Date.parse(instrument?.last_tick_time), now = Date.now();
  const age = now - timestamp - comparisonQuoteDelay(instrument) * 1000;
  return connected && Number.isFinite(timestamp) && age >= 0 && age <= 90000;
}

const completeCandle = candle => candle.is_complete !== false && candle.close_is_valid !== false;

function comparisonCloseValid(candle) {
  // A late start can leave OHLC incomplete while its observed close is valid.
  // Old partial bars without an explicit close-valid flag remain excluded.
  return candle.close_is_valid !== false && (candle.is_complete !== false || candle.close_is_valid === true)
    && Number.isFinite(candle.close) && candle.close > 0;
}

function comparisonCandles(instrument) {
  const selected = getCandles(instrument, selectedInterval);
  if (selectedInterval !== 15) return selected;
  const five = getCandles(instrument, 5);
  // A partial 15m parent must not hide valid, observed 5m closes inside it.
  // Keep the normal 15m sampling wherever the parent is usable; finer samples
  // still carry their own validity and coverage, so real gaps stay disconnected.
  return selected.flatMap(parent => {
    if (completeCandle(parent)) return [parent];
    const { start, end } = candleClock(parent);
    const children = five.filter(child => candleClock(child).start >= start && candleClock(child).end <= end);
    return children.length ? children : [parent];
  });
}

// A close belongs at its completion time; forming samples use the quote clock.
function comparisonPoints(instrument, cutoff = Infinity) {
  if (!(instrument?.session_open > 0)) return [];
  const opening = Date.parse(`${selectedDate}T09:15:00+05:30`);
  // Session open remains the return baseline, but is not a five-minute close.
  const points = [];
  const actualQuoteTime = Date.parse(instrument.last_tick_time);
  let previous = opening, gap = true, hasCurrentBar = false;
  for (const candle of comparisonCandles(instrument)) {
    const { start, end } = candleClock(candle);
    // Backfill has a coverage boundary without claiming a fresh live quote.
    const quoteTime = candle.is_closed ? end : Date.parse(instrument.last_tick_time ?? instrument.history_coverage_time);
    if (!Number.isFinite(quoteTime) || quoteTime > cutoff || !candle.is_closed && !(start <= quoteTime && quoteTime < end)) continue;
    if (!comparisonCloseValid(candle)) { gap = true; continue; }
    points.push({ time: quoteTime, quoteTime, barStart: start, barEnd: end, price: candle.close, gap: gap || start > previous });
    if (!candle.is_closed && quoteTime === actualQuoteTime) hasCurrentBar = true;
    previous = end; gap = false;
  }
  // Native quotes may be newer than delayed fallback history without a valid
  // forming bar. Isolate the actual quote instead of bridging unseen trading.
  const quoteTime = actualQuoteTime;
  const separateNativeQuote = instrument.history_fallback && instrument.quote_source_id === 'shoonya'
    && !hasCurrentBar && quoteTime >= (points.at(-1)?.quoteTime ?? opening);
  if ((!points.length || separateNativeQuote) && !replay && cutoff === Infinity
      && ['live', 'delayed'].includes(instrument.comparison_status) && comparisonQuoteFresh(instrument)
      && quoteTime >= opening && quoteTime < opening + 375 * 60000 && Number.isFinite(instrument.current_price)) {
    points.push({ time: quoteTime, quoteTime, price: instrument.current_price, gap: true, quoteOnly: true });
  }
  return points;
}

function comparisonStatus(instrument, data) {
  if (!(primaryInstrument(data)?.session_open > 0) || !(instrument?.session_open > 0) || !Number.isFinite(instrument.current_price)) return 'unavailable';
  const status = instrument.comparison_status || 'unavailable';
  if (['live', 'delayed'].includes(status)) {
    if (!comparisonQuoteFresh(instrument)) return 'stale';
    return comparisonQuoteDelay(instrument) > 0 ? 'delayed' : status;
  }
  return status;
}

function renderComparison() {
  for (const [group, { id, label }] of Object.entries(comparisonGroups)) {
    const selected = selectedComparisonGroup === group, button = $(id);
    button.setAttribute('aria-selected', String(selected));
    button.setAttribute('aria-pressed', String(selected));
    button.setAttribute('aria-label', label);
    button.title = `${selected ? 'Hide' : 'Show'} ${label} · Select one comparison group, or click the selected group again for NIFTY only. Breakouts use 5m closes outside each index’s first 45m closing range.`;
    button.tabIndex = group === (selectedComparisonGroup || lastComparisonGroup) ? 0 : -1;
  }
  $('series-labels').dataset.group = selectedComparisonGroup || '';
}

function setComparisonGroup(group, toggle = false) {
  if (!(group in comparisonGroups)) return;
  const next = toggle && group === selectedComparisonGroup ? null : group;
  if (next === selectedComparisonGroup) return;
  lastComparisonGroup = group;
  selectedComparisonGroup = next;
  // Group selection changes only this view. Both groups keep receiving data,
  // while old labels, hover geometry and fitted extrema belong to the old view.
  pointer = null;
  comparisonTips.clear();
  clearSeriesLabels();
  chartMotion.reset();
  tipContext = null;
  renderComparison();
  scheduleRender();
}

// Replay only sees completed bars reached by its clock. The final snapshot's
// breakout state must never reveal a confirmation from later in the session.
function replayBreakout(instrument, cutoff) {
  const start = Date.parse(`${selectedDate}T09:15:00+05:30`), end = start + 45 * 60000;
  const closed = getCandles(instrument, 5).filter(c => c.is_closed && candleClock(c).end <= cutoff
    && candleClock(c).start >= start && candleClock(c).end <= start + 375 * 60000
    && candleClock(c).end - candleClock(c).start === 300000 && (candleClock(c).start - start) % 300000 === 0);
  const valid = candle => completeCandle(candle) && Number.isFinite(candle.close) && candle.close > 0;
  const opening = closed.filter(c => candleClock(c).start < end);
  const locked = cutoff >= end && opening.length === 9
    && opening.every((c, index) => valid(c) && candleClock(c).start === start + index * 300000);
  const state = { basis: 'close', range_minutes: 45, confirmation_minutes: 5, range_locked: locked,
    range_high: locked ? Math.max(...opening.map(c => c.close)) : null,
    range_low: locked ? Math.min(...opening.map(c => c.close)) : null,
    direction: null, confirmed_at: null, price: null };
  if (!locked) return state;
  const latest = closed.at(-1);
  if (candleClock(latest).start >= end && valid(latest)) {
    state.direction = latest.close > state.range_high ? 'BULLISH' : latest.close < state.range_low ? 'BEARISH' : null;
    state.confirmed_at = latest.end_time;
    state.price = latest.close;
  }
  return state;
}

function qualifyingDirection(instrument, data) {
  const status = comparisonStatus(instrument, data), breakout = instrument.breakout;
  if (!['live', 'delayed', 'closed'].includes(status) || !breakout?.range_locked
      || !['BULLISH', 'BEARISH'].includes(breakout.direction)) return null;
  if (data?.replay_time != null || status !== 'closed') {
    const clock = data?.replay_time ?? Date.now() - comparisonQuoteDelay(instrument) * 1000;
    const age = clock - Date.parse(breakout.confirmed_at);
    // A fresh quote cannot revive an older confirmation across a missing close.
    if (!(age >= 0 && age < 5 * 60000)) return null;
  }
  return breakout.direction;
}

function comparisonSeries(data) {
  const primary = primaryInstrument(data);
  if (!(primary?.session_open > 0)) return [];
  const origin = primary.session_open;
  const opening = Date.parse(`${selectedDate}T09:15:00+05:30`);
  return comparisonInstruments(data).map((instrument, index) => {
    const points = comparisonPoints(instrument, data?.replay_time ?? Infinity);
    const reference = instrument.session_open;
    if (points.length) {
      // This display anchor is the reported session open, never a strategy bar.
      // A missing opening interval still breaks the line before its first quote.
      if (points[0].barStart === opening) points[0].gap = false;
      points.unshift({ time: opening, quoteTime: opening, price: reference, gap: true, sessionOpen: true });
    }
    for (const point of points) point.normalized = origin * point.price / reference;
    return { instrument, index, origin, reference, referenceTime: opening, points };
  });
}

function distanceToSegment(point, start, end) {
  const dx = end[0] - start[0], dy = end[1] - start[1];
  const fraction = Math.max(0, Math.min(1, ((point.x - start[0]) * dx + (point.y - start[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(point.x - start[0] - fraction * dx, point.y - start[1] - fraction * dy);
}

function comparisonEmphasis(symbol, direction, emphasis) {
  if (emphasis.active) return emphasis.active === symbol ? 'focused' : 'muted';
  if (direction) return 'qualifying';
  return emphasis.overview || emphasis.reveal ? 'revealed' : 'hidden';
}

function primaryLabelGeometry(priceY, { right, top, bottom, width }) {
  const above = priceY - 43 >= top;
  return { left: right + 5, top: Math.max(top, Math.min(bottom - 28, above ? priceY - 42 : priceY + 13)),
    width: Math.max(30, width - right - 9), height: 28, priceY };
}

function comparisonEmphasisState(data, series) {
  const qualifying = new Set(series.filter(item => qualifyingDirection(item.instrument, data)).map(item => item.instrument.symbol));
  const active = [focusedComparison, hoveredComparison, chartHover.comparison].find(symbol => qualifying.has(symbol)) || null;
  const primarySymbol = data?.primary_symbol || '^NSEI';
  // The first NIFTY dual-ORB signal ends the default overview for this session.
  // Replay frames already contain only signals reached by their clock.
  const overview = !(data?.signals?.length);
  const reveal = !active && (chartHover.primary || [focusedComparison, hoveredComparison].includes(primarySymbol));
  return { active, reveal, qualifying, overview };
}

function drawComparisons(data, series, { left, right, top, bottom, xMinute, y }, emphasis) {
  const opening = Date.parse(`${selectedDate}T09:15:00+05:30`);
  const position = point => [xMinute(555 + (point.time - opening) / 60000), y(point.normalized)];
  const { active, reveal, qualifying, overview } = emphasis;
  emphasis = { ...emphasis, position };
  $('series-labels').dataset.focused = active || '';
  $('series-labels').dataset.qualifying = String(qualifying.size);
  $('series-labels').dataset.reveal = String(Boolean(reveal));
  $('series-labels').dataset.overview = String(overview);
  for (const { instrument, index, points } of series) {
    if (!points.length) continue;
    const qualifies = qualifying.has(instrument.symbol);
    const state = comparisonEmphasis(instrument.symbol, qualifies, emphasis);
    const healthy = ['live', 'closed', 'delayed'].includes(comparisonStatus(instrument, data));
    ctx.save();
    ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
    ctx.strokeStyle = comparisonColor(index);
    ctx.lineWidth = qualifies ? 1.9 : 1.2;
    ctx.globalAlpha = state === 'hidden' ? 0 : state === 'muted' ? .06 : state === 'revealed' ? healthy ? .32 : .15 : .95;
    ctx.filter = state === 'muted' ? 'blur(3.2px)' : state === 'revealed' ? 'blur(1.6px)' : 'none';
    ctx.beginPath();
    points.forEach(point => ctx[point.gap ? 'moveTo' : 'lineTo'](...position(point)));
    ctx.stroke();
    ctx.beginPath(); ctx.arc(...position(points.at(-1)), qualifies ? 2.6 : 2, 0, Math.PI * 2);
    ctx.fillStyle = comparisonColor(index); ctx.fill(); ctx.restore();
  }
  return emphasis;
}

function clearSeriesLabels() {
  seriesLabels.clear();
  $('series-labels').replaceChildren();
  $('series-labels').dataset.focused = '';
  $('series-labels').dataset.qualifying = '0';
  $('series-labels').dataset.reveal = 'false';
  $('series-labels').dataset.overview = 'false';
  hoveredComparison = focusedComparison = null;
  chartHover = { primary: false, comparison: null };
  chartHitTest = null;
  chartHoverAnchor = null;
}

function drawSeriesLabels(data, series, geometry, emphasis, shownPrice) {
  const { left, right, top, bottom, xMinute, y, primaryLabel } = geometry;
  const primary = primaryInstrument(data), primarySymbol = data?.primary_symbol || '^NSEI';
  const entries = series.filter(item => item.points.length).map(item => {
    const point = item.points.at(-1);
    return { ...item, point, position: emphasis.position(point), direction: qualifyingDirection(item.instrument, data), primary: false };
  });
  if (primary?.session_open > 0 && Number.isFinite(shownPrice)) {
    const last = getCandles(data).at(-1), opening = Date.parse(`${selectedDate}T09:15:00+05:30`);
    const time = last ? (candleClock(last).start + candleClock(last).end) / 2 : Date.parse(primary.last_tick_time);
    const sample = getCandles(primary, 5).at(-1) || last;
    const quoteTime = data?.replay_time ?? Date.parse(primary.last_tick_time ?? primary.history_coverage_time ?? sample?.end_time);
    if (Number.isFinite(time)) entries.push({ instrument: { ...primary, symbol: primarySymbol, label: 'NIFTY 50' },
      point: { price: shownPrice, quoteTime },
      position: [xMinute(555 + (time - opening) / 60000), y(shownPrice)], primary: true, direction: null });
  }
  const symbols = new Set(entries.map(entry => entry.instrument.symbol));
  for (const [symbol, label] of seriesLabels) if (!symbols.has(symbol)) { label.remove(); seriesLabels.delete(symbol); }
  if (!entries.length) return;
  ctx.save(); ctx.font = `${canvas.clientWidth < 650 ? 9 : 10}px ui-monospace, monospace`;
  for (const entry of entries) {
    const { instrument, primary: isPrimary, direction } = entry;
    const status = comparisonStatus(instrument, data);
    const change = (entry.point.price / instrument.session_open - 1) * 100;
    const provenance = comparisonProvenance(instrument, data);
    const delayed = status === 'delayed', history = Boolean(instrument.history_fallback);
    entry.text = isPrimary ? `${instrument.label}\n${['live', 'closed', 'delayed'].includes(status) ? percent(change) : status}` : `${direction ? direction === 'BULLISH' ? '▲ ' : '▼ ' : ''}${instrument.label} ${percent(change)}${delayed ? ' D' : ''}${history ? ' H' : ''}${status === 'stale' ? ' · stale' : status === 'unavailable' ? ' · unavailable' : ''}`;
    entry.emphasis = isPrimary ? 'primary' : comparisonEmphasis(instrument.symbol, direction, emphasis);
    entry.width = Math.ceil(ctx.measureText(entry.text).width) + 6;
    entry.status = status;
    const timestamp = entry.point.quoteTime;
    const breakout = instrument.breakout;
    const confirmation = breakout?.direction && Number.isFinite(Date.parse(breakout.confirmed_at))
      ? direction ? `${breakout.direction.toLowerCase()} confirmed ${timeLabel(breakout.confirmed_at)}`
        : `last recorded ${breakout.direction.toLowerCase()} close ${timeLabel(breakout.confirmed_at)} · ${status === 'stale' || status === 'unavailable' ? 'current confirmation unavailable' : 'no current 5m confirmation'}`
      : breakout?.range_locked ? Number.isFinite(breakout.price) ? 'latest close within range' : 'awaiting a valid 5m close'
      : 'opening range incomplete';
    entry.title = `${instrument.label} · ${percent(change)} since 09:15 open · ${status}${Number.isFinite(timestamp) ? ` · as of ${timeLabel(timestamp)}` : ''}${delayed ? ` · ${comparisonDelayLabel(instrument)}` : ''}${provenance}${!isPrimary ? ` · 5m close outside the first 45m closing range · ${confirmation}` : ''}`;
    if (!isPrimary && breakout?.range_locked) entry.title += ` · Closing range ${number(breakout.range_low)}–${number(breakout.range_high)} · Last confirmed close ${number(breakout.price)}`;
    if (direction) entry.title += entry.emphasis === 'muted' ? ' · Another index is isolated' : ' · Hover or focus to isolate';
    if (isPrimary) entry.title += ' · Hover or focus to reveal comparisons';
  }
  for (const entry of entries) {
    if (entry.primary) {
      entry.labelX = primaryLabel.left;
      entry.labelY = primaryLabel.top + 9;
      continue;
    }
    // Providers can have different coverage; keep each label beside its own
    // endpoint instead of letting one newer or delayed line move every label.
    const tipX = entry.position[0];
    entry.afterTip = tipX + 11 + entry.width <= right - 5;
    entry.labelX = Math.round(Math.max(left + 4, Math.min(
      entry.afterTip ? tipX + 11 : tipX - entry.width - 11, right - entry.width - 5)));
  }
  const visible = entries.filter(entry => !entry.primary && entry.emphasis !== 'hidden');
  const gap = Math.min(20, (bottom - top - 18) / Math.max(1, visible.length - 1));
  const ordered = [...visible].sort((a, b) => a.position[1] - b.position[1] || a.instrument.symbol.localeCompare(b.instrument.symbol));
  let previousY = top - gap + 9;
  ordered.forEach((entry, index) => {
    const minimum = Math.max(top + 9, previousY + gap);
    const maximum = bottom - 9 - (ordered.length - index - 1) * gap;
    const target = Math.max(minimum, Math.min(maximum, entry.position[1]));
    const blocked = [];
    // A label placed before its endpoint must not erase its own price line.
    // Only observed, connected strokes reserve space; real gaps stay gaps.
    const labelLeft = entry.labelX - 4, labelRight = entry.labelX + entry.width + 4;
    for (let pointIndex = 1; pointIndex < entry.points.length; pointIndex++) {
      if (entry.points[pointIndex].gap) continue;
      const [x1, y1] = emphasis.position(entry.points[pointIndex - 1]);
      const [x2, y2] = emphasis.position(entry.points[pointIndex]);
      if (Math.max(x1, x2) < labelLeft || Math.min(x1, x2) > labelRight) continue;
      const start = Math.max(labelLeft, Math.min(x1, x2)), end = Math.min(labelRight, Math.max(x1, x2));
      const at = x => y1 + (y2 - y1) * (x - x1) / (x2 - x1);
      const ys = Math.abs(x2 - x1) < .001 ? [y1, y2] : [at(start), at(end)];
      blocked.push([Math.min(...ys) - 13, Math.max(...ys) + 13]);
    }
    const candidates = [target, minimum, maximum, ...blocked.flatMap(([low, high]) => [low - 1, high + 1])]
      .filter(candidate => candidate >= minimum && candidate <= maximum
        && blocked.every(([low, high]) => candidate <= low || candidate >= high))
      .sort((a, b) => Math.abs(a - target) - Math.abs(b - target) || a - b);
    entry.labelY = candidates[0] ?? target;
    previousY = entry.labelY;
  });
  for (const entry of entries) {
    const { instrument, primary: isPrimary, direction, index, position: [tipX, tipY] } = entry;
    let label = seriesLabels.get(instrument.symbol);
    if (!label) {
      label = document.createElement(isPrimary ? 'span' : 'button');
      label.className = 'series-endpoint';
      label.dataset.symbol = instrument.symbol;
      if (!isPrimary) label.type = 'button';
      label.addEventListener('pointerenter', () => { hoveredComparison = instrument.symbol; scheduleRender(); });
      label.addEventListener('pointerleave', () => { if (hoveredComparison === instrument.symbol) hoveredComparison = null; scheduleRender(); });
      label.addEventListener('focus', () => { focusedComparison = instrument.symbol; scheduleRender(); });
      label.addEventListener('blur', () => { if (focusedComparison === instrument.symbol) focusedComparison = null; scheduleRender(); });
      $('series-labels').append(label); seriesLabels.set(instrument.symbol, label);
    }
    const state = entry.emphasis;
    if (label.textContent !== entry.text) label.textContent = entry.text;
    label.title = entry.title;
    label.setAttribute('aria-label', entry.title);
    label.dataset.status = entry.status;
    label.dataset.direction = direction || 'none';
    label.dataset.emphasis = state;
    label.dataset.primary = String(isPrimary);
    label.hidden = state === 'hidden';
    if (Number.isFinite(entry.point.quoteTime)) label.dataset.asof = new Date(entry.point.quoteTime).toISOString();
    else delete label.dataset.asof;
    label.tabIndex = isPrimary || direction ? 0 : -1;
    const color = isPrimary ? palette['price-ink'] : comparisonColor(index);
    label.style.setProperty('--series-color', color);
    label.style.left = `${entry.labelX}px`;
    label.style.top = `${Math.round((entry.labelY ?? entry.position[1]) - 9)}px`;
    label.style.maxWidth = `${isPrimary ? primaryLabel.width : Math.max(30, right - entry.labelX - 4)}px`;
    label.style.opacity = isPrimary && emphasis.active ? '.65' : '';
    if (isPrimary || state === 'hidden') continue;
    const joinX = entry.afterTip ? entry.labelX - 4 : Math.min(right, entry.labelX + entry.width + 4);
    ctx.globalAlpha = state === 'muted' ? .08 : state === 'revealed' ? .2 : .35;
    ctx.strokeStyle = color; ctx.lineWidth = .65;
    ctx.beginPath(); ctx.moveTo(tipX + (entry.afterTip ? 4 : -4), tipY);
    ctx.lineTo(joinX, entry.labelY); ctx.stroke();
  }
  ctx.restore();
}

function setupFor(data, interval = selectedInterval) {
  return data?.setups?.[String(interval)] || {
    setup_id: interval === 15 ? 'orb_30m_15m' : 'orb_45m_5m',
    range_minutes: interval === 15 ? 30 : 45, confirmation_minutes: interval,
    orb_high: null, orb_low: null, range_locked: false, signals: []
  };
}

const rangeEndLabel = setup => setup.range_minutes === 30 ? '09:45' : '10:00';
const easeTransition = fraction => fraction < .5 ? 4 * fraction ** 3 : 1 - (-2 * fraction + 2) ** 3 / 2;
const interpolate = (from, to, fraction) => Object.fromEntries(Object.keys(from).map(key => [key, from[key] + (to[key] - from[key]) * fraction]));
function rangeShape(data, interval, now = performance.now()) {
  const setup = setupFor(data, interval);
  const target = { high: setup.orb_high, low: setup.orb_low, endMinute: 555 + setup.range_minutes };
  if (!transition || interval !== transition.to || !transition.rangeFrom) return target;
  const from = transition.rangeFrom;
  const progress = easeTransition(Math.max(0, Math.min(1, (now - transition.started) / transition.duration)));
  if (![from.high, from.low, target.high, target.low].every(Number.isFinite)) return target;
  return interpolate(from, target, progress);
}

function setIntervalMinutes(minutes) {
  if (minutes === selectedInterval) return;
  const previous = selectedInterval, now = performance.now();
  const rangeFrom = rangeShape(displayView(), previous, now);
  selectedInterval = minutes;
  pointer = null;
  transition = reducedMotion.matches ? null : { from: previous, to: minutes, started: now, duration: 420, rangeFrom };
  scheduleRender();
}

// Saved bars follow an illustrative open/extreme/extreme/close path. This is
// candle playback, not a reconstruction of historical tick ordering.
function partialCandle(candle, time, capturedAt) {
  const { start, end } = candleClock(candle);
  if (time < start) return null;
  const observedEnd = candle.is_closed ? end : Math.min(end, Math.max(start + 1, capturedAt));
  if (time >= observedEnd) return { ...candle };
  if (reducedMotion.matches) return { ...candle, high: candle.open, low: candle.open, close: candle.open, volume: 0, is_closed: false };
  const progress = Math.max(0, Math.min(1, (time - start) / (observedEnd - start)));
  const path = candle.close >= candle.open ? [candle.open, candle.low, candle.high, candle.close] : [candle.open, candle.high, candle.low, candle.close];
  const segment = Math.min(2, Math.floor(progress * 3));
  const fraction = progress * 3 - segment;
  const eased = fraction * fraction * (3 - 2 * fraction);
  const price = path[segment] + (path[segment + 1] - path[segment]) * eased;
  const observed = [...path.slice(0, segment + 1), price];
  return { ...candle, high: Math.max(...observed), low: Math.min(...observed), close: price, volume: Math.floor((candle.volume || 0) * progress), is_closed: false };
}

function replayView(time) {
  const snapshot = replay.snapshot;
  const five = replay.five.map(c => partialCandle(c, time, replay.end)).filter(Boolean);
  const fiveByStart = new Map(five.map(c => [c.start_time, c]));
  const fifteen = replay.fifteen.map(candle => {
    const { start, end } = candleClock(candle);
    const children = replay.children.get(candle);
    const coverage = children.length && children.every((c, i) => candleClock(c).start === start + i * 300000) && (!candle.is_closed || children.length === 3);
    if (!coverage) return partialCandle(candle, time, replay.end);
    const shown = children.map(c => fiveByStart.get(c.start_time)).filter(Boolean);
    if (!shown.length) return null;
    // Both displays use the same saved 5m progression when available. Genuine
    // smaller bars can be aggregated; a saved 15m bar is never split into data.
    return { ...candle, open: shown[0].open, high: Math.max(...shown.map(c => c.high)), low: Math.min(...shown.map(c => c.low)), close: shown.at(-1).close, volume: shown.reduce((total, c) => total + (c.volume || 0), 0), is_closed: candle.is_closed && time >= end };
  }).filter(Boolean);
  const bars = five.length ? five : fifteen;
  const instruments = Object.fromEntries(Object.entries(snapshot.instruments || {}).map(([symbol, instrument]) => {
    const closed = getCandles(instrument, 5).filter(c => c.is_closed && candleClock(c).end <= time);
    const closedFifteen = getCandles(instrument, 15).filter(c => c.is_closed && candleClock(c).end <= time);
    const samples = closed.length ? closed : closedFifteen;
    const last = samples.findLast(completeCandle);
    const fresh = last && last === samples.at(-1) && time - candleClock(last).end <= (closed.length ? 5 : 15) * 60000;
    return [symbol, { ...instrument, candles: closedFifteen, candles_5m: closed, active_candle: null, active_candle_5m: null,
      current_price: last?.close ?? instrument.session_open, last_tick_time: last?.end_time ?? null,
      comparison_status: instrument.session_open > 0 && fresh ? 'closed' : 'unavailable', breakout: replayBreakout(instrument, time) }];
  }));
  const primarySymbol = snapshot.primary_symbol || '^NSEI';
  if (instruments[primarySymbol]) instruments[primarySymbol].current_price = bars.at(-1)?.close ?? null;
  const setups = {};
  for (const interval of [15, 5]) {
    const setup = setupFor(snapshot, interval);
    const openingStart = Date.parse(`${selectedDate}T09:15:00+05:30`);
    const openingEnd = openingStart + setup.range_minutes * 60000;
    const opening = (interval === 5 ? five : fifteen).filter(c => Date.parse(c.start_time) >= openingStart && Date.parse(c.start_time) < openingEnd);
    const validOpening = opening.filter(completeCandle);
    const expected = setup.range_minutes / interval;
    const rangeLocked = Boolean(setup.range_locked && time >= openingEnd && validOpening.length === expected && validOpening.every((c, index) => c.is_closed && Date.parse(c.start_time) === openingStart + index * interval * 60000));
    const visibleSignals = rangeLocked ? (setup.signals || []).filter(s => Date.parse(s.timestamp) <= time) : [];
    const confirmed = Date.parse(setup.confirmed_at) <= time;
    setups[String(interval)] = {
      ...setup,
      orb_high: validOpening.length ? Math.max(...validOpening.map(c => c.high)) : null,
      orb_low: validOpening.length ? Math.min(...validOpening.map(c => c.low)) : null,
      range_locked: rangeLocked,
      signals: visibleSignals,
      // The final snapshot's agreement state must not appear before its time.
      confirmation_direction: confirmed ? setup.confirmation_direction : null,
      confirmed_at: confirmed ? setup.confirmed_at : null,
      confirmation_price: confirmed ? setup.confirmation_price : null
    };
  }
  return {
    ...snapshot, setups, instruments, replay_time: time,
    candles: fifteen.filter(c => c.is_closed), active_candle: fifteen.findLast(c => !c.is_closed) || null,
    candles_5m: five.filter(c => c.is_closed), active_candle_5m: five.findLast(c => !c.is_closed) || null,
    current_price: bars.at(-1)?.close ?? null,
    signals: setups['15'].range_locked && setups['5'].range_locked ? (snapshot.signals || []).filter(s => Date.parse(s.timestamp) <= time) : [],
    market_status: 'replay'
  };
}

function startReplay() {
  const candles = getCandles(view);
  if (!candles.length || loading || switchingSource) return;
  transition = null;
  pointer = null;
  const snapshot = JSON.parse(JSON.stringify(view));
  chartMotion.reset();
  const start = Math.min(...candles.map(c => Date.parse(c.start_time)));
  const last = candles.at(-1);
  const end = last.is_closed ? Date.parse(last.end_time) : Math.min(Date.parse(last.end_time), Date.parse(snapshot.last_tick_time) || Date.now());
  replay = { snapshot, start, end: Math.max(start + 1, end), started: performance.now(), duration: reducedMotion.matches ? 12000 : 24000, time: start, complete: false, frame: null };
  replay.five = getCandles(snapshot, 5);
  replay.fifteen = getCandles(snapshot, 15);
  replay.children = new Map(replay.fifteen.map(c => [c, replay.five.filter(child => candleClock(child).start >= candleClock(c).start && candleClock(child).start < candleClock(c).end)]));
  replay.frame = replayView(start);
  scheduleRender();
}

function stopReplay() {
  if (!replay) return;
  replay = null;
  chartMotion.reset();
  pointer = null;
  scheduleRender();
}

function displayView() { return replay ? replay.frame : view; }

function primaryDataGaps(data) {
  const five = getCandles(data, 5).filter(candle => candle.is_closed);
  const interval = five.length ? 5 : 15;
  const bars = five.length ? five : getCandles(data, 15).filter(candle => candle.is_closed);
  if (!bars.length) return { ranges: [], count: 0, interval };
  const byStart = new Map(bars.map(candle => [candleClock(candle).start, candle]));
  const start = candleClock(bars[0]).start, end = candleClock(bars.at(-1)).end;
  const ranges = [];
  let count = 0;
  // Only describe closed coverage already reached; never flag the unfilled
  // future part of a live session or a replay as missing data.
  for (let time = start; time < end && time < start + 375 * 60000; time += interval * 60000) {
    const candle = byStart.get(time);
    if (candle && completeCandle(candle)) continue;
    count++;
    const previous = ranges.at(-1);
    if (previous?.end === time) previous.end += interval * 60000;
    else ranges.push({ start: time, end: time + interval * 60000 });
  }
  return { ranges, count, interval };
}

function emptyStateText(data, missingFive) {
  if (loading) return switchingSource ? 'Switching quote source…' : 'Loading session…';
  if (loadError) return loadError;
  if (data?.feed_status === 'connecting' && selectedDate === today) return `Connecting to ${sourceName(selectedSource)}… Loading candle history.`;
  if (missingFive) return '5-minute data is unavailable for this session.\nSelect 15m to view the saved candles.';
  if (data?.message) return `${data.message}\n${data.history_note || ''}`;
  if (selectedDate === today && timeLabel(new Date()) < '09:15') return 'Market candles appear after 09:15 IST.';
  return `No candles for ${selectedDate}.\nChoose an earlier session or return when quotes arrive.`;
}

function statusText(data = displayView()) {
  if (switchingSource) return 'Switching source…';
  if (loading) return 'Loading…';
  if (replay) return replay.complete ? 'Replay complete' : 'OHLC replay';
  if (selectedDate !== today) return 'Historical';
  const label = timeLabel(new Date());
  const minutes = Number(label.slice(0, 2)) * 60 + Number(label.slice(3));
  if (minutes < 555 || minutes >= 930 || data?.market_status === 'closed') return 'Market closed';
  const lastTick = data?.last_tick_time && Date.parse(data.last_tick_time);
  if (connected && data?.market_status === 'live' && lastTick && Date.now() - lastTick < 90000) return 'Live';
  return 'Waiting for quotes';
}

function scheduleRender() {
  if (!renderFrame) renderFrame = requestAnimationFrame(render);
}

function clockState(data = displayView()) {
  return [statusText(data), comparisonStatus(primaryInstrument(data), data),
    ...comparisonInstruments(data).map(item => `${comparisonStatus(item, data)}:${qualifyingDirection(item, data)}`)].join('|');
}

function render(now = performance.now()) {
  renderFrame = 0;
  if (replay && !replay.complete) {
    const progress = Math.max(0, Math.min(1, (now - replay.started) / replay.duration));
    replay.time = replay.start + progress * (replay.end - replay.start);
    replay.complete = progress >= 1;
    replay.frame = replayView(replay.time);
  }
  const data = displayView();
  renderComparison();
  syncNavigation();
  $('price').textContent = number(data?.current_price);
  const status = statusText(data);
  if ($('session-status').textContent !== status) $('session-status').textContent = status;
  const setup = setupFor(data), high = setup.orb_high, low = setup.orb_low;
  $('range-name').textContent = `ORB ${setup.range_minutes}m`;
  $('confirmation-rule').textContent = `${setup.confirmation_minutes}m close · ${setup.range_minutes}m ORB`;
  $('range').textContent = high != null && low != null
    ? `${setup.range_minutes}m ORB ${setup.range_locked ? 'locked' : 'forming'}  H ${number(high)}  L ${number(low)}`
    : `ORB 09:15–${rangeEndLabel(setup)} IST`;
  $('range').title = `${setup.confirmation_minutes}-minute closes confirm the ${setup.range_minutes}-minute opening range. Hover over a range line to compare the other range.`;
  const signals = data?.signals || [];
  const summary = signals.length
    ? [...signals].sort((a, b) => a.timestamp.localeCompare(b.timestamp)).map(s => `${s.direction === 'BULLISH' ? '↑' : '↓'} ${timeLabel(s.timestamp)} both ORBs · close ${number(s.price)}`).join('   ·   ')
    : data && getCandles(data).length && ![15, 5].every(interval => setupFor(data, interval).range_locked) ? 'Opening ranges incomplete' : 'Waiting for both ORBs';
  if ($('signal-summary').textContent !== summary) $('signal-summary').textContent = summary;
  const feedData = live || view;
  const gaps = primaryDataGaps(data);
  const gapText = gaps.count ? `Data gap ${gaps.ranges.slice(0, 2).map(range => `${timeLabel(range.start)}–${timeLabel(range.end)}`).join(', ')}${gaps.ranges.length > 2 ? ` · +${gaps.ranges.length - 2} more` : ''}` : '';
  const feedState = switchingSource ? 'Switching source…' : sourceError || feedData?.feed_error
    || (!connected ? 'Reconnecting…' : ({ connecting: 'Connecting…', disconnected: 'Feed disconnected', error: 'Feed error' }[feedData?.feed_status]) || gapText);
  $('feed-status').hidden = !feedState;
  $('feed-status').textContent = feedState ? `${sourceName(selectedSource)} · ${feedState}` : '';
  $('feed-status').dataset.error = String(Boolean(sourceError || feedData?.feed_error || gaps.count));
  $('feed-status').title = [sourceError || feedData?.feed_error,
    gaps.count ? `${gaps.count} missing or incomplete ${gaps.interval}m NIFTY candles. Unavailable intervals remain gaps in the chart.` : '',
    historyNote].filter(Boolean).join('\n');
  updateShoonyaHelp(feedData);
  $('replay').disabled = !replay && (loading || switchingSource || !getCandles(view).length);
  $('replay').setAttribute('aria-pressed', String(Boolean(replay)));
  $('replay').setAttribute('aria-label', replay ? 'Stop replay and restore the session' : 'Replay this session');
  $('replay').title = replay ? 'Stop candle replay and restore the latest session data' : 'Candle-based replay: illustrative OHLC progression, including confirmed signals. Live quotes keep collecting.';
  $('replay-progress').hidden = !replay;
  if (replay) {
    const progress = `${timeLabel(replay.time)} · ${replay.complete ? 'Replay complete' : 'OHLC replay'}`;
    if ($('replay-progress').textContent !== progress) $('replay-progress').textContent = progress;
  }
  drawChart(now, data);
  lastClockState = clockState(data);
  if (transition || chartMotion.active || replay && !replay.complete) scheduleRender();
}

function drawChart(now = performance.now(), data = displayView()) {
  const width = canvas.clientWidth, height = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (width < 100 || height < 80) { clearSeriesLabels(); return; }
  const baseline = [primaryInstrument(data)?.session_open, ...comparisonInstruments(data).map(item => `${item.symbol}:${item.session_open}`)];
  const motionContext = JSON.stringify([selectedDate, data?.date, selectedSource, sourceEpoch, sourceGeneration, Boolean(replay), selectedComparisonGroup, baseline]);
  if (tipContext !== motionContext) { comparisonTips.clear(); clearSeriesLabels(); tipContext = motionContext; }
  chartMotion.begin(motionContext, now,
    !reducedMotion.matches && !loading && Boolean(replay || selectedDate === today && data?.market_status !== 'closed'));
  const left = 12, right = width - 76, top = 20, bottom = height - 30;
  // One fixed 09:15–15:30 axis for both candle intervals and opening ranges.
  const dayWidth = right - left;
  const xMinute = minute => left + (minute - 555) / 375 * dayWidth;
  const candles = getCandles(data);
  const empty = $('empty-state');
  empty.hidden = candles.length > 0;
  const missingFive = selectedInterval === 5 && !candles.length && getCandles(data, 15).length;
  if (!candles.length) empty.textContent = emptyStateText(data, missingFive);
  ctx.font = '10px ui-monospace, monospace';
  ctx.fillStyle = palette['chart-label'];
  ctx.textAlign = 'center';
  for (let slot = 0; slot < 25; slot += width < 650 ? 4 : 2) {
    const minutes = 555 + slot * 15;
    const label = `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
    ctx.fillText(label, xMinute(minutes), height - 10);
  }
  let progress = 1, outgoing = [];
  if (transition) {
    const fraction = Math.max(0, Math.min(1, (now - transition.started) / transition.duration));
    progress = easeTransition(fraction);
    outgoing = getCandles(data, transition.from);
    if (fraction >= 1) { transition = null; outgoing = []; }
  }
  if (!candles.length && !outgoing.length) {
    clearSeriesLabels();
    $('ohlc').textContent = '';
    canvas.setAttribute('aria-label', `NIFTY 50, ${selectedDate}, ${selectedInterval}-minute candles. ${empty.textContent}`);
    chartMotion.end();
    return;
  }
  const shownCandle = (candle, interval) => {
    if (candle.is_closed || replay) return candle;
    return { ...candle, ...Object.fromEntries(['open', 'high', 'low', 'close'].map(field => [field,
      chartMotion.value(`candle:${interval}:${candle.start_time}:${field}`, candle[field])])) };
  };
  const shownFive = getCandles(data, 5).map(c => shownCandle(c, 5));
  const shownFifteen = getCandles(data, 15).map(c => shownCandle(c, 15));
  const shown = new Map([...shownFive, ...shownFifteen].map(c => [c.start_time + ':' + (candleClock(c).end - candleClock(c).start), c]));
  const visualCandle = candle => shown.get(candle.start_time + ':' + (candleClock(candle).end - candleClock(candle).start)) || candle;
  const shownPrice = Number.isFinite(data?.current_price) && !replay ? chartMotion.value('price', data.current_price) : data?.current_price;
  // Keep the price scale steady too: both views describe the same session.
  const prices = [...shownFifteen, ...shownFive].flatMap(c => [c.low, c.high]);
  for (const interval of [15, 5]) {
    const setup = setupFor(data, interval);
    [setup.orb_high, setup.orb_low].forEach(p => { if (Number.isFinite(p)) prices.push(p); });
  }
  if (Number.isFinite(shownPrice)) prices.push(shownPrice);
  // Fit the blurred overview until NIFTY confirms, then exclude hidden neutral
  // indices. A later NIFTY hover temporarily fits all comparisons again.
  const shape = rangeShape(data, selectedInterval, now);
  const series = comparisonSeries(data);
  for (const item of series) {
    const { points } = item;
    const last = points.at(-1), previous = points.at(-2);
    const targetTime = last?.time;
    const key = `comparison:${selectedInterval}:${item.instrument.symbol}:${item.referenceTime}:${item.reference}`;
    const previousTip = comparisonTips.get(key);
    const active = selectedInterval === 5 ? item.instrument.active_candle_5m : item.instrument.active_candle;
    if (!last || last.gap || previousTip > last.time || active && (active.is_complete === false || active.close_is_valid === false)) {
      chartMotion.tracks.delete(`${key}:price`);
      chartMotion.tracks.delete(`${key}:time`);
    } else if (previous) {
      last.price = chartMotion.value(`${key}:price`, last.price, replay ? previous.price : last.price);
      last.normalized = item.origin * last.price / item.reference;
      if (replay) {
        last.time = Math.max(previous.time, chartMotion.value(`${key}:time`, last.time, previous.time));
      }
    }
    comparisonTips.set(key, targetTime);
  }
  const emphasisState = comparisonEmphasisState(data, series);
  const fittedSeries = series.filter(item => item.points.length && (emphasisState.overview || emphasisState.reveal || emphasisState.qualifying.has(item.instrument.symbol)));
  for (const { points } of fittedSeries) prices.push(...points.map(point => point.normalized));
  const scaleScope = JSON.stringify(fittedSeries.map(item => [item.instrument.symbol, item.referenceTime, item.reference]).sort());
  const { min, max } = chartMotion.fit(Math.min(...prices), Math.max(...prices), Math.max(20, (primaryInstrument(data)?.session_open || data?.current_price || 0) * .004), scaleScope);
  const y = price => bottom - (price - min) / (max - min) * (bottom - top);
  const line = (x1, y1, x2, y2, color, dash = []) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.setLineDash([]);
  };
  ctx.textAlign = 'left';
  for (let i = 0; i <= 6; i++) {
    const price = min + i / 6 * (max - min);
    line(left, y(price), right, y(price), palette.grid);
    ctx.fillStyle = palette['chart-label'];
    ctx.fillText(number(price), right + 9, y(price) + 3);
  }
  const primary = setupFor(data), secondary = setupFor(data, selectedInterval === 15 ? 5 : 15);
  const hasRange = setup => [setup.orb_high, setup.orb_low].every(Number.isFinite);
  const nearRange = pointer && pointer.x >= left && pointer.x <= right && [primary, secondary].some(setup => hasRange(setup) && [setup.orb_high, setup.orb_low].some(price => Math.abs(pointer.y - y(price)) <= 10));
  if (hasRange(secondary) && nearRange) {
    ctx.globalAlpha = .5;
    const openingX = xMinute(555 + secondary.range_minutes);
    line(openingX, top, openingX, bottom, palette['orb-line'], [1, 5]);
    for (const [price, label] of [[secondary.orb_high, 'H'], [secondary.orb_low, 'L']]) {
      line(left, y(price), right, y(price), palette['orb-line'], [1, 5]);
      ctx.fillStyle = palette['orb-label']; ctx.textAlign = 'left';
      ctx.fillText(`${secondary.range_minutes}m ${label}`, left + 5, y(price) - 5);
    }
    ctx.globalAlpha = 1;
  }
  if ([shape.high, shape.low].every(Number.isFinite)) {
    const highY = y(shape.high), lowY = y(shape.low), openingX = xMinute(shape.endMinute);
    ctx.fillStyle = palette['orb-fill'];
    ctx.fillRect(left, highY, dayWidth, Math.max(1, lowY - highY));
    ctx.fillStyle = palette['orb-opening'];
    ctx.fillRect(left, highY, openingX - left, Math.max(1, lowY - highY));
    line(openingX, top, openingX, bottom, palette['orb-line'], [2, 4]);
    for (const [price, label] of [[shape.high, 'ORB H'], [shape.low, 'ORB L']]) {
      line(left, y(price), right, y(price), palette['orb-line'], [5, 4]);
      ctx.textAlign = 'right'; ctx.fillStyle = palette['orb-label'];
      ctx.fillText(label, right - 5, y(price) - 5);
    }
  }
  const primaryLabel = primaryLabelGeometry(y(shownPrice), { right, top, bottom, width });
  const comparisonGeometry = { left, right, top, bottom, xMinute, y, primaryLabel };
  const emphasis = drawComparisons(data, series, comparisonGeometry, emphasisState);
  function geometry(candle, interval) {
    candle = visualCandle(candle);
    const label = timeLabel(candle.start_time);
    const minute = Number(label.slice(0, 2)) * 60 + Number(label.slice(3));
    const fifteenWidth = Math.min(26, dayWidth / 25 * .55);
    return { center: xMinute(minute + interval / 2), width: Math.max(1.5, fifteenWidth * interval / 15), open: candle.open, high: candle.high, low: candle.low, close: candle.close };
  }
  const parentFor = (candle, list) => list.find(parent => Date.parse(parent.start_time) <= Date.parse(candle.start_time) && Date.parse(candle.start_time) < Date.parse(parent.end_time));
  function paintCandle(candle, shape, alpha = 1) {
    if (alpha <= 0) return;
    ctx.globalAlpha = alpha * (candle.is_complete === false ? .65 : 1) * (emphasis.active ? .65 : 1);
    line(shape.center, y(shape.high), shape.center, y(shape.low), palette.wick);
    const bodyTop = Math.min(y(shape.open), y(shape.close));
    const bodyHeight = Math.max(1.5, Math.abs(y(shape.open) - y(shape.close)));
    ctx.fillStyle = shape.close >= shape.open ? palette.up : palette.down;
    ctx.fillRect(shape.center - shape.width / 2, bodyTop, shape.width, bodyHeight);
    ctx.strokeStyle = shape.close >= shape.open ? palette.up : palette.wick;
    ctx.strokeRect(shape.center - shape.width / 2, bodyTop, shape.width, bodyHeight);
    if (!candle.is_closed || candle.close_is_valid === false || candle.is_complete === false) {
      ctx.strokeStyle = palette.marker; ctx.setLineDash([2, 2]);
      ctx.strokeRect(shape.center - shape.width / 2 - 2, bodyTop - 2, shape.width + 4, bodyHeight + 4);
      ctx.setLineDash([]);
    }
    ctx.globalAlpha = 1;
  }
  const drawnShapes = new Map();
  const passes = [[selectedInterval, candles, transition ? progress : 1]];
  if (transition) passes.unshift([transition.from, outgoing, 1 - progress]);
  let hovered = null;
  for (const [interval, list, alpha] of passes) {
    const incoming = interval === selectedInterval;
    for (const candle of list) {
      const final = geometry(candle, interval);
      let shape = final;
      if (transition) {
        const otherInterval = incoming ? transition.from : transition.to;
        const otherList = incoming ? outgoing : candles;
        const parent = otherInterval === 15 ? parentFor(candle, otherList) : null;
        const children = incoming && otherInterval === 5 ? otherList.filter(c => candleClock(c).start >= candleClock(candle).start && candleClock(c).start < candleClock(candle).end) : [];
        const other = parent ? geometry(parent, 15) : children.length ? geometry(children[Math.floor(children.length / 2)], 5) : { ...final, width: geometry(candle, otherInterval).width };
        shape = incoming ? interpolate(other, final, progress) : interpolate(final, other, progress);
      }
      drawnShapes.set(`${interval}:${candle.start_time}`, shape);
      paintCandle(candle, shape, alpha);
      if (incoming && pointer && Math.abs(pointer.x - final.center) <= dayWidth / (375 / selectedInterval) / 2) hovered = candle;
    }
  }
  // Each view's arrows come from its own range and closing interval. The
  // footer separately records signals confirmed by both setups together.
  for (const [interval, list, alpha] of passes) {
    if (alpha <= 0) continue;
    for (const signal of setupFor(data, interval).signals || []) {
      const confirmedAt = Date.parse(signal.timestamp);
      const candle = list.find(c => Date.parse(c.start_time) < confirmedAt && Date.parse(c.end_time) >= confirmedAt);
      if (!candle) continue;
      const shape = drawnShapes.get(`${interval}:${candle.start_time}`) || geometry(candle, interval);
      const center = shape.center;
      const up = signal.direction === 'BULLISH';
      const markerY = up ? y(shape.low) + 12 : y(shape.high) - 12;
      ctx.globalAlpha = alpha; ctx.fillStyle = palette.marker; ctx.beginPath();
      ctx.moveTo(center, markerY + (up ? -4 : 4));
      ctx.lineTo(center - 4, markerY + (up ? 3 : -3));
      ctx.lineTo(center + 4, markerY + (up ? 3 : -3));
      ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
    }
  }
  if (Number.isFinite(data?.current_price)) {
    const priceY = y(shownPrice);
    line(left, priceY, right, priceY, palette['price-line'], [2, 4]);
    ctx.fillStyle = palette['price-fill']; ctx.fillRect(right, priceY - 10, 73, 20);
    ctx.fillStyle = palette['price-ink']; ctx.textAlign = 'center';
    ctx.fillText(number(data.current_price), right + 36, priceY + 3);
  }
  if (pointer && pointer.x >= left && pointer.x <= right && pointer.y >= top && pointer.y <= bottom) {
    line(pointer.x, top, pointer.x, bottom, palette.crosshair, [3, 3]);
    line(left, pointer.y, right, pointer.y, palette.crosshair, [3, 3]);
    const price = min + (bottom - pointer.y) / (bottom - top) * (max - min);
    if (pointer.y + 10 < primaryLabel.top || pointer.y - 10 > primaryLabel.top + primaryLabel.height) {
      ctx.fillStyle = palette['crosshair-fill']; ctx.fillRect(right, pointer.y - 10, 73, 20);
      ctx.fillStyle = palette['crosshair-ink']; ctx.textAlign = 'center';
      ctx.fillText(number(price), right + 36, pointer.y + 3);
    }
  }
  drawSeriesLabels(data, series, comparisonGeometry, emphasis, shownPrice);
  // Resolve hover against the geometry the user actually pointed at. Retain
  // that target until the pointer moves, so refitting cannot toggle its own
  // reveal off and on beneath a stationary pointer.
  chartHitTest = point => {
    const primary = point.x >= right && point.x <= width
      && point.y >= Math.min(primaryLabel.top, primaryLabel.priceY - 10)
      && point.y <= Math.max(primaryLabel.top + primaryLabel.height, primaryLabel.priceY + 10)
      || candles.some(candle => {
        const shape = geometry(candle, selectedInterval);
        return Math.abs(point.x - shape.center) <= shape.width / 2 + 5
          && point.y >= y(shape.high) - 5 && point.y <= y(shape.low) + 5;
      });
    let comparison = null, distance = 7;
    if (point.x >= left && point.x <= right && point.y >= top && point.y <= bottom) {
      for (const { instrument, points } of series) {
        if (!emphasis.qualifying.has(instrument.symbol)) continue;
        points.forEach((sample, index) => {
          const current = emphasis.position(sample), previous = index && !sample.gap ? emphasis.position(points[index - 1]) : current;
          const candidate = distanceToSegment(point, previous, current);
          if (candidate < distance) { comparison = instrument.symbol; distance = candidate; }
        });
      }
    }
    return { primary, comparison };
  };
  const focus = hovered || candles.at(-1);
  $('ohlc').textContent = focus ? `${timeLabel(focus.start_time)}  O ${number(focus.open)}  H ${number(focus.high)}  L ${number(focus.low)}  C ${number(focus.close)}${focus.close_is_valid === false ? '  · stale close' : focus.is_complete === false ? '  · incomplete' : focus.is_closed ? '' : '  · forming'}` : 'Move over a candle to inspect';
  canvas.setAttribute('aria-label', `NIFTY 50, ${selectedDate}, ${candles.length} candles, ${selectedInterval}-minute interval. Last price ${number(data?.current_price)}. ${$('range').textContent}. ${$('signal-summary').textContent}. ${selectedComparisonGroup ? `${comparisonGroups[selectedComparisonGroup].label} comparisons` : 'NIFTY only'}. ${[...seriesLabels.values()].filter(label => !label.hidden).map(label => label.getAttribute('aria-label')).join('. ')}, percentage change since 09:15 open.${replay ? ' Illustrative candle replay.' : ''}`);
  chartMotion.end();
}

function resetSession() {
  stopReplay();
  transition = null;
  live = view = null;
  ++requestId;
  requestController?.abort();
}

function handleEvent(message) {
  if (!message.date) return;
  const fullSnapshot = ['init', 'session', 'status'].includes(message.type);
  const previousFeedState = `${live?.feed_status}:${live?.feed_error}`;
  let refreshDates = false;
  if (fullSnapshot && message.source_epoch && message.source_epoch !== sourceEpoch) {
    const restarted = sourceEpoch !== null;
    sourceEpoch = message.source_epoch;
    sourceGeneration = -1;
    if (restarted) {
      resetSession();
      refreshDates = true;
    }
  }
  if (message.source_epoch && sourceEpoch && message.source_epoch !== sourceEpoch) return;
  if (message.source_generation != null && message.source_generation < sourceGeneration) return;
  if (message.source_id && message.source_id !== selectedSource) {
    if (switchingSource || !fullSnapshot) return;
    resetSession();
    selectedSource = message.source_id;
    sourceError = '';
    refreshDates = true;
  }
  sourceGeneration = Math.max(sourceGeneration, message.source_generation ?? -1);
  const pending = pendingSourceSwitch;
  if (pending && fullSnapshot && message.source_id === pending.source) {
    const newer = message.source_epoch && message.source_epoch !== pending.epoch || message.source_generation > pending.generation;
    const targetSnapshot = ['init', 'session'].includes(message.type) && (message.source_generation == null || message.source_generation >= pending.generation)
      && pending.source !== pending.previousSource;
    if (newer || targetSnapshot) pending.confirmed = true;
  }
  if (message.today && message.today !== today) {
    today = message.today;
    refreshDates = true;
    if (followingToday) { stopReplay(); selectedDate = today; view = null; pointer = null; }
  }
  if (fullSnapshot) {
    if (message.type !== 'status') chartMotion.reset();
    live = message;
    if (message.type !== 'status') refreshDates = true;
  } else {
    if (!live || message.date !== live.date) return;
    if (message.instruments) {
      live.instruments = { ...live.instruments };
      for (const [symbol, fields] of Object.entries(message.instruments)) {
        live.instruments[symbol] = { ...live.instruments[symbol], ...fields };
      }
    }
    if (message.type === 'tick' || message.type === 'state') {
      const { instruments, ...fields } = message;
      if (!message.symbol || message.symbol === (live.primary_symbol || '^NSEI')) {
        Object.assign(live, fields);
        if (message.price != null) live.current_price = message.price;
      }
    } else if (message.type === 'candle_close') {
      const five = message.interval_minutes === 5;
      const closedKey = five ? 'candles_5m' : 'candles';
      const activeKey = five ? 'active_candle_5m' : 'active_candle';
      live[closedKey] = [...(live[closedKey] || []).filter(c => c.start_time !== message.candle.start_time), message.candle];
      if (live[activeKey]?.start_time === message.candle.start_time) live[activeKey] = null;
      Object.assign(live, Object.fromEntries(['setups', 'signals', 'orb_high', 'orb_low', 'range_locked', 'market_status', 'feed_status', 'feed_error'].filter(k => k in message).map(k => [k, message[k]])));
    }
  }
  if (selectedDate === live.date && (!switchingSource || pendingSourceSwitch?.confirmed)) {
    ++requestId;
    requestController?.abort();
    view = live;
    loading = false;
    loadError = '';
  } else if (!view && !switchingSource) selectDate(selectedDate, followingToday);
  if (refreshDates) loadDates();
  const quietQuote = !fullSnapshot && ['tick', 'state'].includes(message.type)
    && (selectedDate !== live.date || replay?.complete) && connected
    && live.feed_status === 'live' && !live.feed_error && !sourceError
    && previousFeedState === `${live.feed_status}:${live.feed_error}`;
  if (!quietQuote) scheduleRender();
}

$('previous').addEventListener('click', () => moveDate(-1));
$('next').addEventListener('click', () => moveDate(1));
$('today').addEventListener('click', () => { selectDate(today, true); loadDates(); });
$('session-date').addEventListener('change', e => selectDate(e.target.value, e.target.value === today));
$('source-select').addEventListener('change', e => switchSource(e.target.value));
$('source-retry').addEventListener('click', () => switchSource('shoonya'));
$('shoonya-help').addEventListener('toggle', () => { if ($('shoonya-help').open && !$('shoonya-help').hidden) loadShoonyaHelp(); });
$('check-ip').addEventListener('click', checkPublicIp);
$('interval-15m').addEventListener('click', () => setIntervalMinutes(15));
$('interval-5m').addEventListener('click', () => setIntervalMinutes(5));
$('theme-toggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme !== 'dark'));
document.querySelector('.instrument').addEventListener('pointerenter', () => { hoveredComparison = '^NSEI'; scheduleRender(); });
document.querySelector('.instrument').addEventListener('pointerleave', () => { if (hoveredComparison === '^NSEI') hoveredComparison = null; scheduleRender(); });
for (const [group, { id }] of Object.entries(comparisonGroups)) {
  $(id).addEventListener('click', () => setComparisonGroup(group, true));
  $(id).addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 'derivatives' : event.key === 'End' ? 'sectors'
      : group === 'derivatives' ? 'sectors' : 'derivatives';
    setComparisonGroup(next);
    $(comparisonGroups[next].id).focus();
  });
}
$('replay').addEventListener('click', () => replay ? stopReplay() : startReplay());
reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) { chartMotion.finish(); transition = null; } scheduleRender(); });
$('chart-wrap').addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); moveDate(e.key === 'ArrowLeft' ? -1 : 1); }
  if (e.key === 'Home') { e.preventDefault(); selectDate(today, true); }
  if (e.key === 'Escape' && replay) { e.preventDefault(); stopReplay(); }
});
$('chart-wrap').addEventListener('pointermove', e => {
  const rect = canvas.getBoundingClientRect();
  const point = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  if (pointer?.x !== point.x || pointer?.y !== point.y) {
    // Keep the initiating hit area as the scale moves. Small pointer movements
    // within that candle or line must not repeatedly refit the overview.
    const anchored = chartHoverAnchor?.test(point);
    if (chartHoverAnchor?.comparison && anchored?.comparison === chartHoverAnchor.comparison) {
      chartHover = { primary: false, comparison: chartHoverAnchor.comparison };
    } else if (chartHoverAnchor?.primary && anchored?.primary) {
      chartHover = { primary: true, comparison: null };
    } else {
      chartHover = chartHitTest?.(point) || { primary: false, comparison: null };
      chartHoverAnchor = chartHitTest && (chartHover.primary || chartHover.comparison)
        ? { ...chartHover, test: chartHitTest } : null;
    }
    pointer = point;
  }
  scheduleRender();
});
$('chart-wrap').addEventListener('pointerleave', () => {
  pointer = null;
  chartHover = { primary: false, comparison: null };
  chartHoverAnchor = null;
  scheduleRender();
});
new ResizeObserver(() => {
  chartHover = { primary: false, comparison: null };
  chartHitTest = null;
  chartHoverAnchor = null;
  scheduleRender();
}).observe($('chart-wrap'));
const stream = new EventSource('/api/stream');
stream.onopen = () => { connected = true; scheduleRender(); };
stream.onerror = () => { connected = false; scheduleRender(); };
stream.onmessage = event => {
  try { handleEvent(JSON.parse(event.data)); } catch (error) { console.error('Invalid feed event', error); }
};
setInterval(() => {
  const date = sessionDate();
  if (date !== today) {
    today = date;
    if (followingToday) selectDate(today, true);
    loadDates();
  }
  if (clockState() !== lastClockState) scheduleRender();
}, 1000);
selectDate(today, true);
loadDates();
