"""Opening ranges with independently configured closed-bar confirmation."""

from dataclasses import dataclass, fields
from datetime import datetime, time, timezone, timedelta
from enum import Enum

IST = timezone(timedelta(hours=5, minutes=30))


class SignalDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class Tick:
    symbol: str
    price: float
    time: datetime
    volume: int = 0


@dataclass
class Candle:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    start_time: datetime
    end_time: datetime
    is_closed: bool = False
    slot: int = 0
    is_complete: bool = True
    close_is_valid: bool = True

    def update(self, price: float, vol: int = 0):
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += vol

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            **{name: float(round(getattr(self, name), 2)) for name in ("open", "high", "low", "close")},
            "volume": int(self.volume),
            "start_time": self.start_time.isoformat(), "end_time": self.end_time.isoformat(),
            "is_closed": self.is_closed, "slot": int(self.slot),
            "is_complete": self.is_complete, "close_is_valid": self.close_is_valid,
        }


@dataclass
class Signal:
    strategy: str
    symbol: str
    direction: SignalDirection
    signal_type: str
    price: float
    level: float
    diff: float
    timestamp: datetime
    message: str
    stop_loss: float | None = None
    target_1r: float | None = None
    target_2r: float | None = None
    range_minutes: int | None = None
    confirmation_minutes: int | None = None
    setup_id: str | None = None

    def to_dict(self) -> dict:
        data = {field.name: getattr(self, field.name) for field in fields(Signal)}
        data.update(direction=self.direction.value, timestamp=self.timestamp.isoformat())
        for name in ("price", "level", "diff"):
            data[name] = float(round(data[name], 2))
        for name in ("stop_loss", "target_1r", "target_2r"):
            data[name] = float(round(data[name], 2)) if data[name] else None
        return data


def in_ist(value: datetime) -> datetime:
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


class OpeningRangeBreakoutStrategy:
    """Lock a complete opening range, then confirm on the configured closes."""

    def __init__(
        self,
        range_minutes: int = 30,
        buffer_pts: float = 0.0,
        one_signal_per_direction: bool = True,
        *,
        confirmation_minutes: int = 15,
    ):
        if (range_minutes, confirmation_minutes) not in {(30, 15), (45, 5), (45, 15)}:
            raise ValueError("Supported opening-range/confirmation pairs are 30/15, 45/5 and 45/15")
        self.range_minutes = range_minutes
        self.confirmation_minutes = confirmation_minutes
        self.setup_id = f"orb_{range_minutes}m_{confirmation_minutes}m"
        self.name = f"{range_minutes}m_ORB"
        self.buffer_pts = buffer_pts
        self.one_signal_per_direction = one_signal_per_direction

        self.reset_day()

    def reset_day(self):
        self.orb_high = None
        self.orb_low = None
        self.range_locked = False
        self.range_candles: list[Candle] = []
        self.session_date = None
        self.bullish_triggered = False
        self.bearish_triggered = False
        self.last_signal = None
        self.confirmation_direction: SignalDirection | None = None
        self.confirmed_at: datetime | None = None
        self.confirmation_price: float | None = None
        self._last_confirmation_end: datetime | None = None

    def _start_day(self, timestamp: datetime) -> bool:
        day = timestamp.date()
        if self.session_date and day < self.session_date:
            return False
        if day != self.session_date:
            self.reset_day()
            self.session_date = day
        return True

    def on_candle_close(self, candle: Candle) -> Signal | None:
        start, end = in_ist(candle.start_time), in_ist(candle.end_time)
        if not candle.is_closed:
            return None
        session_start = datetime.combine(start.date(), time(9, 15), IST)
        range_end = session_start + timedelta(minutes=self.range_minutes)
        interval = timedelta(minutes=self.confirmation_minutes)
        # Ignore off-session, incomplete and unaligned bars, including premarket.
        if (end - start != interval or start.date() != end.date()
                or not time(9, 15) <= start.time() < end.time() <= time(15, 30)
                or (start - session_start) % interval):
            return None
        if not candle.close_is_valid:
            if (self.session_date == start.date() and start >= range_end
                    and (self._last_confirmation_end is None or end >= self._last_confirmation_end)):
                self.confirmation_direction = None
                self.confirmed_at = None
                self.confirmation_price = None
                self._last_confirmation_end = end
            return None
        if not self._start_day(start):
            return None
        if end <= range_end:
            if self.range_locked or not candle.is_complete:
                return None
            self.range_candles = [c for c in self.range_candles if in_ist(c.start_time) != start]
            self.range_candles.append(candle)
            self.orb_high = max(c.high for c in self.range_candles)
            self.orb_low = min(c.low for c in self.range_candles)
            expected_starts = {
                session_start + slot * interval
                for slot in range(self.range_minutes // self.confirmation_minutes)
            }
            self.range_locked = {in_ist(c.start_time) for c in self.range_candles} == expected_starts
            return None
        if self.range_locked:
            if self._last_confirmation_end is not None and end < self._last_confirmation_end:
                return None
            self.confirmation_direction = self._direction(candle.close)
            self.confirmed_at = end
            self.confirmation_price = candle.close
            self._last_confirmation_end = end
            return self._check_breakout(candle.close, end, candle.symbol)
        return None

    def on_tick(self, tick: Tick, active_candle: Candle | None = None) -> None:
        timestamp = in_ist(tick.time)
        if not time(9, 15) <= timestamp.time() < time(15, 30):
            return
        if not self._start_day(timestamp) or self.range_locked:
            return
        session_start = datetime.combine(timestamp.date(), time(9, 15), IST)
        range_end = session_start + timedelta(minutes=self.range_minutes)
        if timestamp < range_end:
            high = low = tick.price
            if (active_candle and in_ist(active_candle.start_time).date() == timestamp.date()
                    and session_start <= in_ist(active_candle.start_time)
                    and in_ist(active_candle.end_time) <= range_end
                    and active_candle.end_time - active_candle.start_time
                    == timedelta(minutes=self.confirmation_minutes)
                    and in_ist(active_candle.start_time) <= timestamp < in_ist(active_candle.end_time)):
                high, low = max(high, active_candle.high), min(low, active_candle.low)
            self.orb_high = high if self.orb_high is None else max(self.orb_high, high)
            self.orb_low = low if self.orb_low is None else min(self.orb_low, low)

    def _direction(self, price):
        if price > self.orb_high + self.buffer_pts:
            return SignalDirection.BULLISH
        if price < self.orb_low - self.buffer_pts:
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    def _check_breakout(self, price: float, timestamp: datetime, symbol: str) -> Signal | None:
        direction = self._direction(price)
        if direction == SignalDirection.NEUTRAL:
            return None
        latch = f"{direction.value.lower()}_triggered"
        if self.one_signal_per_direction and getattr(self, latch):
            return None
        setattr(self, latch, True)
        bullish = direction == SignalDirection.BULLISH
        level, stop, sign = (self.orb_high, self.orb_low, 1) if bullish else (self.orb_low, self.orb_high, -1)
        span = self.orb_high - self.orb_low
        kind = "BREAKOUT" if sign > 0 else "BREAKDOWN"
        self.last_signal = Signal(
            strategy=self.name,
            symbol=symbol,
            direction=direction,
            signal_type=f"ORB_{self.range_minutes}M_{direction.value}_{kind}",
            price=price,
            level=level,
            diff=abs(price - level),
            timestamp=timestamp,
            message=f"{self.confirmation_minutes}-min close {price:.2f} {'above' if sign > 0 else 'below'} {self.range_minutes}-min opening range {level:.2f}",
            stop_loss=stop,
            target_1r=level + sign * span,
            target_2r=level + sign * 2 * span,
            range_minutes=self.range_minutes,
            confirmation_minutes=self.confirmation_minutes,
            setup_id=self.setup_id,
        )
        return self.last_signal

    def get_state(self) -> dict:
        state = {name: getattr(self, name) for name in (
            "range_minutes", "confirmation_minutes", "setup_id", "range_locked",
            "bullish_triggered", "bearish_triggered")}
        for name in ("orb_high", "orb_low", "confirmation_price"):
            value = getattr(self, name)
            state[name] = float(round(value, 2)) if value is not None else None
        span = self.orb_high - self.orb_low if self.orb_high is not None and self.orb_low is not None else 0.0
        return {**state, "strategy": self.name, "range_span": float(round(span, 2)),
                "confirmation_direction": self.confirmation_direction.value if self.confirmation_direction else None,
                "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
                "last_signal": self.last_signal.to_dict() if self.last_signal else None}
