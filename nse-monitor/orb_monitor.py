#!/usr/bin/env python3
"""NIFTY chart with 15m/30m and 5m/45m opening-range confirmation."""

import argparse
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from dataclasses import dataclass, field, replace
from itertools import groupby
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from signals import Candle, IST, OpeningRangeBreakoutStrategy, Signal, SignalDirection, Tick

PORT = 8765
SYMBOL = "^NSEI"
ROOT = Path(__file__).resolve().parent
HISTORY_NOTE = "Yahoo supplies about 60 days of 15-minute history; saved sessions remain available locally."
SOURCE_NAMES = {"yahoo": "Yahoo Finance · ^NSEI", "shoonya": "Shoonya · NIFTY 50"}
QUOTE_MAX_AGE = timedelta(seconds=90)


def in_ist(value):
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def candle_from_dict(value):
    return Candle(**{**value, "start_time": in_ist(datetime.fromisoformat(value["start_time"])),
                     "end_time": in_ist(datetime.fromisoformat(value["end_time"]))})


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


class MonitorHub:
    def __init__(self, strategy=None, *, now=None, cache_dir=None, replay=False):
        self.breakouts = DualORBState(strategy)
        self.strategy = self.breakouts.strategies["15"]
        self.strategy_5m = self.breakouts.strategies["5"]
        self.now = now or (lambda: datetime.now(IST))
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_root = self.cache_dir
        self.replay = replay
        self.lock = threading.RLock()
        self.clients = set()
        self.sessions = {}
        self.sessions_5m = {}
        self.source_id = "yahoo"
        self.source_generation = 0
        self.source_epoch = uuid4().hex
        self.source_name = SOURCE_NAMES[self.source_id]
        self.feed_status = "waiting"
        self.feed_error = None
        self.feed_manager = None
        self.date = in_ist(self.now()).date().isoformat()
        self.candles = []
        self.active_candle = None
        self.candles_5m = []
        self.active_candle_5m = None
        self.current_price = None
        self.last_tick_time = None
        self.history_coverage_time = None
        self._load_sessions()

    @property
    def signals(self):
        return self.breakouts.signals

    @property
    def history_note(self):
        return HISTORY_NOTE if self.source_id == "yahoo" else (
            "Shoonya history depends on the available NIFTY intraday data; saved sessions remain available locally.")

    def _load_sessions(self):
        if not self.cache_root:
            return
        self.cache_dir = self.cache_root / self.source_id
        locations = [(self.cache_dir, self.sessions), (self.cache_dir / "5m", self.sessions_5m)]
        if self.source_id == "yahoo":
            # Existing date-only files belong to the original Yahoo monitor.
            locations.insert(0, (self.cache_root, self.sessions))
        for directory, sessions in locations:
            for path in sorted(directory.glob("????-??-??.json")):
                try:
                    sessions[path.stem] = [candle_from_dict(c) for c in json.loads(path.read_text())]
                except (OSError, ValueError, TypeError, KeyError):
                    print(f"Could not load saved session: {path.name}", file=sys.stderr)

    def _save_session(self):
        if self.replay:
            return
        if self.candles:
            self.sessions[self.date] = list(self.candles)
            self._save_candles(self.date, self.candles)
        if self.candles_5m:
            self.sessions_5m[self.date] = list(self.candles_5m)
            self._save_candles(self.date, self.candles_5m, interval_minutes=5)

    def _save_candles(self, day, candles, interval_minutes=15):
        if self.cache_dir:
            try:
                directory = self.cache_dir / "5m" if interval_minutes == 5 else self.cache_dir
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"{day}.json"
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps([c.to_dict() for c in candles]))
                temp.replace(path)
            except OSError as exc:
                print(f"Could not save session: {exc}", file=sys.stderr)

    def _reset(self, session_date):
        self._save_session()
        self.date = session_date
        self.breakouts.reset_day()
        self.candles = []
        self.active_candle = None
        self.candles_5m = []
        self.active_candle_5m = None
        self.current_price = None
        self.last_tick_time = None
        self.history_coverage_time = None

    def activate_source(self, source):
        if source not in SOURCE_NAMES:
            raise ValueError("Choose yahoo or shoonya.")
        with self.lock:
            self._save_session()
            self.sessions = {}
            self.sessions_5m = {}
            self.candles = []
            self.candles_5m = []
            self.active_candle = self.active_candle_5m = None
            self.source_id = source
            self.source_generation += 1
            self.source_name = SOURCE_NAMES[source]
            self.feed_status, self.feed_error = "connecting", None
            self._load_sessions()
            self._reset(in_ist(self.now()).date().isoformat())
            self.seed_historical_candles([], interval_minutes=5 if self.sessions_5m else 15)
            self.broadcast({"type": "session", **self._snapshot()})

    def set_feed_status(self, status, error=None):
        with self.lock:
            self.feed_status, self.feed_error = status, error
            if status in ("disconnected", "error"):
                for candle in (self.active_candle, self.active_candle_5m):
                    if candle:
                        candle.is_complete = candle.close_is_valid = False
            self.broadcast({"type": "status", **self._snapshot()})

    def advance_time(self, now=None):
        """Close elapsed bars and clear yesterday even if no quotes arrive."""
        if self.replay:
            return
        now = in_ist(now or self.now())
        with self.lock:
            self._close_elapsed(now)
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
        return "live" if fresh and self.feed_status not in ("connecting", "disconnected", "error") else "waiting"

    def _snapshot(self):
        return {
            "date": self.date,
            "today": in_ist(self.now()).date().isoformat(),
            "source": self.source_name,
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "source_epoch": self.source_epoch,
            "feed_status": self.feed_status,
            "feed_error": self.feed_error,
            "candles": [c.to_dict() for c in self.candles],
            "active_candle": self.active_candle.to_dict() if self.active_candle else None,
            "candles_5m": [c.to_dict() for c in self.candles_5m],
            "active_candle_5m": self.active_candle_5m.to_dict() if self.active_candle_5m else None,
            "current_price": self.current_price,
            "orb_high": self.strategy.orb_high,
            "orb_low": self.strategy.orb_low,
            "range_locked": self.strategy.range_locked,
            "setups": self.breakouts.get_setups(),
            "signals": [s.to_dict() for s in self.signals],
            "market_status": self._market_status(),
            "last_tick_time": self.last_tick_time.isoformat() if self.last_tick_time else None,
        }

    def snapshot(self, session_date=None):
        with self.lock:
            self.advance_time()
            if session_date is None or session_date == self.date:
                return self._snapshot()
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
                "feed_error": self.feed_error, "candles": [c.to_dict() for c in candles],
                "candles_5m": [c.to_dict() for c in five],
                "active_candle_5m": None,
                "active_candle": None, "current_price": candles[-1].close if candles else None,
                "orb_high": strategy.orb_high, "orb_low": strategy.orb_low,
                "range_locked": strategy.range_locked, "setups": breakouts.get_setups(),
                "signals": [s.to_dict() for s in breakouts.signals],
                "market_status": "closed", "last_tick_time": None,
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

    def seed_historical_candles(self, candles, interval_minutes=15):
        """Keep history separate; only today's bars may initialize the live chart."""
        if interval_minutes not in (5, 15):
            raise ValueError("Use 5- or 15-minute candles.")
        with self.lock:
            if interval_minutes == 5:
                return self._seed_5m(candles)
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
            current_five = self.sessions_5m.get(self.date, [])
            self._reset(self.date)
            for candle in current:
                if candle.end_time <= now:
                    candle.is_closed = True
                    self.candles.append(candle)
                else:
                    candle.is_closed = False
                    self.active_candle = candle
                    self.history_coverage_time = now
                self.current_price = candle.close
            self.candles_5m = [replace(c, is_closed=True) for c in current_five if c.end_time <= now]
            self.active_candle_5m = next((replace(c, is_closed=False) for c in current_five
                                         if c.start_time <= now < c.end_time), None)
            self.breakouts.feed_closed([(15, c) for c in self.candles]
                                       + [(5, c) for c in self.candles_5m])
            for strategy, active in ((self.strategy, self.active_candle),
                                     (self.strategy_5m, self.active_candle_5m)):
                if active:
                    strategy.on_tick(Tick(SYMBOL, active.close, active.start_time), active)
                    self.history_coverage_time = now
            if current_five:
                self.current_price = current_five[-1].close
            self._save_session()

    def _seed_5m(self, candles):
        now = in_ist(self.now())
        self.advance_time(now)
        grouped = {}
        for candle in candles:
            start, end = in_ist(candle.start_time), in_ist(candle.end_time)
            if (start > now or end - start != timedelta(minutes=5)
                    or start.weekday() >= 5 or start.minute % 5 or start.second or start.microsecond
                    or not dtime(9, 15) <= start.time() < end.time() <= dtime(15, 30)):
                continue
            if not all(math.isfinite(v) and v > 0 for v in (candle.open, candle.high, candle.low, candle.close)):
                continue
            opening = start.replace(hour=9, minute=15, second=0, microsecond=0)
            item = replace(candle, start_time=start, end_time=end, is_closed=end <= now,
                           slot=int((start - opening).total_seconds() // 300))
            grouped.setdefault(start.date().isoformat(), {})[start] = item
        for day, items in grouped.items():
            existing = {c.start_time: c for c in self.sessions_5m.get(day, [])}
            existing.update(items)
            self.sessions_5m[day] = sorted(existing.values(), key=lambda c: c.start_time)
            if day < self.date and not self.replay:
                self._save_candles(day, self.sessions_5m[day], interval_minutes=5)

        # Aggregate real 5m bars; never manufacture children from a 15m candle.
        groups = {}
        for bars in self.sessions_5m.values():
            for candle in bars:
                opening = candle.start_time.replace(hour=9, minute=15, second=0, microsecond=0)
                slot = int((candle.start_time - opening).total_seconds() // 900)
                start = opening + timedelta(minutes=15 * slot)
                groups.setdefault(start, []).append(candle)
        parents = []
        for start, parts in sorted(groups.items()):
            parts.sort(key=lambda c: c.start_time)
            end = start + timedelta(minutes=15)
            contiguous = parts[0].start_time == start and all(
                a.end_time == b.start_time for a, b in zip(parts, parts[1:]))
            covered = len(parts) == 3 if end <= now else parts[-1].end_time >= now
            complete = contiguous and all(c.is_complete for c in parts) and covered
            valid_close = all(c.close_is_valid for c in parts) and (end > now or len(parts) == 3)
            parents.append(Candle(SYMBOL, parts[0].open, max(c.high for c in parts),
                                  min(c.low for c in parts), parts[-1].close,
                                  sum(c.volume for c in parts), start, end, end <= now,
                                  parts[0].slot // 3, complete, valid_close))
        five_sessions = dict(self.sessions_5m)
        self.seed_historical_candles(parents)
        self.sessions_5m = five_sessions
        if not self.replay:
            current = self.sessions_5m.get(self.date, [])
            self.candles_5m = [replace(c, is_closed=True) for c in current if c.end_time <= now]
            self.active_candle_5m = next((replace(c, is_closed=False) for c in current
                                          if c.start_time <= now < c.end_time), None)
            self._save_session()

    def _validate_close(self, candle):
        if not self.replay and (self.last_tick_time is None
                or not timedelta(0) <= candle.end_time - self.last_tick_time <= QUOTE_MAX_AGE):
            candle.is_complete = candle.close_is_valid = False

    def _close_active_5m(self):
        self._close_bars([5])

    def _close_active(self):
        self._close_bars([15])

    def _close_elapsed(self, timestamp):
        intervals = [interval for interval, candle in (
            (5, self.active_candle_5m), (15, self.active_candle))
            if candle and timestamp >= candle.end_time]
        self._close_bars(intervals)

    def _breakout_fields(self):
        return {"orb_high": self.strategy.orb_high, "orb_low": self.strategy.orb_low,
                "range_locked": self.strategy.range_locked,
                "setups": self.breakouts.get_setups(),
                "signals": [signal.to_dict() for signal in self.signals]}

    def _close_bars(self, intervals):
        closed = []
        for interval in intervals:
            active_key = "active_candle_5m" if interval == 5 else "active_candle"
            candle = getattr(self, active_key)
            if candle is None:
                continue
            setattr(self, active_key, None)
            self._validate_close(candle)
            candle.is_closed = True
            (self.candles_5m if interval == 5 else self.candles).append(candle)
            closed.append((interval, candle))
        if not closed:
            return
        emitted = self.breakouts.feed_closed(closed)
        self._save_session()
        fields = self._breakout_fields()
        for interval, candle in closed:
            self.broadcast({"type": "candle_close", "interval_minutes": interval,
                            "candle": candle.to_dict(), **fields,
                            "market_status": self._market_status()})
        for signal in emitted:
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
            if self.candles_5m and tick_time < self.candles_5m[-1].end_time:
                return False
            opening = tick_time.replace(hour=9, minute=15, second=0, microsecond=0)
            slot = int((tick_time - opening).total_seconds() // 900)
            start = opening + timedelta(minutes=15 * slot)
            if self.active_candle and start < self.active_candle.start_time:
                return False
            self._close_elapsed(tick_time)
            coverage_time = self.last_tick_time or self.history_coverage_time
            if not self.replay and coverage_time and tick_time - coverage_time > QUOTE_MAX_AGE:
                for candle in (self.active_candle, self.active_candle_5m):
                    if candle:
                        candle.is_complete = candle.close_is_valid = False
            if self.active_candle is None:
                self.active_candle = Candle(SYMBOL, price, price, price, price, vol,
                                            start, start + timedelta(minutes=15), slot=slot,
                                            is_complete=tick_time - start < timedelta(minutes=1))
            else:
                self.active_candle.update(price, vol)
            slot_5m = int((tick_time - opening).total_seconds() // 300)
            start_5m = opening + timedelta(minutes=5 * slot_5m)
            if self.active_candle_5m is None:
                complete_5m = tick_time - start_5m < timedelta(minutes=1)
                self.active_candle_5m = Candle(SYMBOL, price, price, price, price, vol,
                    start_5m, start_5m + timedelta(minutes=5), slot=slot_5m,
                    is_complete=complete_5m)
                history_covers_start = (self.history_coverage_time
                    and self.history_coverage_time >= start_5m + timedelta(minutes=1))
                if not complete_5m and not history_covers_start:
                    self.active_candle.is_complete = False
            else:
                self.active_candle_5m.update(price, vol)
            self.current_price = price
            self.last_tick_time = tick_time
            if self.feed_status == "waiting":
                self.feed_status = "live"
            self.strategy.on_tick(Tick(SYMBOL, price, tick_time, vol), self.active_candle)
            self.strategy_5m.on_tick(Tick(SYMBOL, price, tick_time, vol), self.active_candle_5m)
            self.broadcast({"type": "tick", "price": price,
                            "active_candle": self.active_candle.to_dict(),
                            "active_candle_5m": self.active_candle_5m.to_dict(),
                            **self._breakout_fields(),
                            "market_status": self._market_status(), "last_tick_time": tick_time.isoformat()})
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
            return self._json({"message": "Source switching is unavailable in pipe or CLI replay mode."}, 409)
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
    native_five = hub.sessions_5m.get(day, [])
    bars = list(native_five or sessions[day])
    parent_parts = {}
    for candle in native_five:
        opening = candle.start_time.replace(hour=9, minute=15, second=0, microsecond=0)
        start = opening + timedelta(minutes=15 * int((candle.start_time - opening).total_seconds() // 900))
        parent_parts.setdefault(start, []).append(candle)
    parent_quality = {}
    for start, parts in parent_parts.items():
        parts.sort(key=lambda candle: candle.start_time)
        covered = len(parts) == 3 and all(
            candle.start_time == start + timedelta(minutes=5 * index)
            for index, candle in enumerate(parts))
        parent_quality[start] = (covered and all(c.is_complete for c in parts),
                                 covered and all(c.close_is_valid for c in parts))
    with hub.lock:
        hub._reset(day)
        hub.source_name = f"Simulated replay · {day}"
        hub.broadcast({"type": "session", **hub._snapshot()})
    for candle in bars:
        seconds = (candle.end_time - candle.start_time).total_seconds()
        for index, price in enumerate((candle.open, candle.high, candle.low, candle.close)):
            tick_time = candle.start_time + timedelta(seconds=(seconds - 1) * index / 3)
            if native_five:
                hub.process_tick(price, candle.volume // 4, tick_time)
                with hub.lock:
                    if hub.active_candle_5m:
                        hub.active_candle_5m.is_complete &= candle.is_complete
                        hub.active_candle_5m.close_is_valid &= candle.close_is_valid
                    if hub.active_candle:
                        complete, valid = parent_quality[hub.active_candle.start_time]
                        hub.active_candle.is_complete &= complete
                        hub.active_candle.close_is_valid &= valid
            else:
                # A legacy 15m session can replay its own bars without inventing
                # 5m children and consequently manufacturing the second leg.
                with hub.lock:
                    if hub.active_candle is None:
                        hub.active_candle = replace(candle, open=price, high=price, low=price,
                                                    close=price, volume=0, is_closed=False)
                    else:
                        hub.active_candle.update(price, candle.volume // 4)
                    hub.current_price, hub.last_tick_time = price, tick_time
                    hub.strategy.on_tick(Tick(SYMBOL, price, tick_time), hub.active_candle)
                    hub.broadcast({"type": "tick", "price": price,
                                   "active_candle": hub.active_candle.to_dict(),
                                   "active_candle_5m": None, **hub._breakout_fields(),
                                   "market_status": "replay", "last_tick_time": tick_time.isoformat()})
            time.sleep(0.08)
        with hub.lock:
            hub._close_elapsed(candle.end_time)
    with hub.lock:
        hub._close_elapsed(bars[-1].end_time)
    print(f"Replay complete: {day}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--replay", action="store_true", help="Simulate the latest completed session")
    modes.add_argument("--pipe", action="store_true", help="Read timestamped quote JSON from standard input")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--source", choices=tuple(SOURCE_NAMES), default="yahoo", help="Initial live source (default: yahoo)")
    args = parser.parse_args()
    hub = MonitorHub(cache_dir=ROOT / "data", replay=args.replay)
    hub.seed_historical_candles([])
    threading.Thread(target=run_http_server, args=(hub, args.port), daemon=True).start()
    if args.replay or args.pipe:
        stream = run_replay_simulation if args.replay else run_pipe_consumer

        def start_feed():
            hub.seed_historical_candles(fetch_historical_15m_candles())
            hub.broadcast({"type": "init", **hub.snapshot()})
            stream(hub)

        threading.Thread(target=start_feed, daemon=True).start()
    else:
        from feeds import FeedManager
        hub.feed_manager = FeedManager(hub)
        hub.feed_manager.select_source(args.source)
    try:
        while True:
            hub.advance_time()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        if hub.feed_manager:
            hub.feed_manager.stop()


if __name__ == "__main__":
    main()
