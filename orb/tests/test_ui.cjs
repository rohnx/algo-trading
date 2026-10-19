// Run: node tests/test_ui.cjs (requires Playwright and Chrome).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const bundledPlaywright = path.join(require('node:os').homedir(), '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright');
const playwright = require(process.env.PLAYWRIGHT_PATH || (fs.existsSync(bundledPlaywright) ? bundledPlaywright : 'playwright'));

const TODAY = '2026-10-05', PREVIOUS = '2026-10-01', OLDER = '2026-09-30';
const staticDir = path.join(__dirname, '..', 'static');
const COMPARISONS = [
  ['^NSEBANK', 'BANKNIFTY', 50000],
  ['NIFTY_MID_SELECT.NS', 'MIDCAP SELECT', 16000],
  ['^NSMIDCP', 'NIFTY NEXT 50', 70000],
  ['NIFTY_FIN_SERVICE.NS', 'FIN NIFTY', 24000],
  ['FOCIT.BO', 'FOCIT', 30000]
];
const SECTORS = [
  ['^CNXPHARMA', 'PHARMA', 22000],
  ['^CNXAUTO', 'AUTO', 25000],
  ['^CNXFMCG', 'FMCG', 56000],
  ['^CNXMETAL', 'METAL', 9000],
  ['^CNXPSUBANK', 'PSU BANK', 7000],
  ['^CNXREALTY', 'REALTY', 1000],
  ['NIFTY_CONSR_DURBL.NS', 'CONSUMER DURABLES', 37000],
  ['NIFTY_OIL_AND_GAS.NS', 'OIL & GAS', 12000],
  ['NIFTY_PVT_BANK.NS', 'PRIVATE BANK', 26000],
  ['^CNXENERGY', 'ENERGY', 35000],
  ['NIFTY_IND_DEFENCE.NS', 'INDIA DEFENCE', 7000],
  ['^CNXINFRA', 'INFRASTRUCTURE', 9000],
  ['^CNXPSE', 'PSE', 10000],
  ['^CNXCMDT', 'COMMODITIES', 9000],
  ['NIFTY_CPSE.NS', 'CPSE', 6500]
];
const failures = [], browserErrors = [];
let delayedDate = '';
let selectedSource = 'yahoo';
let selectedGeneration = 0;
let sourceEpoch = 'fixture-process-a';
let rejectSource = false;
let holdSource = false, heldSource = null, sourceStarted = null;
let helpRequests = 0, ipRequests = 0, ipFailure = false;
let historyRequests = 0;
let loginUrl = 'https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=fixture-client';
const sourceRequests = [];
function candle(date, slot, open, close, interval = 15) {
  const stamp = minutes => `${date}T${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:00+05:30`;
  return { symbol: '^NSEI', open, close, high: Math.max(open, close) + 12, low: Math.min(open, close) - 10,
    volume: 100, start_time: stamp(555 + slot * interval), end_time: stamp(555 + (slot + 1) * interval), slot, is_closed: true, is_complete: true };
}
function snapshot(date, count = 25, extra = {}) {
  const candles = Array.from({ length: count }, (_, slot) => {
    const open = 25000 + Math.sin(slot * .7) * 34 + slot * 3;
    return candle(date, slot, open, open + (slot % 2 ? -19 : 22));
  });
  const candles_5m = Array.from({ length: count * 3 }, (_, slot) => candle(date, slot, 25000 + slot, 25000 + slot + (slot % 2 ? -5 : 7), 5));
  return { type: 'init', date, today: TODAY, candles, candles_5m, active_candle: null, active_candle_5m: null, signals: [],
    source: `Deterministic ${selectedSource} fixture`, source_id: selectedSource, source_generation: selectedGeneration, source_epoch: sourceEpoch,
    current_price: candles.at(-1)?.close ?? null, orb_high: count ? 25064 : null, orb_low: count ? 24990 : null,
    range_locked: count >= 3, market_status: 'closed',
    setups: {
      '15': { setup_id: 'orb_30m_15m', range_minutes: 30, confirmation_minutes: 15, orb_high: count ? 25048 : null, orb_low: count ? 24994 : null, range_locked: count >= 2, signals: [] },
      '5': { setup_id: 'orb_45m_5m', range_minutes: 45, confirmation_minutes: 5, orb_high: count ? 25064 : null, orb_low: count ? 24990 : null, range_locked: count >= 3, signals: [] }
    }, ...extra };
}
function neutralBreakout(extra = {}) {
  return { basis: 'close', range_minutes: 45, confirmation_minutes: 5, range_locked: false,
    range_high: null, range_low: null, direction: null, confirmed_at: null, price: null, ...extra };
}
function niftySignal() {
  return { strategy: 'dual_ORB', setup_id: 'dual_orb', direction: 'BULLISH',
    timestamp: `${TODAY}T10:05:00+05:30`, price: 25070 };
}
function comparisonSnapshot(date = TODAY, count = 8, bankExtra = {}, primaryExtra = {}) {
  const data = snapshot(date, count, { symbol: '^NSEI', primary_symbol: '^NSEI', ...primaryExtra });
  const bankBars = interval => Array.from({ length: count * 15 / interval }, (_, slot) => ({
    ...candle(date, slot, 50000 + slot * interval * .8, 50000 + (slot + 1) * interval * .8, interval), symbol: '^NSEBANK'
  }));
  const bankFive = bankBars(5);
  const bank = {
    symbol: '^NSEBANK', label: 'BANKNIFTY', date,
    candles: bankBars(15), candles_5m: bankFive, active_candle: null, active_candle_5m: null,
    current_price: bankFive.at(-1)?.close ?? null, last_tick_time: bankFive.at(-1)?.end_time ?? null,
    session_open: count ? 50000 : null, change_pct: count ? (bankFive.at(-1).close / 50000 - 1) * 100 : null,
    comparison_status: count ? 'closed' : 'unavailable', breakout: neutralBreakout(), ...bankExtra
  };
  const primary = {
    symbol: '^NSEI', label: 'NIFTY', date, candles: data.candles, candles_5m: data.candles_5m,
    active_candle: data.active_candle, active_candle_5m: data.active_candle_5m,
    current_price: data.current_price, last_tick_time: data.last_tick_time || data.candles_5m.at(-1)?.end_time || null,
    session_open: data.candles[0]?.open ?? null,
    change_pct: data.candles.length ? (data.current_price / data.candles[0].open - 1) * 100 : null,
    comparison_status: count ? 'closed' : 'unavailable'
  };
  const instruments = { '^NSEI': primary, '^NSEBANK': bank };
  for (const [symbol, label, sessionOpen] of COMPARISONS.slice(1)) {
    const bars = interval => Array.from({ length: count * 15 / interval }, (_, slot) => ({
      ...candle(date, slot, sessionOpen * (1 + slot * interval * .8 / 50000), sessionOpen * (1 + (slot + 1) * interval * .8 / 50000), interval), symbol
    }));
    const five = bars(5);
    instruments[symbol] = { symbol, label, date, candles: bars(15), candles_5m: five, session_open: count ? sessionOpen : null,
      current_price: five.at(-1)?.close ?? null, last_tick_time: five.at(-1)?.end_time ?? null,
      comparison_status: count ? 'closed' : 'unavailable', active_candle: null, active_candle_5m: null, breakout: neutralBreakout() };
  }
  return { ...data, instruments };
}
function breakoutSnapshot(closedCount = 13) {
  const data = comparisonSnapshot(TODAY, 5);
  const closes = [50000, 50002, 49998, 50004, 49996, 50006, 49994, 50008, 49992, 50015, 50008, 49980, 50000];
  const bank = data.instruments['^NSEBANK'];
  bank.candles_5m = closes.slice(0, closedCount).map((close, slot) => ({
    ...candle(TODAY, slot, slot ? closes[slot - 1] : 50000, close, 5),
    symbol: bank.symbol, high: 50100, low: 49900
  }));
  bank.candles = Array.from({ length: Math.floor(closedCount / 3) }, (_, slot) => {
    const group = bank.candles_5m.slice(slot * 3, slot * 3 + 3);
    return { ...candle(TODAY, slot, group[0].open, group.at(-1).close), symbol: bank.symbol, high: 50100, low: 49900 };
  });
  bank.current_price = bank.candles_5m.at(-1).close;
  bank.last_tick_time = bank.candles_5m.at(-1).end_time;
  const direction = ({ 10: 'BULLISH', 12: 'BEARISH' })[closedCount] || null;
  bank.breakout = neutralBreakout({ range_locked: closedCount >= 9, range_high: 50008, range_low: 49992,
    direction, confirmed_at: direction ? bank.last_tick_time : null, price: direction ? bank.current_price : null });
  return data;
}
function sectorSnapshot(date = TODAY, count = 8, primaryExtra = {}) {
  const data = comparisonSnapshot(date, count, {}, primaryExtra);
  for (const instrument of Object.values(data.instruments)) if (instrument.symbol !== '^NSEI') instrument.comparison_group = 'derivatives';
  for (const [index, [symbol, label, open]] of SECTORS.entries()) {
    const closes = Array.from({ length: count * 3 }, (_, slot) => open * (1 + ((slot % 9) - 4) * .00004 + index * .000001));
    const five = closes.map((close, slot) => ({ ...candle(date, slot, slot ? closes[slot - 1] : open, close, 5), symbol }));
    const fifteen = Array.from({ length: count }, (_, slot) => {
      const children = five.slice(slot * 3, slot * 3 + 3);
      return { ...candle(date, slot, children[0].open, children.at(-1).close), symbol };
    });
    const opening = closes.slice(0, 9);
    data.instruments[symbol] = { symbol, label, comparison_group: 'sectors', date,
      candles: fifteen, candles_5m: five, active_candle: null, active_candle_5m: null,
      current_price: five.at(-1)?.close ?? null, last_tick_time: five.at(-1)?.end_time ?? null,
      session_open: count ? open : null, comparison_status: count ? 'closed' : 'unavailable',
      breakout: neutralBreakout({ range_locked: opening.length === 9,
        range_high: opening.length === 9 ? Math.max(...opening) : null,
        range_low: opening.length === 9 ? Math.min(...opening) : null }) };
  }
  return data;
}
function confirmSector(instrument, direction = 'BULLISH', movement = .004) {
  const price = instrument.session_open * (1 + (direction === 'BULLISH' ? movement : -movement));
  instrument.candles_5m.at(-1).close = price;
  instrument.candles.at(-1).close = price;
  instrument.current_price = price;
  instrument.breakout = { ...instrument.breakout, direction, confirmed_at: instrument.last_tick_time, price };
  return instrument;
}
function motionSnapshot() {
  const data = comparisonSnapshot(TODAY, 8, {}, { current_price: 25020, market_status: 'live' });
  data.last_tick_time = `${TODAY}T11:17:00+05:30`;
  for (const instrument of Object.values(data.instruments)) {
    const ratio = instrument.session_open / 25000;
    const active = interval => ({ ...candle(TODAY, interval === 5 ? 24 : 8, 25015 * ratio, 25020 * ratio, interval),
      symbol: instrument.symbol, high: 25055 * ratio, low: 24995 * ratio, is_closed: false });
    instrument.active_candle = active(15);
    instrument.active_candle_5m = active(5);
    instrument.current_price = 25020 * ratio;
    instrument.last_tick_time = data.last_tick_time;
    instrument.comparison_status = 'live';
  }
  data.active_candle = data.instruments['^NSEI'].active_candle;
  data.active_candle_5m = data.instruments['^NSEI'].active_candle_5m;
  return data;
}
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost');
  if (url.pathname === '/api/source' && request.method === 'POST') {
    let body = '';
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      const payload = JSON.parse(body);
      sourceRequests.push({ payload, contentType: request.headers['content-type'] });
      if (rejectSource) {
        response.writeHead(409, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ message: 'Previous feed is still stopping. Retry shortly.' }));
        return;
      }
      selectedSource = payload.source;
      selectedGeneration += 1;
      const data = snapshot(TODAY, selectedSource === 'shoonya' ? 6 : 8);
      if (holdSource) {
        heldSource = { response, data };
        sourceStarted?.();
        sourceStarted = null;
        return;
      }
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(data));
    });
    return;
  }
  if (url.pathname === '/api/shoonya/help') {
    helpRequests += 1;
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ login_url: loginUrl }));
    return;
  }
  if (url.pathname === '/api/public-ip') {
    ipRequests += 1;
    response.writeHead(ipFailure ? 503 : 200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(ipFailure ? { message: 'IP lookup unavailable. Try curl -s ifconfig.me.' } : { ip: '203.0.113.8' }));
    return;
  }
  if (url.pathname.startsWith('/api/')) {
    if (url.pathname === '/api/history') historyRequests += 1;
    const date = url.searchParams.get('date');
    const data = url.pathname === '/api/history'
      ? { dates: [OLDER, PREVIOUS, TODAY], today: TODAY, history_note: 'Fixture history' }
      : snapshot(date, date === TODAY ? 0 : 25);
    setTimeout(() => {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(data));
    }, date === delayedDate ? 350 : 0);
    return;
  }
  const file = path.join(staticDir, url.pathname === '/' ? 'index.html' : path.basename(url.pathname));
  if (!fs.existsSync(file)) { response.writeHead(404); response.end(); return; }
  response.writeHead(200, { 'Content-Type': file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'text/html' });
  fs.createReadStream(file).pipe(response);
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await playwright.chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'America/New_York' });
    await context.addInitScript(() => {
      window.__now = Date.parse('2026-10-05T18:00:00+05:30');
      const NativeDate = Date;
      window.Date = class extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [window.__now])); }
        static now() { return window.__now; }
      };
      window.EventSource = class {
        constructor() { window.__stream = this; setTimeout(() => this.onopen?.(), 0); }
        close() {}
      };
      window.__emit = message => window.__stream.onmessage({ data: JSON.stringify(message) });
      window.__fills = [];
      window.__chartDraws = 0;
      const clearRect = CanvasRenderingContext2D.prototype.clearRect;
      CanvasRenderingContext2D.prototype.clearRect = function (...args) {
        if (this.canvas.id === 'chart') window.__chartDraws += 1;
        return clearRect.apply(this, args);
      };
      const fillRect = CanvasRenderingContext2D.prototype.fillRect;
      CanvasRenderingContext2D.prototype.fillRect = function (x, y, w, h) {
        if (this.canvas.id === 'chart') window.__fills.push({ color: this.fillStyle, x, y, w, h, alpha: this.globalAlpha });
        return fillRect.call(this, x, y, w, h);
      };
      window.__strokes = []; window.__markers = [];
      window.__clips = [];
      let clipRectangle;
      const rect = CanvasRenderingContext2D.prototype.rect, clip = CanvasRenderingContext2D.prototype.clip;
      CanvasRenderingContext2D.prototype.rect = function (...args) {
        if (this.canvas.id === 'chart') clipRectangle = args;
        return rect.apply(this, args);
      };
      CanvasRenderingContext2D.prototype.clip = function (...args) {
        if (this.canvas.id === 'chart') window.__clips.push(clipRectangle);
        return clip.apply(this, args);
      };
      let points = [], commands = [];
      for (const method of ['beginPath', 'moveTo', 'lineTo', 'stroke', 'fill']) {
        const native = CanvasRenderingContext2D.prototype[method];
        CanvasRenderingContext2D.prototype[method] = function (...args) {
          if (this.canvas.id === 'chart') {
            if (method === 'beginPath') { points = []; commands = []; }
            if (method === 'moveTo' || method === 'lineTo') { points.push(args); commands.push({ method, point: args }); }
            if (method === 'stroke') window.__strokes.push({ points: [...points], commands: [...commands], color: this.strokeStyle, dash: this.getLineDash(), alpha: this.globalAlpha, width: this.lineWidth, filter: this.filter });
            if (method === 'fill') window.__markers.push({ points: [...points], color: this.fillStyle, alpha: this.globalAlpha });
          }
          return native.apply(this, args);
        };
      }
    });
    const page = await context.newPage();
    page.on('pageerror', error => browserErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && !/404|409|503/.test(message.text())) browserErrors.push(message.text()); });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    const emit = data => page.evaluate(data => {
      window.__emit(data);
      return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, data);
    const captureChart = options => page.evaluate(options => {
      window.__captureChart = ({ animate = false, now } = {}) => {
      const draw = () => drawChart(now);
      if (!animate) {
        draw(); chartMotion.finish(); draw(); chartMotion.finish();
      }
      window.__fills = []; window.__strokes = []; window.__markers = []; window.__clips = []; draw();
      const css = getComputedStyle(document.documentElement);
      const context = document.querySelector('#chart').getContext('2d');
      const color = name => {
        context.save(); context.strokeStyle = css.getPropertyValue(name).trim();
        const result = context.strokeStyle; context.restore(); return result;
      };
      const count = selectedComparisonGroup === 'sectors' ? 15 : selectedComparisonGroup === 'derivatives' ? 5 : 0;
      const comparisonColors = Array.from({ length: count }, (_, index) => `--comparison-line${index ? `-${index + 1}` : ''}`).map(color);
      const price = color('--price-line');
      const bodyColors = [color('--up'), color('--down')];
      const series = comparisonColors.map(shade => window.__strokes.filter(line => line.color === shade && line.width >= 1.1 && line.points.length > 0));
      return {
        series,
        comparison: series[0],
        allComparisons: series.flat(),
        clips: window.__clips,
        price: window.__strokes.find(line => line.color === price && line.dash.join(',') === '2,4'),
        bodies: window.__fills.filter(fill => bodyColors.includes(fill.color) && fill.w <= 26),
        width: document.querySelector('#chart').clientWidth,
        height: document.querySelector('#chart').clientHeight
      };
      };
      return window.__captureChart(options);
    }, options);
    const selectGroup = async group => {
      const button = page.locator(group === 'sectors' ? '#sectors-tab' : '#comparison-toggle');
      if (await button.getAttribute('aria-selected') !== 'true') await button.click();
      await captureChart();
    };
    const revealComparisons = async () => {
      await page.locator('#series-labels [data-primary="true"]').hover();
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'true');
    };
    const waitDate = date => page.waitForFunction(date => document.querySelector('#session-date').value === date && document.querySelector('#chart').getAttribute('aria-label').includes(date), date);
    async function holdSourceRequest(action) {
      holdSource = true;
      const started = new Promise(resolve => { sourceStarted = resolve; });
      await action();
      await started;
    }
    function releaseSourceRequest(status = 200) {
      holdSource = false;
      if (!heldSource) return;
      const { response, data } = heldSource;
      heldSource = null;
      response.writeHead(status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(status === 200 ? data : { message: 'Fixture acknowledgement unavailable.' }));
    }
    async function check(name, run) {
      try { await run(); console.log(`PASS ${name}`); }
      catch (error) { failures.push(`${name}: ${error.message.split('\n')[0]}`); console.error(`FAIL ${name}: ${error.message}`); }
    }
    await check('today remains visible with an empty closed market, independently of browser timezone', async () => {
      await page.waitForFunction(() => document.querySelector('#session-status').textContent === 'Market closed');
      assert.equal(await page.locator('#session-date').inputValue(), TODAY);
      assert.equal(await page.locator('#empty-state').isVisible(), true);
    });
    await check('preopen explains the empty session and the first forming candle appears at 09:15 IST', async () => {
      const date = '2026-10-19';
      try {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-19T09:14:59+05:30'); });
        await emit(snapshot(date, 0, { today: date, feed_status: 'live' }));
        assert.equal(await page.locator('#session-date').inputValue(), date);
        assert.equal(await page.locator('#empty-state').textContent(), 'Market candles appear after 09:15 IST.');
        await emit(snapshot(date, 0, { today: date, message: 'Session history unavailable.', history_note: 'Retry shortly.' }));
        assert.match(await page.locator('#empty-state').textContent(), /Session history unavailable.*Retry shortly/s);
        await page.evaluate(() => { window.__now = Date.parse('2026-10-19T09:15:00+05:30'); });
        await emit(snapshot(date, 0, { today: date, feed_status: 'live', market_status: 'waiting' }));
        assert.match(await page.locator('#empty-state').textContent(), /No candles for 2026-10-19/);
        const active = { ...candle(date, 0, 25000, 25001), is_closed: false };
        await emit({ type: 'tick', date, today: date, price: active.close, active_candle: active,
          last_tick_time: `${date}T09:15:00+05:30`, market_status: 'live' });
        assert.equal(await page.locator('#empty-state').isVisible(), false);
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /1 candles/);
      } finally {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T18:00:00+05:30'); });
        await emit(snapshot(TODAY, 0, { today: TODAY }));
      }
    });
    await check('scrolling leaves the date and replay intact while date buttons still navigate', async () => {
      await emit(snapshot(TODAY, 8));
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      await page.locator('#next').click(); await waitDate(TODAY);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /8 candles/);
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      await page.locator('#replay').click();
      await page.waitForFunction(() => document.querySelector('#replay').getAttribute('aria-pressed') === 'true');
      for (const target of ['#chart', '#date-nav']) {
        await page.locator(target).hover();
        for (const [deltaX, deltaY] of [[0, -100], [0, 100], [-100, 0], [100, 0]]) {
          await page.mouse.wheel(deltaX, deltaY);
          await page.waitForTimeout(350);
          assert.equal(await page.locator('#session-date').inputValue(), PREVIOUS);
          assert.equal(await page.locator('#replay').getAttribute('aria-pressed'), 'true');
          assert.equal(await page.locator('#replay-progress').isVisible(), true);
        }
      }
      await page.locator('#replay').click();
      await page.locator('#today').click(); await waitDate(TODAY);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /8 candles/);
    });
    await check('live ticks and reconnect snapshots do not replace historical viewing', async () => {
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      await emit({ type: 'tick', date: TODAY, today: TODAY, price: 25123, active_candle: candle(TODAY, 8, 25100, 25123) });
      await emit(snapshot(TODAY, 10));
      assert.equal(await page.locator('#session-date').inputValue(), PREVIOUS);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /25 candles/);
      await page.locator('#today').click(); await waitDate(TODAY);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /10 candles/);
    });
    await check('light quote updates retain candle history and full snapshots replace it', async () => {
      const fixture = motionSnapshot(); fixture.feed_status = 'live';
      await emit(fixture);
      await page.evaluate(() => {
        window.__savedHistory = Object.fromEntries(Object.entries(live.instruments).map(([symbol, instrument]) => [symbol, instrument.candles_5m]));
        window.__savedSetups = live.setups;
      });
      const primary = fixture.instruments['^NSEI'];
      const active = { ...primary.active_candle, close: primary.current_price + 1 };
      await emit({ type: 'tick', date: TODAY, price: active.close, active_candle: active,
        instruments: { '^NSEI': { current_price: active.close, active_candle: active } } });
      await emit({ type: 'state', date: TODAY, symbol: '^NSEBANK',
        instruments: { '^NSEBANK': { current_price: 50088, active_candle: null, active_candle_5m: null } } });
      const result = await page.evaluate(() => ({
        retained: Object.entries(live.instruments).every(([symbol, instrument]) => instrument.candles_5m === window.__savedHistory[symbol]),
        setupsRetained: live.setups === window.__savedSetups,
        primary: live.instruments['^NSEI'].current_price,
        bank: live.instruments['^NSEBANK'].current_price,
        bankLabel: live.instruments['^NSEBANK'].label,
        bankActive: [live.instruments['^NSEBANK'].active_candle, live.instruments['^NSEBANK'].active_candle_5m]
      }));
      assert.equal(result.retained, true); assert.equal(result.setupsRetained, true);
      assert.equal(result.primary, active.close); assert.equal(result.bank, 50088);
      assert.equal(result.bankLabel, 'BANKNIFTY'); assert.deepEqual(result.bankActive, [null, null]);
      await emit(comparisonSnapshot(TODAY, 2));
      assert.deepEqual(await page.evaluate(() => Object.values(live.instruments).map(instrument => instrument.candles_5m.length)), Array(COMPARISONS.length + 1).fill(6));
      await emit(snapshot(TODAY, 8));
    });
    await check('one accepted restart snapshot refreshes history once', async () => {
      await page.waitForTimeout(100);
      const before = historyRequests;
      sourceEpoch = 'fixture-process-cleanup';
      await emit(snapshot(TODAY, 8));
      await page.waitForTimeout(100);
      assert.equal(historyRequests - before, 1);
    });
    await check('historical and completed replay views stay idle while live quotes continue', async () => {
      await emit(snapshot(TODAY, 8, { feed_status: 'live' }));
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      await page.waitForTimeout(100);
      await page.evaluate(() => { window.__chartDraws = 0; });
      await page.waitForTimeout(1250);
      await emit({ type: 'tick', date: TODAY, price: 25123 });
      assert.equal(await page.evaluate(() => window.__chartDraws), 0, 'history ignores idle clock and healthy live quotes');
      assert.equal(await page.locator('#feed-status').isVisible(), false);
      await page.evaluate(() => window.__stream.onerror());
      await page.waitForFunction(() => !document.querySelector('#feed-status').hidden);
      assert.match(await page.locator('#feed-status').textContent(), /Reconnecting/);
      await page.evaluate(() => window.__stream.onopen());
      await page.locator('#today').click(); await waitDate(TODAY);
      await page.locator('#replay').click();
      await page.evaluate(() => { replay.started = performance.now() - replay.duration; scheduleRender(); });
      await page.waitForFunction(() => document.querySelector('#session-status').textContent === 'Replay complete');
      await page.evaluate(() => { window.__chartDraws = 0; });
      await page.waitForTimeout(1250);
      await emit({ type: 'tick', date: TODAY, price: 25124 });
      assert.equal(await page.evaluate(() => window.__chartDraws), 0, 'completed replay stays frozen while live data accumulates');
      await page.locator('#replay').click();
      await page.waitForFunction(() => document.querySelector('#price').textContent === '25124.00');
      assert.equal(await page.locator('#price').textContent(), '25124.00');
    });
    await check('the idle clock still marks live quotes stale at the freshness boundary', async () => {
      await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:17:00+05:30'); });
      const fixture = motionSnapshot(); fixture.feed_status = 'live';
      await emit(fixture);
      assert.equal(await page.locator('#session-status').textContent(), 'Live');
      await page.evaluate(() => { window.__now += 90001; });
      await page.waitForFunction(() => document.querySelector('#series-labels [data-symbol="^NSEI"]')?.dataset.status === 'stale');
      assert.equal(await page.locator('#session-status').textContent(), 'Waiting for quotes');
      await page.evaluate(() => { window.__chartDraws = 0; });
      await page.waitForTimeout(1250);
      assert.equal(await page.evaluate(() => window.__chartDraws), 0, 'unchanged stale status needs no repeated redraw');
      await page.evaluate(() => { window.__now = Date.parse('2026-10-05T18:00:00+05:30'); });
      await emit(snapshot(TODAY, 8));
    });
    await check('a slow historical response cannot replace the newer date selection', async () => {
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      delayedDate = OLDER;
      await page.locator('#previous').click();
      await page.locator('#next').click(); await waitDate(PREVIOUS);
      await page.waitForTimeout(450);
      assert.equal(await page.locator('#session-date').inputValue(), PREVIOUS);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), new RegExp(PREVIOUS));
      delayedDate = '';
    });
    await check('rising and falling candle bodies use dark and light gray', async () => {
      await page.evaluate(() => { window.__fills = []; });
      await page.locator('#today').click(); await waitDate(TODAY);
      const colors = await page.evaluate(() => [...new Set(window.__fills.filter(fill => fill.w <= 26).map(fill => fill.color))]);
      assert.ok(colors.includes('#484c51')); assert.ok(colors.includes('#c8cbce'));
    });
    await check('a new-day session resets the chart while preserving slot-zero alignment', async () => {
      await emit(snapshot(TODAY, 1));
      await page.evaluate(() => { window.__fills = []; });
      await page.locator('#today').click();
      await page.waitForFunction(() => window.__fills.some(fill => fill.color === '#484c51' && fill.w <= 26));
      const firstX = await page.evaluate(() => window.__fills.find(fill => fill.color === '#484c51' && fill.w <= 26).x);
      await page.evaluate(() => { window.__now = Date.parse('2026-10-06T09:20:00+05:30'); window.__fills = []; });
      await emit(snapshot('2026-10-06', 1, { type: 'session', today: '2026-10-06' }));
      await waitDate('2026-10-06');
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /1 candles/);
      assert.equal(await page.evaluate(() => window.__fills.find(fill => fill.color === '#484c51' && fill.w <= 26)?.x), firstX);
    });
    await check('status heartbeats update market state without another price tick', async () => {
      await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:00:00+05:30'); });
      await emit(snapshot(TODAY, 8, { market_status: 'live', last_tick_time: '2026-10-05T11:00:00+05:30' }));
      await page.locator('#today').click();
      assert.equal(await page.locator('#session-status').textContent(), 'Live');
      await emit(snapshot(TODAY, 8, { type: 'status', market_status: 'closed' }));
      assert.equal(await page.locator('#session-status').textContent(), 'Market closed');
    });
    await check('candles keep a fixed full-session width while today fills in', async () => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.evaluate(() => { window.__fills = []; });
      await emit(snapshot(TODAY, 1));
      const sparse = await page.evaluate(() => window.__fills.find(fill => fill.color === '#484c51' && fill.w <= 26));
      await page.evaluate(() => { window.__fills = []; });
      await emit(snapshot(TODAY, 12));
      const fuller = await page.evaluate(() => window.__fills.find(fill => fill.color === '#484c51' && fill.w <= 26));
      assert.ok(sparse && fuller, 'candle bodies were drawn');
      assert.equal(sparse.w, fuller.w);
      assert.equal(sparse.x, fuller.x);
    });
    await check('interval changes morph the selected range and keep one-third candle widths on the same day axis', async () => {
      await emit(snapshot(TODAY, 8));
      await page.evaluate(() => { window.__fills = []; window.__strokes = []; });
      await emit(snapshot(TODAY, 8));
      const first = await page.evaluate(() => ({
        body: window.__fills.find(fill => fill.color === '#484c51' && fill.w <= 26),
        boundary: window.__strokes.find(line => line.dash.join(',') === '2,4' && line.points[0]?.[0] === line.points[1]?.[0]),
        range: document.querySelector('#range').textContent,
        highLine: window.__strokes.find(line => line.dash.join(',') === '5,4')
      }));
      assert.match(first.range, /30m ORB locked/);
      assert.equal(await page.locator('#confirmation-rule').textContent(), '15m close · 30m ORB');
      await page.evaluate(() => {
        window.__fills = []; window.__strokes = [];
        setIntervalMinutes(5);
        transition.started = performance.now() - transition.duration / 2;
        drawChart(performance.now(), displayView());
      });
      const middle = await page.evaluate(() => ({
        boundary: window.__strokes.find(line => line.dash.join(',') === '2,4' && line.points[0]?.[0] === line.points[1]?.[0]),
        highLine: window.__strokes.find(line => line.dash.join(',') === '5,4')
      }));
      await page.waitForTimeout(500);
      assert.equal(await page.locator('#interval-5m').getAttribute('aria-pressed'), 'true');
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /24 candles/);
      assert.match(await page.locator('#range').textContent(), /45m ORB locked/);
      assert.equal(await page.locator('#confirmation-rule').textContent(), '5m close · 45m ORB');
      const final = await page.evaluate(() => {
        window.__fills = []; window.__strokes = []; drawChart();
        return { body: window.__fills.find(fill => fill.color === '#484c51' && fill.w <= 26),
          boundary: window.__strokes.find(line => line.dash.join(',') === '2,4' && line.points[0]?.[0] === line.points[1]?.[0]),
          highLine: window.__strokes.find(line => line.dash.join(',') === '5,4') };
      });
      assert.ok(Math.abs(final.body.w * 3 - first.body.w) < .01, '5m bodies are one-third the 15m width');
      assert.ok(first.boundary.points[0][0] < middle.boundary.points[0][0] && middle.boundary.points[0][0] < final.boundary.points[0][0], 'opening boundary moves smoothly between 09:45 and 10:00');
      assert.ok(final.highLine.points[0][1] < middle.highLine.points[0][1] && middle.highLine.points[0][1] < first.highLine.points[0][1], 'range high moves smoothly on a steady price scale');
      const width = await page.locator('#chart').evaluate(el => el.clientWidth - 88);
      assert.ok(Math.abs(first.boundary.points[0][0] - (12 + width * 30 / 375)) < .01);
      assert.ok(Math.abs(final.boundary.points[0][0] - (12 + width * 45 / 375)) < .01);
      await page.locator('#interval-15m').click(); await page.waitForTimeout(500);
      assert.match(await page.locator('#range').textContent(), /30m ORB locked/);
    });
    await check('arrows belong to each selected setup while the footer requires both setups', async () => {
      const fixture = snapshot(TODAY, 8);
      const fifteen = { timestamp: fixture.candles[2].end_time, direction: 'BULLISH', price: fixture.candles[2].close };
      const five = { timestamp: fixture.candles_5m[9].end_time, direction: 'BULLISH', price: fixture.candles_5m[9].close };
      fixture.setups['15'].signals = [fifteen]; fixture.setups['5'].signals = [five];
      await page.evaluate(() => { window.__markers = []; }); await emit(fixture);
      assert.equal(await page.locator('#signal-summary').textContent(), 'Waiting for both ORBs');
      const marker15 = await page.evaluate(() => window.__markers.at(-1));
      const dayWidth = await page.locator('#chart').evaluate(el => el.clientWidth - 88);
      assert.ok(Math.abs(marker15.points[0][0] - (12 + dayWidth * 37.5 / 375)) < .01);
      const middleMarker = await page.evaluate(() => {
        setIntervalMinutes(5); transition.started = performance.now() - transition.duration / 2;
        window.__markers = []; drawChart(performance.now(), displayView());
        return window.__markers.at(-1);
      });
      assert.ok(middleMarker.points[0][0] > 12 + dayWidth * 47.5 / 375 && middleMarker.points[0][0] < 12 + dayWidth * 52.5 / 375, 'incoming arrow follows its splitting candle');
      await page.waitForTimeout(500);
      const marker5 = await page.evaluate(() => { window.__markers = []; drawChart(); return window.__markers.at(-1); });
      assert.ok(Math.abs(marker5.points[0][0] - (12 + dayWidth * 47.5 / 375)) < .01);
      assert.equal(await page.locator('#signal-summary').textContent(), 'Waiting for both ORBs');
      fixture.signals = [{ ...five, strategy: 'dual_ORB' }]; await emit(fixture);
      assert.match(await page.locator('#signal-summary').textContent(), /10:05 both ORBs/);
      await page.locator('#interval-15m').click(); await page.waitForTimeout(500);
      assert.match(await page.locator('#signal-summary').textContent(), /10:05 both ORBs/);
    });
    await check('the alternate range appears only while hovering a range line', async () => {
      await page.mouse.move(0, 0);
      await page.evaluate(() => { window.__strokes = []; drawChart(); });
      assert.equal(await page.evaluate(() => window.__strokes.some(line => line.dash.join(',') === '1,5')), false);
      const line = await page.evaluate(() => window.__strokes.find(line => line.dash.join(',') === '5,4'));
      const box = await page.locator('#chart').boundingBox();
      await page.mouse.move(box.x + line.points[0][0] + 40, box.y + line.points[0][1]);
      await page.waitForFunction(() => window.__strokes.some(line => line.dash.join(',') === '1,5' && line.alpha === .5));
      await page.mouse.move(0, 0);
    });
    await check('five-minute candle-close events update both setup arrows and combined signals', async () => {
      const fixture = snapshot(TODAY, 8); await emit(fixture);
      const closed = candle(TODAY, 24, 25070, 25080, 5);
      fixture.setups['15'].signals = [{ timestamp: fixture.candles[6].end_time, direction: 'BULLISH', price: fixture.candles[6].close }];
      fixture.setups['5'].signals = [{ timestamp: closed.end_time, direction: 'BULLISH', price: closed.close }];
      await emit({ type: 'candle_close', date: TODAY, interval_minutes: 5, candle: closed, setups: fixture.setups,
        signals: [{ timestamp: closed.end_time, direction: 'BULLISH', price: closed.close, strategy: 'dual_ORB' }] });
      assert.match(await page.locator('#signal-summary').textContent(), /11:20 both ORBs/);
      await page.locator('#interval-5m').click(); await page.waitForTimeout(500);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /25 candles/);
      const markers = await page.evaluate(() => { window.__markers = []; drawChart(); return window.__markers; });
      assert.equal(markers.length, 1);
      const dayWidth = await page.locator('#chart').evaluate(el => el.clientWidth - 88);
      assert.ok(Math.abs(markers[0].points[0][0] - (12 + dayWidth * 122.5 / 375)) < .01);
      await page.locator('#interval-15m').click(); await page.waitForTimeout(500);
    });
    await check('missing five-minute history shows an explanation instead of fabricated bars', async () => {
      await emit(snapshot(TODAY, 8, { candles_5m: [], active_candle_5m: null }));
      await page.locator('#interval-5m').click();
      await page.waitForTimeout(500);
      assert.equal(await page.locator('#empty-state').isVisible(), true);
      assert.match(await page.locator('#empty-state').textContent(), /5|five/i);
      assert.equal(await page.locator('#replay').isDisabled(), true);
      await page.locator('#interval-15m').click();
      await page.waitForTimeout(500);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /8 candles/);
    });
    await check('source dropdown posts its choice and rejects late packets from the old source', async () => {
      await page.locator('#source-select').selectOption('shoonya');
      await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'shoonya' && document.querySelector('#chart').getAttribute('aria-label').includes('6 candles'));
      assert.deepEqual(sourceRequests.at(-1).payload, { source: 'shoonya' });
      assert.match(sourceRequests.at(-1).contentType, /application\/json/);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /6 candles/);
      await emit(snapshot(TODAY, 25, { source_id: 'yahoo', source_generation: selectedGeneration - 1, source: 'Late Yahoo snapshot' }));
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /6 candles/);
      assert.equal(await page.locator('#source-select').inputValue(), 'shoonya');
      rejectSource = true;
      await page.locator('#source-select').selectOption('yahoo');
      await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'shoonya');
      assert.equal(await page.locator('#source-select').inputValue(), 'shoonya');
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /6 candles/);
      rejectSource = false;
      await page.locator('#source-select').selectOption('yahoo');
      await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo' && document.querySelector('#chart').getAttribute('aria-label').includes('8 candles'));
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /8 candles/);
    });
    await check('target stream data paints before a source acknowledgement and cannot be rolled back by it', async () => {
      try {
        await holdSourceRequest(() => page.locator('#source-select').selectOption('shoonya'));
        await emit(snapshot(TODAY, 0, { feed_status: 'connecting' }));
        assert.match(await page.locator('#empty-state').textContent(), /Connecting to Shoonya.*Loading candle history/);
        await emit(snapshot(TODAY, 9, { current_price: 25111 }));
        assert.equal(await page.locator('#source-select').isDisabled(), true, 'the HTTP request remains pending');
        assert.equal(await page.locator('#empty-state').isVisible(), false, 'ready stream data need not wait for the acknowledgement');
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /9 candles/);
        assert.equal(await page.locator('#price').textContent(), '25111.00');
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /9 candles/);
        assert.equal(await page.locator('#price').textContent(), '25111.00', 'the older six-candle POST snapshot cannot overwrite stream quotes');
      } finally {
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        await page.locator('#source-select').selectOption('yahoo');
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo');
      }
    });
    await check('a failed acknowledgement preserves a stream-confirmed Shoonya source and its login error', async () => {
      try {
        await holdSourceRequest(() => page.locator('#source-select').selectOption('shoonya'));
        await emit(snapshot(TODAY, 7, { current_price: 25112, feed_status: 'error', feed_error: 'Shoonya login failed; refresh SHOONYA_AUTH_CODE and check credentials/IP' }));
        releaseSourceRequest(409);
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        assert.equal(await page.locator('#source-select').inputValue(), 'shoonya');
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /7 candles/);
        assert.equal(await page.locator('#price').textContent(), '25112.00');
        assert.match(await page.locator('#feed-status').textContent(), /Shoonya login failed/);
        assert.equal(await page.locator('#shoonya-help').isVisible(), true);
      } finally {
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
      }
    });
    await check('Shoonya recovery guidance loads authorization and public IP only on request', async () => {
      await emit(snapshot(TODAY, 7, { feed_status: 'error', feed_error: 'Missing Shoonya credentials: SHOONYA_AUTH_CODE' }));
      assert.equal(await page.locator('#shoonya-help').isVisible(), true);
      assert.equal(helpRequests, 0, 'hidden or collapsed help does not request authorization metadata');
      assert.equal(ipRequests, 0, 'selecting a provider never looks up a public IP');
      await page.locator('#shoonya-help summary').click();
      await page.waitForFunction(() => !document.querySelector('#shoonya-login').hidden);
      assert.equal(await page.locator('#shoonya-login').getAttribute('href'), loginUrl);
      assert.equal(await page.locator('#shoonya-client-id').textContent(), 'Client ID: fixture-client');
      assert.equal(ipRequests, 0, 'opening guidance still does not look up a public IP');
      await page.locator('#check-ip').click();
      await page.waitForFunction(() => document.querySelector('#public-ip').textContent.includes('203.0.113.8'));
      assert.equal(ipRequests, 1);
      assert.match(await page.locator('#public-ip').textContent(), /confirm it matches Shoonya/);
      ipFailure = true;
      await page.locator('#check-ip').click();
      await page.waitForFunction(() => document.querySelector('#public-ip').textContent.includes('curl -s ifconfig.me'));
      assert.equal(await page.locator('#check-ip').isDisabled(), false);
      ipFailure = false;
      for (const value of [null, 'https://example.com/OAuthlogin/authorize/oauth?client_id=fixture-client', 'https://api.shoonya.com/other?client_id=fixture-client']) {
        loginUrl = value;
        await page.locator('#shoonya-help summary').click();
        const loaded = page.waitForResponse('**/api/shoonya/help');
        await page.locator('#shoonya-help summary').click();
        await loaded;
        await page.waitForFunction(() => document.querySelector('#shoonya-login-note').textContent.includes('SHOONYA_CLIENT_ID'));
        assert.equal(await page.locator('#shoonya-login').isVisible(), false);
        assert.equal(await page.locator('#shoonya-login').getAttribute('href'), null, 'missing or untrusted authorization URLs are never linked');
        assert.equal(await page.locator('#shoonya-client-id').isVisible(), false);
        assert.equal(await page.locator('#shoonya-client-id').textContent(), '');
      }
      loginUrl = 'https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=fixture-client';
    });
    await check('retry waits for a fresh Shoonya session and keeps ready stream data ahead of its acknowledgement', async () => {
      const oldStatus = snapshot(TODAY, 7, { type: 'status', feed_status: 'error', feed_error: 'Shoonya login failed' });
      try {
        await holdSourceRequest(() => page.locator('#source-retry').click());
        await emit(oldStatus);
        assert.equal(await page.locator('#source-select').isDisabled(), true);
        assert.equal(await page.locator('#empty-state').isVisible(), true, 'an old same-source status cannot confirm a retry');
        await emit(snapshot(TODAY, 10, { type: 'session', current_price: 25113, feed_status: 'live' }));
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /10 candles/);
        assert.equal(await page.locator('#source-retry').isDisabled(), true);
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /10 candles/);
        assert.equal(await page.locator('#price').textContent(), '25113.00');
        assert.equal(await page.locator('#shoonya-help').isVisible(), false, 'healthy provider state hides recovery guidance');
      } finally {
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        await page.locator('#source-select').selectOption('yahoo');
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo');
      }
    });
    await check('illustrative replay hides future signals and restores arriving live data on stop', async () => {
      const fixture = snapshot(TODAY, 25);
      const future = fixture.candles[12];
      fixture.signals = [{ timestamp: future.end_time, direction: 'BULLISH', price: future.close, strategy: 'dual_ORB' }];
      fixture.setups['15'].signals = [{ ...fixture.signals[0] }];
      fixture.setups['5'].signals = [{ ...fixture.signals[0], timestamp: fixture.candles_5m[38].end_time }];
      await emit(fixture);
      await page.locator('#replay').click();
      await page.waitForFunction(() => document.querySelector('#replay').getAttribute('aria-pressed') === 'true');
      assert.equal(await page.locator('#replay').getAttribute('aria-pressed'), 'true');
      assert.match(await page.locator('#session-status').textContent(), /replay/i);
      assert.doesNotMatch(await page.locator('#signal-summary').textContent(), /12:30/);
      assert.notEqual(await page.locator('#price').textContent(), fixture.current_price.toFixed(2));
      const opening = await page.evaluate(date => {
        const before30 = replayView(Date.parse(`${date}T09:30:00+05:30`));
        const at30 = replayView(Date.parse(`${date}T09:45:00+05:30`));
        const at45 = replayView(Date.parse(`${date}T10:00:00+05:30`));
        return { before30, at30, at45 };
      }, TODAY);
      assert.equal(opening.before30.setups['15'].range_locked, false);
      assert.equal(opening.at30.setups['15'].range_locked, true);
      assert.equal(opening.at30.setups['5'].range_locked, false);
      assert.equal(opening.at45.setups['5'].range_locked, true);
      assert.deepEqual(opening.at45.setups['15'].signals, []);
      assert.deepEqual(opening.at45.setups['5'].signals, []);
      assert.deepEqual(opening.at45.signals, []);
      assert.ok(opening.before30.setups['15'].orb_high < opening.at45.setups['15'].orb_high, 'replay forming range has no future high');
      await page.evaluate(() => { replay.started = performance.now() - replay.duration * .5; scheduleRender(); });
      await page.waitForFunction(() => document.querySelector('#replay-progress').textContent.includes('12:22'));
      assert.doesNotMatch(await page.locator('#signal-summary').textContent(), /12:30/);
      await page.evaluate(() => { replay.started = performance.now() - replay.duration * .6; scheduleRender(); });
      await page.waitForFunction(() => document.querySelector('#signal-summary').textContent.includes('12:30'));
      await emit(snapshot(TODAY, 25, { current_price: 26001 }));
      assert.notEqual(await page.locator('#price').textContent(), '26001.00');
      await page.locator('#replay').click();
      await page.waitForFunction(() => document.querySelector('#replay').getAttribute('aria-pressed') === 'false' && document.querySelector('#price').textContent === '26001.00');
      assert.equal(await page.locator('#replay').getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator('#price').textContent(), '26001.00');
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /25 candles/);
    });
    await check('server restart accepts a fresh epoch even when its source generation restarts at zero', async () => {
      assert.ok(selectedGeneration > 0, 'fixture switched sources before restarting');
      sourceEpoch = 'fixture-process-b';
      selectedGeneration = 0;
      await emit(snapshot(TODAY, 7, { current_price: 26002 }));
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /7 candles/);
      assert.equal(await page.locator('#price').textContent(), '26002.00');
      assert.equal(await page.locator('#source-select').inputValue(), 'yahoo');
    });
    await check('comparisons use native selected closes at their timestamps with a shared 09:15 origin', async () => {
      const fixture = comparisonSnapshot(TODAY, 8, {}, { current_price: 25040 });
      for (const [symbol, , open] of COMPARISONS) {
        fixture.instruments[symbol].current_price = open * 1.0016;
        for (const [key, movement] of [['candles', .001], ['candles_5m', .0002]]) {
          fixture.instruments[symbol][key][0].close = open * (1 + movement);
          fixture.instruments[symbol][key][0].high = open * (1 + movement) + 12;
          fixture.instruments[symbol][key].at(-1).close = open * 1.0016;
        }
      }
      await emit(fixture);
      assert.equal(await page.locator('#comparison-toggle').getAttribute('aria-pressed'), 'true');
      assert.equal((await page.locator('#comparison-toggle').textContent()).trim(), 'Derivatives');
      assert.match(await page.locator('#comparison-toggle').getAttribute('aria-label'), /derivatives/i);
      assert.equal(await page.locator('#comparison-toggle').getAttribute('aria-selected'), 'true');
      for (const [, label] of COMPARISONS) assert.ok((await page.locator('#series-labels').textContent()).includes(label));
      assert.equal(await page.locator('#primary-return, #comparison-return, #chart-inspector, #comparison-detail').count(), 0, 'the top return list and hover card are removed');
      assert.match(await page.locator('#series-labels [data-symbol="^NSEI"]').textContent(), /NIFTY\s?50[\s\S]*0\.16/);
      const fifteen = await captureChart();
      assert.equal(new Set(fifteen.series.map(lines => lines[0]?.color)).size, COMPARISONS.length, 'all comparison colors are distinct');
      assert.deepEqual(await page.locator('#series-labels button.series-endpoint').evaluateAll(items => items.map(item => item.dataset.symbol)), COMPARISONS.map(([symbol]) => symbol), 'endpoint controls keep a stable order across all indices');
      for (const interval of [15, 5]) {
        if (interval === 5) { await page.locator('#interval-5m').click(); await page.waitForTimeout(500); }
        const chart = interval === 15 ? fifteen : await captureChart();
        const expected = interval === 15 ? 8 : 24;
        const native = await page.evaluate(symbols => Object.fromEntries(symbols.map(symbol => [symbol, comparisonPoints(view.instruments[symbol]).map(point => point.price)])), COMPARISONS.map(([symbol]) => symbol));
        for (const [index, [symbol]] of COMPARISONS.entries()) {
          const lines = chart.series[index], points = lines.flatMap(line => line.points);
          assert.equal(points.length, expected + 1, 'one native close per selected candle plus the explicit session-open anchor');
          assert.deepEqual(native[symbol], fixture.instruments[symbol][interval === 15 ? 'candles' : 'candles_5m'].map(bar => bar.close));
          assert.ok(lines.every(line => line.width <= 1.5));
          assert.equal(points[0][0], 12, 'the session-open anchor sits at the left edge');
          assert.ok(Math.abs(points[0][1] - chart.series[0][0].points[0][1]) < .01, 'all session-open anchors share zero-return geometry');
          assert.ok(Math.abs(points.at(-1)[1] - chart.series[0][0].points.at(-1)[1]) < .01, 'equal returns from the session open share vertical geometry');
          for (let slot = 0; slot < expected; slot++) assert.ok(Math.abs(points[slot + 1][0] - (12 + (chart.width - 88) * (slot + 1) * interval / 375)) < .01, 'each observed close sits at its real candle end');
        }
        const bounds = await page.locator('#chart').boundingBox();
        await page.mouse.move(bounds.x + 12 + (chart.width - 88) / 375, bounds.y + 40);
        assert.match(await page.locator('#ohlc').textContent(), /09:15/);
        assert.equal(await page.locator('#chart-inspector, #comparison-detail').count(), 0, 'chart hover cannot create a floating card');
        assert.match(await page.locator('#series-labels [data-symbol="^NSEBANK"]').textContent(), /BANKNIFTY.*0\.16/, 'the endpoint retains the actual return while chart hover moves');
        await page.mouse.move(0, 0);
      }
      const liveIntervals = motionSnapshot();
      for (const [symbol, , open] of COMPARISONS) liveIntervals.instruments[symbol].active_candle.close = open * 1.0018;
      await emit(liveIntervals);
      await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
      await captureChart();
      const switched = await page.evaluate(() => {
        const canonical = JSON.stringify(view);
        setIntervalMinutes(5); transition = null;
        const first = window.__captureChart({ animate: true });
        chartMotion.finish();
        return { first, settled: window.__captureChart({ animate: true }), unchanged: JSON.stringify(view) === canonical };
      });
      assert.deepEqual(switched.first.series, switched.settled.series, 'switching granularity cannot reuse a different interval\'s price tween');
      assert.equal(switched.unchanged, true);
      await emit(comparisonSnapshot(TODAY, 25));
      for (const interval of [15, 5]) {
        await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
        const detail = await page.evaluate(() => {
          pointer = { x: canvas.clientWidth - 76, y: 40 }; drawChart();
          pointer = null; return { ohlc: document.querySelector('#ohlc').textContent };
        });
        const start = interval === 15 ? '15:15' : '15:25';
        assert.ok(detail.ohlc.includes(start));
        assert.equal(await page.locator('#chart-inspector').count(), 0, 'final-candle hover stays minimal');
      }
      await page.locator('#interval-15m').click(); await page.waitForTimeout(500);
    });
    await check('comparison deltas preserve primary candles, quote and strategies while sharing a fitted scale', async () => {
      const fixture = comparisonSnapshot(TODAY, 8, {}, { current_price: 25040 });
      await emit(fixture);
      const primaryState = () => page.evaluate(() => ({ candles: view.candles, five: view.candles_5m, price: view.current_price, setups: view.setups, signals: view.signals }));
      const primaryBefore = await primaryState();
      for (const [symbol, , open] of COMPARISONS) {
        const comparison = { ...fixture.instruments[symbol], comparison_status: 'closed', current_price: open * 1.0016,
          last_tick_time: `${TODAY}T11:17:00+05:30`,
          active_candle: { ...candle(TODAY, 8, open, open * 1.0016), symbol, is_closed: false },
          active_candle_5m: { ...candle(TODAY, 24, open, open * 1.0016, 5), symbol, is_closed: false } };
        const unaffectedBefore = await page.evaluate(symbol => Object.fromEntries(Object.entries(view.instruments).filter(([key]) => key !== symbol)), symbol);
        await emit({ type: 'state', symbol, date: TODAY, source_id: selectedSource, source_generation: selectedGeneration,
          source_epoch: sourceEpoch, instruments: { [symbol]: comparison } });
        assert.deepEqual(await primaryState(), primaryBefore);
        assert.deepEqual(await page.evaluate(symbol => Object.fromEntries(Object.entries(view.instruments).filter(([key]) => key !== symbol)), symbol), unaffectedBefore, 'a delta changes only its own instrument');
        assert.equal(await page.locator('#price').textContent(), '25040.00');
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /8 candles/);
        const chart = await captureChart();
        const terminal = chart.series[COMPARISONS.findIndex(([key]) => key === symbol)].flatMap(line => line.points).at(-1);
        assert.ok(Math.abs(terminal[0] - (12 + (chart.width - 88) * 122 / 375)) < .01, 'forming comparison uses its actual quote timestamp');
        const display = await page.evaluate(symbol => ({ normalized: comparisonSeries(view).find(item => item.instrument.symbol === symbol).points.at(-1).normalized,
          min: chartMotion.domain.shownMin, max: chartMotion.domain.shownMax }), symbol);
        const expectedY = chart.height - 30 - (display.normalized - display.min) / (display.max - display.min) * (chart.height - 50);
        assert.ok(Math.abs(terminal[1] - expectedY) < .01, 'forming comparison uses its session-open return and the shared NIFTY open');
      }
    });
    await check('forming backfill stays continuous before the next live quote and through rollover', async () => {
      await page.evaluate(() => { setIntervalMinutes(5); transition = null; });
      const originalNow = await page.evaluate(() => window.__now);
      const fixture = comparisonSnapshot(TODAY, 8);
      const coverage = `${TODAY}T11:17:00+05:30`;
      for (const [symbol, , open] of COMPARISONS) {
        const instrument = fixture.instruments[symbol];
        instrument.active_candle_5m = { ...candle(TODAY, 24, open * 1.0016, open * 1.002, 5), symbol, is_closed: false };
        instrument.current_price = instrument.active_candle_5m.close;
        instrument.last_tick_time = null;
        instrument.history_coverage_time = coverage;
        // Even a nominally live server status needs an actual live timestamp.
        instrument.comparison_status = 'live';
      }
      await page.evaluate(coverage => { window.__now = Date.parse(coverage); }, coverage);
      await emit(fixture);
      const assertContinuousEndpoint = (chart, minute) => {
        const expected = 12 + (chart.width - 88) * minute / 375;
        for (const lines of chart.series) {
          const commands = lines.flatMap(line => line.commands);
          assert.equal(commands.filter(command => command.method === 'moveTo').length, 1, 'continuous history has only its opening path start');
          assert.ok(Math.abs(commands.at(-1).point[0] - expected) < .01, `comparison reaches session minute ${minute}`);
        }
      };
      assertContinuousEndpoint(await captureChart(), 122);
      assert.deepEqual(await page.locator('#series-labels button.series-endpoint').evaluateAll(items => items.map(item => item.dataset.status)), COMPARISONS.map(() => 'stale'), 'historical coverage never impersonates a live quote');
      const guards = await page.evaluate(instrument => {
        const points = change => comparisonPoints({ ...instrument, ...change });
        const lastTime = change => points(change).at(-1).time;
        const closedEnd = Date.parse(instrument.candles_5m.at(-1).end_time);
        return {
          closedEnd,
          rejected: [null, 'invalid', `${instrument.date}T11:14:59+05:30`, `${instrument.date}T11:20:00+05:30`].map(history_coverage_time => lastTime({ history_coverage_time })),
          invalidActive: [{ is_complete: false }, { close_is_valid: false }].map(flags => lastTime({ active_candle_5m: { ...instrument.active_candle_5m, ...flags } })),
          cutoff: comparisonPoints(instrument, Date.parse(`${instrument.date}T11:16:00+05:30`)).at(-1).time,
          invalidGap: points({ candles_5m: instrument.candles_5m.map((bar, index, list) => index === list.length - 1 ? { ...bar, close_is_valid: false } : bar) }).at(-1).gap,
          missingGap: points({ candles_5m: instrument.candles_5m.slice(0, -1) }).at(-1).gap
        };
      }, fixture.instruments[COMPARISONS[0][0]]);
      assert.deepEqual(guards.rejected, Array(4).fill(guards.closedEnd), 'unknown or out-of-bar coverage cannot invent a point');
      assert.deepEqual(guards.invalidActive, Array(2).fill(guards.closedEnd), 'invalid forming history remains excluded');
      assert.equal(guards.cutoff, guards.closedEnd, 'replay cannot expose a future historical close');
      assert.equal(guards.invalidGap, true, 'invalid closed bars retain genuine gaps');
      assert.equal(guards.missingGap, true, 'missing closed bars retain genuine gaps');
      const quote = `${TODAY}T11:17:01+05:30`;
      await page.evaluate(quote => { window.__now = Date.parse(quote); }, quote);
      for (const [symbol] of COMPARISONS) {
        const instrument = fixture.instruments[symbol];
        instrument.last_tick_time = quote;
        instrument.active_candle_5m.close += 1;
        instrument.current_price = instrument.active_candle_5m.close;
        await emit({ type: 'state', symbol, date: TODAY, instruments: { [symbol]: instrument } });
      }
      assertContinuousEndpoint(await captureChart(), 122 + 1 / 60);
      assert.deepEqual(await page.locator('#series-labels button.series-endpoint').evaluateAll(items => items.map(item => item.dataset.status)), COMPARISONS.map(() => 'live'));
      const rollover = `${TODAY}T11:20:01+05:30`;
      await page.evaluate(rollover => { window.__now = Date.parse(rollover); }, rollover);
      for (const [symbol] of COMPARISONS) {
        const instrument = fixture.instruments[symbol];
        const closed = { ...instrument.active_candle_5m, is_closed: true };
        instrument.candles_5m.push(closed);
        instrument.active_candle_5m = { ...candle(TODAY, 25, closed.close, closed.close + 1, 5), symbol, is_closed: false };
        instrument.current_price = instrument.active_candle_5m.close;
        instrument.last_tick_time = rollover;
        await emit({ type: 'state', symbol, date: TODAY, instruments: { [symbol]: instrument } });
      }
      assertContinuousEndpoint(await captureChart(), 125 + 1 / 60);
      await page.evaluate(originalNow => { window.__now = originalNow; }, originalNow);
      await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
    });
    await check('live candles ease toward quotes and retarget from their displayed position without changing market data', async () => {
      await emit(motionSnapshot());
      const initial = await captureChart();
      const frames = await page.evaluate(() => {
        const update = price => {
          const primary = view.instruments['^NSEI'];
          const next = { ...primary, current_price: price,
            active_candle: { ...primary.active_candle, close: price },
            active_candle_5m: { ...primary.active_candle_5m, close: price } };
          window.__emit({ type: 'tick', date: view.date, symbol: '^NSEI', price,
            active_candle: next.active_candle, active_candle_5m: next.active_candle_5m,
            instruments: { '^NSEI': next } });
        };
        const sample = now => { render(now); return window.__captureChart({ animate: true, now }); };
        const started = performance.now();
        update(25045);
        const canonicalFirst = JSON.stringify(view);
        const first = sample(started), middle = sample(started + 90);
        const firstUnchanged = JSON.stringify(view) === canonicalFirst;
        const firstHeader = document.querySelector('#price').textContent;
        update(25025);
        const canonicalLast = JSON.stringify(view);
        const retarget = sample(started + 90), returning = sample(started + 180), settled = sample(started + 1090);
        chartMotion.finish();
        return { first, middle, retarget, returning, settled, firstUnchanged,
          lastUnchanged: JSON.stringify(view) === canonicalLast, firstHeader,
          lastHeader: document.querySelector('#price').textContent, lastPrice: view.current_price };
      });
      const closeY = chart => chart.bodies.at(-1).y;
      assert.ok(Math.abs(closeY(frames.first) - closeY(initial)) < .01, 'new quote begins at the displayed close');
      assert.ok(closeY(frames.middle) < closeY(frames.first), 'an intermediate frame moves toward the higher close');
      assert.ok(Math.abs(closeY(frames.retarget) - closeY(frames.middle)) < .01, 'rapid retargeting preserves the current displayed position');
      assert.ok(closeY(frames.middle) < closeY(frames.returning) && closeY(frames.returning) < closeY(frames.settled), 'retargeting moves smoothly toward the latest quote');
      assert.ok(closeY(frames.settled) < closeY(frames.first), 'the final close matches the latest upward quote');
      for (const frame of Object.values(frames).filter(value => value?.bodies)) {
        assert.deepEqual(frame.bodies.slice(0, -1), initial.bodies.slice(0, -1), 'in-range quotes leave historical candle geometry fixed');
        assert.deepEqual(frame.series, initial.series, 'primary quote motion leaves the comparison series fixed');
      }
      assert.ok(Math.abs(frames.settled.price.points[0][1] - closeY(frames.settled)) < .01, 'price line settles at the actual closing price');
      assert.equal(frames.firstUnchanged, true); assert.equal(frames.lastUnchanged, true);
      assert.equal(frames.firstHeader, '25045.00'); assert.equal(frames.lastHeader, '25025.00');
      assert.equal(frames.lastPrice, 25025);
    });
    await check('comparison quote motion preserves closed points, gaps and all other instruments', async () => {
      await emit(motionSnapshot());
      const initial = await captureChart();
      const frames = await page.evaluate(() => {
        const bank = view.instruments['^NSEBANK'];
        const next = { ...bank, current_price: 50090,
          active_candle: { ...bank.active_candle, close: 50090 },
          active_candle_5m: { ...bank.active_candle_5m, close: 50090 } };
        window.__emit({ type: 'state', date: view.date, symbol: bank.symbol, instruments: { [bank.symbol]: next } });
        const canonical = JSON.stringify(view), started = performance.now();
        const frame = now => {
          const chart = window.__captureChart({ animate: true, now });
          return { ...chart, label: document.querySelector('#series-labels [data-symbol="^NSEBANK"]').textContent,
            min: chartMotion.domain.shownMin, max: chartMotion.domain.shownMax };
        };
        const first = frame(started), middle = frame(started + 90), settled = frame(started + 1000);
        chartMotion.finish();
        return { first, middle, settled, unchanged: JSON.stringify(view) === canonical };
      });
      const endpoint = chart => chart.series[0].flatMap(line => line.points).at(-1);
      assert.ok(Math.abs(endpoint(frames.first)[1] - endpoint(initial)[1]) < .01);
      assert.ok(endpoint(frames.settled)[1] < endpoint(frames.middle)[1] && endpoint(frames.middle)[1] < endpoint(frames.first)[1], 'comparison endpoint has an intermediate price before settling');
      for (const frame of [frames.first, frames.middle, frames.settled]) {
        const normalized = frame.min + (frame.height - 30 - endpoint(frame)[1]) / (frame.height - 50) * (frame.max - frame.min);
        const plottedReturn = (normalized / 25000 - 1) * 100;
        assert.ok(frame.label.includes(`${plottedReturn >= 0 ? '+' : ''}${plottedReturn.toFixed(2)}%`), 'the animated endpoint label matches the price currently painted');
        assert.deepEqual(frame.bodies, initial.bodies);
        assert.deepEqual(frame.series.slice(1), initial.series.slice(1), 'other comparison lines do not move');
        const commands = frame.series[0].flatMap(line => line.commands);
        assert.equal(commands.filter(command => command.method === 'moveTo').length, 1, 'motion does not introduce a path break');
        assert.deepEqual(commands.slice(0, -1), initial.series[0].flatMap(line => line.commands).slice(0, -1), 'closed comparison history stays fixed');
      }
      assert.equal(frames.unchanged, true);
      const withGap = motionSnapshot();
      withGap.instruments['^NSEBANK'].candles.at(-1).close_is_valid = false;
      withGap.instruments['^NSEBANK'].candles_5m.at(-1).close_is_valid = false;
      await emit(withGap); await captureChart();
      const gaps = await page.evaluate(() => {
        const bank = view.instruments['^NSEBANK'];
        window.__emit({ type: 'state', date: view.date, symbol: bank.symbol,
          instruments: { [bank.symbol]: { ...bank, current_price: 50080,
            active_candle: { ...bank.active_candle, close: 50080 }, active_candle_5m: { ...bank.active_candle_5m, close: 50080 } } } });
        const started = performance.now();
        return [0, 90, 1000].map(offset => window.__captureChart({ animate: true, now: started + offset }).series[0].flatMap(line => line.commands).at(-1).method);
      });
      assert.deepEqual(gaps, ['moveTo', 'moveTo', 'moveTo'], 'an invalid bar remains a genuine gap throughout motion');
    });
    await check('reduced motion snaps an in-flight candle and subsequent quotes to their targets', async () => {
      await emit(motionSnapshot()); await captureChart();
      await page.evaluate(() => {
        const primary = view.instruments['^NSEI'];
        const next = { ...primary, current_price: 25045, active_candle: { ...primary.active_candle, close: 25045 },
          active_candle_5m: { ...primary.active_candle_5m, close: 25045 } };
        window.__emit({ type: 'tick', date: view.date, symbol: primary.symbol, price: 25045,
          active_candle: next.active_candle, active_candle_5m: next.active_candle_5m, instruments: { [primary.symbol]: next } });
        const now = performance.now(); drawChart(now); drawChart(now + 90);
      });
      try {
        await page.emulateMedia({ reducedMotion: 'reduce' });
        const first = await captureChart({ animate: true });
        const settled = await captureChart();
        assert.deepEqual(first.bodies, settled.bodies, 'enabling reduced motion finishes the current candle');
        assert.deepEqual(first.price, settled.price);
        const next = await page.evaluate(() => {
          const primary = view.instruments['^NSEI'];
          const updated = { ...primary, current_price: 25030, active_candle: { ...primary.active_candle, close: 25030 },
            active_candle_5m: { ...primary.active_candle_5m, close: 25030 } };
          window.__emit({ type: 'tick', date: view.date, symbol: primary.symbol, price: 25030,
            active_candle: updated.active_candle, active_candle_5m: updated.active_candle_5m, instruments: { [primary.symbol]: updated } });
          render(performance.now());
          return { chart: window.__captureChart({ animate: true }), active: chartMotion.active,
            header: document.querySelector('#price').textContent };
        });
        assert.deepEqual(next.chart.bodies, (await captureChart()).bodies, 'new quotes are drawn at their target immediately');
        assert.equal(next.active, false); assert.equal(next.header, '25030.00');
      } finally { await page.emulateMedia({ reducedMotion: 'no-preference' }); }
    });
    await check('date, source and replay changes discard pending quote geometry', async () => {
      const beginMotion = async () => {
        await emit(motionSnapshot()); await captureChart();
        await page.evaluate(() => {
          const primary = view.instruments['^NSEI'];
          window.__emit({ type: 'tick', date: view.date, symbol: primary.symbol, price: 25045,
            active_candle: { ...primary.active_candle, close: 25045 },
            active_candle_5m: { ...primary.active_candle_5m, close: 25045 },
            instruments: { [primary.symbol]: { ...primary, current_price: 25045,
              active_candle: { ...primary.active_candle, close: 25045 },
              active_candle_5m: { ...primary.active_candle_5m, close: 25045 } } } });
          const now = performance.now(); render(now); drawChart(now + 90);
        });
      };
      for (const [name, replacement] of [
        ['date', snapshot(PREVIOUS, 8, { type: 'session', today: PREVIOUS, current_price: 40000 })],
        ['source', snapshot(TODAY, 8, { source_id: 'shoonya', current_price: 40000 })]
      ]) {
        await beginMotion();
        const result = await page.evaluate(replacement => {
          window.__emit(replacement); render(performance.now());
          const first = window.__captureChart({ animate: true });
          chartMotion.finish();
          return { first, settled: window.__captureChart({ animate: true }), price: view.current_price };
        }, replacement);
        assert.deepEqual(result.first.bodies, result.settled.bodies, `${name} change cannot reuse earlier candle motion`);
        assert.deepEqual(result.first.price, result.settled.price, `${name} change displays its own latest price immediately`);
        assert.equal(result.price, 40000);
        await emit(snapshot(TODAY, 8, { type: 'session', today: TODAY }));
      }
      await beginMotion();
      const replayReset = await page.evaluate(() => {
        const liveData = JSON.stringify(view);
        startReplay(); replay.complete = true;
        render(performance.now());
        const first = window.__captureChart({ animate: true });
        chartMotion.finish();
        const settled = window.__captureChart({ animate: true });
        stopReplay(); render(performance.now());
        const restored = window.__captureChart({ animate: true });
        chartMotion.finish();
        return { first, settled, restored, final: window.__captureChart({ animate: true }),
          unchanged: JSON.stringify(view) === liveData, header: document.querySelector('#price').textContent };
      });
      assert.deepEqual(replayReset.first.bodies, replayReset.settled.bodies, 'replay begins at its own opening candle');
      assert.equal(replayReset.first.bodies.length, 1);
      assert.deepEqual(replayReset.restored.bodies, replayReset.final.bodies, 'stopping replay restores actual live geometry');
      assert.deepEqual(replayReset.restored.price, replayReset.final.price);
      assert.equal(replayReset.unchanged, true); assert.equal(replayReset.header, '25045.00');
    });
    await check('hidden post-breakout extremes do not stretch either source, and NIFTY reveal restores the idle scale', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        await page.mouse.move(0, 0);
        await page.evaluate(() => { window.__now = Date.parse(`${today}T10:19:00+05:30`); });
        for (const source_id of ['yahoo', 'shoonya']) {
          const fixture = comparisonSnapshot(TODAY, 4, {}, { source_id, market_status: 'live', signals: [niftySignal()] });
          const focit = fixture.instruments['FOCIT.BO'];
          for (const key of ['candles', 'candles_5m']) {
            focit[key].forEach((bar, index, bars) => { bar.close *= 1 + .02 * index / (bars.length - 1); });
          }
          focit.current_price = focit.candles_5m.at(-1).close;
          await emit({ ...fixture, instruments: { '^NSEI': fixture.instruments['^NSEI'] } });
          const primaryOnly = await captureChart();
          await emit(fixture);
          const idle = await captureChart();
          assert.deepEqual(idle.bodies, primaryOnly.bodies, `${source_id}: hidden +2% FOCIT leaves NIFTY's scale intact`);
          assert.ok(idle.series.every(lines => lines.every(line => line.alpha === 0)));
          const box = await page.locator('#chart').boundingBox(), body = idle.bodies.at(-1);
          await page.mouse.move(box.x + body.x + body.w / 2, box.y + body.y + body.h / 2);
          await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'true');
          const revealed = await captureChart();
          assert.notDeepEqual(revealed.bodies, idle.bodies, 'revealing an extreme temporarily fits the overview');
          assert.ok(revealed.series[4].flatMap(line => line.points).every(([, y]) => y >= 20 && y <= revealed.height - 30), 'revealed FOCIT fits the plot');
          await page.waitForTimeout(250);
          assert.equal(await page.locator('#series-labels').getAttribute('data-reveal'), 'true', 'rescaling cannot unhover a stationary pointer');
          assert.deepEqual((await captureChart()).bodies, revealed.bodies, 'the stationary overview does not oscillate');
          for (const shift of [1, 2, 3, 4, 5]) {
            await page.mouse.move(box.x + body.x + body.w / 2 + shift, box.y + body.y + body.h / 2);
            await captureChart();
            assert.equal(await page.locator('#series-labels').getAttribute('data-reveal'), 'true', 'small pointer movements inside the initiating candle keep the overview stable');
          }
          await page.mouse.move(0, 0);
          await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'false');
          assert.deepEqual((await captureChart()).bodies, idle.bodies, 'leaving removes the hidden extreme from the fitted domain');
          await emit({ type: 'state', date: TODAY, source_id, instruments: { 'FOCIT.BO': { current_price: focit.current_price + 1 } } });
          await page.waitForTimeout(250);
          assert.deepEqual((await captureChart()).bodies, idle.bodies, 'later quotes cannot resurrect the overview scale');
          await page.locator('#series-labels [data-primary="true"]').focus();
          await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'true');
          assert.deepEqual((await captureChart()).bodies, revealed.bodies, 'keyboard reveal fits the same overview');
          await page.locator('#comparison-toggle').focus();
          await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'false');
          assert.deepEqual((await captureChart()).bodies, idle.bodies);
        }
      } finally {
        await page.mouse.move(0, 0);
        await page.evaluate(now => { window.__now = now; }, originalNow);
        await emit(snapshot(TODAY, 8));
      }
    });
    await check('live scale expands for a revealed extreme and stays steady when the quote returns inside', async () => {
      const fixture = motionSnapshot(); await emit(fixture);
      await revealComparisons();
      const initial = await captureChart();
      const bank = { ...fixture.instruments['^NSEBANK'], current_price: 52500,
        active_candle: { ...fixture.instruments['^NSEBANK'].active_candle, close: 52500 },
        active_candle_5m: { ...fixture.instruments['^NSEBANK'].active_candle_5m, close: 52500 } };
      await emit({ type: 'state', date: TODAY, symbol: bank.symbol, instruments: { [bank.symbol]: bank } });
      const expanded = await captureChart();
      assert.notDeepEqual(expanded.bodies, initial.bodies, 'a new extreme expands the fitted scale');
      await emit({ type: 'state', date: TODAY, symbol: bank.symbol, instruments: { [bank.symbol]: fixture.instruments[bank.symbol] } });
      assert.deepEqual((await captureChart()).bodies, expanded.bodies, 'returning quotes do not make the scale breathe');
      await emit(fixture);
      await revealComparisons();
      assert.deepEqual((await captureChart()).bodies, initial.bodies, 'a fresh snapshot resets the fitted scale');
    });
    await check('large positive and negative comparison moves fit fully and comparison tabs display only their selected group', async () => {
      const fixture = comparisonSnapshot(TODAY, 8);
      for (const [index, [symbol, , open]] of COMPARISONS.entries()) {
        const close = open * (index % 2 ? .72 : 1.35);
        for (const key of ['candles', 'candles_5m']) fixture.instruments[symbol][key].at(-1).close = close;
        fixture.instruments[symbol].current_price = close;
      }
      await emit(fixture);
      await revealComparisons();
      const enabled = await captureChart();
      assert.ok(enabled.clips.length >= 1, 'comparison paths use the plot bounds');
      const [left, top, width, height] = enabled.clips.find(rect => rect?.length === 4);
      for (const lines of enabled.series) {
        assert.ok(lines.length > 0);
        assert.ok(lines.flatMap(line => line.points).every(([x, y]) => x >= left - .01 && x <= left + width + .01 && y >= top - .01 && y <= top + height + .01), 'every positive or negative comparison point fits inside the plot');
      }
      const up = enabled.series[0].flatMap(line => line.points), down = enabled.series[1].flatMap(line => line.points);
      assert.ok(Math.abs(up[0][1] - down[0][1]) < .01, 'session-open anchors share the same zero-return level');
      assert.ok(Math.abs((up[0][1] - up.at(-1)[1]) / (down.at(-1)[1] - down[0][1]) - 35 / 28) < .001, '35% up and 28% down retain their true shared percentage ratio from the session open');
      const primaryBefore = await page.evaluate(() => JSON.stringify(view));
      await page.locator('#sectors-tab').click();
      await page.waitForFunction(() => document.querySelector('#comparison-toggle').getAttribute('aria-pressed') === 'false');
      const hidden = await captureChart();
      assert.equal(hidden.allComparisons.length, 0, 'an empty sector fixture never leaks derivative lines into the selected sector group');
      assert.equal(await page.locator('#sectors-tab').getAttribute('aria-selected'), 'true');
      assert.notDeepEqual(hidden.bodies.map(body => body.y), enabled.bodies.map(body => body.y), 'the inactive group no longer stretches primary values');
      assert.equal(await page.evaluate(() => JSON.stringify(view)), primaryBefore, 'display group changes never alter market data');
      await selectGroup('derivatives');
      await page.waitForFunction(() => document.querySelector('#comparison-toggle').getAttribute('aria-pressed') === 'true');
      await revealComparisons();
      assert.deepEqual((await captureChart()).series, enabled.series, 'returning to Derivatives restores every fitted comparison');
    });
    await check('delayed quotes retain their actual return and timestamp until their adjusted freshness expires', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        const fixture = comparisonSnapshot(TODAY, 7), focit = fixture.instruments['FOCIT.BO'];
        focit.comparison_status = 'delayed'; focit.quote_delay_seconds = 900;
        focit.last_tick_time = `${TODAY}T11:02:00+05:30`; focit.current_price = 30060;
        focit.active_candle = { ...candle(TODAY, 7, 30048, 30060), symbol: focit.symbol, is_closed: false };
        focit.active_candle_5m = { ...candle(TODAY, 21, 30048, 30060, 5), symbol: focit.symbol, is_closed: false };
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:17:00+05:30'); });
        await emit(fixture);
        const legend = page.locator('#series-labels [data-symbol="FOCIT.BO"]');
        assert.equal(await legend.getAttribute('data-status'), 'delayed');
        assert.match(await legend.textContent(), /FOCIT.*\+0\.20%.*D/);
        assert.equal(Date.parse(await legend.getAttribute('data-asof')), Date.parse(focit.last_tick_time));
        assert.match(await legend.getAttribute('title'), /as of 11:02.*15m delayed/);
        await revealComparisons();
        const delayed = await captureChart();
        assert.ok(delayed.series[4].every(line => line.alpha === .32), 'expected provider delay retains healthy reveal opacity');
        assert.equal(await page.evaluate(() => view.instruments['FOCIT.BO'].last_tick_time), focit.last_tick_time, 'freshness adjustment never rewrites the upstream timestamp');
        await emit({ type: 'state', date: TODAY, instruments: { 'FOCIT.BO': { comparison_status: 'live' } } });
        assert.equal(await legend.getAttribute('data-status'), 'delayed', 'declared quote delay cannot be presented as live');
        assert.equal(await page.evaluate(() => comparisonStatus({ ...view.instruments['FOCIT.BO'],
          last_tick_time: '2026-10-05T11:03:00+05:30' }, view)), 'stale', 'a quote ahead of the delayed source clock is not fresh');
        await page.evaluate(() => { window.__now += 90001; });
        await page.waitForFunction(() => document.querySelector('#series-labels [data-symbol="FOCIT.BO"]').dataset.status === 'stale');
        assert.match(await legend.textContent(), /FOCIT.*stale/);
        assert.ok((await captureChart()).series[4].every(line => line.alpha === .15), 'an interrupted delayed feed becomes stale');
      } finally {
        await page.evaluate(now => { window.__now = now; }, originalNow);
        await emit(snapshot(TODAY, 8));
      }
    });
    await check('Shoonya displays the complete FOCIT Yahoo fallback with provenance after hours and during replay', async () => {
      try {
        await holdSourceRequest(() => page.locator('#source-select').selectOption('shoonya'));
        const fixture = comparisonSnapshot(TODAY, 25), focit = fixture.instruments['FOCIT.BO'];
        Object.assign(focit, { history_fallback: true, history_source_id: 'yahoo', quote_source_id: 'yahoo',
          history_delay_seconds: 900, quote_delay_seconds: 900 });
        await emit(fixture);
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        assert.equal(await page.locator('#source-select').inputValue(), 'shoonya');
        assert.equal(await page.locator('#session-status').textContent(), 'Market closed');
        const legend = page.locator('#series-labels [data-symbol="FOCIT.BO"]');
        assert.equal(await legend.getAttribute('data-status'), 'closed');
        assert.match(await legend.textContent(), /FOCIT.*\+0\.60%.*H/);
        assert.doesNotMatch(await legend.textContent(), /stale|unavailable/);
        assert.match(await legend.getAttribute('title'), /History: Yahoo \(15m delayed\).*After-hours price: Yahoo/);
        for (const interval of [15, 5]) {
          await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
          const points = await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']));
          assert.equal(points.length, interval === 15 ? 25 : 75, 'the fallback contains each real selected close');
          await revealComparisons();
          const chart = await captureChart();
          assert.equal(chart.series[4].flatMap(line => line.points).length, points.length + 1, 'the FOCIT fallback line includes its session-open anchor at both intervals');
          assert.ok(chart.series[4].every(line => line.alpha === .32), 'closed fallback history can be revealed at both intervals');
        }
        await page.locator('#replay').click();
        await page.evaluate(() => {
          replay.time = Date.parse(`${selectedDate}T10:30:00+05:30`); replay.complete = true;
          replay.frame = replayView(replay.time); scheduleRender();
          return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        assert.match(await legend.textContent(), /H/);
        assert.match(await legend.getAttribute('title'), /History: Yahoo.*Replay price: Yahoo/);
        assert.doesNotMatch(await legend.getAttribute('title'), /After-hours price/);
        assert.equal(await page.evaluate(() => displayView().instruments['FOCIT.BO'].history_fallback), true);
        await page.locator('#replay').click();
        await page.waitForFunction(() => document.querySelector('#series-labels [data-symbol="FOCIT.BO"]').title.includes('After-hours price: Yahoo'));
        assert.match(await legend.getAttribute('title'), /After-hours price: Yahoo/);
        await emit({ type: 'state', date: TODAY, instruments: { 'FOCIT.BO': { quote_source_id: 'shoonya' } } });
        assert.match(await legend.getAttribute('title'), /Live quotes: Shoonya/, 'source-only metadata changes invalidate the legend cache');
        await emit({ type: 'state', date: TODAY, instruments: { 'FOCIT.BO': { history_delay_seconds: 0 } } });
        assert.doesNotMatch(await legend.getAttribute('title'), /15m delayed/, 'history-delay-only changes invalidate the legend cache');
        await emit({ type: 'state', date: TODAY, instruments: { 'FOCIT.BO': { history_fallback: false } } });
        assert.doesNotMatch(await legend.textContent(), /\bH\b/, 'fallback-only changes invalidate the legend cache');
        await page.locator('#source-select').selectOption('yahoo');
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo');
        await emit(comparisonSnapshot(TODAY, 25));
        assert.doesNotMatch(await legend.textContent(), /\bH\b/, 'switching sources clears the previous fallback provenance');
        assert.doesNotMatch(await legend.getAttribute('title'), /Live quotes: Shoonya/);
      } finally {
        releaseSourceRequest();
        if (await page.evaluate(() => Boolean(replay))) await page.locator('#replay').click();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        if (await page.locator('#source-select').inputValue() !== 'yahoo') {
          await page.locator('#source-select').selectOption('yahoo');
          await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo');
        }
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
        await emit(snapshot(TODAY, 8));
      }
    });
    await check('fresh Shoonya quotes remain isolated beyond delayed FOCIT history until a valid selected bar exists', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:17:00+05:30'); });
        selectedSource = 'shoonya'; selectedGeneration += 1;
        const fixture = comparisonSnapshot(TODAY, 7), focit = fixture.instruments['FOCIT.BO'];
        Object.assign(focit, { history_fallback: true, history_source_id: 'yahoo', quote_source_id: 'shoonya',
          history_delay_seconds: 900, quote_delay_seconds: 0, comparison_status: 'live',
          last_tick_time: `${TODAY}T11:17:00+05:30`, current_price: 30090 });
        const historyBefore = JSON.stringify([focit.candles, focit.candles_5m]);
        await emit(fixture);
        const legend = page.locator('#series-labels [data-symbol="FOCIT.BO"]');
        assert.match(await legend.textContent(), /FOCIT.*\+0\.30%.*H/);
        assert.match(await legend.getAttribute('title'), /History: Yahoo \(15m delayed\).*Live quotes: Shoonya/);
        assert.doesNotMatch(await legend.textContent(), /delayed/, 'history delay does not mark the native quote delayed');
        for (const interval of [15, 5]) {
          await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
          const points = await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']));
          assert.equal(points.length, (interval === 15 ? 7 : 21) + 1);
          assert.deepEqual(points.at(-1), { time: Date.parse(focit.last_tick_time), quoteTime: Date.parse(focit.last_tick_time),
            price: 30090, gap: true, quoteOnly: true });
          const chart = await captureChart(), command = chart.series[4].flatMap(line => line.commands).at(-1);
          assert.equal(command.method, 'moveTo', 'the native quote never bridges the missing history');
          assert.ok(Math.abs(command.point[0] - (12 + (chart.width - 88) * 122 / 375)) < .01, 'the separate quote is placed at its observed timestamp');
        }
        const activeFive = { ...candle(TODAY, 24, 30080, 30090, 5), symbol: focit.symbol, is_closed: false };
        const incompleteFifteen = { ...candle(TODAY, 8, 30070, 30090), symbol: focit.symbol, is_closed: false, is_complete: false };
        await emit({ type: 'state', date: TODAY, instruments: { 'FOCIT.BO': {
          active_candle_5m: activeFive, active_candle: incompleteFifteen
        } } });
        assert.equal(await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']).some(point => point.quoteOnly)), false, 'a valid active 5m bar already represents the latest quote');
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
        assert.equal(await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']).at(-1).barStart), Date.parse(activeFive.start_time), 'an incomplete 15m parent uses its valid live 5m child');
        const guards = await page.evaluate(() => {
          const instrument = view.instruments['FOCIT.BO'];
          const quotes = change => comparisonPoints({ ...instrument, ...change }).filter(point => point.quoteOnly);
          return { stale: quotes({ last_tick_time: `${instrument.date}T11:15:00+05:30` }),
            yahoo: quotes({ quote_source_id: 'yahoo' }), cutoff: comparisonPoints(instrument, Date.parse(`${instrument.date}T11:16:00+05:30`)).filter(point => point.quoteOnly),
            validActive: quotes({ active_candle: { ...instrument.active_candle, is_complete: true } }),
            futureHistory: quotes({ candles: [...instrument.candles, { ...instrument.active_candle, is_closed: true, is_complete: true }], active_candle: null }) };
        });
        for (const [key, points] of Object.entries(guards)) assert.deepEqual(points, [], `${key} cannot add a separate native quote`);
        assert.equal(await page.evaluate(() => JSON.stringify([view.instruments['FOCIT.BO'].candles, view.instruments['FOCIT.BO'].candles_5m])), historyBefore, 'quote display never fabricates candle history');
        await page.evaluate(() => { window.__now += 60000; });
        await emit({ type: 'state', date: TODAY, instruments: { 'FOCIT.BO': {
          last_tick_time: `${TODAY}T11:18:00+05:30`, current_price: 30120,
          active_candle_5m: { ...activeFive, close: 30120 }
        } } });
        const latest = await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']).at(-1));
        assert.equal(latest.quoteTime, Date.parse(`${TODAY}T11:18:00+05:30`));
        assert.equal(latest.time, Date.parse(`${TODAY}T11:18:00+05:30`), 'the usable live child uses its actual quote timestamp');
        assert.equal(latest.price, 30120); assert.equal(latest.gap, true);
      } finally {
        await page.evaluate(now => { window.__now = now; setIntervalMinutes(15); transition = null; }, originalNow);
        selectedSource = 'yahoo'; selectedGeneration += 1;
        await emit(snapshot(TODAY, 8));
      }
    });
    await check('partial FOCIT OHLC retains explicitly valid observed closes and live updates', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:27:00+05:30'); setIntervalMinutes(15); transition = null; });
        selectedSource = 'shoonya'; selectedGeneration += 1;
        const fixture = comparisonSnapshot(TODAY, 8), focit = fixture.instruments['FOCIT.BO'];
        const child = (slot, close, extra = {}) => ({ ...candle(TODAY, slot, close - 10, close, 5), symbol: focit.symbol, ...extra });
        focit.candles_5m.push(child(24, 30100), child(25, 30120, { is_complete: false, close_is_valid: true }));
        Object.assign(focit, { history_fallback: true, history_source_id: 'yahoo', quote_source_id: 'shoonya',
          quote_delay_seconds: 0, history_delay_seconds: 900, comparison_status: 'live',
          current_price: 30150, last_tick_time: `${TODAY}T11:27:00+05:30`,
          active_candle_5m: child(26, 30150, { is_closed: false, is_complete: false, close_is_valid: true }),
          active_candle: { ...candle(TODAY, 8, 30100, 30150), symbol: focit.symbol, is_closed: false, is_complete: false } });
        const historyBefore = JSON.stringify([focit.candles, focit.candles_5m]);
        await emit(fixture);
        await revealComparisons();
        const points = await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']));
        assert.deepEqual(points.slice(-3).map(point => point.price), [30100, 30120, 30150]);
        assert.ok(points.slice(-3).every(point => !point.gap && !point.quoteOnly), 'observed contiguous children stay connected');
        assert.equal(points.at(-1).quoteTime, Date.parse(focit.last_tick_time));
        const chart = await captureChart();
        assert.equal(chart.series[4].flatMap(line => line.commands).at(-1).method, 'lineTo', 'the live native child extends the line instead of becoming a separated dot');
        await page.evaluate(() => { setIntervalMinutes(5); transition = null; });
        const fivePoints = await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']));
        assert.deepEqual(fivePoints.slice(-3).map(point => point.price), [30100, 30120, 30150]);
        assert.ok(fivePoints.slice(-3).every(point => !point.gap && !point.quoteOnly), 'partial OHLC cannot hide valid closes in the 5m chart either');
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
        await page.evaluate(() => { window.__now += 60000; });
        await emit({ type: 'quote', date: TODAY, instruments: { 'FOCIT.BO': {
          current_price: 30180, last_tick_time: `${TODAY}T11:28:00+05:30`,
          active_candle_5m: { ...focit.active_candle_5m, close: 30180 },
          active_candle: { ...focit.active_candle, close: 30180 }
        } } });
        const updated = await page.evaluate(() => comparisonPoints(view.instruments['FOCIT.BO']).at(-1));
        assert.equal(updated.price, 30180); assert.equal(updated.quoteTime, Date.parse(`${TODAY}T11:28:00+05:30`));
        assert.match(await page.locator('#series-labels [data-symbol="FOCIT.BO"]').textContent(), /FOCIT.*\+0\.60%.*H/);
        assert.equal(await page.evaluate(() => JSON.stringify([view.instruments['FOCIT.BO'].candles, view.instruments['FOCIT.BO'].candles_5m])), historyBefore);
        const guarded = await page.evaluate(() => {
          const instrument = view.instruments['FOCIT.BO'];
          const five = instrument.candles_5m.map(candle => candle.slot === 25 ? { ...candle, close_is_valid: false } : candle);
          return { gap: comparisonPoints({ ...instrument, candles_5m: five }).at(-1),
            cutoff: comparisonPoints(instrument, Date.parse(`${instrument.date}T11:24:00+05:30`)),
            noChildren: comparisonPoints({ ...instrument, candles_5m: [], active_candle_5m: null }).at(-1) };
        });
        assert.equal(guarded.gap.gap, true, 'an invalid child still breaks the live line');
        assert.ok(guarded.cutoff.every(point => !point.quoteTime || point.quoteTime <= Date.parse(`${TODAY}T11:24:00+05:30`)), 'finer sampling respects the cutoff');
        assert.equal(guarded.noChildren.quoteOnly, true, 'without usable children the native quote remains isolated');
      } finally {
        await page.evaluate(now => { window.__now = now; setIntervalMinutes(15); transition = null; }, originalNow);
        selectedSource = 'yahoo'; selectedGeneration += 1;
        await emit(snapshot(TODAY, 8));
      }
    });
    await check('a live-only comparison shows an isolated actual quote without inventing candle history', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:17:00+05:30'); });
        const fixture = comparisonSnapshot(TODAY, 8), focit = fixture.instruments['FOCIT.BO'];
        Object.assign(focit, { candles: [], candles_5m: [], active_candle: null, active_candle_5m: null,
          comparison_status: 'live', last_tick_time: `${TODAY}T11:17:00+05:30`, current_price: 30090 });
        const primaryBefore = JSON.stringify(fixture.candles);
        await emit(fixture);
        const chart = await captureChart(), commands = chart.series[4].flatMap(line => line.commands);
        assert.equal(commands.length, 2, 'the session-open anchor and isolated observed quote are the only points');
        assert.ok(commands.every(command => command.method === 'moveTo'), 'no line connects the opening to an unobserved interval');
        assert.ok(Math.abs(commands.at(-1).point[0] - (12 + (chart.width - 88) * 122 / 375)) < .01, 'the quote uses its actual 11:17 timestamp');
        assert.match(await page.locator('#series-labels [data-symbol="FOCIT.BO"]').textContent(), /FOCIT.*\+0\.30/);
        const actual = await page.evaluate(() => ({ points: comparisonPoints(view.instruments['FOCIT.BO']),
          candles: view.instruments['FOCIT.BO'].candles, five: view.instruments['FOCIT.BO'].candles_5m,
          primary: JSON.stringify(view.candles), cutoff: comparisonPoints(view.instruments['FOCIT.BO'], Date.parse('2026-10-05T11:00:00+05:30')) }));
        assert.equal(actual.points.at(-1).price, 30090); assert.equal(actual.points.at(-1).quoteOnly, true);
        assert.deepEqual(actual.candles, []); assert.deepEqual(actual.five, []);
        assert.equal(actual.primary, primaryBefore, 'comparison fallback cannot change primary bars');
        assert.equal(actual.cutoff.length, 0, 'a replay cutoff never exposes the current quote or an invented opening close');
        const bounds = await page.locator('#chart').boundingBox();
        await page.mouse.move(bounds.x + commands.at(-1).point[0], bounds.y + 40);
        assert.match(await page.locator('#series-labels [data-symbol="FOCIT.BO"]').getAttribute('title'), /as of 11:17/);
        assert.equal(await page.locator('#chart-inspector').count(), 0, 'a separate live quote needs no floating card');
        await page.mouse.move(0, 0);
        await page.locator('#replay').click();
        const replayChart = await captureChart();
        assert.equal(replayChart.series[4].flatMap(line => line.points).length, 0, 'quote-only data remains hidden throughout replay');
        await page.locator('#replay').click();
        await emit(fixture);
        const guarded = await page.evaluate(() => {
          const instrument = view.instruments['FOCIT.BO'];
          const points = change => comparisonPoints({ ...instrument, ...change }).filter(point => point.quoteOnly);
          const invalid = { ...view.candles[0], symbol: instrument.symbol, close_is_valid: false };
          return { stale: points({ last_tick_time: `${instrument.date}T11:15:00+05:30` }),
            noOpen: points({ session_open: null }), noPrice: points({ current_price: null }),
            invalidTime: points({ last_tick_time: 'invalid' }), incomplete: points({ candles: [invalid] }),
            finerHistory: points({ candles_5m: [{ ...view.candles_5m[0], symbol: instrument.symbol }] }),
            validHistory: points({ candles: [{ ...invalid, close_is_valid: true }] }) };
        });
        for (const key of ['stale', 'noOpen', 'noPrice', 'invalidTime', 'validHistory']) assert.deepEqual(guarded[key], [], `${key} cannot invent a quote-only endpoint`);
        assert.equal(guarded.incomplete.length, 1, 'an invalid historical candle cannot suppress a separate valid current quote');
        assert.equal(guarded.finerHistory.length, 1, 'a completed 5m bar cannot hide the current quote in the 15m chart');
      } finally {
        if (await page.evaluate(() => Boolean(replay))) await page.locator('#replay').click();
        await page.evaluate(now => { window.__now = now; }, originalNow);
        await emit(snapshot(TODAY, 8));
      }
    });
    await check('stale and missing comparison baselines are handled independently for each series', async () => {
      const fixture = comparisonSnapshot();
      const [bank, midcap, next, fin] = COMPARISONS.map(([symbol]) => fixture.instruments[symbol]);
      bank.comparison_status = 'stale'; bank.current_price = 55000;
      midcap.session_open = null;
      next.session_open = 0;
      await emit(fixture);
      const legend = await page.locator('#series-labels').textContent();
      assert.match(legend, /BANKNIFTY.*stale/i);
      assert.equal(await page.locator('#series-labels [data-symbol="NIFTY_MID_SELECT.NS"], #series-labels [data-symbol="^NSMIDCP"]').count(), 0, 'unplottable baselines have no misplaced endpoint label');
      assert.match(legend, /FIN NIFTY.*0\.19/);
      await revealComparisons();
      const chart = await captureChart();
      assert.equal(chart.series[0].flatMap(line => line.points).length, 9, 'stale quotes keep their anchor and native selected history');
      assert.ok(chart.series[0].every(line => line.alpha < chart.series[3][0].alpha), 'stale history is dimmed independently');
      assert.equal(chart.series[1].length, 0);
      assert.equal(chart.series[2].length, 0);
      assert.ok(chart.series[3].length > 0, 'healthy comparison stays visible');
      const stalePrimary = comparisonSnapshot(); stalePrimary.instruments['^NSEI'].comparison_status = 'stale';
      await emit(stalePrimary);
      assert.match(await page.locator('#series-labels [data-symbol="^NSEI"]').textContent(), /stale/i);
      assert.match(await page.locator('#series-labels').textContent(), /FIN NIFTY.*0\.19/, 'primary freshness does not hide fresh comparison returns');
      const noPrimaryOpen = comparisonSnapshot();
      noPrimaryOpen.instruments['^NSEI'].session_open = null;
      await emit(noPrimaryOpen);
      assert.equal((await captureChart()).allComparisons.length, 0, 'primary candles cannot invent an unavailable session-open baseline');
      const delayedPrimary = comparisonSnapshot(TODAY, 8, {}, { candles: [], candles_5m: [], active_candle: candle(TODAY, 1, 25010, 25020) });
      delayedPrimary.instruments['^NSEI'].session_open = null;
      await emit(delayedPrimary);
      assert.equal((await captureChart()).allComparisons.length, 0, 'a later forming candle cannot invent the primary session open');
      await emit(snapshot(TODAY, 8));
      assert.equal(await page.locator('#series-labels button.series-endpoint').count(), 0, 'legacy snapshots have no comparison endpoint controls');
      assert.equal((await captureChart()).allComparisons.length, 0, 'legacy snapshots still render without comparison data');
    });
    await check('invalid and missing selected bars leave gaps only in the affected comparison', async () => {
      for (const interval of [15, 5]) {
        await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
        const fixture = comparisonSnapshot(), key = interval === 15 ? 'candles' : 'candles_5m';
        const bank = fixture.instruments['^NSEBANK'];
        bank[key][2].close_is_valid = false;
        if (interval === 15) bank.candles_5m.slice(6, 9).forEach(child => { child.close_is_valid = false; });
        bank[key] = bank[key].filter((_, index) => index !== 5);
        await emit(fixture);
        const chart = await captureChart(), commands = chart.comparison.flatMap(line => line.commands);
        const xAt = slot => 12 + (chart.width - 88) * (slot + 1) * interval / 375;
        for (const slot of [3, 6]) assert.equal(commands.find(command => Math.abs(command.point[0] - xAt(slot)) < .01)?.method, 'moveTo', 'path restarts at the next valid selected close timestamp');
        for (const slot of [2, 5]) {
          assert.ok(!commands.some(command => Math.abs(command.point[0] - xAt(slot)) < .01), 'invalid or missing selected close is not fabricated');
          for (const lines of chart.series.slice(1)) assert.ok(lines.flatMap(line => line.points).some(([x]) => Math.abs(x - xAt(slot)) < .01), 'other comparison history remains complete');
        }
      }
      await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
    });
    await check('missing or incomplete closed primary candles have a compact data-gap explanation in both intervals', async () => {
      const fixture = snapshot(TODAY, 12);
      fixture.candles_5m = fixture.candles_5m.filter((_, index) => index !== 30 && index !== 31);
      fixture.candles[10].is_complete = false;
      fixture.candles[10].close_is_valid = false;
      for (const interval of [15, 5]) {
        await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
        await emit(fixture);
        assert.match(await page.locator('#feed-status').textContent(), /Data gap 11:45–11:55/);
        assert.match(await page.locator('#feed-status').getAttribute('title'), /2 missing or incomplete 5m NIFTY candles/);
        assert.equal(await page.evaluate(() => view.candles_5m.some(c => /T11:(45|50):/.test(c.start_time))), false, 'the notice does not manufacture missing candles');
      }
      const invalid = snapshot(TODAY, 8);
      invalid.candles_5m[12].close_is_valid = false;
      await emit(invalid);
      assert.match(await page.locator('#feed-status').textContent(), /Data gap 10:15–10:20/);
      await emit(snapshot(TODAY, 8));
      assert.equal(await page.locator('#feed-status').isHidden(), true, 'complete coverage removes the notice without flagging the rest of the unfilled trading day');
      await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
    });
    await check('data-gap notices follow the selected history and cannot reveal unreached replay gaps', async () => {
      const fixture = snapshot(PREVIOUS, 12);
      fixture.candles_5m = fixture.candles_5m.filter((_, index) => index !== 30 && index !== 31);
      await emit(fixture);
      await page.locator('#session-date').fill(PREVIOUS);
      await page.locator('#session-date').dispatchEvent('change');
      await waitDate(PREVIOUS);
      assert.match(await page.locator('#feed-status').textContent(), /Data gap 11:45–11:55/);
      await page.locator('#replay').click();
      const reached = async clock => page.evaluate(clock => {
        replay.time = Date.parse(`${selectedDate}T${clock}+05:30`);
        replay.complete = true; replay.frame = replayView(replay.time); render();
      }, clock);
      await reached('11:40:00');
      assert.equal(await page.locator('#feed-status').isHidden(), true);
      await reached('12:00:00');
      assert.match(await page.locator('#feed-status').textContent(), /Data gap 11:45–11:55/);
      await page.locator('#replay').click();
      await page.evaluate(() => {
        const five = view.candles_5m.slice(0, 10);
        view = { ...view, candles: view.candles.slice(0, 4).map((bar, index) => index === 3 ? { ...bar, is_complete: false } : bar),
          candles_5m: five, session_open: five[0].open, current_price: five.at(-1).close };
        render();
      });
      assert.equal(Date.parse(await page.locator('#series-labels [data-primary="true"]').getAttribute('data-asof')),
        Date.parse(`${PREVIOUS}T10:05:00+05:30`), 'a partial 15m parent cannot date the primary price later than its last actual 5m close');
      await emit(snapshot(TODAY, 8));
      await page.locator('#today').click();
      await waitDate(TODAY);
      assert.equal(await page.locator('#feed-status').isHidden(), true, 'another date cannot carry an older session gap warning');
    });
    await check('replay exposes reached closes for all comparisons and excludes final quotes from the scale', async () => {
      const fixture = comparisonSnapshot(TODAY, 25);
      for (const [symbol, , open] of COMPARISONS) {
        fixture.instruments[symbol].current_price = open * 1.1;
        fixture.instruments[symbol].last_tick_time = `${TODAY}T15:29:00+05:30`;
        for (const key of ['candles', 'candles_5m']) fixture.instruments[symbol][key].at(-1).close = open * 1.1;
      }
      const cutoff = Date.parse(`${TODAY}T09:40:00+05:30`);
      for (const interval of [15, 5]) {
        await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
        await emit(fixture); await page.locator('#replay').click();
        await page.waitForFunction(() => document.querySelector('#replay').getAttribute('aria-pressed') === 'true');
        await page.evaluate(cutoff => {
          replay.time = cutoff; replay.complete = true; replay.frame = replayView(cutoff); scheduleRender();
          return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }, cutoff);
        const chart = await captureChart(), expected = interval === 15 ? 1 : 5;
        const closeX = 12 + (chart.width - 88) * expected * interval / 375;
        for (const lines of chart.series) {
          const points = lines.flatMap(line => line.points);
          assert.equal(points.length, expected + 1, 'replay displays the session-open anchor and only reached closes');
          assert.ok(Math.abs(points.at(-1)[0] - closeX) < .01, 'reached closes appear at their actual close timestamps');
        }
        const guarded = await page.evaluate(({ instrument, cutoff }) => comparisonPoints(instrument, cutoff).map(point => point.quoteTime), { instrument: fixture.instruments['^NSEBANK'], cutoff });
        assert.equal(guarded.length, expected, 'a future close is excluded until its actual close timestamp');
        assert.ok(guarded.every(time => time <= cutoff));
        const legend = await page.locator('#series-labels').textContent();
        for (const [symbol] of COMPARISONS) assert.match(await page.locator(`#series-labels [data-symbol="${symbol}"]`).textContent(),
          interval === 15 ? /\+0\.02%/ : /\+0\.04%/, 'replay labels describe the last plotted close at the selected interval');
        assert.doesNotMatch(legend, /10\.00/);
        if (interval === 15) {
          const legacy = await page.evaluate(() => {
            replay.snapshot.instruments['^NSEBANK'].candles_5m = [];
            replay.frame = replayView(replay.time);
            return replay.frame.instruments['^NSEBANK'];
          });
          assert.equal(legacy.current_price, fixture.instruments['^NSEBANK'].candles[0].close, '15-minute-only replay retains its latest reached quote');
          assert.equal(legacy.comparison_status, 'closed');
        }
        await page.evaluate(() => {
          for (const item of Object.values(replay.snapshot.instruments)) if (item.symbol !== '^NSEI') {
            item.current_price *= 10;
            for (const key of ['candles', 'candles_5m']) if (item[key].length) item[key].at(-1).close *= 10;
          }
          replay.frame = replayView(replay.time);
        });
        assert.deepEqual((await captureChart()).bodies, chart.bodies, 'unreached extreme closes cannot expand replay scale');
        await page.locator('#replay').click();
        await page.waitForFunction(() => document.querySelector('#replay').getAttribute('aria-pressed') === 'false');
      }
      await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
    });
    await check('replay eases reached comparison closes without revealing later samples or extrema', async () => {
      await page.evaluate(() => { setIntervalMinutes(5); transition = null; });
      await emit(comparisonSnapshot(TODAY, 25)); await captureChart();
      const result = await page.evaluate(() => {
        startReplay(); replay.complete = true;
        replay.time = Date.parse(`${selectedDate}T09:19:59+05:30`); replay.frame = replayView(replay.time);
        drawChart(); chartMotion.finish();
        const opening = window.__captureChart({ animate: true });
        replay.time = Date.parse(`${selectedDate}T09:20:00+05:30`); replay.frame = replayView(replay.time);
        const firstTime = performance.now();
        const firstReveal = [0, 90, 1000].map(offset => window.__captureChart({ animate: true, now: firstTime + offset }));
        chartMotion.finish();
        const before = Date.parse(`${selectedDate}T09:24:59+05:30`);
        const reached = Date.parse(`${selectedDate}T09:25:00+05:30`);
        replay.time = before; replay.frame = replayView(before);
        drawChart(); chartMotion.finish();
        const initial = window.__captureChart({ animate: true });
        replay.time = reached; replay.frame = replayView(reached);
        const canonical = JSON.stringify(replay.frame), now = performance.now();
        const first = window.__captureChart({ animate: true, now });
        pointer = { x: 12 + (canvas.clientWidth - 88) * 7.5 / 375, y: 40 };
        const middle = window.__captureChart({ animate: true, now: now + 90 });
        const labels = [...document.querySelectorAll('#series-labels button.series-endpoint')].map(item => ({ text: item.textContent, title: item.title }));
        pointer = null;
        const settled = window.__captureChart({ animate: true, now: now + 1000 });
        const unchanged = JSON.stringify(replay.frame) === canonical;
        const bar = { symbol: '^NSEI', open: 100, high: 120, low: 90, close: 110, volume: 100,
          start_time: `${selectedDate}T09:15:00+05:30`, end_time: `${selectedDate}T09:20:00+05:30`, is_closed: true };
        const start = Date.parse(bar.start_time), end = Date.parse(bar.end_time);
        const partial = seconds => partialCandle(bar, start + seconds * 1000, end);
        const path = { before: partial(-1), opening: partial(0), early: partial(75), low: partial(100), halfway: partial(150), high: partial(200), final: partial(300),
          intoTurn: Math.abs(partial(100).close - partial(99).close),
          outOfTurn: Math.abs(partial(101).close - partial(100).close),
          intoMiddle: Math.abs(partial(50).close - partial(49).close),
          outOfMiddle: Math.abs(partial(150).close - partial(149).close) };
        chartMotion.finish(); stopReplay();
        return { opening, firstReveal, initial, first, middle, settled, unchanged, path, labels };
      });
      const endX = (chart, index) => chart.series[index].flatMap(line => line.points).at(-1)[0];
      const cutoffX = 12 + (result.settled.width - 88) * 10 / 375;
      for (let index = 0; index < COMPARISONS.length; index++) {
        const [start, middle, settled] = result.firstReveal.map(chart => endX(chart, index));
        assert.equal(result.opening.series[index].length, 0, 'there is no comparison price before the first close is reached');
        assert.ok(start <= middle && middle <= settled, 'the first reached close eases from the explicit session-open anchor');
        assert.ok(Math.abs(settled - (12 + (result.settled.width - 88) * 5 / 375)) < .01, 'the first sample settles at 09:20');
        assert.ok(result.firstReveal.every(chart => chart.series[index].flatMap(line => line.points).every(([x]) => x <= 12 + (chart.width - 88) * 5 / 375 + .01)), 'the first reveal never passes its actual close time');
        assert.ok(Math.abs(endX(result.first, index) - endX(result.initial, index)) < .01, 'a reached close starts at the preceding displayed sample');
        assert.ok(endX(result.first, index) < endX(result.middle, index) && endX(result.middle, index) < endX(result.settled, index), 'the newly reached close has intermediate horizontal positions');
        assert.ok(Math.abs(endX(result.settled, index) - cutoffX) < .01);
        for (const frame of [result.initial, result.first, result.middle, result.settled]) {
          assert.ok(frame.series[index].flatMap(line => line.points).every(([x]) => x <= cutoffX + .01), 'motion never displays a future comparison vertex');
          assert.equal(frame.series[index].flatMap(line => line.commands).filter(command => command.method === 'moveTo').length, 1);
        }
      }
      assert.ok(result.labels.every(item => /as of 09:25/.test(item.title)), 'endpoint metadata identifies the actual reached quote maturity during display easing');
      assert.equal(await page.locator('#chart-inspector, #comparison-detail').count(), 0, 'replay has no floating inspection card');
      assert.equal(result.unchanged, true, 'replay display motion does not change the replay samples');
      assert.equal(result.path.before, null); assert.equal(result.path.opening.close, 100);
      assert.ok(result.path.early.close > 90 && result.path.early.close < 100);
      assert.equal(result.path.early.high, 100, 'an unreached high is absent from the partial candle');
      assert.equal(result.path.low.close, 90); assert.equal(result.path.high.close, 120);
      assert.deepEqual([result.path.halfway.open, result.path.halfway.high, result.path.halfway.low, result.path.halfway.close], [100, 105, 90, 105], 'halfway OHLC contains only the illustrative path reached so far');
      assert.equal(result.path.halfway.is_closed, false);
      assert.equal(result.path.final.close, 110); assert.equal(result.path.final.is_closed, true);
      assert.ok(result.path.intoTurn < result.path.intoMiddle / 10 && result.path.outOfTurn < result.path.outOfMiddle / 10, 'illustrative candle motion eases into and out of its extrema');
      await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
    });
    await check('FIN NIFTY plots the same closing samples as its range, without a false opening-price peak', async () => {
      const fixture = comparisonSnapshot(TODAY, 5), fin = fixture.instruments['NIFTY_FIN_SERVICE.NS'];
      const closes = [25240.05, 25282.35, 25204.65, 25156.65, 25171.25, 25218.05, 25202.60, 25233.75, 25260.00, 25308.35];
      fin.session_open = 25385.60;
      fin.candles_5m = closes.map((close, slot) => ({ ...candle(TODAY, slot, slot ? closes[slot - 1] : fin.session_open, close, 5), symbol: fin.symbol }));
      fin.candles = Array.from({ length: 3 }, (_, slot) => {
        const children = fin.candles_5m.slice(slot * 3, slot * 3 + 3);
        return { ...candle(TODAY, slot, children[0].open, children.at(-1).close), symbol: fin.symbol,
          high: Math.max(...children.map(bar => bar.high)), low: Math.min(...children.map(bar => bar.low)) };
      });
      fin.current_price = closes.at(-1); fin.last_tick_time = `${TODAY}T10:05:00+05:30`;
      fin.breakout = neutralBreakout({ range_locked: true, range_high: 25282.35, range_low: 25156.65,
        direction: 'BULLISH', confirmed_at: fin.last_tick_time, price: fin.current_price });
      try {
        await page.evaluate(() => { setIntervalMinutes(5); transition = null; });
        await emit(fixture);
        const points = await page.evaluate(() => comparisonPoints(view.instruments['NIFTY_FIN_SERVICE.NS']));
        assert.deepEqual(points.map(point => point.price), closes, 'only observed five-minute closes belong to the line');
        assert.equal(Math.max(...points.slice(0, 9).map(point => point.price)), fin.breakout.range_high);
        assert.ok(points.at(-1).price > fin.breakout.range_high && points.at(-1).price < fin.session_open);
        const label = page.locator('#series-labels [data-symbol="NIFTY_FIN_SERVICE.NS"]');
        assert.equal(await label.getAttribute('data-direction'), 'BULLISH');
        assert.match(await label.getAttribute('title'), /Closing range 25156\.65–25282\.35.*Last confirmed close 25308\.35/);
      } finally {
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
        await emit(comparisonSnapshot());
      }
    });
    await check('endpoint labels leave their own closing-price line visible near the session edge', async () => {
      const fixture = comparisonSnapshot(TODAY, 25), focit = fixture.instruments['FOCIT.BO'];
      for (const bars of [focit.candles, focit.candles_5m]) {
        for (const bar of bars.slice(-12)) bar.close = 30300;
      }
      focit.current_price = 30300;
      focit.breakout = neutralBreakout({ range_locked: true, range_high: 30100, range_low: 29900,
        direction: 'BULLISH', confirmed_at: focit.last_tick_time, price: focit.current_price });
      try {
        await emit(fixture); await page.mouse.move(0, 0);
        for (const [width, height] of [[1440, 900], [390, 844]]) {
          await page.setViewportSize({ width, height });
          for (const interval of [15, 5]) {
            await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
            const chart = await captureChart();
            const canvas = await page.locator('#chart').boundingBox();
            const label = await page.locator('#series-labels [data-symbol="FOCIT.BO"]').boundingBox();
            const tip = chart.series[4].flatMap(line => line.points).at(-1);
            assert.ok(label.x + label.width < canvas.x + tip[0], 'the closing-session label sits before its endpoint');
            const top = label.y - canvas.y - 2, bottom = top + label.height + 4;
            assert.ok(bottom < tip[1] - 1 || top > tip[1] + 1,
              `${width}px ${interval}m: the label background must not erase its flat terminal stroke`);
            assert.equal(chart.series[4].flatMap(line => line.commands).filter(command => command.method === 'moveTo').length, 1,
              'label placement does not manufacture missing-price gaps');
          }
        }
      } finally {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
        await emit(comparisonSnapshot());
      }
    });
    await check('fresh live and delayed quotes cannot revive an expired or future comparison confirmation', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        for (const delay of [0, 900]) {
          const fixture = breakoutSnapshot(10), bank = fixture.instruments['^NSEBANK'];
          bank.comparison_status = delay ? 'delayed' : 'live'; bank.quote_delay_seconds = delay;
          await page.evaluate(delay => { window.__now = Date.parse(`${today}T10:05:00+05:30`) + delay * 1000; }, delay);
          await emit(fixture);
          const label = page.locator('#series-labels [data-symbol="^NSEBANK"]');
          assert.equal(await label.getAttribute('data-direction'), 'BULLISH');
          await page.evaluate(delay => { window.__now = Date.parse(`${today}T10:10:00+05:30`) + delay * 1000; }, delay);
          await emit({ type: 'state', date: TODAY, instruments: { '^NSEBANK': {
            last_tick_time: `${TODAY}T10:10:00+05:30`, current_price: 50030 } } });
          assert.equal(await label.getAttribute('data-status'), delay ? 'delayed' : 'live');
          assert.equal(await label.getAttribute('data-direction'), 'none', 'a fresh quote after a missing five-minute close does not keep the old arrow');
          assert.match(await label.getAttribute('title'), /last recorded bullish close 10:05.*no current 5m confirmation/,
            'an expired confirmation is not described as a close back inside the range');
          await emit({ type: 'state', date: TODAY, instruments: { '^NSEBANK': {
            breakout: { ...bank.breakout, confirmed_at: `${TODAY}T10:15:00+05:30` } } } });
          assert.equal(await label.getAttribute('data-direction'), 'none', 'a confirmation from after the source clock cannot qualify');
          await emit({ type: 'state', date: TODAY, instruments: { '^NSEBANK': {
            breakout: { ...bank.breakout, confirmed_at: `${TODAY}T10:10:00+05:30` } } } });
          assert.equal(await label.getAttribute('data-direction'), 'BULLISH', 'a new current confirmation restores emphasis');
          await page.evaluate(() => { window.__now += 120000; });
          await captureChart();
          assert.equal(await label.getAttribute('data-status'), 'stale');
          assert.equal(await label.getAttribute('data-direction'), 'none');
          assert.match(await label.getAttribute('title'), /last recorded bullish close 10:10.*current confirmation unavailable/,
            'stale quotes keep the recorded breakout distinguishable from a current signal');
        }
      } finally {
        await page.evaluate(now => { window.__now = now; }, originalNow);
        await emit(comparisonSnapshot());
      }
    });
    await check('blurred comparisons stay visible until NIFTY confirms, including source changes and replay', async () => {
      try {
        for (const source_id of ['yahoo', 'shoonya']) {
          await page.mouse.move(0, 0);
          const fixture = comparisonSnapshot(TODAY, 5, {}, { source_id });
          await emit(fixture);
          const before = await captureChart();
          assert.equal(await page.locator('#series-labels').getAttribute('data-overview'), 'true');
          assert.ok(before.series.every(lines => lines.every(line => line.alpha === .32 && /blur/.test(line.filter))), `${source_id}: neutral comparisons show the same soft blur as a NIFTY hover`);
          assert.equal(await page.locator('#series-labels button.series-endpoint:visible').count(), COMPARISONS.length);
          await emit({ type: 'state', date: TODAY, source_id, symbol: '^NSEI',
            setups: { ...fixture.setups, '15': { ...fixture.setups['15'], signals: [niftySignal()] } } });
          assert.equal(await page.locator('#series-labels').getAttribute('data-overview'), 'true', 'one NIFTY setup cannot end the overview');
          const bank = breakoutSnapshot(10).instruments['^NSEBANK'];
          await emit({ type: 'state', date: TODAY, source_id, instruments: { [bank.symbol]: bank } });
          assert.equal(await page.locator('#series-labels').getAttribute('data-overview'), 'true', 'a comparison breakout does not end NIFTY\'s overview');
          assert.equal(await page.locator('#series-labels [data-symbol="^NSEBANK"]').getAttribute('data-emphasis'), 'qualifying');
          await emit({ type: 'tick', date: TODAY, source_id, symbol: '^NSEI', signals: [niftySignal()] });
          assert.equal(await page.locator('#series-labels').getAttribute('data-overview'), 'false');
          const neutral = page.locator('#series-labels [data-symbol="NIFTY_MID_SELECT.NS"]');
          assert.equal(await neutral.isHidden(), true, 'the confirmed dual-ORB signal restores the post-breakout idle view');
          await revealComparisons();
          assert.equal(await neutral.getAttribute('data-emphasis'), 'revealed');
          await page.mouse.move(0, 0);
          await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'false');
          assert.equal(await neutral.isHidden(), true);
          await emit({ type: 'state', date: TODAY, source_id, symbol: '^NSEI',
            setups: fixture.setups });
          assert.equal(await page.locator('#series-labels').getAttribute('data-overview'), 'false', 'a later return inside the range cannot undo the session\'s first signal');
          await emit({ ...fixture, signals: [niftySignal()] });
          await page.locator('#replay').click();
          for (const [clock, expected] of [['10:04:59', 'true'], ['10:05:00', 'false'], ['10:10:00', 'false']]) {
            await page.evaluate(clock => {
              replay.time = Date.parse(`${selectedDate}T${clock}+05:30`); replay.complete = true;
              replay.frame = replayView(replay.time); render(performance.now());
            }, clock);
            assert.equal(await page.locator('#series-labels').getAttribute('data-overview'), expected, `replay overview at ${clock}`);
          }
          await page.locator('#replay').click();
        }
      } finally {
        if (await page.locator('#replay').getAttribute('aria-pressed') === 'true') await page.locator('#replay').click();
        await page.mouse.move(0, 0);
        await emit(comparisonSnapshot());
      }
    });
    await check('closed comparison breakouts emphasize either direction and soften the other lines', async () => {
      const fixture = breakoutSnapshot(10), bank = fixture.instruments['^NSEBANK'];
      fixture.signals = [niftySignal()];
      const fin = fixture.instruments['NIFTY_FIN_SERVICE.NS'];
      const invert = bar => ({ ...bar, symbol: fin.symbol, open: 24000 - (bar.open - 50000) * .48,
        close: 24000 - (bar.close - 50000) * .48, high: 24048, low: 23952 });
      fin.candles = bank.candles.map(invert); fin.candles_5m = bank.candles_5m.map(invert);
      fin.current_price = fin.candles_5m.at(-1).close; fin.last_tick_time = bank.last_tick_time;
      fin.breakout = neutralBreakout({ range_locked: true, range_high: 24003.84, range_low: 23996.16,
        direction: 'BEARISH', confirmed_at: fin.last_tick_time, price: fin.current_price });
      await emit(fixture); await captureChart();
      const endpoint = symbol => page.locator(`#series-labels [data-symbol="${symbol}"]`);
      assert.equal(await page.locator('#series-labels').getAttribute('data-qualifying'), '2');
      assert.equal(await endpoint(bank.symbol).getAttribute('data-direction'), 'BULLISH');
      assert.equal(await endpoint(fin.symbol).getAttribute('data-direction'), 'BEARISH');
      for (const symbol of [bank.symbol, fin.symbol]) {
        assert.equal(await endpoint(symbol).getAttribute('data-emphasis'), 'qualifying');
        assert.match(await endpoint(symbol).getAttribute('aria-label'), /5m|5.minute|5 minute/i);
        assert.match(await endpoint(symbol).getAttribute('aria-label'), /45m|45.minute|45 minute/i);
      }
      assert.equal(await endpoint('NIFTY_MID_SELECT.NS').getAttribute('data-emphasis'), 'hidden');
      assert.equal(await endpoint('NIFTY_MID_SELECT.NS').isHidden(), true);
      const chart = await captureChart();
      assert.ok(chart.series[0][0].alpha > chart.series[1][0].alpha, 'a qualified line is more prominent than a neutral index');
      assert.ok(chart.series[0][0].width > chart.series[1][0].width, 'qualification changes the line weight');
      assert.equal(chart.series[1][0].alpha, 0, 'neutral paths are fully hidden without changing their geometry');
      assert.deepEqual(await page.evaluate(() => view.instruments['^NSEI'].candles), fixture.instruments['^NSEI'].candles, 'comparison highlighting leaves primary market data intact');
      await page.screenshot({ path: '/tmp/orb-breakout-qualified.png', fullPage: true });
    });
    await check('NIFTY hover and focus reveal neutral indices softly and leaving restores the idle view', async () => {
      const primary = page.locator('#series-labels [data-primary="true"]');
      const neutral = page.locator('#series-labels [data-symbol="NIFTY_MID_SELECT.NS"]');
      await page.mouse.move(0, 0);
      assert.equal(await neutral.isHidden(), true);
      await revealComparisons();
      assert.equal(await neutral.isVisible(), true);
      assert.equal(await neutral.getAttribute('data-emphasis'), 'revealed');
      const revealed = await captureChart();
      assert.equal(revealed.series[1][0].alpha, .32);
      assert.match(revealed.series[1][0].filter, /blur/);
      assert.equal(revealed.series[0][0].alpha, .95, 'breakout indices remain crisp during the NIFTY reveal');
      assert.equal(revealed.series[0][0].filter, 'none');
      await page.screenshot({ path: '/tmp/orb-primary-reveal.png', fullPage: true });
      await page.mouse.move(0, 0);
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'false');
      assert.equal(await neutral.isHidden(), true);
      await primary.focus();
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'true');
      assert.equal(await neutral.isVisible(), true, 'keyboard focus offers the same reveal');
      await page.locator('#comparison-toggle').focus();
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'false');
      await page.locator('.instrument').hover();
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'true');
      await page.mouse.move(0, 0);
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'false');
      const chart = await captureChart(), box = await page.locator('#chart').boundingBox(), candle = chart.bodies.at(-1);
      await page.mouse.move(box.x + candle.x + candle.w / 2, box.y + candle.y + candle.h / 2);
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'true');
      assert.equal(await neutral.isVisible(), true, 'hovering a NIFTY candle reveals neutral indices');
      await page.mouse.move(0, 0);
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'false');
    });
    await check('hover and keyboard focus isolate a qualified endpoint and restore the other indices', async () => {
      const endpoint = page.locator('#series-labels [data-symbol="^NSEBANK"]');
      await endpoint.hover();
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.focused === '^NSEBANK');
      assert.equal(await endpoint.getAttribute('data-emphasis'), 'focused');
      assert.equal(await page.locator('#series-labels [data-symbol="NIFTY_FIN_SERVICE.NS"]').getAttribute('data-emphasis'), 'muted');
      const isolated = await captureChart();
      assert.ok(isolated.series[3].every(line => line.alpha <= .06 && /blur\(3/.test(line.filter)), 'hover deeply softens even the other qualified index');
      await page.screenshot({ path: '/tmp/orb-breakout-focused.png', fullPage: true });
      await page.mouse.move(0, 0);
      await page.waitForFunction(() => !document.querySelector('#series-labels').dataset.focused);
      assert.equal(await endpoint.getAttribute('data-emphasis'), 'qualifying');
      const paths = await captureChart(), chartBox = await page.locator('#chart').boundingBox();
      const tip = paths.series[0][0].points.at(-1);
      await page.mouse.move(chartBox.x + tip[0], chartBox.y + tip[1]);
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.focused === '^NSEBANK');
      assert.equal(await endpoint.getAttribute('data-emphasis'), 'focused', 'hovering the qualified line itself also isolates it');
      await page.mouse.move(0, 0);
      await page.waitForFunction(() => !document.querySelector('#series-labels').dataset.focused);
      await endpoint.focus();
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.focused === '^NSEBANK');
      assert.equal(await endpoint.evaluate(element => element === document.activeElement), true, 'the endpoint is keyboard reachable');
      await page.evaluate(() => { window.__focusedEndpoint = document.activeElement; });
      await emit({ type: 'state', date: TODAY, instruments: { '^NSEBANK': { current_price: 50013 } } });
      assert.equal(await page.evaluate(() => document.activeElement === window.__focusedEndpoint), true, 'quote updates retain the focused endpoint node');
      assert.equal(await endpoint.getAttribute('data-emphasis'), 'focused');
      await page.locator('#comparison-toggle').focus();
      await page.waitForFunction(() => !document.querySelector('#series-labels').dataset.focused);
      assert.equal(await endpoint.getAttribute('data-emphasis'), 'qualifying');
      assert.equal(await page.locator('#series-labels [data-symbol="NIFTY_MID_SELECT.NS"]').isHidden(), true, 'neutral labels cannot intercept idle chart hover');
      await page.mouse.move(0, 0);
      await page.locator('#sectors-tab').click();
      assert.equal(await page.locator('#series-labels button.series-endpoint').count(), 0, 'leaving derivatives removes their interactive labels');
      await selectGroup('derivatives');
      await emit(comparisonSnapshot());
      assert.equal(await page.locator('#series-labels').getAttribute('data-qualifying'), '0');
      assert.ok((await captureChart()).series.every(lines => lines.every(line => line.alpha === .32 && /blur/.test(line.filter))), 'neutral comparisons return to the blurred overview before NIFTY confirms');
    });
    await check('ticks and forming comparison candles do not confirm a close-only breakout', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T10:04:59+05:30'); });
        const fixture = breakoutSnapshot(9), bank = fixture.instruments['^NSEBANK'];
        fixture.market_status = 'live';
        bank.comparison_status = 'live'; bank.current_price = 60000; bank.last_tick_time = `${TODAY}T10:04:59+05:30`;
        bank.active_candle_5m = { ...candle(TODAY, 9, 49992, 60000, 5), symbol: bank.symbol, is_closed: false };
        await emit(fixture);
        const endpoint = page.locator('#series-labels [data-symbol="^NSEBANK"]');
        assert.equal(await endpoint.getAttribute('data-emphasis'), 'revealed');
        await emit({ type: 'state', date: TODAY, instruments: { '^NSEBANK': { current_price: 61000,
          active_candle_5m: { ...bank.active_candle_5m, close: 61000 } } } });
        assert.equal(await endpoint.getAttribute('data-emphasis'), 'revealed', 'an out-of-range live price cannot make the blurred overview crisp');
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T10:05:00+05:30'); });
        const confirmed = breakoutSnapshot(10).instruments['^NSEBANK'];
        confirmed.comparison_status = 'live';
        await emit({ type: 'state', date: TODAY, instruments: { '^NSEBANK': confirmed } });
        assert.equal(await endpoint.getAttribute('data-direction'), 'BULLISH');
        assert.equal(await endpoint.getAttribute('data-emphasis'), 'qualifying');
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T10:06:31+05:30'); render(); });
        assert.equal(await endpoint.getAttribute('data-direction'), 'none', 'a stale quote cannot keep a confirmed index emphasized');
        assert.equal(await endpoint.getAttribute('data-status'), 'stale');
        const inside = breakoutSnapshot(11).instruments['^NSEBANK'];
        inside.comparison_status = 'live'; inside.last_tick_time = `${TODAY}T10:10:00+05:30`;
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T10:10:00+05:30'); });
        await emit({ type: 'state', date: TODAY, instruments: { '^NSEBANK': inside } });
        assert.equal(await endpoint.getAttribute('data-emphasis'), 'revealed', 'a later closed candle on the boundary returns to the blurred overview');
      } finally {
        await page.evaluate(now => { window.__now = now; }, originalNow);
        await emit(comparisonSnapshot());
      }
    });
    await check('comparison replay uses nine opening closes and only reached valid closed five-minute confirmations', async () => {
      const fixture = breakoutSnapshot(13);
      await emit(fixture); await page.locator('#replay').click();
      try {
        const reached = async clock => page.evaluate(clock => {
          replay.time = Date.parse(`${selectedDate}T${clock}+05:30`); replay.complete = true;
          replay.frame = replayView(replay.time); render(performance.now());
          return replay.frame.instruments['^NSEBANK'].breakout;
        }, clock);
        const before = await reached('09:59:59');
        assert.equal(before.range_locked, false); assert.equal(before.direction, null);
        const opening = await reached('10:00:00');
        assert.equal(opening.range_locked, true);
        assert.equal(opening.range_high, 50008); assert.equal(opening.range_low, 49992, 'opening wick extremes do not become breakout boundaries');
        assert.equal(opening.direction, null);
        assert.equal((await reached('10:04:59')).direction, null, 'the incomplete confirmation cannot qualify');
        const bullish = await reached('10:05:00');
        assert.equal(bullish.direction, 'BULLISH'); assert.equal(bullish.confirmed_at, `${TODAY}T10:05:00+05:30`);
        assert.equal(await page.locator('#series-labels [data-symbol="^NSEBANK"]').getAttribute('data-emphasis'), 'qualifying');
        assert.equal((await reached('10:10:00')).direction, null, 'an exact opening close boundary is neutral');
        assert.equal((await reached('10:15:00')).direction, 'BEARISH');
        assert.equal((await reached('10:20:00')).direction, null, 'a return inside the opening closes clears qualification');
        await page.evaluate(() => { replay.snapshot.instruments['^NSEBANK'].candles_5m[9].close_is_valid = false; });
        assert.equal((await reached('10:05:00')).direction, null, 'invalid historical confirmation closes never qualify');
        await page.evaluate(() => { replay.snapshot.instruments['^NSEBANK'].candles_5m[9].close_is_valid = true; replay.snapshot.instruments['^NSEBANK'].candles_5m[4].is_complete = false; });
        const missingOpening = await reached('10:05:00');
        assert.equal(missingOpening.range_locked, false); assert.equal(missingOpening.direction, null, 'incomplete opening coverage never invents a range');
      } finally { await page.locator('#replay').click(); await emit(comparisonSnapshot()); }
    });
    await check('comparison tabs switch exclusively and toggle off to NIFTY only without changing market data', async () => {
      const fixture = sectorSnapshot();
      await emit(fixture); await page.mouse.move(0, 0);
      const tabs = page.locator('#comparison-tabs [role="tab"]');
      assert.equal(await page.locator('#comparison-tabs').getAttribute('role'), 'tablist');
      assert.deepEqual(await tabs.allTextContents(), ['Derivatives', 'NIFTY Sectors']);
      assert.deepEqual(await tabs.evaluateAll(items => items.map(item => item.getAttribute('aria-selected'))), ['true', 'false']);
      assert.deepEqual(await page.locator('#series-labels button.series-endpoint').evaluateAll(items => items.map(item => item.dataset.symbol)), COMPARISONS.map(([symbol]) => symbol));
      const requests = [];
      const record = request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(request.url()); };
      page.on('request', record);
      try {
        const data = await page.evaluate(() => JSON.stringify(view));
        await page.locator('#sectors-tab').click();
        const sectors = await captureChart();
        assert.deepEqual(await tabs.evaluateAll(items => items.map(item => item.getAttribute('aria-selected'))), ['false', 'true']);
        assert.equal(sectors.series.length, SECTORS.length);
        assert.ok(sectors.series.every(lines => lines.length > 0), 'all 15 sectors have observed closing-price paths');
        const labels = await page.locator('#series-labels button.series-endpoint').evaluateAll(items => items.map(item => ({ symbol: item.dataset.symbol, text: item.textContent })));
        assert.deepEqual(labels.map(item => item.symbol), SECTORS.map(([symbol]) => symbol), 'the requested sector order excludes Bank and IT');
        for (const [index, label] of labels.entries()) {
          assert.ok(label.text.includes(SECTORS[index][1]));
          assert.doesNotMatch(label.text, /NIFTY/i, 'the sector tab already supplies the NIFTY context');
        }
        const first = await captureChart();
        await page.locator('#sectors-tab').click();
        const hidden = await captureChart();
        assert.deepEqual(await tabs.evaluateAll(items => items.map(item => item.getAttribute('aria-selected'))), ['false', 'false']);
        assert.equal(hidden.allComparisons.length, 0);
        assert.equal(await page.locator('#series-labels button.series-endpoint').count(), 0);
        assert.equal(await page.locator('#series-labels [data-primary="true"]').count(), 1);
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /NIFTY only/);
        assert.equal(await page.locator('#sectors-tab').getAttribute('tabindex'), '0', 'the last selected tab stays keyboard reachable');
        await page.locator('#sectors-tab').click();
        assert.deepEqual((await captureChart()).series, first.series, 'clicking again restores the same sector paths');
        await selectGroup('derivatives');
        await captureChart();
        assert.equal(await page.locator('#series-labels button.series-endpoint').count(), COMPARISONS.length);
        await page.locator('#comparison-toggle').click();
        assert.equal((await captureChart()).allComparisons.length, 0, 'Derivatives can also be toggled off');
        assert.deepEqual(await tabs.evaluateAll(items => items.map(item => item.getAttribute('aria-selected'))), ['false', 'false']);
        await page.locator('#comparison-toggle').press('Home');
        await captureChart();
        assert.equal(await page.locator('#comparison-toggle').getAttribute('aria-selected'), 'true', 'Home selects from NIFTY-only mode');
        await page.locator('#comparison-toggle').press('Home');
        assert.equal(await page.locator('#comparison-toggle').getAttribute('aria-selected'), 'true', 'Home on the selected group does not toggle it off');
        assert.equal(await page.evaluate(() => JSON.stringify(view)), data, 'group selection preserves every instrument and strategy');
        assert.deepEqual(requests, [], 'view selection neither reloads history nor restarts the provider');
      } finally { page.off('request', record); await selectGroup('derivatives'); }
    });
    await check('comparison charts rank actual session-open returns in both groups and intervals', async () => {
      const fixture = sectorSnapshot();
      for (const [index, instrument] of Object.values(fixture.instruments).filter(item => item.symbol !== '^NSEI').entries()) {
        const firstReturn = instrument.symbol === '^CNXPSUBANK' ? .03 : instrument.symbol === '^CNXAUTO' ? -.01 : index % 2 ? -.01 : .03;
        const finalReturn = instrument.symbol === '^CNXPSUBANK' ? .0152 : instrument.symbol === '^CNXAUTO' ? .0004 : .002;
        for (const key of ['candles', 'candles_5m']) for (const [slot, bar] of instrument[key].entries()) {
          bar.close = instrument.session_open * (1 + (slot === instrument[key].length - 1 ? finalReturn : firstReturn));
          bar.high = Math.max(bar.high, bar.close); bar.low = Math.min(bar.low, bar.close);
        }
        instrument.current_price = instrument.session_open * (1 + finalReturn);
        instrument.change_pct = finalReturn * 100;
      }
      await emit(fixture);
      const canonical = await page.evaluate(() => JSON.stringify(view));
      try {
        for (const group of ['derivatives', 'sectors']) {
          await selectGroup(group);
          for (const interval of [15, 5]) {
            await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
            const series = await page.evaluate(() => comparisonSeries(view).map(item => ({ symbol: item.instrument.symbol,
              origin: item.origin, reference: item.reference, points: item.points })));
            const chart = await captureChart();
            for (const item of series) {
              const original = fixture.instruments[item.symbol];
              const final = item.points.at(-1), anchor = item.points[0];
              assert.equal(anchor.sessionOpen, true);
              assert.equal(anchor.time, Date.parse(`${TODAY}T09:15:00+05:30`));
              assert.equal(anchor.normalized, item.origin);
              assert.equal(item.origin, fixture.instruments['^NSEI'].session_open);
              assert.equal(item.points[1].time, Date.parse(original[interval === 15 ? 'candles' : 'candles_5m'][0].end_time));
              const painted = chart.series[series.indexOf(item)][0].points[0];
              assert.equal(painted[0], 12, '09:15 anchors share the chart’s left edge');
              assert.ok(Math.abs(painted[1] - chart.series[0][0].points[0][1]) < .01, 'all paths share the same zero-return height');
              assert.equal(item.reference, original.session_open, 'each line uses its 09:15 session open, never its first observed close');
              assert.ok(Math.abs((final.normalized / item.origin - 1) * 100 - original.change_pct) < 1e-8,
                'the plotted endpoint and percentage label use the same return');
              const label = await page.locator(`#series-labels [data-symbol="${item.symbol}"]`).textContent();
              assert.ok(label.includes(`${original.change_pct >= 0 ? '+' : ''}${original.change_pct.toFixed(2)}%`));
            }
            if (group === 'sectors') {
              const psu = SECTORS.findIndex(([symbol]) => symbol === '^CNXPSUBANK');
              const auto = SECTORS.findIndex(([symbol]) => symbol === '^CNXAUTO');
              assert.ok(chart.series[psu][0].points.at(-1)[1] < chart.series[auto][0].points.at(-1)[1],
                'PSU BANK +1.52% plots above AUTO +0.04% despite their unequal first closes');
            }
          }
        }
        assert.equal(await page.evaluate(() => JSON.stringify(view)), canonical, 'chart normalization never mutates prices or strategies');
      } finally {
        await selectGroup('derivatives');
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
        await emit(comparisonSnapshot());
      }
    });
    await check('comparison origins preserve missing opening slots and use stable references for lone live quotes', async () => {
      await emit(comparisonSnapshot());
      const result = await page.evaluate(() => {
        const bank = view.instruments['^NSEBANK'];
        const withBank = instrument => ({ ...view, instruments: { '^NSEI': view.instruments['^NSEI'], '^NSEBANK': instrument } });
        const late = { ...bank, candles: bank.candles.slice(2), candles_5m: bank.candles_5m.slice(6) };
        const missing = comparisonSeries(withBank(late))[0];
        const noRange = { ...withBank(bank), setups: {} };
        const beforeRange = comparisonSeries(noRange)[0];
        const stamp = `${selectedDate}T11:17:00+05:30`;
        const quote = { ...bank, candles: [], candles_5m: [], comparison_status: 'live', last_tick_time: stamp, current_price: 50100 };
        const originalNow = window.__now;
        window.__now = Date.parse(stamp);
        const first = comparisonSeries(withBank(quote))[0];
        const next = comparisonSeries(withBank({ ...quote, current_price: 50200 }))[0];
        const forming = { ...quote, active_candle: { ...view.candles.at(-1), symbol: bank.symbol,
          start_time: `${selectedDate}T11:15:00+05:30`, end_time: `${selectedDate}T11:30:00+05:30`,
          open: 50000, high: 50150, low: 49990, close: 50100, is_closed: false } };
        const partial = comparisonSeries(withBank(forming))[0];
        window.__now = originalNow;
        return { missing, beforeRange, first, next, partial, sessionOpen: bank.session_open, primaryOpen: view.instruments['^NSEI'].session_open };
      });
      assert.equal(result.missing.points.length, 7);
      assert.equal(result.missing.points[0].sessionOpen, true);
      assert.equal(result.missing.points[1].gap, true, 'missing opening history is not connected to the session-open anchor');
      assert.match(new Date(result.missing.points[1].barStart).toISOString(), /04:15:00/); // 09:45 IST
      assert.equal(result.missing.reference, result.sessionOpen, 'late history retains the real session-open baseline without filling earlier slots');
      assert.equal(result.beforeRange.origin, result.primaryOpen);
      assert.equal(result.first.points.length, 2); assert.equal(result.first.points[1].quoteOnly, true);
      assert.equal(result.first.points[1].gap, true, 'a quote without opening history stays disconnected from the anchor');
      assert.equal(result.first.reference, result.sessionOpen); assert.equal(result.next.reference, result.sessionOpen);
      assert.ok(result.next.points.at(-1).normalized > result.first.points.at(-1).normalized, 'a lone live quote moves rather than rebasing itself to a fixed dot');
      assert.equal(result.partial.reference, result.sessionOpen, 'a forming first bar cannot change its own reference on every tick');
      await emit(comparisonSnapshot());
    });
    await check('completing the first comparison close keeps its session-open baseline and scale', async () => {
      await emit(comparisonSnapshot());
      const result = await page.evaluate(() => {
        const bank = view.instruments['^NSEBANK'];
        const stamp = `${selectedDate}T09:22:00+05:30`;
        const originalNow = window.__now;
        window.__now = Date.parse(stamp);
        const bar = { ...bank.candles[0], close: 65000, high: 65000, is_closed: false };
        const instrument = { ...bank, candles: [bar], candles_5m: [], active_candle: null,
          current_price: 65000, last_tick_time: stamp, comparison_status: 'live' };
        const data = item => ({ ...view, instruments: { '^NSEI': view.instruments['^NSEI'], '^NSEBANK': item } });
        chartMotion.reset(); drawChart(performance.now(), data(instrument));
        const before = { ...chartMotion.domain };
        window.__now = Date.parse(bar.end_time);
        const closed = { ...instrument, candles: [{ ...bar, is_closed: true }], last_tick_time: bar.end_time };
        drawChart(performance.now(), data(closed));
        const after = { ...chartMotion.domain };
        chartMotion.reset(); drawChart(performance.now(), data(closed));
        const fresh = { ...chartMotion.domain };
        window.__now = originalNow;
        chartMotion.reset();
        return { before, after, fresh };
      });
      assert.deepEqual(result.after, result.before, 'closing an unchanged first price cannot rebase it or collapse its actual opening displacement');
      assert.deepEqual(result.after, result.fresh, 'the retained scale matches the same actual session-open returns in a fresh chart');
      await captureChart();
    });
    await check('replay uses only reached closes and interval changes preserve the session-open origin', async () => {
      await emit(comparisonSnapshot());
      const result = await page.evaluate(() => {
        const cutoff = Date.parse(`${selectedDate}T09:30:00+05:30`);
        const reached = comparisonSeries({ ...view, replay_time: cutoff })[0];
        const beforeFirst = comparisonSeries({ ...view, replay_time: cutoff - 1 })[0];
        const futureChanged = structuredClone(view);
        futureChanged.instruments['^NSEBANK'].candles[1].close = 500000;
        const withoutFuture = comparisonSeries({ ...futureChanged, replay_time: cutoff })[0];
        setIntervalMinutes(5);
        const duringMorph = comparisonSeries(view)[0];
        const opening = view.instruments['^NSEI'].session_open, reference = view.instruments['^NSEBANK'].session_open;
        transition = null;
        setIntervalMinutes(15); transition = null;
        return { reached, beforeFirst, withoutFuture, duringMorph, opening, reference };
      });
      assert.equal(result.reached.points.length, 2);
      assert.equal(result.reached.points[0].sessionOpen, true);
      assert.equal(result.reached.reference, result.reference);
      assert.equal(result.reached.points[1].time, Date.parse(`${TODAY}T09:30:00+05:30`));
      assert.equal(result.beforeFirst.points.length, 0, 'a future first close cannot anchor unreached replay');
      assert.deepEqual(result.withoutFuture.points, result.reached.points, 'future prices cannot affect the reached visual origin');
      assert.equal(result.duringMorph.origin, result.opening, 'changing the ORB interval cannot move zero return');
      assert.equal(result.duringMorph.reference, result.reference);
      assert.equal(result.duringMorph.points[0].normalized, result.opening);
      await captureChart();
    });
    await check('sector tabs support arrow and boundary keys without changing date navigation', async () => {
      const date = await page.locator('#session-date').inputValue();
      const derivatives = page.locator('#comparison-toggle'), sectors = page.locator('#sectors-tab');
      await derivatives.focus(); await derivatives.press('ArrowRight');
      assert.equal(await sectors.getAttribute('aria-selected'), 'true');
      assert.equal(await sectors.evaluate(element => element === document.activeElement), true);
      assert.equal(await sectors.getAttribute('tabindex'), '0');
      assert.equal(await derivatives.getAttribute('tabindex'), '-1');
      await sectors.press('ArrowLeft');
      assert.equal(await derivatives.getAttribute('aria-selected'), 'true');
      await derivatives.press('End');
      assert.equal(await sectors.getAttribute('aria-selected'), 'true');
      await sectors.press('Home');
      assert.equal(await derivatives.getAttribute('aria-selected'), 'true');
      assert.equal(await page.locator('#session-date').inputValue(), date, 'tab arrow keys never navigate the shared chart date');
    });
    await check('group switches preserve historical replay and receive inactive-group updates in the background', async () => {
      const fixture = sectorSnapshot(PREVIOUS, 25);
      await emit(fixture);
      await page.locator('#session-date').fill(PREVIOUS); await page.locator('#session-date').dispatchEvent('change'); await waitDate(PREVIOUS);
      await page.locator('#interval-5m').click();
      await page.locator('#replay').click();
      await page.evaluate(() => {
        replay.time = Date.parse(`${selectedDate}T10:20:00+05:30`); replay.complete = true;
        replay.frame = replayView(replay.time); render();
      });
      const before = await page.evaluate(() => ({ date: selectedDate, source: selectedSource, interval: selectedInterval,
        replayTime: replay.time, replayData: JSON.stringify(replay.frame), snapshot: JSON.stringify(replay.snapshot) }));
      try {
        await page.locator('#sectors-tab').click();
        assert.deepEqual(await page.evaluate(() => ({ date: selectedDate, source: selectedSource, interval: selectedInterval,
          replayTime: replay.time, replayData: JSON.stringify(replay.frame), snapshot: JSON.stringify(replay.snapshot) })), before);
        assert.equal(await page.locator('#replay').getAttribute('aria-pressed'), 'true');
        const chart = await captureChart();
        assert.ok(chart.series.every(lines => lines.flatMap(line => line.points).length === 14), 'sector replay only exposes its session-open anchor and 13 reached five-minute closes');
        await selectGroup('derivatives');
        const liveFixture = sectorSnapshot(TODAY, 25);
        const pharma = confirmSector(liveFixture.instruments['^CNXPHARMA']);
        await emit(liveFixture);
        assert.equal(await page.locator('#session-date').inputValue(), PREVIOUS);
        assert.equal(await page.locator('#replay').getAttribute('aria-pressed'), 'true', 'a live full-registry snapshot leaves historical replay intact');
        await page.locator('#replay').click();
        await page.locator('#today').click(); await waitDate(TODAY);
        assert.equal(await page.locator('#series-labels [data-symbol="^CNXPHARMA"]').count(), 0);
        await emit({ type: 'state', date: TODAY, instruments: { '^CNXPHARMA': { ...pharma, current_price: pharma.current_price + 1 } } });
        assert.equal(await page.locator('#series-labels [data-symbol="^CNXPHARMA"]').count(), 0, 'background prices do not leak inactive labels');
        await page.locator('#sectors-tab').click();
        assert.equal(await page.evaluate(() => view.instruments['^CNXPHARMA'].current_price), pharma.current_price + 1);
        assert.equal(await page.locator('#series-labels [data-symbol="^CNXPHARMA"]').getAttribute('data-direction'), 'BULLISH');
        assert.equal(await page.locator('#interval-5m').getAttribute('aria-pressed'), 'true');
      } finally {
        if (await page.locator('#replay').getAttribute('aria-pressed') === 'true') await page.locator('#replay').click();
        await selectGroup('derivatives');
        await emit(comparisonSnapshot()); await page.locator('#today').click();
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
      }
    });
    await check('inactive-group extremes never stretch either source and switching clears old isolation targets', async () => {
      try {
        for (const source_id of ['yahoo', 'shoonya']) {
          const fixture = sectorSnapshot(TODAY, 8, { source_id });
          const bank = fixture.instruments['^NSEBANK'];
          bank.breakout = neutralBreakout({ range_locked: true, direction: 'BULLISH', range_high: 50020, range_low: 49980,
            confirmed_at: bank.last_tick_time, price: bank.current_price });
          const pharma = confirmSector(fixture.instruments['^CNXPHARMA']);
          const extreme = confirmSector(fixture.instruments['^CNXAUTO'], 'BULLISH', .5);
          await emit({ ...fixture, instruments: Object.fromEntries(Object.entries(fixture.instruments).filter(([, instrument]) => instrument.comparison_group !== 'sectors')) });
          await page.mouse.move(0, 0);
          const baseline = await captureChart();
          await emit(fixture);
          assert.deepEqual((await captureChart()).bodies, baseline.bodies, `${source_id}: inactive +50% Auto cannot shrink NIFTY candles`);
          await page.locator('#series-labels [data-symbol="^NSEBANK"]').focus();
          await page.waitForFunction(symbol => document.querySelector('#series-labels').dataset.focused === symbol, bank.symbol);
          assert.equal(await page.locator('#series-labels').getAttribute('data-focused'), bank.symbol);
          await page.locator('#sectors-tab').click();
          await captureChart();
          assert.equal(await page.locator('#series-labels').getAttribute('data-focused'), '');
          assert.equal(await page.locator('#series-labels [data-symbol="^NSEBANK"]').count(), 0);
          assert.equal(await page.locator('#series-labels [data-symbol="^CNXPHARMA"]').getAttribute('data-emphasis'), 'qualifying');
          assert.notDeepEqual((await captureChart()).bodies, baseline.bodies, 'selecting the extreme group fits its actual returns');
          await page.locator('#series-labels [data-symbol="^CNXPHARMA"]').hover();
          await page.waitForFunction(() => document.querySelector('#series-labels [data-symbol="^CNXAUTO"]')?.dataset.emphasis === 'muted');
          assert.equal(await page.locator('#series-labels [data-symbol="^CNXAUTO"]').getAttribute('data-emphasis'), 'muted');
          await selectGroup('derivatives');
          await page.mouse.move(0, 0);
          await captureChart();
          assert.equal(await page.locator('#series-labels').getAttribute('data-focused'), '');
          assert.equal(await page.locator('#series-labels [data-symbol="^NSEBANK"]').getAttribute('data-emphasis'), 'qualifying');
          assert.deepEqual((await captureChart()).bodies, baseline.bodies, 'leaving an extreme sector group resets its fitted scale');
        }
      } finally { await selectGroup('derivatives'); await emit(comparisonSnapshot()); }
    });
    await check('sectors share close-only emphasis, NIFTY overview, hover isolation and source provenance', async () => {
      const originalNow = await page.evaluate(() => window.__now);
      const fixture = sectorSnapshot(TODAY, 4, { market_status: 'live' });
      const pharma = fixture.instruments['^CNXPHARMA'], auto = fixture.instruments['^CNXAUTO'];
      pharma.comparison_status = 'live'; auto.comparison_status = 'live';
      pharma.last_tick_time = auto.last_tick_time = `${TODAY}T10:19:00+05:30`;
      pharma.history_fallback = true; pharma.history_source = 'yahoo'; pharma.quote_source_id = 'shoonya'; pharma.quote_delay_seconds = 0;
      auto.quote_delay_seconds = 900; auto.comparison_status = 'delayed'; auto.last_tick_time = `${TODAY}T10:04:00+05:30`;
      try {
        await page.evaluate(() => { window.__now = Date.parse(`${today}T10:19:00+05:30`); });
        await emit(fixture); await page.locator('#sectors-tab').click(); await page.mouse.move(0, 0);
        const label = page.locator('#series-labels [data-symbol="^CNXPHARMA"]');
        assert.equal(await label.getAttribute('data-emphasis'), 'revealed');
        assert.match(await label.textContent(), /H/); assert.doesNotMatch(await label.textContent(), /\sD(?:\s|$)/);
        assert.match(await page.locator('#series-labels [data-symbol="^CNXAUTO"]').textContent(), /\sD(?:\s|$)/);
        const quote = pharma.session_open * 1.01;
        await emit({ type: 'state', date: TODAY, instruments: { '^CNXPHARMA': { current_price: quote,
          active_candle_5m: { ...candle(TODAY, 12, pharma.current_price, quote, 5), symbol: pharma.symbol, is_closed: false } } } });
        assert.equal(await label.getAttribute('data-direction'), 'none', 'a sector live price above the closing range is not a completed-close confirmation');
        const confirmed = confirmSector({ ...pharma, candles: structuredClone(pharma.candles), candles_5m: structuredClone(pharma.candles_5m) });
        confirmed.breakout.confirmed_at = `${TODAY}T10:15:00+05:30`;
        await emit({ type: 'state', date: TODAY, instruments: { '^CNXPHARMA': confirmed } });
        assert.equal(await label.getAttribute('data-direction'), 'BULLISH');
        assert.equal(await label.getAttribute('data-emphasis'), 'qualifying');
        await emit({ type: 'tick', date: TODAY, symbol: '^NSEI', signals: [niftySignal()] });
        const neutral = page.locator('#series-labels [data-symbol="^CNXMETAL"]');
        assert.equal(await neutral.isHidden(), true);
        await revealComparisons();
        assert.equal(await neutral.getAttribute('data-emphasis'), 'revealed');
        assert.equal(await label.getAttribute('data-emphasis'), 'qualifying');
        await page.mouse.move(0, 0); await label.focus();
        await page.waitForFunction(symbol => document.querySelector(`#series-labels [data-symbol="${symbol}"]`)?.dataset.emphasis === 'focused', pharma.symbol);
        assert.equal(await label.getAttribute('data-emphasis'), 'focused');
        assert.equal(await neutral.getAttribute('data-emphasis'), 'muted');
        await page.locator('#sectors-tab').focus();
        await page.evaluate(() => { window.__now += 120000; }); await captureChart();
        assert.equal(await label.getAttribute('data-status'), 'stale');
        assert.equal(await label.getAttribute('data-direction'), 'none', 'a sector confirmation loses crisp emphasis when its quote expires');
      } finally {
        await page.evaluate(now => { window.__now = now; }, originalNow);
        await selectGroup('derivatives'); await page.mouse.move(0, 0); await emit(comparisonSnapshot());
      }
    });
    await check('sector replay recomputes all opening closes and cannot reveal future confirmations', async () => {
      const fixture = sectorSnapshot(TODAY, 25);
      const bank = breakoutSnapshot(13).instruments['^NSEBANK'];
      const pharma = fixture.instruments['^CNXPHARMA'];
      const ratio = pharma.session_open / bank.session_open;
      for (const key of ['candles', 'candles_5m']) pharma[key] = bank[key].map(bar => ({ ...bar, symbol: pharma.symbol,
        open: bar.open * ratio, high: bar.high * ratio, low: bar.low * ratio, close: bar.close * ratio }));
      pharma.current_price = pharma.candles_5m.at(-1).close; pharma.last_tick_time = pharma.candles_5m.at(-1).end_time;
      try {
        await emit(fixture); await page.locator('#sectors-tab').click(); await page.locator('#replay').click();
        for (const [clock, expected] of [['09:59:59', 'none'], ['10:04:59', 'none'], ['10:05:00', 'BULLISH'], ['10:10:00', 'none'], ['10:15:00', 'BEARISH'], ['10:20:00', 'none']]) {
          await page.evaluate(clock => { replay.time = Date.parse(`${selectedDate}T${clock}+05:30`); replay.complete = true;
            replay.frame = replayView(replay.time); render(); }, clock);
          assert.equal(await page.locator('#series-labels [data-symbol="^CNXPHARMA"]').getAttribute('data-direction'), expected, `sector replay at ${clock}`);
        }
        await page.evaluate(() => { replay.snapshot.instruments['^CNXPHARMA'].candles_5m[4].is_complete = false;
          replay.time = Date.parse(`${selectedDate}T10:05:00+05:30`); replay.frame = replayView(replay.time); render(); });
        assert.equal(await page.locator('#series-labels [data-symbol="^CNXPHARMA"]').getAttribute('data-direction'), 'none');
      } finally {
        if (await page.locator('#replay').getAttribute('aria-pressed') === 'true') await page.locator('#replay').click();
        await selectGroup('derivatives'); await emit(comparisonSnapshot());
      }
    });
    await check('all 15 sector endpoint labels remain readable on desktop and mobile in both intervals', async () => {
      const fixture = sectorSnapshot(TODAY, 25);
      for (const [index, [symbol]] of SECTORS.entries()) confirmSector(fixture.instruments[symbol], index % 2 ? 'BEARISH' : 'BULLISH', .004 + index * .00002);
      try {
        await emit(fixture); await page.locator('#sectors-tab').click(); await page.mouse.move(0, 0);
        for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]]) {
          await page.setViewportSize({ width, height });
          for (const interval of [15, 5]) {
            await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
            const chart = await captureChart(), box = await page.locator('#chart').boundingBox();
            const labels = await page.locator('#series-labels button.series-endpoint:visible').evaluateAll(items => items.map(item => {
              const box = item.getBoundingClientRect(), style = getComputedStyle(item);
              return { symbol: item.dataset.symbol, x: box.x, y: box.y, width: box.width, height: box.height, text: item.textContent,
                background: style.backgroundColor, scrollWidth: item.scrollWidth, clientWidth: item.clientWidth };
            }));
            assert.equal(labels.length, SECTORS.length, `${name} ${interval}m has 15 qualified labels`);
            for (const label of labels) {
              assert.ok(label.x >= box.x - .01 && label.x + label.width <= box.x + box.width + .01, `${label.symbol}: horizontal bounds`);
              assert.ok(label.y >= box.y - .01 && label.y + label.height <= box.y + box.height + .01, `${label.symbol}: vertical bounds`);
              assert.ok(label.scrollWidth <= label.clientWidth + 1, `${label.symbol}: full text fits`);
              assert.doesNotMatch(label.text, /NIFTY/i);
              assert.notEqual(label.background, 'rgba(0, 0, 0, 0)');
            }
            const ordered = labels.sort((a, b) => a.y - b.y);
            for (let index = 1; index < ordered.length; index++) assert.ok(ordered[index].y >= ordered[index - 1].y + ordered[index - 1].height - .01, 'dense sector labels have separate rows');
            assert.ok(chart.series.every(lines => lines.length > 0));
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1), 'tabs and chart fit without page overflow');
            await page.screenshot({ path: `/tmp/orb-sectors-${name}-${interval}m.png`, fullPage: true });
          }
        }
      } finally {
        await page.setViewportSize({ width: 1440, height: 900 });
        await selectGroup('derivatives');
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; }); await emit(comparisonSnapshot());
      }
    });
    await check('dark theme has readable text and survives reloading', async () => {
      await page.locator('#theme-toggle').click();
      const contrast = await page.evaluate(() => {
        const style = getComputedStyle(document.body);
        const channel = c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
        const luminance = color => {
          const parts = color.match(/\d+(?:\.\d+)?/g).slice(0, 3).map(c => channel(Number(c) / 255));
          return .2126 * parts[0] + .7152 * parts[1] + .0722 * parts[2];
        };
        const background = getComputedStyle(document.documentElement).backgroundColor;
        const values = [luminance(style.color), luminance(background)].sort((a, b) => b - a);
        return { ratio: (values[0] + .05) / (values[1] + .05), theme: document.documentElement.dataset.theme };
      });
      assert.equal(contrast.theme, 'dark');
      assert.ok(contrast.ratio >= 4.5, `body text contrast ${contrast.ratio.toFixed(1)}`);
      await page.reload();
      await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
      assert.equal(await page.locator('#theme-toggle').getAttribute('aria-pressed'), 'true');
      await emit(snapshot(TODAY, 25));
      await page.locator('#today').click();
    });
    const breakout = snapshot(TODAY).candles[12];
    const chartFixture = comparisonSnapshot(TODAY, 25, {}, { signals: [{ timestamp: breakout.end_time, direction: 'BULLISH', price: breakout.close }] });
    for (const [index, [symbol, , open]] of COMPARISONS.entries()) {
      const instrument = chartFixture.instruments[symbol];
      const movement = [.006, -.002, .0035, -.0045, .002][index];
      for (const bars of [instrument.candles, instrument.candles_5m]) for (const bar of bars) {
        const minute = (Date.parse(bar.end_time) - Date.parse(`${TODAY}T09:15:00+05:30`)) / 60000;
        bar.close = open * (1 + movement * minute / 375 + Math.sin(minute / 40 + index) * .00025);
      }
      instrument.current_price = instrument.candles_5m.at(-1).close;
      instrument.breakout = neutralBreakout({ range_locked: true, direction: movement > 0 ? 'BULLISH' : 'BEARISH',
        confirmed_at: instrument.last_tick_time, price: instrument.current_price });
    }
    chartFixture.setups['15'].signals = [{ timestamp: breakout.end_time, direction: 'BULLISH', price: breakout.close }];
    chartFixture.setups['5'].signals = [{ timestamp: chartFixture.candles_5m[40].end_time, direction: 'BULLISH', price: chartFixture.candles_5m[40].close }];
    await emit(chartFixture);
    await page.locator('#today').click(); await page.mouse.move(0, 0);
    for (const [name, width, height, ratio] of [['desktop', 1440, 900, .85], ['mobile', 390, 844, .7]]) {
      await check(`${name} layout gives most of the screen to the chart without overflow`, async () => {
        await page.setViewportSize({ width, height }); await page.waitForTimeout(100);
        const layout = await page.evaluate(() => ({ height: document.querySelector('#chart').getBoundingClientRect().height,
          width: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight, viewport: innerWidth }));
        console.log(`LAYOUT ${name}: chart ${layout.height}px (${Math.round(layout.height / height * 100)}%), overflow ${layout.width - layout.viewport}px horizontal / ${layout.scrollHeight - height}px vertical`);
        assert.ok(layout.height / height > ratio, `chart occupies ${Math.round(layout.height / height * 100)}%`);
        assert.ok(layout.width <= layout.viewport, `horizontal overflow ${layout.width - layout.viewport}px`);
        assert.ok(layout.scrollHeight <= height + 1, `vertical overflow ${layout.scrollHeight - height}px`);
      });
      for (const theme of ['light', 'dark']) {
        if (await page.evaluate(() => document.documentElement.dataset.theme) !== theme) {
          await page.locator('#theme-toggle').click();
          await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
        }
        for (const interval of ['15m', '5m']) {
          await page.locator(`#interval-${interval}`).click();
          await page.waitForTimeout(500);
          assert.ok((await captureChart()).series.every(lines => lines.length > 0), `${name} ${theme} ${interval} screenshot includes all comparisons`);
          await page.screenshot({ path: `/tmp/orb-${name}-${theme}-${interval}.png`, fullPage: true });
        }
      }
      await page.locator('#interval-15m').click();
      await page.waitForTimeout(500);
      await check(`${name} endpoint labels remain readable within the chart without hover cards`, async () => {
        const chart = await page.locator('#chart').boundingBox();
        await revealComparisons();
        const labels = await page.locator('#series-labels .series-endpoint').evaluateAll(items => items.map(item => {
          const box = item.getBoundingClientRect();
          const style = getComputedStyle(item);
          return { symbol: item.dataset.symbol, primary: item.dataset.primary === 'true', x: box.x, y: box.y, width: box.width, height: box.height,
            label: item.getAttribute('aria-label'), color: style.color, background: style.backgroundColor, halo: style.boxShadow };
        }));
        assert.equal(labels.length, COMPARISONS.length + 1, 'primary and comparison labels are present');
        for (const label of labels) {
          assert.ok(label.x >= chart.x - .01 && label.x + label.width <= chart.x + chart.width + .01, `${label.symbol} fits the chart width`);
          assert.ok(label.y >= chart.y - .01 && label.y + label.height <= chart.y + chart.height + .01, `${label.symbol} fits the chart height`);
          assert.ok(label.label && label.label.includes('%'), `${label.symbol} has an accessible return`);
          assert.notEqual(label.background, 'rgba(0, 0, 0, 0)', `${label.symbol} masks chart strokes beneath its text`);
          assert.notEqual(label.halo, 'none', `${label.symbol} has clearance around its text`);
        }
        const primary = labels.find(label => label.primary);
        assert.ok(primary.x >= chart.x + chart.width - 76, 'NIFTY text stays entirely in the price-axis margin');
        const ordered = labels.filter(label => !label.primary).sort((a, b) => a.y - b.y);
        for (let i = 1; i < ordered.length; i++) assert.ok(ordered[i].y >= ordered[i - 1].y + ordered[i - 1].height - .01, 'collision handling leaves each label a separate row');
        assert.equal(await page.locator('#chart-inspector, #comparison-detail, #primary-return, #comparison-return').count(), 0);
        await page.screenshot({ path: `/tmp/orb-${name}-endpoints.png`, fullPage: true });
        await page.mouse.move(0, 0);
      });
    }
    await check('expanded Shoonya guidance fits desktop and mobile without using real credentials or IP', async () => {
      selectedSource = 'shoonya'; selectedGeneration += 1;
      loginUrl = 'https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=fixture-client';
      ipFailure = false;
      await emit(snapshot(TODAY, 25, { feed_status: 'error', feed_error: 'Shoonya login failed; refresh SHOONYA_AUTH_CODE and check credentials/IP' }));
      for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]]) {
        await page.setViewportSize({ width, height });
        if (!await page.locator('#shoonya-help').evaluate(help => help.open)) await page.locator('#shoonya-help summary').click();
        await page.waitForFunction(() => !document.querySelector('#shoonya-login').hidden);
        await page.locator('#check-ip').click();
        await page.waitForFunction(() => document.querySelector('#public-ip').textContent.includes('203.0.113.8') && !document.querySelector('#check-ip').disabled);
        const bounds = await page.locator('#shoonya-help .feed-help-panel').boundingBox();
        assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1, `${name} help fits the viewport width`);
        assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= height + 1, `${name} help fits the viewport height`);
        assert.equal(await page.locator('#shoonya-client-id').textContent(), 'Client ID: fixture-client');
        await page.screenshot({ path: `/tmp/orb-source-help-${name}.png`, fullPage: true });
        if (name === 'desktop') await page.screenshot({ path: '/tmp/orb-source-help.png', fullPage: true });
      }
    });
    await check('no browser errors', async () => assert.deepEqual(browserErrors, []));
    if (failures.length) { console.error(`\n${failures.length} failed:\n${failures.join('\n')}`); process.exitCode = 1; }
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
