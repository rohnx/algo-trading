const assert = require('node:assert/strict');
const { TODAY, PREVIOUS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'motion/candle-retarget',
    name: 'live candles ease toward quotes and retarget from their displayed position without changing market data',
    async run({ page, emit, captureChart, motionSnapshot }) {
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
    }
  },
  {
    id: 'motion/comparison-retarget',
    name: 'comparison quote motion preserves closed points, gaps and all other instruments',
    async run({ page, emit, captureChart, motionSnapshot }) {
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
    }
  },
  {
    id: 'motion/reduced-motion',
    name: 'reduced motion snaps an in-flight candle and subsequent quotes to their targets',
    async run({ page, emit, captureChart, motionSnapshot }) {
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
    }
  },
  {
    id: 'motion/transition-reset',
    name: 'date, source and replay changes discard pending quote geometry',
    async run({ page, emit, captureChart, snapshot, motionSnapshot }) {
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
    }
  },
  {
    id: 'motion/scale-expansion',
    name: 'live scale expands for a revealed extreme and stays steady when the quote returns inside',
    async run({ emit, captureChart, revealComparisons, motionSnapshot }) {
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
    }
  },
];
