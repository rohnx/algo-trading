const assert = require('node:assert/strict');
const { TODAY, COMPARISONS, SECTORS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'comparison_returns/native-closes',
    name: 'comparisons use native selected closes at their timestamps with a shared 09:15 origin',
    async run({ page, emit, captureChart, comparisonSnapshot, motionSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/instrument-deltas',
    name: 'comparison deltas preserve primary candles, quote and strategies while sharing a fitted scale',
    async run({ page, emit, captureChart, serverState, candle, comparisonSnapshot }) {
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
        await emit({ type: 'state', symbol, date: TODAY, source_id: serverState.selectedSource, source_generation: serverState.selectedGeneration,
          source_epoch: serverState.sourceEpoch, instruments: { [symbol]: comparison } });
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
    }
  },
  {
    id: 'comparison_returns/forming-backfill',
    name: 'forming backfill stays continuous before the next live quote and through rollover',
    async run({ page, emit, captureChart, candle, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/extreme-returns',
    name: 'large positive and negative comparison moves fit fully and comparison tabs display only their selected group',
    async run({ page, emit, captureChart, selectGroup, revealComparisons, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/independent-baselines',
    name: 'stale and missing comparison baselines are handled independently for each series',
    async run({ page, emit, captureChart, revealComparisons, candle, snapshot, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/selected-bar-gaps',
    name: 'invalid and missing selected bars leave gaps only in the affected comparison',
    async run({ page, emit, captureChart, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/fin-nifty-closes',
    name: 'FIN NIFTY plots the same closing samples as its range, without a false opening-price peak',
    async run({ page, emit, candle, neutralBreakout, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/session-open-ranking',
    name: 'comparison charts rank actual session-open returns in both groups and intervals',
    async run({ page, emit, captureChart, selectGroup, comparisonSnapshot, sectorSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/missing-opening-slots',
    name: 'comparison origins preserve missing opening slots and use stable references for lone live quotes',
    async run({ page, emit, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/first-close-origin',
    name: 'completing the first comparison close keeps its session-open baseline and scale',
    async run({ page, emit, captureChart, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_returns/interval-and-replay-origin',
    name: 'replay uses only reached closes and interval changes preserve the session-open origin',
    async run({ page, emit, captureChart, comparisonSnapshot }) {
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
    }
  },
];
