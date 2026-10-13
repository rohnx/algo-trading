"""Instrument registry and shared intraday bars, sessions, and cache storage."""

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
import sys

from signals import Candle, IST

PRIMARY_SYMBOL = "^NSEI"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    label: str
    shoonya_token: str
    yahoo_symbol: str


INSTRUMENTS = {item.symbol: item for item in (
    Instrument(PRIMARY_SYMBOL, "NIFTY 50", "26000", "^NSEI"),
    Instrument("^NSEBANK", "BANKNIFTY", "26009", "^NSEBANK"),
    Instrument("NIFTY_MID_SELECT.NS", "MIDCAP SELECT", "26074", "NIFTY_MID_SELECT.NS"),
    Instrument("^NSMIDCP", "NIFTY NEXT 50", "26013", "^NSMIDCP"),
    # Yahoo's ^CNXFIN is the distinct Financial Services 25/50 index.
    Instrument("NIFTY_FIN_SERVICE.NS", "FIN NIFTY", "26037", "NIFTY_FIN_SERVICE.NS"),
)}
QUOTE_MAX_AGE = timedelta(seconds=90)
HISTORY_DAYS = 60


def in_ist(value):
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def history_cutoff(now):
    return (in_ist(now).date() - timedelta(days=HISTORY_DAYS)).isoformat()


def prune_session_files(cache_root, cutoff):
    if not cache_root:
        return
    for path in Path(cache_root).rglob("????-??-??.json"):
        try:
            if date.fromisoformat(path.stem).isoformat() != path.stem:
                continue
        except ValueError:
            continue
        if path.stem < cutoff and not path.is_symlink() and path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                print(f"Could not remove expired session {path}: {exc}", file=sys.stderr)


def candle_from_dict(value):
    return Candle(**{**value, "start_time": in_ist(datetime.fromisoformat(value["start_time"])),
                     "end_time": in_ist(datetime.fromisoformat(value["end_time"]))})


class InstrumentState:
    """One bar engine for each instrument; strategies belong to the monitor hub."""

    def __init__(self, instrument, *, now, cache_root=None, on_close=None):
        self.instrument, self.now = instrument, now
        self.cache_root = Path(cache_root) if cache_root else None
        self.cache_dir = None
        self._saved_content = {}
        self.on_close = on_close
        self.source_id = "yahoo"
        self.sessions, self.sessions_5m = {}, {}
        self.reset(in_ist(now()).date().isoformat())
        self._load_sessions()
        self._source_history_ready = not self.sessions_5m
        self._source_sessions = {self.source_id: (self.sessions, self.sessions_5m, self.cache_dir,
                                                  self._source_history_ready, self.date)}

    def reset(self, session_date, *, save=True):
        if save:
            self._save_session()
        self.date = session_date
        self.candles, self.candles_5m = [], []
        self.active_candle = self.active_candle_5m = None
        self.current_price = self.last_tick_time = self.history_coverage_time = self.session_open = None

    def activate_source(self, source):
        self._save_session()
        # Match the saved cache: never carry an unsaved forming live bar across sources.
        for sessions, candles, active in ((self.sessions, self.candles, self.active_candle),
                                         (self.sessions_5m, self.candles_5m, self.active_candle_5m)):
            if active and not candles:
                sessions.pop(self.date, None)
        self._source_sessions[self.source_id] = (self.sessions, self.sessions_5m, self.cache_dir,
                                                self._source_history_ready, self.date)
        self.reset(in_ist(self.now()).date().isoformat(), save=False)
        self.source_id = source
        saved = self._source_sessions.get(source)
        if saved is None:
            self.sessions, self.sessions_5m = {}, {}
            self._load_sessions()
            self.seed_historical_candles([], interval_minutes=5 if self.sessions_5m else 15)
        else:
            self.sessions, self.sessions_5m, self.cache_dir, self._source_history_ready, saved_date = saved
            # A former active parent needs rebuilding once its source crosses a date.
            if self.sessions_5m:
                same_day = self._source_history_ready and saved_date == self.date
                self._seed_5m([], session_date=self.date if same_day else None)
            else:
                self.seed_historical_candles([])
        self._source_history_ready = True
        self._source_sessions[source] = (self.sessions, self.sessions_5m, self.cache_dir, True, self.date)

    def _load_sessions(self):
        if not self.cache_root:
            return
        self.cache_dir = self.cache_root / self.source_id
        if self.instrument.symbol != PRIMARY_SYMBOL:
            self.cache_dir /= self.instrument.symbol.lstrip("^")
        locations = [(self.cache_dir, self.sessions), (self.cache_dir / "5m", self.sessions_5m)]
        if self.source_id == "yahoo" and self.instrument.symbol == PRIMARY_SYMBOL:
            # Existing date-only files belong to the original Yahoo monitor.
            locations.insert(0, (self.cache_root, self.sessions))
        cutoff = history_cutoff(self.now())
        for directory, sessions in locations:
            for path in sorted(directory.glob("????-??-??.json")):
                if path.stem < cutoff:
                    continue
                try:
                    content = path.read_text()
                    bars = [candle_from_dict(c) for c in json.loads(content)]
                    sessions[path.stem] = [c for c in bars if c.symbol == self.instrument.symbol]
                    self._saved_content[path] = hashlib.sha256(content.encode()).digest()
                except (OSError, ValueError, TypeError, KeyError):
                    print(f"Could not load saved session: {path.name}", file=sys.stderr)

    def prune_history(self, cutoff):
        for saved in self._source_sessions.values():
            for sessions in saved[:2]:
                for day in list(sessions):
                    if day < cutoff:
                        del sessions[day]
        self._saved_content = {path: digest for path, digest in self._saved_content.items()
                               if path.stem >= cutoff}

    def _save_session(self):
        if not hasattr(self, "candles"):
            return
        if self.candles:
            self.sessions[self.date] = list(self.candles)
            self._save_candles(self.date, self.candles)
        if self.candles_5m:
            self.sessions_5m[self.date] = list(self.candles_5m)
            self._save_candles(self.date, self.candles_5m, interval_minutes=5)

    def _save_candles(self, day, candles, interval_minutes=15):
        if self.cache_dir and day >= history_cutoff(self.now()):
            directory = self.cache_dir / "5m" if interval_minutes == 5 else self.cache_dir
            path = directory / f"{day}.json"
            temp = path.with_suffix(".tmp")
            try:
                content = json.dumps([c.to_dict() for c in candles])
                fingerprint = hashlib.sha256(content.encode()).digest()
                if self._saved_content.get(path) == fingerprint:
                    return
                directory.mkdir(parents=True, exist_ok=True)
                temp.write_text(content)
                temp.replace(path)
                # Failed writes must remain eligible for a later retry.
                self._saved_content[path] = fingerprint
            except OSError as exc:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                print(f"Could not save session: {exc}", file=sys.stderr)

    def seed_historical_candles(self, candles, interval_minutes=15):
        """Keep history separate; only today's bars may initialize the live chart."""
        if interval_minutes not in (5, 15):
            raise ValueError("Use 5- or 15-minute candles.")
        if interval_minutes == 5:
            return self._seed_5m(candles)
        now = in_ist(self.now())
        grouped = {}
        for candle in self._history_items(candles, interval_minutes, now):
            day = candle.start_time.date().isoformat()
            grouped.setdefault(day, {})[candle.start_time] = candle
        for day, items in grouped.items():
            existing = {c.start_time: c for c in self.sessions.get(day, [])}
            existing.update(items)
            self.sessions[day] = sorted(existing.values(), key=lambda c: c.start_time)
            if day < self.date:
                self._save_candles(day, self.sessions[day])
        current = self.sessions.get(self.date, [])
        current_five = self.sessions_5m.get(self.date, [])
        self.reset(self.date, save=False)
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
        if self.active_candle_5m:
            self.history_coverage_time = now
        self.session_open = self.opening_price(current_five or current)
        if current_five:
            self.current_price = current_five[-1].close
        self._save_session()

    def _history_items(self, candles, interval, now):
        cutoff = history_cutoff(now)
        for candle in candles:
            start, end = in_ist(candle.start_time), in_ist(candle.end_time)
            opening = start.replace(hour=9, minute=15, second=0, microsecond=0)
            if (candle.symbol != self.instrument.symbol or start > now
                    or start.date().isoformat() < cutoff
                    or end - start != timedelta(minutes=interval) or start.weekday() >= 5
                    or (start - opening).total_seconds() % (interval * 60)
                    or not dtime(9, 15) <= start.time() < end.time() <= dtime(15, 30)):
                continue
            if not all(math.isfinite(v) and v > 0 for v in (candle.open, candle.high, candle.low, candle.close)):
                continue
            yield replace(candle, start_time=start, end_time=end, is_closed=end <= now,
                          slot=int((start - opening).total_seconds() // (interval * 60)))

    def _seed_5m(self, candles, *, session_date=None):
        now = in_ist(self.now())
        grouped = {}
        for item in self._history_items(candles, 5, now):
            grouped.setdefault(item.start_time.date().isoformat(), {})[item.start_time] = item
        for day, items in grouped.items():
            existing = {c.start_time: c for c in self.sessions_5m.get(day, [])}
            existing.update(items)
            self.sessions_5m[day] = sorted(existing.values(), key=lambda c: c.start_time)
            if day < self.date:
                self._save_candles(day, self.sessions_5m[day], interval_minutes=5)

        # Aggregate real 5m bars; never manufacture children from a 15m candle.
        groups = {}
        histories = [self.sessions_5m.get(session_date, [])] if session_date else self.sessions_5m.values()
        for bars in histories:
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
            parents.append(Candle(self.instrument.symbol, parts[0].open, max(c.high for c in parts),
                                  min(c.low for c in parts), parts[-1].close,
                                  sum(c.volume for c in parts), start, end, end <= now,
                                  parts[0].slot // 3, complete, valid_close))
        self.seed_historical_candles(parents)
        if session_date is None:
            self._source_history_ready = True

    def _validate_close(self, candle):
        if (self.last_tick_time is None
                or not timedelta(0) <= candle.end_time - self.last_tick_time <= QUOTE_MAX_AGE):
            candle.is_complete = candle.close_is_valid = False

    def _close_elapsed(self, timestamp):
        intervals = [interval for interval, candle in (
            (5, self.active_candle_5m), (15, self.active_candle))
            if candle and timestamp >= candle.end_time]
        return self._close_bars(intervals)

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
        if closed:
            self._save_session()
            if self.on_close:
                self.on_close(closed)
        return closed

    def process_tick(self, price, vol=0, tick_time=None):
        now = in_ist(self.now())
        tick_time = in_ist(tick_time or now)
        if not math.isfinite(price) or price <= 0:
            return False
        if tick_time.date().isoformat() != self.date or tick_time.weekday() >= 5:
            return False
        if not dtime(9, 15) <= tick_time.time() < dtime(15, 30):
            return False
        if tick_time > now + timedelta(seconds=60):
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
        if coverage_time and tick_time - coverage_time > QUOTE_MAX_AGE:
            for candle in (self.active_candle, self.active_candle_5m):
                if candle:
                    candle.is_complete = candle.close_is_valid = False
        if self.active_candle is None:
            self.active_candle = Candle(self.instrument.symbol, price, price, price, price, vol,
                                        start, start + timedelta(minutes=15), slot=slot,
                                        is_complete=tick_time - start < timedelta(minutes=1))
        else:
            self.active_candle.update(price, vol)
        slot_5m = int((tick_time - opening).total_seconds() // 300)
        start_5m = opening + timedelta(minutes=5 * slot_5m)
        if self.active_candle_5m is None:
            complete_5m = tick_time - start_5m < timedelta(minutes=1)
            self.active_candle_5m = Candle(self.instrument.symbol, price, price, price, price, vol,
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
        if tick_time == opening:
            self.session_open = price
        return True

    @staticmethod
    def opening_price(candles):
        return next((c.open for c in candles if c.start_time.time() == dtime(9, 15)
                     and c.is_complete and c.close_is_valid and math.isfinite(c.open) and c.open > 0), None)

    def snapshot(self, now, feed_status, session_date=None, *, include_history=True):
        historical = session_date is not None and session_date != self.date
        candles = self.sessions.get(session_date, []) if historical else self.candles
        five = self.sessions_5m.get(session_date, []) if historical else self.candles_5m
        active = None if historical else self.active_candle
        active_five = None if historical else self.active_candle_5m
        price = (five or candles)[-1].close if historical and (five or candles) else self.current_price
        if historical and not (five or candles):
            price = None
        opening = self.opening_price(five or candles) if historical else self.session_open
        tick_time = None if historical else self.last_tick_time
        coverage_time = None if historical else self.history_coverage_time
        offmarket = historical or now.weekday() >= 5 or not dtime(9, 15) <= now.time() < dtime(15, 30)
        latest = (five or candles)[-1] if five or candles else None
        sample = active_five or (five[-1] if five else active or latest)
        valid_return = (tick_time and not offmarket) or (sample and sample.is_complete and sample.close_is_valid)
        if price is None or opening is None or not valid_return:
            status = "unavailable"
        elif offmarket:
            status = "closed"
        else:
            fresh = tick_time and timedelta(0) <= now - tick_time <= QUOTE_MAX_AGE
            status = "live" if fresh and feed_status not in ("connecting", "disconnected", "error") else "stale"
        snapshot = {"symbol": self.instrument.symbol, "label": self.instrument.label, "date": session_date or self.date,
                "active_candle": active.to_dict() if active else None,
                "active_candle_5m": active_five.to_dict() if active_five else None,
                "current_price": price, "last_tick_time": tick_time.isoformat() if tick_time else None,
                "history_coverage_time": coverage_time.isoformat() if coverage_time else None,
                "session_open": opening, "change_pct": (price / opening - 1) * 100
                if status in ("live", "closed") else None, "comparison_status": status}
        if include_history:
            snapshot.update(candles=[c.to_dict() for c in candles],
                            candles_5m=[c.to_dict() for c in five])
        return snapshot
