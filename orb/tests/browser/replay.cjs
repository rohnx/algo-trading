const assert = require('node:assert/strict');
const { TODAY, COMPARISONS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'replay/signals-and-live-restore',
    name: 'illustrative replay hides future signals and restores arriving live data on stop',
    async run({ page, emit, snapshot }) {
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
    }
  },
  {
    id: 'replay/reached-closes',
    name: 'replay exposes reached closes for all comparisons and excludes final quotes from the scale',
    async run({ page, emit, captureChart, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'replay/reached-close-motion',
    name: 'replay eases reached comparison closes without revealing later samples or extrema',
    async run({ page, emit, captureChart, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'replay/opening-and-confirmation',
    name: 'comparison replay uses nine opening closes and only reached valid closed five-minute confirmations',
    async run({ page, emit, comparisonSnapshot, breakoutSnapshot }) {
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
    }
  },
];
