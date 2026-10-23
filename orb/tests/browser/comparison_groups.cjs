const assert = require('node:assert/strict');
const { TODAY, PREVIOUS, COMPARISONS, SECTORS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'comparison_groups/selection',
    name: 'comparison tabs switch exclusively and toggle off to NIFTY only without changing market data',
    async run({ page, emit, captureChart, selectGroup, sectorSnapshot }) {
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
    }
  },
  {
    id: 'comparison_groups/keyboard-navigation',
    name: 'sector tabs support arrow and boundary keys without changing date navigation',
    async run({ page }) {
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
    }
  },
  {
    id: 'comparison_groups/historical-and-inactive-data',
    name: 'group switches preserve historical replay and receive inactive-group updates in the background',
    async run({ page, emit, captureChart, selectGroup, waitDate, comparisonSnapshot, sectorSnapshot, confirmSector }) {
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
    }
  },
  {
    id: 'comparison_groups/inactive-extremes',
    name: 'inactive-group extremes never stretch either source and switching clears old isolation targets',
    async run({ page, emit, captureChart, selectGroup, neutralBreakout, comparisonSnapshot, sectorSnapshot, confirmSector }) {
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
    }
  },
  {
    id: 'comparison_groups/sector-emphasis',
    name: 'sectors share close-only emphasis, NIFTY overview, hover isolation and source provenance',
    async run({ page, emit, captureChart, selectGroup, revealComparisons, candle, niftySignal, comparisonSnapshot, sectorSnapshot, confirmSector }) {
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
    }
  },
  {
    id: 'comparison_groups/sector-confirmations',
    name: 'sector replay recomputes all opening closes and cannot reveal future confirmations',
    async run({ page, emit, selectGroup, comparisonSnapshot, breakoutSnapshot, sectorSnapshot }) {
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
    }
  },
];
