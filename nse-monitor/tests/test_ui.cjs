// Run: node tests/test_ui.cjs (requires Playwright and Chrome).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const bundledPlaywright = path.join(require('node:os').homedir(), '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright');
const playwright = require(process.env.PLAYWRIGHT_PATH || (fs.existsSync(bundledPlaywright) ? bundledPlaywright : 'playwright'));

const TODAY = '2026-10-05', PREVIOUS = '2026-10-01', OLDER = '2026-09-30';
const staticDir = path.join(__dirname, '..', 'static');
const failures = [], browserErrors = [];
let delayedDate = '';
let selectedSource = 'yahoo';
let selectedGeneration = 0;
let sourceEpoch = 'fixture-process-a';
let rejectSource = false;
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
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(snapshot(TODAY, selectedSource === 'shoonya' ? 6 : 8)));
    });
    return;
  }
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
        if (this.canvas.id === 'chart') window.__fills.push({ color: this.fillStyle, x, y, w, h, alpha: this.globalAlpha });
        return fillRect.call(this, x, y, w, h);
      };
      window.__strokes = []; window.__markers = [];
      let points = [];
      for (const method of ['beginPath', 'moveTo', 'lineTo', 'stroke', 'fill']) {
        const native = CanvasRenderingContext2D.prototype[method];
        CanvasRenderingContext2D.prototype[method] = function (...args) {
          if (this.canvas.id === 'chart') {
            if (method === 'beginPath') points = [];
            if (method === 'moveTo' || method === 'lineTo') points.push(args);
            if (method === 'stroke') window.__strokes.push({ points: [...points], color: this.strokeStyle, dash: this.getLineDash(), alpha: this.globalAlpha });
            if (method === 'fill') window.__markers.push({ points: [...points], color: this.fillStyle, alpha: this.globalAlpha });
          }
          return native.apply(this, args);
        };
      }
    });
    const page = await context.newPage();
    page.on('pageerror', error => browserErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && !/404|409/.test(message.text())) browserErrors.push(message.text()); });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    const emit = data => page.evaluate(data => {
      window.__emit(data);
      return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, data);
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
    await check('the alternate range fades after switching and is revealed by hovering a range line', async () => {
      await page.mouse.move(0, 0);
      await page.evaluate(() => { rangeEchoUntil = performance.now() - 1; window.__strokes = []; drawChart(); });
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
    const chartFixture = snapshot(TODAY, 25, { signals: [{ timestamp: breakout.end_time, direction: 'BULLISH', price: breakout.close }] });
    chartFixture.setups['15'].signals = [{ timestamp: breakout.end_time, direction: 'BULLISH', price: breakout.close }];
    chartFixture.setups['5'].signals = [{ timestamp: chartFixture.candles_5m[40].end_time, direction: 'BULLISH', price: chartFixture.candles_5m[40].close }];
    await emit(chartFixture);
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
      for (const theme of ['light', 'dark']) {
        if (await page.evaluate(() => document.documentElement.dataset.theme) !== theme) {
          await page.locator('#theme-toggle').click();
          await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
        }
        for (const interval of ['15m', '5m']) {
          await page.locator(`#interval-${interval}`).click();
          await page.waitForTimeout(500);
          await page.screenshot({ path: `/tmp/nse-monitor-${name}-${theme}-${interval}.png`, fullPage: true });
        }
      }
      await page.locator('#interval-15m').click();
      await page.waitForTimeout(500);
    }
    await check('no browser errors', async () => assert.deepEqual(browserErrors, []));
    if (failures.length) { console.error(`\n${failures.length} failed:\n${failures.join('\n')}`); process.exitCode = 1; }
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
