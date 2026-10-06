#!/usr/bin/env python3
"""NIFTY 15-minute chart and 45-minute opening range, served at localhost:8765."""

import argparse
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from signals import Candle, IST, OpeningRangeBreakoutStrategy, Tick

PORT = 8765
SYMBOL = "^NSEI"
ROOT = Path(__file__).resolve().parent
HISTORY_NOTE = "Yahoo supplies about 60 days of 15-minute history; saved sessions remain available locally."


def in_ist(value):
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def candle_from_dict(value):
    return Candle(**{**value, "start_time": in_ist(datetime.fromisoformat(value["start_time"])),
                     "end_time": in_ist(datetime.fromisoformat(value["end_time"]))})


class MonitorHub:
    def __init__(self, strategy=None, *, now=None, cache_dir=None, replay=False):
        self.strategy = strategy or OpeningRangeBreakoutStrategy()
        self.now = now or (lambda: datetime.now(IST))
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.replay = replay
        self.lock = threading.RLock()
        self.clients = set()
        self.sessions = {}
        self.source_name = "Yahoo Finance · ^NSEI"
        self.date = in_ist(self.now()).date().isoformat()
        self.candles = []
        self.active_candle = None
        self.current_price = None
        self.last_tick_time = None
        self.signals = []
        if self.cache_dir and self.cache_dir.exists():
            for path in sorted(self.cache_dir.glob("????-??-??.json")):
                try:
                    self.sessions[path.stem] = [candle_from_dict(c) for c in json.loads(path.read_text())]
                except (OSError, ValueError, TypeError, KeyError):
                    print(f"Could not load saved session: {path.name}", file=sys.stderr)

    def _save_session(self):
        if self.replay or not self.candles:
            return
        self.sessions[self.date] = list(self.candles)
        self._save_candles(self.date, self.candles)

    def _save_candles(self, day, candles):
        if self.cache_dir:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path = self.cache_dir / f"{day}.json"
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps([c.to_dict() for c in candles]))
                temp.replace(path)
            except OSError as exc:
                print(f"Could not save session: {exc}", file=sys.stderr)

    def _reset(self, session_date):
        self._save_session()
        self.date = session_date
        self.strategy.reset_day()
        self.candles = []
        self.active_candle = None
        self.current_price = None
        self.last_tick_time = None
        self.signals = []

    def advance_time(self, now=None):
        """Close elapsed bars and clear yesterday even if no quotes arrive."""
        if self.replay:
            return
        now = in_ist(now or self.now())
        with self.lock:
            if self.active_candle and now >= self.active_candle.end_time:
                self._close_active()
            if now.date().isoformat() != self.date:
                self._reset(now.date().isoformat())
                self.broadcast({"type": "session", **self._snapshot()})

    def _market_status(self):
        if self.replay:
            return "replay"
        now = in_ist(self.now())
        if now.weekday() >= 5 or not dtime(9, 15) <= now.time() < dtime(15, 30):
            return "closed"
        fresh = self.last_tick_time and 0 <= (now - self.last_tick_time).total_seconds() <= 90
        return "live" if fresh else "waiting"

    def _snapshot(self):
        return {
            "date": self.date,
            "today": in_ist(self.now()).date().isoformat(),
            "source": self.source_name,
            "candles": [c.to_dict() for c in self.candles],
            "active_candle": self.active_candle.to_dict() if self.active_candle else None,
            "current_price": self.current_price,
            "orb_high": self.strategy.orb_high,
            "orb_low": self.strategy.orb_low,
            "range_locked": self.strategy.range_locked,
            "signals": [s.to_dict() for s in self.signals],
            "market_status": self._market_status(),
            "last_tick_time": self.last_tick_time.isoformat() if self.last_tick_time else None,
        }

    def snapshot(self, session_date=None):
        with self.lock:
            self.advance_time()
            if session_date is None or session_date == self.date:
                return self._snapshot()
            strategy = OpeningRangeBreakoutStrategy()
            candles = self.sessions.get(session_date, [])
            signals = [signal.to_dict() for c in candles
                       if (signal := strategy.on_candle_close(c))]
            result = {
                "date": session_date, "today": in_ist(self.now()).date().isoformat(),
                "source": "Yahoo Finance · history", "candles": [c.to_dict() for c in candles],
                "active_candle": None, "current_price": candles[-1].close if candles else None,
                "orb_high": strategy.orb_high, "orb_low": strategy.orb_low,
                "range_locked": strategy.range_locked, "signals": list(reversed(signals)),
                "market_status": "closed", "last_tick_time": None,
            }
            if not candles:
                result.update(message="No 15-minute prices available for this date.", history_note=HISTORY_NOTE)
            return result

    def history(self):
        with self.lock:
            self.advance_time()
            dates = {day for day, candles in self.sessions.items() if candles}
            if self.candles or self.active_candle:
                dates.add(self.date)
            dates = sorted(dates)
            return {"dates": dates, "today": in_ist(self.now()).date().isoformat(),
                    "earliest_date": dates[0] if dates else None, "history_note": HISTORY_NOTE}

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
            event = {"date": self.date, "today": in_ist(self.now()).date().isoformat(), **event}
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
                    client.put_nowait({"type": "init", **self._snapshot()})

    def seed_historical_candles(self, candles):
        """Keep history separate; only today's bars may initialize the live chart."""
        with self.lock:
            self.advance_time()
            now = in_ist(self.now())
            grouped = {}
            for candle in candles:
                day = candle.start_time.date().isoformat()
                if candle.start_time <= now:
                    candle.is_closed = candle.end_time <= now
                    grouped.setdefault(day, {})[candle.start_time] = candle
            for day, items in grouped.items():
                existing = {c.start_time: c for c in self.sessions.get(day, [])}
                existing.update(items)
                self.sessions[day] = sorted(existing.values(), key=lambda c: c.start_time)
                if day < self.date and not self.replay:
                    self._save_candles(day, self.sessions[day])
            if self.replay:
                return
            current = self.sessions.get(self.date, [])
            self._reset(self.date)
            for candle in current:
                if candle.end_time <= now:
                    candle.is_closed = True
                    self.candles.append(candle)
                    signal = self.strategy.on_candle_close(candle)
                    if signal:
                        self.signals.insert(0, signal)
                else:
                    candle.is_closed = False
                    self.active_candle = candle
                    self.strategy.on_tick(Tick(SYMBOL, candle.close, candle.start_time), candle)
                self.current_price = candle.close
            self._save_session()

    def _close_active(self):
        candle, self.active_candle = self.active_candle, None
        if candle is None:
            return
        candle.is_closed = True
        self.candles.append(candle)
        signal = self.strategy.on_candle_close(candle)
        if signal:
            self.signals.insert(0, signal)
        self._save_session()
        self.broadcast({"type": "candle_close", "candle": candle.to_dict(),
                        "orb_high": self.strategy.orb_high, "orb_low": self.strategy.orb_low,
                        "range_locked": self.strategy.range_locked, "market_status": self._market_status()})
        if signal:
            self.broadcast({"type": "signal", "signal": signal.to_dict()})
            print(signal.message, flush=True)

    def process_tick(self, price, vol=0, tick_time=None):
        now = in_ist(self.now())
        tick_time = in_ist(tick_time or now)
        with self.lock:
            self.advance_time(now)
            if not math.isfinite(price) or price <= 0:
                return False
            if tick_time.date().isoformat() != self.date or tick_time.weekday() >= 5:
                return False
            if not dtime(9, 15) <= tick_time.time() < dtime(15, 30):
                return False
            if not self.replay and tick_time > now + timedelta(seconds=60):
                return False
            if self.last_tick_time and tick_time < self.last_tick_time:
                return False
            if self.candles and tick_time < self.candles[-1].end_time:
                return False
            opening = tick_time.replace(hour=9, minute=15, second=0, microsecond=0)
            slot = int((tick_time - opening).total_seconds() // 900)
            start = opening + timedelta(minutes=15 * slot)
            if self.active_candle and start < self.active_candle.start_time:
                return False
            if self.active_candle and start != self.active_candle.start_time:
                self._close_active()
            if self.active_candle is None:
                self.active_candle = Candle(SYMBOL, price, price, price, price, vol,
                                            start, start + timedelta(minutes=15), slot=slot,
                                            is_complete=tick_time - start < timedelta(minutes=1))
            else:
                self.active_candle.update(price, vol)
            self.current_price = price
            self.last_tick_time = tick_time
            self.strategy.on_tick(Tick(SYMBOL, price, tick_time, vol), self.active_candle)
            self.broadcast({"type": "tick", "price": price,
                            "active_candle": self.active_candle.to_dict(),
                            "orb_high": self.strategy.orb_high, "orb_low": self.strategy.orb_low,
                            "range_locked": self.strategy.range_locked,
                            "market_status": self._market_status(), "last_tick_time": tick_time.isoformat()})
            return True


class MonitorHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, hub=None, **kwargs):
        self.hub = hub
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def log_message(self, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/history":
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
                        data = f"data: {json.dumps({'type': 'status', **self.hub.snapshot()})}\n\n"
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


def fetch_historical_15m_candles():
    import yfinance as yf
    try:
        history = yf.Ticker(SYMBOL).history(period="60d", interval="15m")
        now = datetime.now(IST)
        candles = []
        for timestamp, row in history.iterrows():
            start = in_ist(timestamp.to_pydatetime())
            if start.weekday() >= 5 or not dtime(9, 15) <= start.time() < dtime(15, 30):
                continue
            values = [float(row[key]) for key in ("Open", "High", "Low", "Close")]
            if not all(math.isfinite(value) for value in values):
                continue
            opening = start.replace(hour=9, minute=15, second=0, microsecond=0)
            slot = int((start - opening).total_seconds() // 900)
            end = start + timedelta(minutes=15)
            candles.append(Candle(SYMBOL, *values, int(row["Volume"] or 0), start, end, end <= now, slot))
        return candles
    except Exception as exc:
        print(f"History unavailable: {exc}", file=sys.stderr)
        return []


def process_quote(hub, message):
    """Use the exchange timestamp, never the arrival time of an old quote."""
    try:
        if message.get("id", SYMBOL) != SYMBOL:
            return
        timestamp = datetime.fromtimestamp(int(message["time"]) / 1000, tz=IST)
        # day_volume is cumulative; adding it to each tick would inflate volume.
        hub.process_tick(float(message["price"]), int(message.get("last_size") or 0), timestamp)
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        return


def run_direct_websocket(hub):
    import yfinance as yf
    while True:
        try:
            with yf.WebSocket(verbose=False) as websocket:
                websocket.subscribe([SYMBOL])
                websocket.listen(lambda message: process_quote(hub, message))
        except Exception as exc:
            print(f"Stream disconnected: {exc}. Retrying in 5 seconds.", file=sys.stderr)
            time.sleep(5)


def run_pipe_consumer(hub):
    hub.source_name = "Yahoo Finance · piped quotes"
    for line in sys.stdin:
        try:
            process_quote(hub, json.loads(line))
        except json.JSONDecodeError:
            continue


def run_replay_simulation(hub):
    now = in_ist(hub.now())
    sessions = {day: bars for day, bars in hub.sessions.items()
                if bars and (day < now.date().isoformat() or now.time() >= dtime(15, 30))}
    if not sessions:
        print("No completed session available to replay.", file=sys.stderr)
        return
    day = max(sessions)
    bars = list(sessions[day])
    with hub.lock:
        hub._reset(day)
        hub.source_name = f"Simulated replay · {day}"
        hub.broadcast({"type": "session", **hub._snapshot()})
    for candle in bars:
        for price, minutes in ((candle.open, 0), (candle.high, 4), (candle.low, 9), (candle.close, 14)):
            hub.process_tick(price, candle.volume // 4, candle.start_time + timedelta(minutes=minutes))
            time.sleep(0.08)
    with hub.lock:
        hub._close_active()
    print(f"Replay complete: {day}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--replay", action="store_true", help="Simulate the latest completed session")
    modes.add_argument("--pipe", action="store_true", help="Read timestamped quote JSON from standard input")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    hub = MonitorHub(cache_dir=ROOT / "data", replay=args.replay)
    hub.seed_historical_candles([])
    threading.Thread(target=run_http_server, args=(hub, args.port), daemon=True).start()
    stream = run_replay_simulation if args.replay else run_pipe_consumer if args.pipe else run_direct_websocket

    def start_feed():
        hub.seed_historical_candles(fetch_historical_15m_candles())
        hub.broadcast({"type": "init", **hub.snapshot()})
        stream(hub)

    threading.Thread(target=start_feed, daemon=True).start()
    try:
        while True:
            hub.advance_time()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
