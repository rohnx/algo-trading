const assert = require('node:assert/strict');
const { TODAY } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'comparison_quotes/delayed-quotes',
    name: 'delayed quotes retain their actual return and timestamp until their adjusted freshness expires',
    async run({ page, emit, captureChart, revealComparisons, candle, snapshot, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_quotes/fallback-provenance',
    name: 'Shoonya displays the complete FOCIT Yahoo fallback with provenance after hours and during replay',
    async run({ page, emit, captureChart, revealComparisons, holdSourceRequest, releaseSourceRequest, snapshot, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_quotes/native-quote-with-fallback',
    name: 'fresh Shoonya quotes remain isolated beyond delayed FOCIT history until a valid selected bar exists',
    async run({ page, emit, captureChart, serverState, candle, snapshot, comparisonSnapshot }) {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:17:00+05:30'); });
        serverState.selectedSource = 'shoonya'; serverState.selectedGeneration += 1;
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
        serverState.selectedSource = 'yahoo'; serverState.selectedGeneration += 1;
        await emit(snapshot(TODAY, 8));
      }
    }
  },
  {
    id: 'comparison_quotes/partial-ohlc',
    name: 'partial FOCIT OHLC retains explicitly valid observed closes and live updates',
    async run({ page, emit, captureChart, revealComparisons, serverState, candle, snapshot, comparisonSnapshot }) {
      const originalNow = await page.evaluate(() => window.__now);
      try {
        await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:27:00+05:30'); setIntervalMinutes(15); transition = null; });
        serverState.selectedSource = 'shoonya'; serverState.selectedGeneration += 1;
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
        serverState.selectedSource = 'yahoo'; serverState.selectedGeneration += 1;
        await emit(snapshot(TODAY, 8));
      }
    }
  },
  {
    id: 'comparison_quotes/quote-only',
    name: 'a live-only comparison shows an isolated actual quote without inventing candle history',
    async run({ page, emit, captureChart, snapshot, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_quotes/confirmation-expiry',
    name: 'fresh live and delayed quotes cannot revive an expired or future comparison confirmation',
    async run({ page, emit, captureChart, comparisonSnapshot, breakoutSnapshot }) {
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
    }
  },
];
