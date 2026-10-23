const assert = require('node:assert/strict');
const { TODAY, PREVIOUS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'chart/candle-colors',
    name: 'rising and falling candle bodies use dark and light gray',
    async run({ page, emit, waitDate, snapshot }) {
      await emit(snapshot(TODAY, 8));
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      await page.evaluate(() => { window.__fills = []; });
      await page.locator('#today').click(); await waitDate(TODAY);
      const colors = await page.evaluate(() => [...new Set(window.__fills.filter(fill => fill.w <= 26).map(fill => fill.color))]);
      assert.ok(colors.includes('#484c51')); assert.ok(colors.includes('#c8cbce'));
    }
  },
  {
    id: 'chart/fixed-candle-width',
    name: 'candles keep a fixed full-session width while today fills in',
    async run({ page, emit, snapshot }) {
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
    }
  },
  {
    id: 'chart/interval-morph',
    name: 'interval changes morph the selected range and keep one-third candle widths on the same day axis',
    async run({ page, emit, snapshot }) {
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
    }
  },
  {
    id: 'chart/setup-arrows',
    name: 'arrows belong to each selected setup while the footer requires both setups',
    async run({ page, emit, snapshot }) {
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
    }
  },
  {
    id: 'chart/alternate-range-hover',
    name: 'the alternate range appears only while hovering a range line',
    async run({ page, emit, snapshot }) {
      await emit(snapshot(TODAY, 8));
      await page.mouse.move(0, 0);
      await page.evaluate(() => { window.__strokes = []; drawChart(); });
      assert.equal(await page.evaluate(() => window.__strokes.some(line => line.dash.join(',') === '1,5')), false);
      const line = await page.evaluate(() => window.__strokes.find(line => line.dash.join(',') === '5,4'));
      const box = await page.locator('#chart').boundingBox();
      await page.mouse.move(box.x + line.points[0][0] + 40, box.y + line.points[0][1]);
      await page.waitForFunction(() => window.__strokes.some(line => line.dash.join(',') === '1,5' && line.alpha === .5));
      await page.mouse.move(0, 0);
    }
  },
  {
    id: 'chart/five-minute-close',
    name: 'five-minute candle-close events update both setup arrows and combined signals',
    async run({ page, emit, candle, snapshot }) {
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
    }
  },
  {
    id: 'chart/missing-five-minute-history',
    name: 'missing five-minute history shows an explanation instead of fabricated bars',
    async run({ page, emit, snapshot }) {
      await emit(snapshot(TODAY, 8, { candles_5m: [], active_candle_5m: null }));
      await page.locator('#interval-5m').click();
      await page.waitForTimeout(500);
      assert.equal(await page.locator('#empty-state').isVisible(), true);
      assert.match(await page.locator('#empty-state').textContent(), /5|five/i);
      assert.equal(await page.locator('#replay').isDisabled(), true);
      await page.locator('#interval-15m').click();
      await page.waitForTimeout(500);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /8 candles/);
    }
  },
];
