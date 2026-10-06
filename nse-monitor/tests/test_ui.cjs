// Run: node tests/test_ui.cjs (requires Playwright and Chrome).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const playwright = require(process.env.PLAYWRIGHT_PATH || 'playwright');

const TODAY = '2026-10-05', PREVIOUS = '2026-10-01', OLDER = '2026-09-30';
const staticDir = path.join(__dirname, '..', 'static');
const failures = [], browserErrors = [];
let delayedDate = '';
function candle(date, slot, open, close) {
  const stamp = minutes => `${date}T${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:00+05:30`;
  return { symbol: '^NSEI', open, close, high: Math.max(open, close) + 12, low: Math.min(open, close) - 10,
    volume: 100, start_time: stamp(555 + slot * 15), end_time: stamp(570 + slot * 15), slot, is_closed: true };
}
function snapshot(date, count = 25, extra = {}) {
  const candles = Array.from({ length: count }, (_, slot) => {
    const open = 25000 + Math.sin(slot * .7) * 34 + slot * 3;
    return candle(date, slot, open, open + (slot % 2 ? -19 : 22));
  });
  return { type: 'init', date, today: TODAY, candles, active_candle: null, signals: [], source: 'Deterministic fixture',
    current_price: candles.at(-1)?.close ?? null, orb_high: count ? 25064 : null, orb_low: count ? 24990 : null,
    range_locked: count >= 3, market_status: 'closed', ...extra };
}
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost');
  if (url.pathname.startsWith('/api/')) {
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
      const fillRect = CanvasRenderingContext2D.prototype.fillRect;
      CanvasRenderingContext2D.prototype.fillRect = function (x, y, w, h) {
        if (this.canvas.id === 'chart') window.__fills.push({ color: this.fillStyle, x, y, w, h });
        return fillRect.call(this, x, y, w, h);
      };
    });
    const page = await context.newPage();
    page.on('pageerror', error => browserErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && !message.text().includes('404')) browserErrors.push(message.text()); });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    const emit = data => page.evaluate(data => window.__emit(data), data);
    const waitDate = date => page.waitForFunction(date => document.querySelector('#session-date').value === date && document.querySelector('#chart').getAttribute('aria-label').includes(date), date);
    async function check(name, run) {
      try { await run(); console.log(`PASS ${name}`); }
      catch (error) { failures.push(`${name}: ${error.message.split('\n')[0]}`); console.error(`FAIL ${name}: ${error.message}`); }
    }
    await check('today remains visible with an empty closed market, independently of browser timezone', async () => {
      await page.waitForFunction(() => document.querySelector('#session-status').textContent === 'Market closed');
      assert.equal(await page.locator('#session-date').inputValue(), TODAY);
      assert.equal(await page.locator('#empty-state').isVisible(), true);
    });
    await check('wheel moves across closed dates and Today restores the live session', async () => {
      await page.locator('#chart').hover();
      await page.mouse.wheel(0, -100); await waitDate(PREVIOUS);
      await page.waitForTimeout(350);
      await page.mouse.wheel(0, 100); await waitDate(TODAY);
      await emit(snapshot(TODAY, 8));
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
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
    const breakout = snapshot(TODAY).candles[12];
    await emit(snapshot(TODAY, 25, { signals: [{ timestamp: breakout.end_time, direction: 'BULLISH', price: breakout.close }] }));
    await page.locator('#today').click(); await page.mouse.move(0, 0);
    for (const [name, width, height, ratio] of [['desktop', 1440, 900, .8], ['mobile', 390, 844, .6]]) {
      await check(`${name} layout gives most of the screen to the chart without overflow`, async () => {
        await page.setViewportSize({ width, height }); await page.waitForTimeout(100);
        const layout = await page.evaluate(() => ({ height: document.querySelector('#chart').getBoundingClientRect().height,
          width: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight, viewport: innerWidth }));
        assert.ok(layout.height / height > ratio, `chart occupies ${Math.round(layout.height / height * 100)}%`);
        assert.ok(layout.width <= layout.viewport, `horizontal overflow ${layout.width - layout.viewport}px`);
        assert.ok(layout.scrollHeight <= height + 1, `vertical overflow ${layout.scrollHeight - height}px`);
      });
      await page.screenshot({ path: `/tmp/nse-monitor-${name}.png`, fullPage: true });
    }
    await check('no browser errors', async () => assert.deepEqual(browserErrors, []));
    if (failures.length) { console.error(`\n${failures.length} failed:\n${failures.join('\n')}`); process.exitCode = 1; }
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
