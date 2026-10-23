// Deterministic market data and a private HTTP fixture for each browser scenario.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const TODAY = '2026-10-05', PREVIOUS = '2026-10-01', OLDER = '2026-09-30';
const staticDir = path.join(__dirname, '..', '..', 'static');
const COMPARISONS = [
  ['^NSEBANK', 'BANKNIFTY', 50000],
  ['NIFTY_MID_SELECT.NS', 'MIDCAP SELECT', 16000],
  ['^NSMIDCP', 'NIFTY NEXT 50', 70000],
  ['NIFTY_FIN_SERVICE.NS', 'FIN NIFTY', 24000],
  ['FOCIT.BO', 'FOCIT', 30000]
];
const SECTORS = [
  ['^CNXPHARMA', 'PHARMA', 22000],
  ['^CNXAUTO', 'AUTO', 25000],
  ['^CNXFMCG', 'FMCG', 56000],
  ['^CNXMETAL', 'METAL', 9000],
  ['^CNXPSUBANK', 'PSU BANK', 7000],
  ['^CNXREALTY', 'REALTY', 1000],
  ['NIFTY_CONSR_DURBL.NS', 'CONSUMER DURABLES', 37000],
  ['NIFTY_OIL_AND_GAS.NS', 'OIL & GAS', 12000],
  ['NIFTY_PVT_BANK.NS', 'PRIVATE BANK', 26000],
  ['^CNXENERGY', 'ENERGY', 35000],
  ['NIFTY_IND_DEFENCE.NS', 'INDIA DEFENCE', 7000],
  ['^CNXINFRA', 'INFRASTRUCTURE', 9000],
  ['^CNXPSE', 'PSE', 10000],
  ['^CNXCMDT', 'COMMODITIES', 9000],
  ['NIFTY_CPSE.NS', 'CPSE', 6500]
];

function createFixture() {
  const serverState = {
    delayedDate: '', selectedSource: 'yahoo', selectedGeneration: 0, sourceEpoch: 'fixture-process-a',
    rejectSource: false, holdSource: false, heldSource: null, sourceStarted: null,
    helpRequests: 0, ipRequests: 0, ipFailure: false, historyRequests: 0,
    loginUrl: 'https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=fixture-client', sourceRequests: []
  };
  const timers = new Set();
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
      source: `Deterministic ${serverState.selectedSource} fixture`, source_id: serverState.selectedSource, source_generation: serverState.selectedGeneration, source_epoch: serverState.sourceEpoch,
      current_price: candles.at(-1)?.close ?? null, orb_high: count ? 25064 : null, orb_low: count ? 24990 : null,
      range_locked: count >= 3, market_status: 'closed',
      setups: {
        '15': { setup_id: 'orb_30m_15m', range_minutes: 30, confirmation_minutes: 15, orb_high: count ? 25048 : null, orb_low: count ? 24994 : null, range_locked: count >= 2, signals: [] },
        '5': { setup_id: 'orb_45m_5m', range_minutes: 45, confirmation_minutes: 5, orb_high: count ? 25064 : null, orb_low: count ? 24990 : null, range_locked: count >= 3, signals: [] }
      }, ...extra };
  }
  function neutralBreakout(extra = {}) {
    return { basis: 'close', range_minutes: 45, confirmation_minutes: 5, range_locked: false,
      range_high: null, range_low: null, direction: null, confirmed_at: null, price: null, ...extra };
  }
  function niftySignal() {
    return { strategy: 'dual_ORB', setup_id: 'dual_orb', direction: 'BULLISH',
      timestamp: `${TODAY}T10:05:00+05:30`, price: 25070 };
  }
  function comparisonSnapshot(date = TODAY, count = 8, bankExtra = {}, primaryExtra = {}) {
    const data = snapshot(date, count, { symbol: '^NSEI', primary_symbol: '^NSEI', ...primaryExtra });
    const bankBars = interval => Array.from({ length: count * 15 / interval }, (_, slot) => ({
      ...candle(date, slot, 50000 + slot * interval * .8, 50000 + (slot + 1) * interval * .8, interval), symbol: '^NSEBANK'
    }));
    const bankFive = bankBars(5);
    const bank = {
      symbol: '^NSEBANK', label: 'BANKNIFTY', date,
      candles: bankBars(15), candles_5m: bankFive, active_candle: null, active_candle_5m: null,
      current_price: bankFive.at(-1)?.close ?? null, last_tick_time: bankFive.at(-1)?.end_time ?? null,
      session_open: count ? 50000 : null, change_pct: count ? (bankFive.at(-1).close / 50000 - 1) * 100 : null,
      comparison_status: count ? 'closed' : 'unavailable', breakout: neutralBreakout(), ...bankExtra
    };
    const primary = {
      symbol: '^NSEI', label: 'NIFTY', date, candles: data.candles, candles_5m: data.candles_5m,
      active_candle: data.active_candle, active_candle_5m: data.active_candle_5m,
      current_price: data.current_price, last_tick_time: data.last_tick_time || data.candles_5m.at(-1)?.end_time || null,
      session_open: data.candles[0]?.open ?? null,
      change_pct: data.candles.length ? (data.current_price / data.candles[0].open - 1) * 100 : null,
      comparison_status: count ? 'closed' : 'unavailable'
    };
    const instruments = { '^NSEI': primary, '^NSEBANK': bank };
    for (const [symbol, label, sessionOpen] of COMPARISONS.slice(1)) {
      const bars = interval => Array.from({ length: count * 15 / interval }, (_, slot) => ({
        ...candle(date, slot, sessionOpen * (1 + slot * interval * .8 / 50000), sessionOpen * (1 + (slot + 1) * interval * .8 / 50000), interval), symbol
      }));
      const five = bars(5);
      instruments[symbol] = { symbol, label, date, candles: bars(15), candles_5m: five, session_open: count ? sessionOpen : null,
        current_price: five.at(-1)?.close ?? null, last_tick_time: five.at(-1)?.end_time ?? null,
        comparison_status: count ? 'closed' : 'unavailable', active_candle: null, active_candle_5m: null, breakout: neutralBreakout() };
    }
    return { ...data, instruments };
  }
  function breakoutSnapshot(closedCount = 13) {
    const data = comparisonSnapshot(TODAY, 5);
    const closes = [50000, 50002, 49998, 50004, 49996, 50006, 49994, 50008, 49992, 50015, 50008, 49980, 50000];
    const bank = data.instruments['^NSEBANK'];
    bank.candles_5m = closes.slice(0, closedCount).map((close, slot) => ({
      ...candle(TODAY, slot, slot ? closes[slot - 1] : 50000, close, 5),
      symbol: bank.symbol, high: 50100, low: 49900
    }));
    bank.candles = Array.from({ length: Math.floor(closedCount / 3) }, (_, slot) => {
      const group = bank.candles_5m.slice(slot * 3, slot * 3 + 3);
      return { ...candle(TODAY, slot, group[0].open, group.at(-1).close), symbol: bank.symbol, high: 50100, low: 49900 };
    });
    bank.current_price = bank.candles_5m.at(-1).close;
    bank.last_tick_time = bank.candles_5m.at(-1).end_time;
    const direction = ({ 10: 'BULLISH', 12: 'BEARISH' })[closedCount] || null;
    bank.breakout = neutralBreakout({ range_locked: closedCount >= 9, range_high: 50008, range_low: 49992,
      direction, confirmed_at: direction ? bank.last_tick_time : null, price: direction ? bank.current_price : null });
    return data;
  }
  function sectorSnapshot(date = TODAY, count = 8, primaryExtra = {}) {
    const data = comparisonSnapshot(date, count, {}, primaryExtra);
    for (const instrument of Object.values(data.instruments)) if (instrument.symbol !== '^NSEI') instrument.comparison_group = 'derivatives';
    for (const [index, [symbol, label, open]] of SECTORS.entries()) {
      const closes = Array.from({ length: count * 3 }, (_, slot) => open * (1 + ((slot % 9) - 4) * .00004 + index * .000001));
      const five = closes.map((close, slot) => ({ ...candle(date, slot, slot ? closes[slot - 1] : open, close, 5), symbol }));
      const fifteen = Array.from({ length: count }, (_, slot) => {
        const children = five.slice(slot * 3, slot * 3 + 3);
        return { ...candle(date, slot, children[0].open, children.at(-1).close), symbol };
      });
      const opening = closes.slice(0, 9);
      data.instruments[symbol] = { symbol, label, comparison_group: 'sectors', date,
        candles: fifteen, candles_5m: five, active_candle: null, active_candle_5m: null,
        current_price: five.at(-1)?.close ?? null, last_tick_time: five.at(-1)?.end_time ?? null,
        session_open: count ? open : null, comparison_status: count ? 'closed' : 'unavailable',
        breakout: neutralBreakout({ range_locked: opening.length === 9,
          range_high: opening.length === 9 ? Math.max(...opening) : null,
          range_low: opening.length === 9 ? Math.min(...opening) : null }) };
    }
    return data;
  }
  function confirmSector(instrument, direction = 'BULLISH', movement = .004) {
    const price = instrument.session_open * (1 + (direction === 'BULLISH' ? movement : -movement));
    instrument.candles_5m.at(-1).close = price;
    instrument.candles.at(-1).close = price;
    instrument.current_price = price;
    instrument.breakout = { ...instrument.breakout, direction, confirmed_at: instrument.last_tick_time, price };
    return instrument;
  }
  function motionSnapshot() {
    const data = comparisonSnapshot(TODAY, 8, {}, { current_price: 25020, market_status: 'live' });
    data.last_tick_time = `${TODAY}T11:17:00+05:30`;
    for (const instrument of Object.values(data.instruments)) {
      const ratio = instrument.session_open / 25000;
      const active = interval => ({ ...candle(TODAY, interval === 5 ? 24 : 8, 25015 * ratio, 25020 * ratio, interval),
        symbol: instrument.symbol, high: 25055 * ratio, low: 24995 * ratio, is_closed: false });
      instrument.active_candle = active(15);
      instrument.active_candle_5m = active(5);
      instrument.current_price = 25020 * ratio;
      instrument.last_tick_time = data.last_tick_time;
      instrument.comparison_status = 'live';
    }
    data.active_candle = data.instruments['^NSEI'].active_candle;
    data.active_candle_5m = data.instruments['^NSEI'].active_candle_5m;
    return data;
  }

  function layoutSnapshot() {
    const breakout = snapshot(TODAY).candles[12];
    const chartFixture = comparisonSnapshot(TODAY, 25, {}, { signals: [{ timestamp: breakout.end_time, direction: 'BULLISH', price: breakout.close }] });
    for (const [index, [symbol, , open]] of COMPARISONS.entries()) {
      const instrument = chartFixture.instruments[symbol];
      const movement = [.006, -.002, .0035, -.0045, .002][index];
      for (const bars of [instrument.candles, instrument.candles_5m]) for (const bar of bars) {
        const minute = (Date.parse(bar.end_time) - Date.parse(`${TODAY}T09:15:00+05:30`)) / 60000;
        bar.close = open * (1 + movement * minute / 375 + Math.sin(minute / 40 + index) * .00025);
      }
      instrument.current_price = instrument.candles_5m.at(-1).close;
      instrument.breakout = neutralBreakout({ range_locked: true, direction: movement > 0 ? 'BULLISH' : 'BEARISH',
        confirmed_at: instrument.last_tick_time, price: instrument.current_price });
    }
    chartFixture.setups['15'].signals = [{ timestamp: breakout.end_time, direction: 'BULLISH', price: breakout.close }];
    chartFixture.setups['5'].signals = [{ timestamp: chartFixture.candles_5m[40].end_time, direction: 'BULLISH', price: chartFixture.candles_5m[40].close }];
      return chartFixture;
  }

  function qualifiedComparisonSnapshot() {
    const fixture = breakoutSnapshot(10), bank = fixture.instruments['^NSEBANK'];
    fixture.signals = [niftySignal()];
    const fin = fixture.instruments['NIFTY_FIN_SERVICE.NS'];
    const invert = bar => ({ ...bar, symbol: fin.symbol, open: 24000 - (bar.open - 50000) * .48,
      close: 24000 - (bar.close - 50000) * .48, high: 24048, low: 23952 });
    fin.candles = bank.candles.map(invert); fin.candles_5m = bank.candles_5m.map(invert);
    fin.current_price = fin.candles_5m.at(-1).close; fin.last_tick_time = bank.last_tick_time;
    fin.breakout = neutralBreakout({ range_locked: true, range_high: 24003.84, range_low: 23996.16,
      direction: 'BEARISH', confirmed_at: fin.last_tick_time, price: fin.current_price });
    return fixture;
  }

  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/api/source' && request.method === 'POST') {
      let body = '';
      request.on('data', chunk => { body += chunk; });
      request.on('end', () => {
        const payload = JSON.parse(body);
        serverState.sourceRequests.push({ payload, contentType: request.headers['content-type'] });
        if (serverState.rejectSource) {
          response.writeHead(409, { 'Content-Type': 'application/json' });
          response.end(JSON.stringify({ message: 'Previous feed is still stopping. Retry shortly.' }));
          return;
        }
        serverState.selectedSource = payload.source;
        serverState.selectedGeneration += 1;
        const data = snapshot(TODAY, serverState.selectedSource === 'shoonya' ? 6 : 8);
        if (serverState.holdSource) {
          serverState.heldSource = { response, data };
          serverState.sourceStarted?.();
          serverState.sourceStarted = null;
          return;
        }
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify(data));
      });
      return;
    }
    if (url.pathname === '/api/shoonya/help') {
      serverState.helpRequests += 1;
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ login_url: serverState.loginUrl }));
      return;
    }
    if (url.pathname === '/api/public-ip') {
      serverState.ipRequests += 1;
      response.writeHead(serverState.ipFailure ? 503 : 200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(serverState.ipFailure ? { message: 'IP lookup unavailable. Try curl -s ifconfig.me.' } : { ip: '203.0.113.8' }));
      return;
    }
    if (url.pathname.startsWith('/api/')) {
      if (url.pathname === '/api/history') serverState.historyRequests += 1;
      const date = url.searchParams.get('date');
      const data = url.pathname === '/api/history'
        ? { dates: [OLDER, PREVIOUS, TODAY], today: TODAY, history_note: 'Fixture history' }
        : snapshot(date, date === TODAY ? 0 : 25);
      const timer = setTimeout(() => {
        timers.delete(timer);
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify(data));
      }, date === serverState.delayedDate ? 350 : 0);
      timers.add(timer);
      return;
    }
    const file = path.join(staticDir, url.pathname === '/' ? 'index.html' : path.basename(url.pathname));
    if (!fs.existsSync(file)) { response.writeHead(404); response.end(); return; }
    response.writeHead(200, { 'Content-Type': file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'text/html' });
    fs.createReadStream(file).pipe(response);
  });

  return {
    serverState, server, candle, snapshot, neutralBreakout, niftySignal, comparisonSnapshot,
    breakoutSnapshot, sectorSnapshot, confirmSector, motionSnapshot, layoutSnapshot, qualifiedComparisonSnapshot,
    async close() {
      for (const timer of timers) clearTimeout(timer);
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    }
  };
}

module.exports = { TODAY, PREVIOUS, OLDER, COMPARISONS, SECTORS, createFixture };
