const assert = require('node:assert/strict');
const { TODAY, PREVIOUS, OLDER, COMPARISONS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'sessions/empty-session',
    name: 'today remains visible with an empty closed market, independently of browser timezone',
    async run({ page }) {
      await page.waitForFunction(() => document.querySelector('#session-status').textContent === 'Market closed');
      assert.equal(await page.locator('#session-date').inputValue(), TODAY);
      assert.equal(await page.locator('#empty-state').isVisible(), true);
    }
  },
  {
    id: 'sessions/preopen',
    name: 'preopen explains the empty session and the first forming candle appears at 09:15 IST',
    async run({ page, emit, candle, snapshot }) {
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
    }
  },
  {
    id: 'sessions/scroll-and-date',
    name: 'scrolling leaves the date and replay intact while date buttons still navigate',
    async run({ page, emit, waitDate, snapshot }) {
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
    }
  },
  {
    id: 'sessions/live-history-isolation',
    name: 'live ticks and reconnect snapshots do not replace historical viewing',
    async run({ page, emit, waitDate, candle, snapshot }) {
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      await emit({ type: 'tick', date: TODAY, today: TODAY, price: 25123, active_candle: candle(TODAY, 8, 25100, 25123) });
      await emit(snapshot(TODAY, 10));
      assert.equal(await page.locator('#session-date').inputValue(), PREVIOUS);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /25 candles/);
      await page.locator('#today').click(); await waitDate(TODAY);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /10 candles/);
    }
  },
  {
    id: 'sessions/light-updates',
    name: 'light quote updates retain candle history and full snapshots replace it',
    async run({ page, emit, snapshot, comparisonSnapshot, motionSnapshot }) {
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
    }
  },
  {
    id: 'sessions/restart-history-refresh',
    name: 'one accepted restart snapshot refreshes history once',
    async run({ page, emit, serverState, snapshot }) {
      await emit(snapshot(TODAY, 8));
      await page.waitForTimeout(100);
      const before = serverState.historyRequests;
      serverState.sourceEpoch = 'fixture-process-cleanup';
      await emit(snapshot(TODAY, 8));
      await page.waitForTimeout(100);
      assert.equal(serverState.historyRequests - before, 1);
    }
  },
  {
    id: 'sessions/idle-history-and-replay',
    name: 'historical and completed replay views stay idle while live quotes continue',
    async run({ page, emit, waitDate, snapshot }) {
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
    }
  },
  {
    id: 'sessions/quote-freshness',
    name: 'the idle clock still marks live quotes stale at the freshness boundary',
    async run({ page, emit, snapshot, motionSnapshot }) {
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
    }
  },
  {
    id: 'sessions/history-response-race',
    name: 'a slow historical response cannot replace the newer date selection',
    async run({ page, waitDate, serverState }) {
      await page.locator('#previous').click(); await waitDate(PREVIOUS);
      serverState.delayedDate = OLDER;
      await page.locator('#previous').click();
      await page.locator('#next').click(); await waitDate(PREVIOUS);
      await page.waitForTimeout(450);
      assert.equal(await page.locator('#session-date').inputValue(), PREVIOUS);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), new RegExp(PREVIOUS));
      serverState.delayedDate = '';
    }
  },
  {
    id: 'sessions/new-day',
    name: 'a new-day session resets the chart while preserving slot-zero alignment',
    async run({ page, emit, waitDate, snapshot }) {
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
    }
  },
  {
    id: 'sessions/status-heartbeat',
    name: 'status heartbeats update market state without another price tick',
    async run({ page, emit, snapshot }) {
      await page.evaluate(() => { window.__now = Date.parse('2026-10-05T11:00:00+05:30'); });
      await emit(snapshot(TODAY, 8, { market_status: 'live', last_tick_time: '2026-10-05T11:00:00+05:30' }));
      await page.locator('#today').click();
      assert.equal(await page.locator('#session-status').textContent(), 'Live');
      await emit(snapshot(TODAY, 8, { type: 'status', market_status: 'closed' }));
      assert.equal(await page.locator('#session-status').textContent(), 'Market closed');
    }
  },
  {
    id: 'sessions/primary-data-gaps',
    name: 'missing or incomplete closed primary candles have a compact data-gap explanation in both intervals',
    async run({ page, emit, snapshot }) {
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
    }
  },
  {
    id: 'sessions/historical-and-replay-gaps',
    name: 'data-gap notices follow the selected history and cannot reveal unreached replay gaps',
    async run({ page, emit, waitDate, snapshot }) {
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
    }
  },
];
