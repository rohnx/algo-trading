const $ = id => document.getElementById(id);
const canvas = $('chart');
const ctx = canvas.getContext('2d');
const dateFormat = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' });
const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const sessionDate = (time = new Date()) => dateFormat.format(new Date(time));
const timeLabel = time => timeFormat.format(new Date(time));
const number = value => Number.isFinite(value) ? value.toFixed(2) : '—';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const sourceName = source => source === 'shoonya' ? 'Shoonya' : 'Yahoo Finance';
let today = sessionDate();
let selectedDate = today;
let followingToday = true;
let selectedInterval = 15;
let selectedSource = 'yahoo';
let sourceGeneration = -1;
let sourceEpoch = null;
let switchingSource = false;
let sourceError = '';
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
let rangeEchoUntil = -Infinity;
let replay = null;
let renderFrame = 0;
let palette;

function readPalette() {
  const style = getComputedStyle(document.documentElement);
  palette = Object.fromEntries(['up', 'down', 'wick', 'grid', 'chart-label', 'orb-line', 'orb-label', 'orb-fill', 'orb-opening', 'marker', 'price-line', 'price-fill', 'price-ink', 'crosshair', 'crosshair-fill', 'crosshair-ink'].map(key => [key, style.getPropertyValue(`--${key}`).trim()]));
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
    const response = await fetch('/api/history');
    if (!response.ok) throw new Error('History unavailable');
    const data = await response.json();
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
  transition = null;
  rangeEchoUntil = -Infinity;
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
    const response = await fetch(`/api/session?date=${date}`, { signal: requestController.signal });
    if (!response.ok) throw new Error(`Session unavailable (${response.status})`);
    const data = await response.json();
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
  stopReplay();
  transition = null;
  rangeEchoUntil = -Infinity;
  const previousSource = selectedSource, previousLive = live, previousView = view;
  selectedSource = source;
  switchingSource = true;
  sourceError = '';
  pointer = null;
  ++requestId;
  requestController?.abort();
  view = null;
  live = null;
  loading = true;
  scheduleRender();
  try {
    const response = await fetch('/api/source', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.message || `Source switch failed (${response.status})`);
    selectedSource = data.source_id || source;
    if (data.source_epoch && data.source_epoch !== sourceEpoch) { sourceEpoch = data.source_epoch; sourceGeneration = -1; }
    sourceGeneration = Math.max(sourceGeneration, data.source_generation ?? -1);
    live = data;
    switchingSource = false;
    loading = false;
    if (selectedDate === live.date) view = live;
    else await selectDate(selectedDate, followingToday);
    loadDates();
  } catch (error) {
    selectedSource = previousSource;
    live = previousLive;
    view = previousView?.date === selectedDate ? previousView : null;
    sourceError = `Could not switch to ${sourceName(source)}: ${error.message}`;
    switchingSource = false;
    if (!view) await selectDate(selectedDate, followingToday);
  } finally {
    switchingSource = false;
    loading = false;
    scheduleRender();
  }
}

function moveDate(direction) {
  const date = direction < 0 ? dates.filter(d => d < selectedDate).at(-1) : dates.find(d => d > selectedDate);
  if (date) selectDate(date, date === today);
}

function getCandles(data = displayView(), interval = selectedInterval) {
  const closed = interval === 5 ? data?.candles_5m : data?.candles;
  const active = interval === 5 ? data?.active_candle_5m : data?.active_candle;
  const all = [...(closed || []), ...(active ? [active] : [])];
  return [...new Map(all.filter(c => sessionDate(c.start_time) === selectedDate && [c.open, c.high, c.low, c.close].every(Number.isFinite)).map(c => [c.start_time, c])).values()]
    .sort((a, b) => a.start_time.localeCompare(b.start_time));
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
function rangeShape(data, interval, now = performance.now()) {
  const setup = setupFor(data, interval);
  const target = { high: setup.orb_high, low: setup.orb_low, endMinute: 555 + setup.range_minutes };
  if (!transition || interval !== transition.to || !transition.rangeFrom) return target;
  const from = transition.rangeFrom;
  const progress = easeTransition(Math.max(0, Math.min(1, (now - transition.started) / transition.duration)));
  if (![from.high, from.low, target.high, target.low].every(Number.isFinite)) return target;
  return Object.fromEntries(Object.keys(target).map(key => [key, from[key] + (target[key] - from[key]) * progress]));
}

function setIntervalMinutes(minutes) {
  if (minutes === selectedInterval) return;
  const previous = selectedInterval, now = performance.now();
  const rangeFrom = rangeShape(displayView(), previous, now);
  selectedInterval = minutes;
  pointer = null;
  rangeEchoUntil = now + (reducedMotion.matches ? 0 : 1800);
  transition = reducedMotion.matches ? null : { from: previous, to: minutes, started: now, duration: 420, rangeFrom };
  scheduleRender();
}

// Saved bars follow an illustrative open/extreme/extreme/close path. This is
// candle playback, not a reconstruction of historical tick ordering.
function partialCandle(candle, time, capturedAt) {
  const start = Date.parse(candle.start_time), end = Date.parse(candle.end_time);
  if (time < start) return null;
  const observedEnd = candle.is_closed ? end : Math.min(end, Math.max(start + 1, capturedAt));
  if (time >= observedEnd) return { ...candle };
  if (reducedMotion.matches) return { ...candle, high: candle.open, low: candle.open, close: candle.open, volume: 0, is_closed: false };
  const progress = Math.max(0, Math.min(1, (time - start) / (observedEnd - start)));
  const path = candle.close >= candle.open ? [candle.open, candle.low, candle.high, candle.close] : [candle.open, candle.high, candle.low, candle.close];
  const segment = Math.min(2, Math.floor(progress * 3));
  const price = path[segment] + (path[segment + 1] - path[segment]) * (progress * 3 - segment);
  const observed = [...path.slice(0, segment + 1), price];
  return { ...candle, high: Math.max(...observed), low: Math.min(...observed), close: price, volume: Math.floor((candle.volume || 0) * progress), is_closed: false };
}

function replayView(time) {
  const snapshot = replay.snapshot;
  const originalFive = getCandles(snapshot, 5);
  const five = originalFive.map(c => partialCandle(c, time, replay.end)).filter(Boolean);
  const fifteen = getCandles(snapshot, 15).map(candle => {
    const start = Date.parse(candle.start_time), end = Date.parse(candle.end_time);
    const children = originalFive.filter(c => Date.parse(c.start_time) >= start && Date.parse(c.start_time) < end);
    const coverage = children.length && children.every((c, i) => Date.parse(c.start_time) === start + i * 300000) && (!candle.is_closed || children.length === 3);
    if (!coverage) return partialCandle(candle, time, replay.end);
    const shown = five.filter(c => Date.parse(c.start_time) >= start && Date.parse(c.start_time) < end);
    if (!shown.length) return null;
    // Both displays use the same saved 5m progression when available. Genuine
    // smaller bars can be aggregated; a saved 15m bar is never split into data.
    return { ...candle, open: shown[0].open, high: Math.max(...shown.map(c => c.high)), low: Math.min(...shown.map(c => c.low)), close: shown.at(-1).close, volume: shown.reduce((total, c) => total + (c.volume || 0), 0), is_closed: candle.is_closed && time >= end };
  }).filter(Boolean);
  const bars = five.length ? five : fifteen;
  const setups = {};
  for (const interval of [15, 5]) {
    const setup = setupFor(snapshot, interval);
    const openingStart = Date.parse(`${selectedDate}T09:15:00+05:30`);
    const openingEnd = openingStart + setup.range_minutes * 60000;
    const opening = (interval === 5 ? five : fifteen).filter(c => Date.parse(c.start_time) >= openingStart && Date.parse(c.start_time) < openingEnd);
    const validOpening = opening.filter(c => c.is_complete !== false && c.close_is_valid !== false);
    const expected = setup.range_minutes / interval;
    const rangeLocked = Boolean(setup.range_locked && time >= openingEnd && validOpening.length === expected && validOpening.every((c, index) => c.is_closed && Date.parse(c.start_time) === openingStart + index * interval * 60000));
    const visibleSignals = rangeLocked ? (setup.signals || []).filter(s => Date.parse(s.timestamp) <= time) : [];
    setups[String(interval)] = {
      ...setup,
      orb_high: validOpening.length ? Math.max(...validOpening.map(c => c.high)) : null,
      orb_low: validOpening.length ? Math.min(...validOpening.map(c => c.low)) : null,
      range_locked: rangeLocked,
      signals: visibleSignals,
      // The final snapshot's agreement state must not appear before its time.
      confirmation_direction: Date.parse(setup.confirmed_at) <= time ? setup.confirmation_direction : null,
      confirmed_at: Date.parse(setup.confirmed_at) <= time ? setup.confirmed_at : null,
      confirmation_price: Date.parse(setup.confirmed_at) <= time ? setup.confirmation_price : null
    };
  }
  return {
    ...snapshot, setups,
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
  rangeEchoUntil = -Infinity;
  pointer = null;
  const snapshot = JSON.parse(JSON.stringify(view));
  const start = Math.min(...candles.map(c => Date.parse(c.start_time)));
  const last = candles.at(-1);
  const end = last.is_closed ? Date.parse(last.end_time) : Math.min(Date.parse(last.end_time), Date.parse(snapshot.last_tick_time) || Date.now());
  replay = { snapshot, start, end: Math.max(start + 1, end), started: performance.now(), duration: reducedMotion.matches ? 12000 : 24000, time: start, complete: false, frame: null };
  replay.frame = replayView(start);
  scheduleRender();
}

function stopReplay() {
  if (!replay) return;
  replay = null;
  pointer = null;
  scheduleRender();
}

function displayView() { return replay ? replay.frame : view; }

function statusText(data = displayView()) {
  if (switchingSource) return 'Switching source…';
  if (loading) return 'Loading…';
  if (replay) return replay.complete ? 'Replay complete' : 'OHLC replay';
  if (data?.market_status === 'replay') return 'Replay';
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

function render(now = performance.now()) {
  renderFrame = 0;
  if (replay && !replay.complete) {
    const progress = Math.max(0, Math.min(1, (now - replay.started) / replay.duration));
    replay.time = replay.start + progress * (replay.end - replay.start);
    replay.complete = progress >= 1;
    replay.frame = replayView(replay.time);
  }
  const data = displayView();
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
  const feedState = switchingSource ? 'Switching source…' : sourceError || feedData?.feed_error || ({ connecting: 'Connecting…', live: 'Feed live', disconnected: 'Feed disconnected', error: 'Feed error' }[feedData?.feed_status]) || (connected ? 'Feed connected' : 'Reconnecting…');
  $('feed-status').textContent = `${sourceName(selectedSource)} · ${feedState} · IST`;
  $('feed-status').dataset.error = String(Boolean(sourceError || feedData?.feed_error));
  $('feed-status').title = [sourceError || feedData?.feed_error, historyNote].filter(Boolean).join('\n');
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
  if (transition || now < rangeEchoUntil || replay && !replay.complete) scheduleRender();
}

function drawChart(now = performance.now(), data = displayView()) {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  ctx.clearRect(0, 0, width, height);
  if (width < 100 || height < 80) return;
  const left = 12, right = width - 76, top = 28, bottom = height - 30;
  // One fixed 09:15–15:30 axis for both candle intervals and opening ranges.
  const dayWidth = right - left;
  const xMinute = minute => left + (minute - 555) / 375 * dayWidth;
  const candles = getCandles(data);
  const empty = $('empty-state');
  empty.hidden = candles.length > 0;
  const missingFive = selectedInterval === 5 && !candles.length && getCandles(data, 15).length;
  empty.textContent = loading ? switchingSource ? 'Switching quote source…' : 'Loading session…' : loadError || (missingFive ? '5-minute data is unavailable for this session.\nSelect 15m to view the saved candles.' : data?.message ? `${data.message}\n${data.history_note || ''}` : `No candles for ${selectedDate}.\nChoose an earlier session or return when quotes arrive.`);
  ctx.font = '10px ui-monospace, monospace';
  ctx.fillStyle = palette['chart-label'];
  ctx.textAlign = 'center';
  for (let slot = 0; slot < 25; slot += width < 650 ? 4 : 2) {
    const minutes = 555 + slot * 15;
    const label = `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
    ctx.fillText(label, xMinute(minutes + 7.5), height - 10);
  }
  let progress = 1, outgoing = [];
  if (transition) {
    const fraction = Math.max(0, Math.min(1, (now - transition.started) / transition.duration));
    progress = easeTransition(fraction);
    outgoing = getCandles(data, transition.from);
    if (fraction >= 1) { transition = null; outgoing = []; }
  }
  if (!candles.length && !outgoing.length) {
    $('ohlc').textContent = 'Move over a candle to inspect';
    canvas.setAttribute('aria-label', `NIFTY 50, ${selectedDate}, ${selectedInterval}-minute candles. ${empty.textContent}`);
    return;
  }
  // Keep the price scale steady too: both views describe the same session.
  const prices = [...getCandles(data, 15), ...getCandles(data, 5)].flatMap(c => [c.low, c.high]);
  for (const interval of [15, 5]) {
    const setup = setupFor(data, interval);
    [setup.orb_high, setup.orb_low].forEach(p => { if (Number.isFinite(p)) prices.push(p); });
  }
  if (Number.isFinite(data?.current_price)) prices.push(data.current_price);
  let min = Math.min(...prices), max = Math.max(...prices);
  const padding = (max - min || 20) * .12;
  min -= padding; max += padding;
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
  const shape = rangeShape(data, selectedInterval, now);
  const hasRange = setup => [setup.orb_high, setup.orb_low].every(Number.isFinite);
  const nearRange = pointer && pointer.x >= left && pointer.x <= right && [primary, secondary].some(setup => hasRange(setup) && [setup.orb_high, setup.orb_low].some(price => Math.abs(pointer.y - y(price)) <= 10));
  const echo = Math.max(0, Math.min(1, (rangeEchoUntil - now) / 1380));
  if (hasRange(secondary) && (nearRange || echo > 0)) {
    ctx.globalAlpha = nearRange ? .5 : .28 * echo;
    ctx.shadowColor = palette['orb-line']; ctx.shadowBlur = nearRange ? 0 : 2;
    const openingX = xMinute(555 + secondary.range_minutes);
    line(openingX, top, openingX, bottom, palette['orb-line'], [1, 5]);
    for (const [price, label] of [[secondary.orb_high, 'H'], [secondary.orb_low, 'L']]) {
      line(left, y(price), right, y(price), palette['orb-line'], [1, 5]);
      if (nearRange) {
        ctx.fillStyle = palette['orb-label']; ctx.textAlign = 'left';
        ctx.fillText(`${secondary.range_minutes}m ${label}`, left + 5, y(price) - 5);
      }
    }
    ctx.shadowBlur = 0; ctx.globalAlpha = 1;
  }
  if ([shape.high, shape.low].every(Number.isFinite)) {
    const highY = y(shape.high), lowY = y(shape.low), openingX = xMinute(shape.endMinute);
    ctx.fillStyle = palette['orb-fill'];
    ctx.fillRect(left, highY, dayWidth, Math.max(1, lowY - highY));
    ctx.fillStyle = palette['orb-opening'];
    ctx.fillRect(left, highY, openingX - left, Math.max(1, lowY - highY));
    line(openingX, top, openingX, bottom, palette['orb-line'], [2, 4]);
    ctx.fillStyle = palette['orb-label']; ctx.textAlign = 'left';
    ctx.fillText(rangeEndLabel(primary), openingX + 5, top - 10);
    for (const [price, label] of [[shape.high, 'ORB H'], [shape.low, 'ORB L']]) {
      line(left, y(price), right, y(price), palette['orb-line'], [5, 4]);
      ctx.textAlign = 'right'; ctx.fillStyle = palette['orb-label'];
      ctx.fillText(label, right - 5, y(price) - 5);
    }
  }
  function geometry(candle, interval) {
    const label = timeLabel(candle.start_time);
    const minute = Number(label.slice(0, 2)) * 60 + Number(label.slice(3));
    const fifteenWidth = Math.min(26, dayWidth / 25 * .55);
    return { center: xMinute(minute + interval / 2), width: Math.max(1.5, fifteenWidth * interval / 15), open: candle.open, high: candle.high, low: candle.low, close: candle.close };
  }
  const interpolate = (a, b, fraction) => Object.fromEntries(Object.keys(a).map(k => [k, a[k] + (b[k] - a[k]) * fraction]));
  const parentFor = (candle, list) => list.find(parent => Date.parse(parent.start_time) <= Date.parse(candle.start_time) && Date.parse(candle.start_time) < Date.parse(parent.end_time));
  function paintCandle(candle, shape, alpha = 1) {
    if (alpha <= 0) return;
    ctx.globalAlpha = alpha * (candle.is_complete === false ? .65 : 1);
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
  if (transition) {
    for (const candle of outgoing) {
      const original = geometry(candle, transition.from);
      const parent = transition.to === 15 ? parentFor(candle, candles) : null;
      const destination = parent ? geometry(parent, 15) : { ...original, width: geometry(candle, transition.to).width };
      const shape = interpolate(original, destination, progress);
      drawnShapes.set(`${transition.from}:${candle.start_time}`, shape);
      paintCandle(candle, shape, 1 - progress);
    }
  }
  let hovered = null;
  for (const candle of candles) {
    const final = geometry(candle, selectedInterval);
    let shape = final;
    if (transition) {
      const parent = transition.from === 15 ? parentFor(candle, outgoing) : null;
      const children = transition.from === 5 ? outgoing.filter(c => Date.parse(c.start_time) >= Date.parse(candle.start_time) && Date.parse(c.start_time) < Date.parse(candle.end_time)) : [];
      const origin = parent ? geometry(parent, 15) : children.length ? geometry(children[Math.floor(children.length / 2)], 5) : { ...final, width: geometry(candle, transition.from).width };
      shape = interpolate(origin, final, progress);
    }
    drawnShapes.set(`${selectedInterval}:${candle.start_time}`, shape);
    paintCandle(candle, shape, transition ? progress : 1);
    if (pointer && Math.abs(pointer.x - final.center) <= dayWidth / (375 / selectedInterval) / 2) hovered = candle;
  }
  function paintSignals(interval, list, alpha) {
    if (alpha <= 0) return;
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
  // Each view's arrows come from its own range and closing interval. The
  // footer separately records signals confirmed by both setups together.
  if (transition) paintSignals(transition.from, outgoing, 1 - progress);
  paintSignals(selectedInterval, candles, transition ? progress : 1);
  if (Number.isFinite(data?.current_price)) {
    const priceY = y(data.current_price);
    line(left, priceY, right, priceY, palette['price-line'], [2, 4]);
    ctx.fillStyle = palette['price-fill']; ctx.fillRect(right, priceY - 10, 73, 20);
    ctx.fillStyle = palette['price-ink']; ctx.textAlign = 'center';
    ctx.fillText(number(data.current_price), right + 36, priceY + 3);
  }
  if (pointer && pointer.x >= left && pointer.x <= right && pointer.y >= top && pointer.y <= bottom) {
    line(pointer.x, top, pointer.x, bottom, palette.crosshair, [3, 3]);
    line(left, pointer.y, right, pointer.y, palette.crosshair, [3, 3]);
    const price = min + (bottom - pointer.y) / (bottom - top) * (max - min);
    ctx.fillStyle = palette['crosshair-fill']; ctx.fillRect(right, pointer.y - 10, 73, 20);
    ctx.fillStyle = palette['crosshair-ink']; ctx.textAlign = 'center';
    ctx.fillText(number(price), right + 36, pointer.y + 3);
  }
  const focus = hovered || candles.at(-1);
  $('ohlc').textContent = focus ? `${timeLabel(focus.start_time)}  O ${number(focus.open)}  H ${number(focus.high)}  L ${number(focus.low)}  C ${number(focus.close)}${focus.close_is_valid === false ? '  · stale close' : focus.is_complete === false ? '  · incomplete' : focus.is_closed ? '' : '  · forming'}` : 'Move over a candle to inspect';
  canvas.setAttribute('aria-label', `NIFTY 50, ${selectedDate}, ${candles.length} candles, ${selectedInterval}-minute interval. Last price ${number(data?.current_price)}. ${$('range').textContent}. ${$('signal-summary').textContent}.${replay ? ' Illustrative candle replay.' : ''}`);
}

function handleEvent(message) {
  if (!message.date) return;
  const fullSnapshot = ['init', 'session', 'status'].includes(message.type);
  if (fullSnapshot && message.source_epoch && message.source_epoch !== sourceEpoch) {
    const restarted = sourceEpoch !== null;
    sourceEpoch = message.source_epoch;
    sourceGeneration = -1;
    if (restarted) {
      stopReplay();
      transition = null;
      rangeEchoUntil = -Infinity;
      live = null;
      view = null;
      ++requestId;
      requestController?.abort();
      loadDates();
    }
  }
  if (message.source_epoch && sourceEpoch && message.source_epoch !== sourceEpoch) return;
  if (message.source_generation != null && message.source_generation < sourceGeneration) return;
  if (message.source_id && message.source_id !== selectedSource) {
    if (switchingSource || !fullSnapshot) return;
    stopReplay();
    transition = null;
    rangeEchoUntil = -Infinity;
    selectedSource = message.source_id;
    live = null;
    view = null;
    sourceError = '';
    ++requestId;
    requestController?.abort();
    loadDates();
  }
  sourceGeneration = Math.max(sourceGeneration, message.source_generation ?? -1);
  if (message.today && message.today !== today) {
    today = message.today;
    loadDates();
    if (followingToday) { stopReplay(); selectedDate = today; view = null; pointer = null; }
  }
  if (fullSnapshot) {
    const firstSession = !live || message.type === 'session';
    live = message;
    if (message.type !== 'status') loadDates();
    if (message.market_status === 'replay' && firstSession && followingToday && message.date !== today) {
      stopReplay();
      selectedDate = message.date;
      followingToday = false;
    }
  } else {
    if (!live || message.date !== live.date) return;
    if (message.type === 'tick' || message.type === 'state') {
      Object.assign(live, message);
      if (message.price != null) live.current_price = message.price;
    } else if (message.type === 'candle_close') {
      const five = message.interval_minutes === 5;
      const closedKey = five ? 'candles_5m' : 'candles';
      const activeKey = five ? 'active_candle_5m' : 'active_candle';
      live[closedKey] = [...(live[closedKey] || []).filter(c => c.start_time !== message.candle.start_time), message.candle];
      if (live[activeKey]?.start_time === message.candle.start_time) live[activeKey] = null;
      Object.assign(live, Object.fromEntries(['setups', 'signals', 'orb_high', 'orb_low', 'range_locked', 'market_status', 'feed_status', 'feed_error'].filter(k => k in message).map(k => [k, message[k]])));
    } else if (message.type === 'signal') {
      if (!(live.signals || []).some(s => s.timestamp === message.signal.timestamp && s.direction === message.signal.direction)) live.signals = [message.signal, ...(live.signals || [])];
    }
  }
  if (selectedDate === live.date && !switchingSource) {
    ++requestId;
    requestController?.abort();
    view = live;
    loading = false;
    loadError = '';
  } else if (!view && !switchingSource) selectDate(selectedDate, followingToday);
  scheduleRender();
}

$('previous').addEventListener('click', () => moveDate(-1));
$('next').addEventListener('click', () => moveDate(1));
$('today').addEventListener('click', () => { selectDate(today, true); loadDates(); });
$('session-date').addEventListener('change', e => selectDate(e.target.value, e.target.value === today));
$('source-select').addEventListener('change', e => switchSource(e.target.value));
$('interval-15m').addEventListener('click', () => setIntervalMinutes(15));
$('interval-5m').addEventListener('click', () => setIntervalMinutes(5));
$('theme-toggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme !== 'dark'));
$('replay').addEventListener('click', () => replay ? stopReplay() : startReplay());
reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) { transition = null; rangeEchoUntil = -Infinity; } scheduleRender(); });
$('chart-wrap').addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); moveDate(e.key === 'ArrowLeft' ? -1 : 1); }
  if (e.key === 'Home') { e.preventDefault(); selectDate(today, true); }
  if (e.key === 'Escape' && replay) { e.preventDefault(); stopReplay(); }
});
canvas.addEventListener('pointermove', e => {
  const rect = canvas.getBoundingClientRect();
  pointer = { x: e.clientX - rect.left, y: e.clientY - rect.top }; scheduleRender();
});
canvas.addEventListener('pointerleave', () => { pointer = null; scheduleRender(); });
new ResizeObserver(() => {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * dpr);
  canvas.height = Math.round(canvas.clientHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); scheduleRender();
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
  $('session-status').textContent = statusText();
}, 1000);
setInterval(loadDates, 60000);
selectDate(today, true);
loadDates();
