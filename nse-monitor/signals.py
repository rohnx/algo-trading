"""45-minute opening range; breakouts confirmed by 15-minute closes."""

from dataclasses import dataclass, field
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
    raw: dict = field(default_factory=dict)


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

    def update(self, price: float, vol: int = 0):
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += vol

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "open": float(round(self.open, 2)),
            "high": float(round(self.high, 2)),
            "low": float(round(self.low, 2)),
            "close": float(round(self.close, 2)),
            "volume": int(self.volume),
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "is_closed": self.is_closed,
            "slot": int(self.slot),
            "is_complete": self.is_complete,
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

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "signal_type": self.signal_type,
            "price": float(round(self.price, 2)),
            "level": float(round(self.level, 2)),
            "diff": float(round(self.diff, 2)),
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "stop_loss": float(round(self.stop_loss, 2)) if self.stop_loss else None,
            "target_1r": float(round(self.target_1r, 2)) if self.target_1r else None,
            "target_2r": float(round(self.target_2r, 2)) if self.target_2r else None,
        }


def _ist(value: datetime) -> datetime:
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


class OpeningRangeBreakoutStrategy:
    """Lock 09:15–10:00 only after all three opening candles have closed."""
    name = "45m_ORB"

    def __init__(
        self,
        range_minutes: int = 45,
        buffer_pts: float = 0.0,
        one_signal_per_direction: bool = True,
    ):
        if range_minutes != 45:
            raise ValueError("The opening range is fixed at 45 minutes")
        self.range_minutes = range_minutes
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

    def _start_day(self, timestamp: datetime) -> bool:
        day = timestamp.date()
        if self.session_date and day < self.session_date:
            return False
        if day != self.session_date:
            self.reset_day()
            self.session_date = day
        return True

    def feed_historical_candle(self, candle: Candle) -> Signal | None:
        return self.on_candle_close(candle)

    def on_candle_close(self, candle: Candle) -> Signal | None:
        start, end = _ist(candle.start_time), _ist(candle.end_time)
        if not candle.is_closed:
            return None
        # Ignore off-session, incomplete and unaligned bars, including premarket.
        if (end - start != timedelta(minutes=15) or start.date() != end.date()
                or not time(9, 15) <= start.time() < end.time() <= time(15, 30)
                or start.minute % 15 or start.second or start.microsecond):
            return None
        if not self._start_day(start):
            return None
        if end.time() <= time(10):
            if self.range_locked or not candle.is_complete:
                return None
            self.range_candles = [c for c in self.range_candles if _ist(c.start_time) != start]
            self.range_candles.append(candle)
            self.orb_high = max(c.high for c in self.range_candles)
            self.orb_low = min(c.low for c in self.range_candles)
            self.range_locked = len(self.range_candles) == 3
            return None
        if self.range_locked:
            return self._check_breakout(candle.close, end, candle.symbol)
        return None

    def on_tick(self, tick: Tick, active_candle: Candle | None = None) -> None:
        timestamp = _ist(tick.time)
        if not time(9, 15) <= timestamp.time() < time(15, 30):
            return
        if not self._start_day(timestamp) or self.range_locked:
            return
        if time(9, 15) <= timestamp.time() < time(10):
            high = low = tick.price
            if (active_candle and _ist(active_candle.start_time).date() == timestamp.date()
                    and time(9, 15) <= _ist(active_candle.start_time).time() < time(10)
                    and _ist(active_candle.start_time) <= timestamp < _ist(active_candle.end_time)):
                high, low = max(high, active_candle.high), min(low, active_candle.low)
            self.orb_high = high if self.orb_high is None else max(self.orb_high, high)
            self.orb_low = low if self.orb_low is None else min(self.orb_low, low)

    def _check_breakout(self, price: float, timestamp: datetime, symbol: str) -> Signal | None:
        if price > self.orb_high + self.buffer_pts:
            direction, level, stop, sign = SignalDirection.BULLISH, self.orb_high, self.orb_low, 1
            if self.one_signal_per_direction and self.bullish_triggered:
                return None
            self.bullish_triggered = True
        elif price < self.orb_low - self.buffer_pts:
            direction, level, stop, sign = SignalDirection.BEARISH, self.orb_low, self.orb_high, -1
            if self.one_signal_per_direction and self.bearish_triggered:
                return None
            self.bearish_triggered = True
        else:
            return None
        span = self.orb_high - self.orb_low
        kind = "BREAKOUT" if sign > 0 else "BREAKDOWN"
        self.last_signal = Signal(
            strategy=self.name,
            symbol=symbol,
            direction=direction,
            signal_type=f"ORB_45M_{direction.value}_{kind}",
            price=price,
            level=level,
            diff=abs(price - level),
            timestamp=timestamp,
            message=f"15-min close {price:.2f} {'above' if sign > 0 else 'below'} opening range {level:.2f}",
            stop_loss=stop,
            target_1r=level + sign * span,
            target_2r=level + sign * 2 * span,
        )
        return self.last_signal

    def get_state(self) -> dict:
        span = (self.orb_high - self.orb_low) if (self.orb_high is not None and self.orb_low is not None) else 0.0
        return {
            "strategy": self.name,
            "range_minutes": self.range_minutes,
            "orb_high": float(round(self.orb_high, 2)) if self.orb_high is not None else None,
            "orb_low": float(round(self.orb_low, 2)) if self.orb_low is not None else None,
            "range_span": float(round(span, 2)),
            "range_locked": self.range_locked,
            "bullish_triggered": self.bullish_triggered,
            "bearish_triggered": self.bearish_triggered,
            "last_signal": self.last_signal.to_dict() if self.last_signal else None,
        }
