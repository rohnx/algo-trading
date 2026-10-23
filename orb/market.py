"""Instrument registry and shared intraday bars, sessions, and cache storage."""

import hashlib
import json
import math
from dataclasses import dataclass, replace
from itertools import chain
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
import sys

from .signals import Candle, Tick, in_ist

PRIMARY_SYMBOL = "^NSEI"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    label: str
    shoonya_token: str
    yahoo_symbol: str
    shoonya_exchange: str = "NSE"
    yahoo_delay_minutes: int = 0
    shoonya_history_fallback: str | None = None
    comparison_group: str = "derivatives"


INSTRUMENTS = {item.symbol: item for item in (
    Instrument(PRIMARY_SYMBOL, "NIFTY 50", "26000", "^NSEI", comparison_group="primary"),
    Instrument("^NSEBANK", "BANKNIFTY", "26009", "^NSEBANK"),
    Instrument("NIFTY_MID_SELECT.NS", "MIDCAP SELECT", "26074", "NIFTY_MID_SELECT.NS"),
    Instrument("^NSMIDCP", "NIFTY NEXT 50", "26013", "^NSMIDCP"),
    # Yahoo's ^CNXFIN is the distinct Financial Services 25/50 index.
    Instrument("NIFTY_FIN_SERVICE.NS", "FIN NIFTY", "26037", "NIFTY_FIN_SERVICE.NS"),
    # Shoonya GetIndexList uses token 75; Yahoo publishes this index 15 minutes late.
    Instrument("FOCIT.BO", "FOCIT", "75", "FOCIT.BO", shoonya_exchange="BSE",
               yahoo_delay_minutes=15, shoonya_history_fallback="yahoo"),
    # These NSE indices have native quotes and Yahoo history backfill.
    *(Instrument(symbol, label, token, symbol, shoonya_history_fallback="yahoo",
                 comparison_group="sectors") for symbol, label, token in (
        ("^CNXPHARMA", "PHARMA", "26023"),
        ("^CNXAUTO", "AUTO", "26029"),
        ("^CNXFMCG", "FMCG", "26021"),
        ("^CNXMETAL", "METAL", "26030"),
        ("^CNXPSUBANK", "PSU BANK", "26025"),
        ("^CNXREALTY", "REALTY", "26018"),
        ("NIFTY_CONSR_DURBL.NS", "CONSUMER DURABLES", "26070"),
        ("NIFTY_OIL_AND_GAS.NS", "OIL & GAS", "26071"),
        ("NIFTY_PVT_BANK.NS", "PRIVATE BANK", "26047"),
        ("^CNXENERGY", "ENERGY", "26020"),
        ("NIFTY_IND_DEFENCE.NS", "INDIA DEFENCE", "26092"),
        ("^CNXINFRA", "INFRASTRUCTURE", "26019"),
        ("^CNXPSE", "PSE", "26024"),
        ("^CNXCMDT", "COMMODITIES", "26035"),
        ("NIFTY_CPSE.NS", "CPSE", "26041"),
    )),
)}
QUOTE_MAX_AGE = timedelta(seconds=90)
HISTORY_DAYS = 60


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


def aggregate_candles(candles, now):
    """Aggregate one instrument's real 5m bars, preserving gaps and validity."""
    groups = {}
    for candle in candles:
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
        parents.append(Candle(parts[0].symbol, parts[0].open, max(c.high for c in parts),
                              min(c.low for c in parts), parts[-1].close,
                              sum(c.volume for c in parts), start, end, end <= now,
                              parts[0].slot // 3, complete, valid_close))
    return parents


class _SessionBars:
    """Native bars and saved provider sessions; never a composed display view."""

    def __init__(self, instrument, *, now, cache_root=None):
        self.instrument, self.now = instrument, now
        self.cache_root = Path(cache_root) if cache_root else None
        self.cache_dir = None
        self._saved_content = {}
        self.source_id = "yahoo"
        self._source_opens = {self.source_id: {}}
        self.sessions, self.sessions_5m = {}, {}
        self._reset(in_ist(now()).date().isoformat(), save=False)
        self._load_sessions()
        self._source_history_ready = not self.sessions_5m
        self._source_sessions = {self.source_id: (self.sessions, self.sessions_5m, self.cache_dir,
                                                  self._source_history_ready, self.date)}

    @property
    def quote_delay(self):
        return timedelta(minutes=self.instrument.yahoo_delay_minutes if self.source_id == "yahoo" else 0)

    def market_time(self, now=None):
        """The source's current market clock, distinct from arrival and retention time."""
        return in_ist(now if now is not None else self.now()) - self.quote_delay

    @property
    def session_opens(self):
        return self._source_opens.setdefault(self.source_id, {})

    def _reset(self, session_date, *, save=True):
        if save:
            self._save_session()
        self.date = session_date
        self.candles, self.candles_5m = [], []
        self.active_candle = self.active_candle_5m = None
        self.current_price = self.last_tick_time = self.history_coverage_time = None
        self.session_open = self.session_opens.get(session_date)

    def activate_source(self, source):
        self._save_session()
        # Match the saved cache: never carry an unsaved forming live bar across sources.
        for _, _, sessions, candles, active in self._bar_series():
            if active and not candles:
                sessions.pop(self.date, None)
        self._source_sessions[self.source_id] = (self.sessions, self.sessions_5m, self.cache_dir,
                                                self._source_history_ready, self.date)
        self._reset(in_ist(self.now()).date().isoformat(), save=False)
        self.source_id = source
        saved = self._source_sessions.get(source)
        if saved is None:
            self.sessions, self.sessions_5m = {}, {}
            self._load_sessions()
        else:
            self.sessions, self.sessions_5m, self.cache_dir, self._source_history_ready, saved_date = saved
        if self.sessions_5m:
            # Rebuild old active parents, but do not revisit past sessions on a same-day switch.
            same_day = saved is not None and self._source_history_ready and saved_date == self.date
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
        locations = [(self.cache_dir, self.sessions), (self.cache_dir / "5m", self.sessions_5m),
                     (self.cache_dir / "opens", None)]
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
                    values = json.loads(content)
                    if sessions is not None:
                        bars = map(candle_from_dict, values)
                        sessions[path.stem] = [c for c in bars if c.symbol == self.instrument.symbol]
                        self._saved_content[path] = hashlib.sha256(content.encode()).digest()
                    else:
                        opening = float(values["session_open"])
                        timestamp = in_ist(datetime.fromisoformat(values["timestamp"]))
                        if (values["symbol"] == self.instrument.symbol and values["source_id"] == self.source_id
                                and timestamp.date().isoformat() == path.stem
                                and dtime(9, 15) <= timestamp.time() < dtime(15, 30)
                                and math.isfinite(opening) and opening > 0):
                            self.session_opens[path.stem] = opening
                except (OSError, ValueError, TypeError, KeyError, OverflowError):
                    kind = "session" if sessions is not None else "opening"
                    print(f"Could not load saved {kind}: {path.name}", file=sys.stderr)
        self.session_open = (self.opening_price(self.sessions_5m.get(self.date, []))
                             or self.opening_price(self.sessions.get(self.date, []))
                             or self.session_opens.get(self.date))

    def prune_history(self, cutoff):
        histories = chain.from_iterable(saved[:2] for saved in self._source_sessions.values())
        for sessions in chain(histories, self._source_opens.values()):
            for day in list(sessions):
                if day < cutoff:
                    del sessions[day]
        self._saved_content = {path: digest for path, digest in self._saved_content.items()
                               if path.stem >= cutoff}

    def _bar_series(self, intervals=(15, 5)):
        for interval in intervals:
            suffix = "_5m" if interval == 5 else ""
            yield (interval, suffix, getattr(self, f"sessions{suffix}"),
                   getattr(self, f"candles{suffix}"), getattr(self, f"active_candle{suffix}"))

    def _view_bars(self, day):
        return {interval: sessions.get(day, []) if day != self.date else
                [*candles, *([active] if active else [])]
                for interval, _, sessions, candles, active in self._bar_series()}

    def _save_session(self):
        for interval, _, sessions, candles, _ in self._bar_series():
            if candles:
                sessions[self.date] = list(candles)
                self._save_candles(self.date, candles, interval)

    def _write_json(self, path, values, *, error, track_content=False):
        temp = path.with_suffix(".tmp")
        try:
            content = json.dumps(values)
            fingerprint = hashlib.sha256(content.encode()).digest()
            if track_content and self._saved_content.get(path) == fingerprint:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(content)
            temp.replace(path)
            if track_content:
                self._saved_content[path] = fingerprint
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            print(f"{error}: {exc}", file=sys.stderr)

    def _save_candles(self, day, candles, interval_minutes=15):
        if self.cache_dir and day >= history_cutoff(self.now()):
            directory = self.cache_dir / "5m" if interval_minutes == 5 else self.cache_dir
            self._write_json(directory / f"{day}.json", [c.to_dict() for c in candles],
                             error="Could not save session", track_content=True)

    def _merge_history(self, candles, interval, now):
        sessions = self.sessions_5m if interval == 5 else self.sessions
        grouped = {}
        for candle in self._history_items(candles, interval, now):
            grouped.setdefault(candle.start_time.date().isoformat(), {})[candle.start_time] = candle
        for day, items in grouped.items():
            existing = {c.start_time: c for c in sessions.get(day, [])}
            existing.update(items)
            sessions[day] = sorted(existing.values(), key=lambda c: c.start_time)
            if day < self.date:
                self._save_candles(day, sessions[day], interval)

    def seed_historical_candles(self, candles, interval_minutes=15):
        """Keep history separate; only today's bars may initialize the live chart."""
        if interval_minutes not in (5, 15):
            raise ValueError("Use 5- or 15-minute candles.")
        if interval_minutes == 5:
            return self._seed_5m(candles)
        now = self.market_time()
        self._merge_history(candles, interval_minutes, now)
        current = self.sessions.get(self.date, [])
        current_five = self.sessions_5m.get(self.date, [])
        self._reset(self.date, save=False)
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
        self.session_open = (self.opening_price(current_five) or self.opening_price(current)
                             or self.session_opens.get(self.date))
        if current_five:
            self.current_price = current_five[-1].close
        self._save_session()

    def _history_items(self, candles, interval, now):
        cutoff = history_cutoff(self.now())
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
        now = self.market_time()
        self._merge_history(candles, 5, now)

        # Aggregate real 5m bars; never manufacture children from a 15m candle.
        histories = [self.sessions_5m.get(session_date, [])] if session_date else self.sessions_5m.values()
        parents = aggregate_candles((candle for bars in histories for candle in bars), now)
        self.seed_historical_candles(parents)
        if session_date is None:
            self._source_history_ready = True

    def _validate_close(self, candle):
        if (self.last_tick_time is None
                or not timedelta(0) <= candle.end_time - self.last_tick_time <= QUOTE_MAX_AGE):
            candle.is_complete = candle.close_is_valid = False

    def invalidate_active_bars(self):
        for candle in (self.active_candle, self.active_candle_5m):
            if candle:
                candle.is_complete = candle.close_is_valid = False

    def advance_time(self, now=None):
        """Persist elapsed closes using this provider's source clock."""
        return self._close_elapsed(self.market_time(now))

    def _close_elapsed(self, timestamp):
        closed = []
        for interval, suffix, _, candles, candle in self._bar_series((5, 15)):
            if candle is None or timestamp < candle.end_time:
                continue
            setattr(self, f"active_candle{suffix}", None)
            self._validate_close(candle)
            candle.is_closed = True
            candles.append(candle)
            closed.append((interval, candle))
        if closed:
            self._save_session()
        return closed

    def begin_tick(self, price, vol=0, tick_time=None):
        """Admit a quote and persist elapsed closes before applying its price.

        The caller handles the closed batch before apply_tick commits the quote.
        Rejected quotes have no tick-clock effects.
        """
        now = self.market_time()
        tick_time = in_ist(tick_time or now)
        if not math.isfinite(price) or price <= 0:
            return None
        if tick_time.date().isoformat() != self.date or tick_time.weekday() >= 5:
            return None
        if not dtime(9, 15) <= tick_time.time() < dtime(15, 30):
            return None
        if tick_time > now + timedelta(seconds=60):
            return None
        if self.last_tick_time and tick_time < self.last_tick_time:
            return None
        if self.candles and tick_time < self.candles[-1].end_time:
            return None
        if self.candles_5m and tick_time < self.candles_5m[-1].end_time:
            return None
        opening = tick_time.replace(hour=9, minute=15, second=0, microsecond=0)
        slot = int((tick_time - opening).total_seconds() // 900)
        start = opening + timedelta(minutes=15 * slot)
        if self.active_candle and start < self.active_candle.start_time:
            return None
        tick = Tick(self.instrument.symbol, price, tick_time, vol)
        return tick, self._close_elapsed(tick_time)

    def apply_tick(self, tick):
        """Commit an admitted quote after its closed batch has been handled."""
        price, vol, tick_time = tick.price, tick.volume, tick.time
        opening = tick_time.replace(hour=9, minute=15, second=0, microsecond=0)
        coverage_time = self.last_tick_time or self.history_coverage_time
        if coverage_time and tick_time - coverage_time > QUOTE_MAX_AGE:
            self.invalidate_active_bars()
        for interval, suffix, _, _, active in self._bar_series():
            slot = int((tick_time - opening).total_seconds() // (interval * 60))
            start = opening + timedelta(minutes=interval * slot)
            if active is not None:
                active.update(price, vol)
                continue
            complete = tick_time - start < timedelta(minutes=1)
            candle = Candle(self.instrument.symbol, price, price, price, price, vol,
                            start, start + timedelta(minutes=interval), slot=slot,
                            is_complete=complete)
            setattr(self, f"active_candle{suffix}", candle)
            history_covers_start = (self.history_coverage_time
                and self.history_coverage_time >= start + timedelta(minutes=1))
            if interval == 5 and not complete and not history_covers_start:
                self.active_candle.is_complete = False
        self.current_price = price
        self.last_tick_time = tick_time
        if tick_time == opening:
            self.session_open = price

    def process_tick(self, price, vol=0, tick_time=None):
        pending = self.begin_tick(price, vol, tick_time)
        if pending is None:
            return False
        tick, _ = pending
        self.apply_tick(tick)
        return True

    def set_session_open(self, opening, timestamp):
        """Accept an upstream session open without inventing an opening candle."""
        try:
            opening = float(opening)
            timestamp = in_ist(timestamp)
        except (TypeError, ValueError, AttributeError, OverflowError):
            return False
        actual_now = in_ist(self.now())
        if (not math.isfinite(opening) or opening <= 0 or self.session_open is not None
                or timestamp.date() != actual_now.date() or timestamp.date().isoformat() != self.date
                or not dtime(9, 15) <= timestamp.time() < dtime(15, 30)
                or not timedelta(0) <= self.market_time(actual_now) - timestamp <= QUOTE_MAX_AGE):
            return False
        self.session_open = opening
        self.session_opens[self.date] = opening
        if self.cache_dir:
            self._write_json(self.cache_dir / "opens" / f"{self.date}.json", {
                "symbol": self.instrument.symbol, "source_id": self.source_id,
                "session_open": opening, "timestamp": timestamp.isoformat()},
                error=f"Could not save session opening {self.date}")
        return True

    @staticmethod
    def opening_price(candles):
        return next((c.open for c in candles if c.start_time.time() == dtime(9, 15)
                     and c.is_complete and c.close_is_valid and math.isfinite(c.open) and c.open > 0), None)

    def _snapshot(self, now, feed_status, session_date=None, *, include_history=True):
        now = self.market_time(now)
        historical = session_date is not None and session_date != self.date
        candles = self.sessions.get(session_date, []) if historical else self.candles
        five = self.sessions_5m.get(session_date, []) if historical else self.candles_5m
        active = None if historical else self.active_candle
        active_five = None if historical else self.active_candle_5m
        latest = (five or candles)[-1] if five or candles else None
        if historical:
            price = latest.close if latest else None
            opening = self.opening_price(five) or self.opening_price(candles) or self.session_opens.get(session_date)
            tick_time = coverage_time = None
        else:
            price, opening = self.current_price, self.session_open
            tick_time, coverage_time = self.last_tick_time, self.history_coverage_time
        offmarket = historical or now.weekday() >= 5 or not dtime(9, 15) <= now.time() < dtime(15, 30)
        sample = active_five or (five[-1] if five else active or latest)
        valid_return = (tick_time and not offmarket) or (sample and sample.is_complete and sample.close_is_valid)
        if price is None or opening is None or not valid_return:
            status = "unavailable"
        elif offmarket:
            status = "closed"
        elif (tick_time and timedelta(0) <= now - tick_time <= QUOTE_MAX_AGE
              and feed_status not in ("connecting", "disconnected", "error")):
            status = "delayed" if self.quote_delay else "live"
        else:
            status = "stale"
        snapshot = {"symbol": self.instrument.symbol, "label": self.instrument.label,
                "comparison_group": self.instrument.comparison_group, "date": session_date or self.date,
                "active_candle": active.to_dict() if active else None,
                "active_candle_5m": active_five.to_dict() if active_five else None,
                "current_price": price, "last_tick_time": tick_time.isoformat() if tick_time else None,
                "history_coverage_time": coverage_time.isoformat() if coverage_time else None,
                "session_open": opening, "change_pct": (price / opening - 1) * 100
                if status in ("live", "delayed", "closed") else None, "comparison_status": status,
                "quote_delay_seconds": int(self.quote_delay.total_seconds())}
        if include_history:
            snapshot.update(candles=[c.to_dict() for c in candles],
                            candles_5m=[c.to_dict() for c in five])
        return snapshot


class InstrumentState(_SessionBars):
    """Own one instrument's native sessions, fallback history and final view."""

    def __init__(self, instrument, *, now, cache_root=None):
        super().__init__(instrument, now=now, cache_root=cache_root)
        self._fallback = None
        if instrument.shoonya_history_fallback == "yahoo":
            self._fallback = _SessionBars(instrument, now=now, cache_root=cache_root)
            self._fallback.seed_historical_candles([], interval_minutes=5)

    @staticmethod
    def _merge_sessions(target, sessions, *, clock=None, replace_valid=False):
        def valid(bar):
            return bar and bar.is_closed and bar.is_complete and bar.close_is_valid

        for day, bars in sessions.items():
            merged = {bar.start_time: bar for bar in target.get(day, [])}
            for bar in bars:
                if clock is not None and (not valid(bar) or bar.end_time > clock):
                    continue
                if not valid(merged.get(bar.start_time)) or replace_valid and valid(bar):
                    merged[bar.start_time] = replace(bar)
            if merged:
                target[day] = sorted(merged.values(), key=lambda bar: bar.start_time)

    def activate_source(self, source):
        fallback = self._fallback
        if fallback and source == "yahoo" and (saved := self._source_sessions.get("yahoo")):
            for target, sessions in zip(saved[:2], (fallback.sessions, fallback.sessions_5m)):
                self._merge_sessions(target, sessions, clock=fallback.market_time())
        super().activate_source(source)
        if fallback and source == "shoonya":
            if saved := self._source_sessions.get("yahoo"):
                for target, sessions in zip((fallback.sessions, fallback.sessions_5m), saved[:2]):
                    self._merge_sessions(target, sessions, replace_valid=True)
            fallback._reset(self.date, save=False)
            fallback._seed_5m([], session_date=self.date)

    def reset(self, session_date, *, save=True):
        self._reset(session_date, save=save)
        if self._fallback:
            # An inactive fallback must not overwrite newer native Yahoo history.
            self._fallback._reset(session_date, save=False)

    def prune_history(self, cutoff):
        super().prune_history(cutoff)
        if self._fallback:
            self._fallback.prune_history(cutoff)

    def seed_fallback_history(self, candles, *, history_source_id="yahoo"):
        fallback = self._fallback
        if self.source_id != "shoonya" or fallback is None or history_source_id != "yahoo":
            return False
        clock = fallback.market_time()
        completed = [c for c in candles if c.is_closed and c.end_time <= clock]
        fallback.seed_historical_candles(completed, interval_minutes=5)
        return bool(completed)

    def snapshot(self, now, feed_status, session_date=None, *, include_history=True):
        result = self._snapshot(now, feed_status, session_date, include_history=include_history)
        result.update(history_source_id=self.source_id, history_fallback=False,
                      history_delay_seconds=int(self.quote_delay.total_seconds()),
                      quote_source_id=self.source_id)
        if self.source_id == "shoonya" and self._fallback:
            self._compose_history(result, in_ist(now), feed_status, include_history)
        return result

    def _compose_history(self, result, now, feed_status, include_history):
        """Build a display-only view; never mutate or persist composed bars."""
        def valid(bar):
            return bool(bar and bar.is_complete and bar.close_is_valid)

        def positive(value):
            return isinstance(value, (int, float)) and math.isfinite(value) and value > 0

        day, fallback = result["date"], self._fallback
        native, backfill = self._view_bars(day), fallback._view_bars(day)
        delay = int(fallback.quote_delay.total_seconds())
        # Even cached history must respect Yahoo's delayed source clock.
        yahoo = {c.start_time: c for c in backfill[5] if c.is_closed and valid(c)
                 and c.end_time <= fallback.market_time(now) and c.start_time.date().isoformat() == day}
        chosen = {c.start_time: c for c in native[5]}
        repairs = {start: c for start, c in yahoo.items() if not valid(chosen.get(start))}
        if not repairs:
            return
        chosen.update(repairs)
        five = sorted(chosen.values(), key=lambda c: c.start_time)
        active_five = next((c for c in reversed(five) if not c.is_closed), None)
        parents = {c.start_time: c for c in native[15]}
        # Composition historically aggregates displayed child prices/volumes.
        children = (replace(c, **{name: float(round(getattr(c, name), 2))
                    for name in ("open", "high", "low", "close")}, volume=int(c.volume)) for c in five)
        for parent in aggregate_candles(children, now):
            existing = parents.get(parent.start_time)
            # A valid native 15m bar can exist without its saved 5m children.
            if existing is None or not valid(existing) or valid(parent):
                parents[parent.start_time] = parent
        native_active = self.active_candle if day == self.date else None
        if native_active and active_five:
            composed = parents.get(native_active.start_time)
            if valid(native_active) or not valid(composed):
                parents[native_active.start_time] = native_active
        parents = sorted(parents.values(), key=lambda c: c.start_time)
        active = next((c for c in reversed(parents) if not c.is_closed), None)
        if include_history:
            result.update(candles=[c.to_dict() for c in parents if c.is_closed],
                          candles_5m=[c.to_dict() for c in five if c.is_closed])
        result.update(active_candle=active.to_dict() if active else None,
                      active_candle_5m=active_five.to_dict() if active_five else None,
                      history_source_id="yahoo", history_fallback=True, history_delay_seconds=delay)

        # Admission uses the source clock; display freshness uses actual time.
        historical = day != now.date().isoformat()
        offmarket = historical or now.weekday() >= 5 or not dtime(9, 15) <= now.time() < dtime(15, 30)
        native_tick = self.last_tick_time if not historical and day == self.date else None
        samples = native[5] or native[15]
        latest = samples[-1] if samples else None
        coverage = self.history_coverage_time if day == self.date else None
        native_time = native_tick or coverage or (latest.end_time if latest else None)
        if latest and latest.is_closed and valid(latest):
            native_time = max(native_time, latest.end_time) if native_time else latest.end_time
        native_valid = positive(result["current_price"]) and bool(native_tick or valid(latest))
        yahoo_last = max(yahoo.values(), key=lambda c: c.end_time)
        use_yahoo = not native_valid or native_time is None or yahoo_last.end_time > native_time
        opening = result["session_open"]
        if not positive(opening):
            opening = (fallback.session_open if day == fallback.date else
                       fallback.opening_price(backfill[5]) or fallback.opening_price(backfill[15])
                       or fallback.session_opens.get(day))
        result["session_open"] = opening
        if use_yahoo:
            result.update(current_price=float(round(yahoo_last.close, 2)), last_tick_time=None,
                          history_coverage_time=None, quote_source_id="yahoo", quote_delay_seconds=delay)

        price = result["current_price"]
        sample = yahoo_last if use_yahoo else latest
        valid_return = bool(native_tick and not offmarket and not use_yahoo) or valid(sample)
        if not positive(price) or not positive(opening) or not valid_return:
            status = "unavailable"
        elif offmarket:
            status = "closed"
        elif (not use_yahoo and native_tick and timedelta(0) <= now - native_tick <= QUOTE_MAX_AGE
              and feed_status not in ("connecting", "disconnected", "error")):
            status = "live"
        else:
            status = "stale"
        result.update(comparison_status=status,
                      change_pct=(price / opening - 1) * 100 if status in ("live", "closed") else None)
