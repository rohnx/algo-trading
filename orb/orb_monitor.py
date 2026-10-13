#!/usr/bin/env python3
"""NIFTY chart with 15m/30m and 5m/45m opening-range confirmation."""

import argparse
import json
from ipaddress import ip_address
from pathlib import Path
import queue
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from dataclasses import dataclass, field
from itertools import groupby
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from signals import IST, OpeningRangeBreakoutStrategy, Signal, SignalDirection, Tick
from market import (INSTRUMENTS, PRIMARY_SYMBOL, InstrumentState, in_ist,
                    history_cutoff, prune_session_files)

PORT = 8765
SYMBOL = PRIMARY_SYMBOL
ROOT = Path(__file__).resolve().parent
HISTORY_NOTE = "Yahoo supplies 5-minute history; saved sessions are retained for 60 calendar days."
SOURCE_NAMES = {"yahoo": "Yahoo Finance · ^NSEI", "shoonya": "Shoonya · NIFTY 50"}


@dataclass
class ConfluenceSignal(Signal):
    confirmations: dict = field(default_factory=dict)

    def to_dict(self):
        return {**super().to_dict(), "confirmations": self.confirmations}


class DualORBState:
    """Keep each chart's arrows separate from agreement between latest closes."""

    def __init__(self, strategy=None):
        self.strategies = {
            "15": strategy or OpeningRangeBreakoutStrategy(range_minutes=30, confirmation_minutes=15),
            "5": OpeningRangeBreakoutStrategy(range_minutes=45, confirmation_minutes=5),
        }
        self.reset_day()

    def reset_day(self):
        for strategy in self.strategies.values():
            strategy.reset_day()
        self.setup_signals = {key: [] for key in self.strategies}
        self.signals = []
        self.triggered = set()

    def get_setups(self):
        return {key: {**strategy.get_state(),
                      "signals": [signal.to_dict() for signal in self.setup_signals[key]]}
                for key, strategy in self.strategies.items()}

    def feed_closed(self, candles):
        """Evaluate simultaneous 5m/15m closes together, including backfills."""
        emitted = []
        ordered = sorted(candles, key=lambda item: (item[1].end_time, item[0]))
        for timestamp, group in groupby(ordered, key=lambda item: item[1].end_time):
            for interval, candle in group:
                key = str(interval)
                signal = self.strategies[key].on_candle_close(candle)
                if signal:
                    self.setup_signals[key].insert(0, signal)
            signal = self._confirm_agreement(in_ist(timestamp))
            if signal:
                self.signals.insert(0, signal)
                emitted.append(signal)
        return emitted

    def _confirm_agreement(self, timestamp):
        states = self.get_setups()
        direction = states["15"]["confirmation_direction"]
        if direction not in ("BULLISH", "BEARISH") or direction in self.triggered:
            return None
        for state in states.values():
            if state["confirmation_direction"] != direction or not state["confirmed_at"]:
                return None
            confirmed = datetime.fromisoformat(state["confirmed_at"])
            # A skipped close cannot leave an older leg eligible indefinitely.
            if not timedelta(0) <= timestamp - confirmed < timedelta(minutes=state["confirmation_minutes"]):
                return None
        latest = max(states.values(), key=lambda state: state["confirmed_at"])
        price = latest["confirmation_price"]
        level = latest["orb_high"] if direction == "BULLISH" else latest["orb_low"]
        self.triggered.add(direction)
        return ConfluenceSignal(
            strategy="dual_ORB", setup_id="dual_orb", symbol=SYMBOL,
            direction=SignalDirection(direction), signal_type=f"DUAL_ORB_{direction}",
            price=price, level=level, diff=abs(price - level), timestamp=timestamp,
            message=f"Both ORBs {direction.lower()}: 15m close / 30m range + 5m close / 45m range",
            confirmations={key: {name: state[name] for name in (
                "setup_id", "range_minutes", "confirmation_minutes", "confirmed_at",
                "confirmation_price", "orb_high", "orb_low")}
                for key, state in states.items()},
        )


def _primary_field(name):
    """Preserve the original hub's public primary chart attributes."""
    return property(lambda hub: getattr(hub.instruments[PRIMARY_SYMBOL], name),
                    lambda hub, value: setattr(hub.instruments[PRIMARY_SYMBOL], name, value))


class MonitorHub:
    date = _primary_field("date")
    sessions = _primary_field("sessions")
    sessions_5m = _primary_field("sessions_5m")
    candles = _primary_field("candles")
    candles_5m = _primary_field("candles_5m")
    active_candle = _primary_field("active_candle")
    active_candle_5m = _primary_field("active_candle_5m")
    current_price = _primary_field("current_price")
    last_tick_time = _primary_field("last_tick_time")

    def __init__(self, strategy=None, *, now=None, cache_dir=None):
        self.breakouts = DualORBState(strategy)
        self.strategy = self.breakouts.strategies["15"]
        self.strategy_5m = self.breakouts.strategies["5"]
        self.now = now or (lambda: datetime.now(IST))
        self.lock = threading.RLock()
        self.clients = set()
        self.source_id = "yahoo"
        self.source_generation = 0
        self.source_epoch = uuid4().hex
        self.source_name = SOURCE_NAMES[self.source_id]
        self.feed_status, self.feed_error = "waiting", None
        self.feed_manager = None
        self.cache_dir = cache_dir
        prune_session_files(cache_dir, history_cutoff(self.now()))
        self.instruments = {symbol: InstrumentState(instrument, now=self.now,
            cache_root=cache_dir,
            on_close=lambda closed, symbol=symbol: self._bars_closed(symbol, closed))
            for symbol, instrument in INSTRUMENTS.items()}
        self._market_closed = self._market_status() == "closed"

    @property
    def signals(self):
        return self.breakouts.signals

    @property
    def history_note(self):
        return HISTORY_NOTE if self.source_id == "yahoo" else (
            "Shoonya history depends on available intraday data; saved sessions are retained for 60 calendar days.")

    def _reset(self, session_date):
        for state in self.instruments.values():
            state.reset(session_date)
        self.breakouts.reset_day()

    def activate_source(self, source):
        if source not in SOURCE_NAMES:
            raise ValueError("Choose yahoo or shoonya.")
        with self.lock:
            self.advance_time()
            self.source_id = source
            self.source_generation += 1
            self.source_name = SOURCE_NAMES[source]
            self.feed_status, self.feed_error = "connecting", None
            for state in self.instruments.values():
                state.activate_source(source)
            self._rebuild_strategy()
            self.broadcast({"type": "session", **self._snapshot()})

    def set_feed_status(self, status, error=None):
        with self.lock:
            self.feed_status, self.feed_error = status, error
            if status in ("disconnected", "error"):
                for state in self.instruments.values():
                    for candle in (state.active_candle, state.active_candle_5m):
                        if candle:
                            candle.is_complete = candle.close_is_valid = False
            self.broadcast({"type": "status", **self._snapshot()})

    def advance_time(self, now=None):
        """Close elapsed bars and clear yesterday even if no quotes arrive."""
        now = in_ist(now or self.now())
        with self.lock:
            for state in self.instruments.values():
                state._close_elapsed(now)
            market_closed = now.weekday() >= 5 or not dtime(9, 15) <= now.time() < dtime(15, 30)
            if now.date().isoformat() != self.date:
                self._reset(now.date().isoformat())
                cutoff = history_cutoff(now)
                prune_session_files(self.cache_dir, cutoff)
                for state in self.instruments.values():
                    state.prune_history(cutoff)
                self.broadcast({"type": "session", **self._snapshot()})
            elif market_closed != self._market_closed:
                self.broadcast({"type": "status", **self._snapshot()})
            self._market_closed = market_closed

    def _market_status(self):
        now = in_ist(self.now())
        if now.weekday() >= 5 or not dtime(9, 15) <= now.time() < dtime(15, 30):
            return "closed"
        fresh = self.last_tick_time and 0 <= (now - self.last_tick_time).total_seconds() <= 90
        return "live" if fresh and self.feed_status not in ("connecting", "disconnected", "error") else "waiting"

    def _snapshot(self):
        instruments = self._instrument_snapshots()
        return {
            "date": self.date,
            "today": in_ist(self.now()).date().isoformat(),
            "source": self.source_name,
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "source_epoch": self.source_epoch,
            "feed_status": self.feed_status,
            "feed_error": self.feed_error,
            **instruments[PRIMARY_SYMBOL],
            "primary_symbol": PRIMARY_SYMBOL,
            "instruments": instruments,
            "orb_high": self.strategy.orb_high,
            "orb_low": self.strategy.orb_low,
            "range_locked": self.strategy.range_locked,
            "setups": self.breakouts.get_setups(),
            "signals": [s.to_dict() for s in self.signals],
            "market_status": self._market_status(),
        }

    def snapshot(self, session_date=None):
        with self.lock:
            self.advance_time()
            if session_date is None or session_date == self.date:
                return self._snapshot()
            instruments = self._instrument_snapshots(session_date)
            primary = instruments[PRIMARY_SYMBOL]
            candles = self.sessions.get(session_date, [])
            five = self.sessions_5m.get(session_date, [])
            breakouts = DualORBState()
            breakouts.feed_closed([(15, c) for c in candles if c.is_closed]
                                  + [(5, c) for c in five if c.is_closed])
            strategy = breakouts.strategies["15"]
            result = {
                "date": session_date, "today": in_ist(self.now()).date().isoformat(),
                "source": f"{SOURCE_NAMES[self.source_id]} · history", "source_id": self.source_id,
                "source_generation": self.source_generation, "source_epoch": self.source_epoch,
                "feed_status": self.feed_status,
                "feed_error": self.feed_error, **primary,
                "primary_symbol": PRIMARY_SYMBOL, "instruments": instruments,
                "orb_high": strategy.orb_high, "orb_low": strategy.orb_low,
                "range_locked": strategy.range_locked, "setups": breakouts.get_setups(),
                "signals": [s.to_dict() for s in breakouts.signals],
                "market_status": "closed",
            }
            if not candles:
                result.update(message="No prices available for this date.", history_note=self.history_note)
            return result

    def history(self):
        with self.lock:
            self.advance_time()
            dates = {day for day, candles in self.sessions.items() if candles}
            dates.update(day for day, candles in self.sessions_5m.items() if candles)
            if self.candles or self.active_candle or self.candles_5m or self.active_candle_5m:
                dates.add(self.date)
            dates = sorted(dates)
            return {"dates": dates, "today": in_ist(self.now()).date().isoformat(),
                    "earliest_date": dates[0] if dates else None, "history_note": self.history_note,
                    "source_id": self.source_id, "source_generation": self.source_generation,
                    "source_epoch": self.source_epoch}

    def register_client(self, client):
        with self.lock:
            self.advance_time()
            self.clients.add(client)
            client.put_nowait({"type": "init", **self._snapshot()})

    def unregister_client(self, client):
        with self.lock:
            self.clients.discard(client)

    def broadcast(self, event):
        with self.lock:
            event = {"date": self.date, "today": in_ist(self.now()).date().isoformat(),
                     "source_id": self.source_id, "source_generation": self.source_generation,
                     "source_epoch": self.source_epoch, **event}
            recovery = None
            for client in list(self.clients):
                try:
                    client.put_nowait(event)
                except queue.Full:
                    # Replace missed incremental updates with a complete state.
                    while not client.empty():
                        try:
                            client.get_nowait()
                        except queue.Empty:
                            break
                    if recovery is None:
                        recovery = {"type": "init", **self._snapshot()}
                    client.put_nowait(recovery)

    def _instrument_snapshot(self, symbol, session_date=None, *, include_history=True):
        state = self.instruments[symbol]
        return state.snapshot(in_ist(self.now()), self.feed_status, session_date or self.date,
                              include_history=include_history)

    def _instrument_snapshots(self, session_date=None):
        return {symbol: self._instrument_snapshot(symbol, session_date) for symbol in self.instruments}

    def _instrument_event(self, symbol, *, include_history=True):
        return {"symbol": symbol, "instruments": {
            symbol: self._instrument_snapshot(symbol, include_history=include_history)}}

    def _rebuild_strategy(self):
        self.breakouts.reset_day()
        self.breakouts.feed_closed([(15, c) for c in self.candles]
                                   + [(5, c) for c in self.candles_5m])
        for strategy, active in ((self.strategy, self.active_candle),
                                 (self.strategy_5m, self.active_candle_5m)):
            if active:
                strategy.on_tick(Tick(SYMBOL, active.close, active.start_time), active)

    def seed_historical_candles(self, candles, interval_minutes=15, *, symbol=PRIMARY_SYMBOL):
        """Initialize each instrument independently; only NIFTY rebuilds strategies."""
        with self.lock:
            self.advance_time()
            self.instruments[symbol].seed_historical_candles(candles, interval_minutes)
            if symbol == PRIMARY_SYMBOL:
                self._rebuild_strategy()

    def _breakout_fields(self):
        return {"orb_high": self.strategy.orb_high, "orb_low": self.strategy.orb_low,
                "range_locked": self.strategy.range_locked,
                "setups": self.breakouts.get_setups(),
                "signals": [signal.to_dict() for signal in self.signals]}

    def _bars_closed(self, symbol, closed):
        if symbol != PRIMARY_SYMBOL:
            self.broadcast({"type": "state", **self._instrument_event(symbol)})
            return
        emitted = self.breakouts.feed_closed(closed)
        fields = {**self._breakout_fields(), **self._instrument_event(symbol)}
        for interval, candle in closed:
            self.broadcast({"type": "candle_close", "interval_minutes": interval,
                            "candle": candle.to_dict(), **fields,
                            "market_status": self._market_status()})
        for signal in emitted:
            print(signal.message, flush=True)

    def process_tick(self, price, vol=0, tick_time=None, *, symbol=PRIMARY_SYMBOL):
        with self.lock:
            self.advance_time()
            state = self.instruments[symbol]
            if not state.process_tick(price, vol, tick_time):
                return False
            if self.feed_status == "waiting":
                self.feed_status = "live"
            event = self._instrument_event(symbol, include_history=False)
            if symbol == PRIMARY_SYMBOL:
                tick = Tick(symbol, price, state.last_tick_time, vol)
                self.strategy.on_tick(tick, state.active_candle)
                self.strategy_5m.on_tick(tick, state.active_candle_5m)
                event.update(type="tick", price=price,
                    active_candle=state.active_candle.to_dict(),
                    active_candle_5m=state.active_candle_5m.to_dict(),
                    **self._breakout_fields(), market_status=self._market_status(),
                    last_tick_time=state.last_tick_time.isoformat())
            else:
                event["type"] = "state"
            self.broadcast(event)
            return True


class MonitorHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, hub=None, **kwargs):
        self.hub = hub
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        if not urlsplit(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/source":
            return self._json({"message": "Unknown endpoint."}, 404)
        manager = self.hub.feed_manager
        if manager is None:
            return self._json({"message": "Source switching is unavailable."}, 409)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            source = data.get("source")
            if source not in SOURCE_NAMES:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            return self._json({"message": "Choose yahoo or shoonya."}, 400)
        try:
            manager.select_source(source)
        except RuntimeError:
            return self._json({"message": "The previous feed is still stopping. Retry shortly."}, 409)
        self._json(self.hub.snapshot())

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/shoonya/help":
            from feeds import shoonya_login_url
            self._json({"login_url": shoonya_login_url()})
        elif url.path == "/api/public-ip":
            # Explicit, read-only check from the monitor's host, not the browser.
            try:
                request = Request("https://ifconfig.me/ip", headers={"Accept": "text/plain"})
                with urlopen(request, timeout=3) as response:
                    public_ip = str(ip_address(response.read(128).decode("ascii").strip()))
                self._json({"ip": public_ip})
            except (OSError, ValueError, UnicodeError):
                self._json({"message": "Could not check the public IP. Run curl -s ifconfig.me on the monitor's machine."}, 503)
        elif url.path == "/api/history":
            self._json(self.hub.history())
        elif url.path == "/api/session":
            day = parse_qs(url.query).get("date", [None])[0]
            try:
                if day is not None and date.fromisoformat(day).isoformat() != day:
                    raise ValueError
            except ValueError:
                self._json({"message": "Use a date in YYYY-MM-DD format."}, 400)
                return
            self._json(self.hub.snapshot(day))
        elif url.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            client = queue.Queue(maxsize=100)
            self.hub.register_client(client)
            try:
                while True:
                    try:
                        event = client.get(timeout=15)
                        data = f"data: {json.dumps(event)}\n\n"
                    except queue.Empty:
                        data = ": keep-alive\n\n"
                    self.wfile.write(data.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.hub.unregister_client(client)
        elif url.path.startswith("/api/"):
            self._json({"message": "Unknown endpoint."}, 404)
        else:
            super().do_GET()


def run_http_server(hub, port=PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                lambda *args, **kwargs: MonitorHTTPHandler(*args, hub=hub, **kwargs))
    print(f"NIFTY monitor: http://localhost:{port}", flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--source", choices=tuple(SOURCE_NAMES), default="yahoo", help="Initial live source (default: yahoo)")
    args = parser.parse_args()
    from feeds import FeedManager
    hub = MonitorHub(cache_dir=ROOT / "data")
    hub.feed_manager = FeedManager(hub)
    hub.feed_manager.select_source(args.source)
    threading.Thread(target=run_http_server, args=(hub, args.port), daemon=True).start()
    try:
        while True:
            hub.advance_time()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        hub.feed_manager.stop()


if __name__ == "__main__":
    main()
