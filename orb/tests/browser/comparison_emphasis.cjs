const assert = require('node:assert/strict');
const { TODAY, COMPARISONS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'comparison_emphasis/hidden-extremes',
    name: 'hidden post-breakout extremes do not stretch either source, and NIFTY reveal restores the idle scale',
    async run({ page, emit, captureChart, snapshot, niftySignal, comparisonSnapshot }) {
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
    }
  },
  {
    id: 'comparison_emphasis/pre-signal-overview',
    name: 'blurred comparisons stay visible until NIFTY confirms, including source changes and replay',
    async run({ page, emit, captureChart, revealComparisons, niftySignal, comparisonSnapshot, breakoutSnapshot }) {
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
    }
  },
  {
    id: 'comparison_emphasis/qualified-directions',
    name: 'closed comparison breakouts emphasize either direction and soften the other lines',
    async run({ page, emit, captureChart, neutralBreakout, niftySignal, breakoutSnapshot }) {
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
    }
  },
  {
    id: 'comparison_emphasis/primary-reveal',
    name: 'NIFTY hover and focus reveal neutral indices softly and leaving restores the idle view',
    async run({ page, emit, captureChart, revealComparisons, qualifiedComparisonSnapshot }) {
      await emit(qualifiedComparisonSnapshot()); await captureChart();
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
    }
  },
  {
    id: 'comparison_emphasis/endpoint-isolation',
    name: 'hover and keyboard focus isolate a qualified endpoint and restore the other indices',
    async run({ page, emit, captureChart, selectGroup, comparisonSnapshot, qualifiedComparisonSnapshot }) {
      await emit(qualifiedComparisonSnapshot()); await captureChart();
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
    }
  },
  {
    id: 'comparison_emphasis/close-only-confirmation',
    name: 'ticks and forming comparison candles do not confirm a close-only breakout',
    async run({ page, emit, candle, comparisonSnapshot, breakoutSnapshot }) {
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
    }
  },
];
