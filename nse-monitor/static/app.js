const $ = id => document.getElementById(id);
const canvas = $('chart');
const ctx = canvas.getContext('2d');
const dateFormat = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' });
const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const sessionDate = (time = new Date()) => dateFormat.format(new Date(time));
const timeLabel = time => timeFormat.format(new Date(time));
const number = value => Number.isFinite(value) ? value.toFixed(2) : '—';
let today = sessionDate();
let selectedDate = today;
let followingToday = true;
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

function syncNavigation() {
  $('session-date').value = selectedDate;
  $('session-date').max = today;
  $('previous').disabled = !dates.some(date => date < selectedDate);
  $('next').disabled = !dates.some(date => date > selectedDate);
  $('today').setAttribute('aria-pressed', String(followingToday));
  document.title = `${selectedDate} · NIFTY ORB`;
}

async function loadDates() {
  try {
    const response = await fetch('/api/history');
    if (!response.ok) throw new Error('History unavailable');
    const data = await response.json();
    historyNote = data.history_note || '';
    dates = [...new Set([...(data.dates || []), today, ...(live ? [live.date] : [])])].sort();
    syncNavigation();
  } catch {
    historyNote = 'History could not be loaded. Select a date to retry.';
    dates = [...new Set([...dates, today])].sort();
  }
  $('date-nav').title = `Scroll up for earlier sessions, down for later sessions. ${historyNote}`;
}

async function selectDate(date, follow = false) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date > today) return syncNavigation();
  selectedDate = date;
  followingToday = follow;
  pointer = null;
  loadError = '';
  const id = ++requestId;
  requestController?.abort();
  if (live?.date === date) {
    view = live;
    loading = false;
    return render();
  }
  view = null;
  loading = true;
  render();
  requestController = new AbortController();
  try {
    const response = await fetch(`/api/session?date=${date}`, { signal: requestController.signal });
    if (!response.ok) throw new Error(`Session unavailable (${response.status})`);
    const data = await response.json();
    if (id !== requestId) return;
    view = live?.date === date ? live : data;
  } catch (error) {
    if (id !== requestId || error.name === 'AbortError') return;
    loadError = 'Could not load this session. Select the date again to retry.';
  } finally {
    if (id === requestId) {
      loading = false;
      render();
    }
  }
}

function moveDate(direction) {
  const date = direction < 0 ? dates.filter(d => d < selectedDate).at(-1) : dates.find(d => d > selectedDate);
  if (date) selectDate(date, date === today);
}

function statusText() {
  if (loading) return 'Loading…';
  if (view?.market_status === 'replay') return 'Replay';
  if (selectedDate !== today) return 'Historical';
  const minutes = Number(timeLabel(new Date()).slice(0, 2)) * 60 + Number(timeLabel(new Date()).slice(3));
  if (minutes < 555 || minutes >= 930 || view?.market_status === 'closed') return 'Market closed';
  const lastTick = view?.last_tick_time && new Date(view.last_tick_time).getTime();
  if (connected && view?.market_status === 'live' && lastTick && Date.now() - lastTick < 90000) return 'Live';
  return 'Waiting for quotes';
}

function render() {
  syncNavigation();
  $('price').textContent = number(view?.current_price);
  $('session-status').textContent = statusText();
  const high = view?.orb_high, low = view?.orb_low;
  $('range').textContent = high != null && low != null
    ? `ORB ${view.range_locked ? 'locked' : 'forming'}  H ${number(high)}  L ${number(low)}`
    : 'ORB 09:15–10:00 IST';
  const signals = view?.signals || [];
  $('signal-summary').textContent = signals.length
    ? [...signals].sort((a, b) => a.timestamp.localeCompare(b.timestamp)).map(s => `${s.direction === 'BULLISH' ? '↑' : '↓'} ${timeLabel(s.timestamp)} close ${number(s.price)}`).join('   ·   ')
    : view && !view.range_locked && getCandles().length ? 'Opening range incomplete' : 'No confirmed breakout';
  $('feed-status').textContent = `${view?.source || 'Yahoo Finance'} · ${connected ? 'Feed connected' : 'Reconnecting…'} · IST`;
  $('feed-status').title = historyNote;
  drawChart();
}

function getCandles() {
  const all = [...(view?.candles || []), ...(view?.active_candle ? [view.active_candle] : [])];
  return [...new Map(all.filter(c => sessionDate(c.start_time) === selectedDate).map(c => [c.start_time, c])).values()]
    .sort((a, b) => a.start_time.localeCompare(b.start_time));
}

function drawChart() {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  ctx.clearRect(0, 0, width, height);
  if (width < 100 || height < 80) return;
  const left = 12, right = width - 76, top = 28, bottom = height - 30;
  const step = (right - left) / 25;
  const x = slot => left + (slot + .5) * step;
  const candles = getCandles();
  const empty = $('empty-state');
  empty.hidden = candles.length > 0;
  empty.textContent = loading ? 'Loading session…' : loadError || (view?.message ? `${view.message}\n${view.history_note || ''}` : `No candles for ${selectedDate}.\nChoose an earlier session or return when quotes arrive.`);
  ctx.font = '10px ui-monospace, monospace';
  ctx.fillStyle = '#85898e';
  ctx.textAlign = 'center';
  for (let slot = 0; slot < 25; slot += width < 650 ? 4 : 2) {
    const minutes = 555 + slot * 15;
    const label = `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
    ctx.fillText(label, x(slot), height - 10);
  }
  if (!candles.length) {
    $('ohlc').textContent = 'Move over a candle to inspect';
    canvas.setAttribute('aria-label', `NIFTY 50, ${selectedDate}. ${empty.textContent}`);
    return;
  }
  const prices = candles.flatMap(c => [c.low, c.high]);
  [view.orb_high, view.orb_low, view.current_price].forEach(p => { if (Number.isFinite(p)) prices.push(p); });
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
    line(left, y(price), right, y(price), '#e8e9e9');
    ctx.fillStyle = '#85898e';
    ctx.fillText(number(price), right + 9, y(price) + 3);
  }
  if (view.orb_high != null && view.orb_low != null) {
    const highY = y(view.orb_high), lowY = y(view.orb_low);
    ctx.fillStyle = '#567c960b';
    ctx.fillRect(left, highY, right - left, Math.max(1, lowY - highY));
    ctx.fillStyle = '#567c9612';
    ctx.fillRect(left, highY, step * 3, Math.max(1, lowY - highY));
    line(left + step * 3, top, left + step * 3, bottom, '#a8b9c3', [2, 4]);
    ctx.fillStyle = '#768d9b';
    ctx.fillText('10:00', left + step * 3 + 5, top - 10);
    for (const [price, label] of [[view.orb_high, 'ORB H'], [view.orb_low, 'ORB L']]) {
      line(left, y(price), right, y(price), '#8199a8', [5, 4]);
      ctx.textAlign = 'right'; ctx.fillStyle = '#607c8f';
      ctx.fillText(label, right - 5, y(price) - 5);
    }
  }
  let hovered = null;
  const bodyWidth = Math.max(2, Math.min(26, step * .55));
  for (const candle of candles) {
    const center = x(candle.slot);
    line(center, y(candle.high), center, y(candle.low), '#686d73');
    const bodyTop = Math.min(y(candle.open), y(candle.close));
    const bodyHeight = Math.max(1.5, Math.abs(y(candle.open) - y(candle.close)));
    ctx.fillStyle = candle.close >= candle.open ? '#484c51' : '#c8cbce';
    ctx.fillRect(center - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
    ctx.strokeStyle = candle.close >= candle.open ? '#484c51' : '#91969b';
    ctx.strokeRect(center - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
    if (!candle.is_closed) {
      ctx.strokeStyle = '#69879b'; ctx.setLineDash([2, 2]);
      ctx.strokeRect(center - bodyWidth / 2 - 3, bodyTop - 3, bodyWidth + 6, bodyHeight + 6);
      ctx.setLineDash([]);
    }
    for (const signal of view.signals || []) {
      if (signal.timestamp !== candle.end_time) continue;
      const up = signal.direction === 'BULLISH';
      const markerY = up ? y(candle.low) + 12 : y(candle.high) - 12;
      ctx.fillStyle = '#567c96'; ctx.beginPath();
      ctx.moveTo(center, markerY + (up ? -4 : 4));
      ctx.lineTo(center - 4, markerY + (up ? 3 : -3));
      ctx.lineTo(center + 4, markerY + (up ? 3 : -3));
      ctx.closePath(); ctx.fill();
    }
    if (pointer && Math.abs(pointer.x - center) <= step / 2) hovered = candle;
  }
  if (Number.isFinite(view.current_price)) {
    const priceY = y(view.current_price);
    line(left, priceY, right, priceY, '#a2a8ad', [2, 4]);
    ctx.fillStyle = '#e4e9ec'; ctx.fillRect(right, priceY - 10, 73, 20);
    ctx.fillStyle = '#354b59'; ctx.textAlign = 'center';
    ctx.fillText(number(view.current_price), right + 36, priceY + 3);
  }
  if (pointer && pointer.x >= left && pointer.x <= right && pointer.y >= top && pointer.y <= bottom) {
    line(pointer.x, top, pointer.x, bottom, '#a1a6ab', [3, 3]);
    line(left, pointer.y, right, pointer.y, '#a1a6ab', [3, 3]);
    const price = min + (bottom - pointer.y) / (bottom - top) * (max - min);
    ctx.fillStyle = '#484c51'; ctx.fillRect(right, pointer.y - 10, 73, 20);
    ctx.fillStyle = '#fff'; ctx.textAlign = 'center';
    ctx.fillText(number(price), right + 36, pointer.y + 3);
  }
  const focus = hovered || candles.at(-1);
  $('ohlc').textContent = `${timeLabel(focus.start_time)}  O ${number(focus.open)}  H ${number(focus.high)}  L ${number(focus.low)}  C ${number(focus.close)}${focus.is_closed ? '' : '  · forming'}`;
  canvas.setAttribute('aria-label', `NIFTY 50, ${selectedDate}, ${candles.length} candles. Last price ${number(view.current_price)}. ${$('range').textContent}. ${$('signal-summary').textContent}.`);
}

function handleEvent(message) {
  if (!message.date) return;
  if (message.today && message.today !== today) {
    today = message.today;
    loadDates();
    if (followingToday) { selectedDate = today; view = null; pointer = null; }
  }
  if (['init', 'session', 'status'].includes(message.type)) {
    const firstSession = !live || message.type === 'session';
    live = message;
    if (message.type !== 'status') loadDates();
    if (message.market_status === 'replay' && firstSession && followingToday && message.date !== today) {
      selectedDate = message.date;
      followingToday = false;
    }
  } else {
    if (!live || message.date !== live.date) return;
    if (message.type === 'tick' || message.type === 'state') {
      Object.assign(live, message);
      if (message.price != null) live.current_price = message.price;
    } else if (message.type === 'candle_close') {
      live.candles = [...(live.candles || []).filter(c => c.start_time !== message.candle.start_time), message.candle];
      if (live.active_candle?.start_time === message.candle.start_time) live.active_candle = null;
      Object.assign(live, Object.fromEntries(['orb_high', 'orb_low', 'range_locked', 'market_status'].filter(k => k in message).map(k => [k, message[k]])));
    } else if (message.type === 'signal') {
      if (!(live.signals || []).some(s => s.timestamp === message.signal.timestamp && s.direction === message.signal.direction)) {
        live.signals = [message.signal, ...(live.signals || [])];
      }
    }
  }
  if (selectedDate === live.date) {
    ++requestId;
    requestController?.abort();
    view = live;
    loading = false;
    loadError = '';
  }
  render();
}

$('previous').addEventListener('click', () => moveDate(-1));
$('next').addEventListener('click', () => moveDate(1));
$('today').addEventListener('click', () => { selectDate(today, true); loadDates(); });
$('session-date').addEventListener('change', e => selectDate(e.target.value, e.target.value === today));
let scrollAmount = 0, lastScroll = 0, lastMove = 0;
function scrollDates(event) {
  if (event.ctrlKey || event.metaKey) return;
  event.preventDefault();
  const now = performance.now();
  if (now - lastScroll > 180) scrollAmount = 0;
  const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
  if (Math.sign(delta) !== Math.sign(scrollAmount)) scrollAmount = 0;
  scrollAmount += delta * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 100 : 1);
  lastScroll = now;
  if (Math.abs(scrollAmount) >= 55 && now - lastMove > 300) {
    moveDate(Math.sign(scrollAmount));
    scrollAmount = 0; lastMove = now;
  }
}
$('chart-wrap').addEventListener('wheel', scrollDates, { passive: false });
$('date-nav').addEventListener('wheel', scrollDates, { passive: false });
$('chart-wrap').addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); moveDate(e.key === 'ArrowLeft' ? -1 : 1); }
  if (e.key === 'Home') { e.preventDefault(); selectDate(today, true); }
});
canvas.addEventListener('pointermove', e => {
  const rect = canvas.getBoundingClientRect();
  pointer = { x: e.clientX - rect.left, y: e.clientY - rect.top }; drawChart();
});
canvas.addEventListener('pointerleave', () => { pointer = null; drawChart(); });
new ResizeObserver(() => {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * dpr);
  canvas.height = Math.round(canvas.clientHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); drawChart();
}).observe($('chart-wrap'));
const stream = new EventSource('/api/stream');
stream.onopen = () => { connected = true; render(); };
stream.onerror = () => { connected = false; render(); };
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
