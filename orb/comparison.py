"""Close-only opening ranges for comparison lines, independent of NIFTY signals."""

from datetime import date, datetime, time, timedelta
import math

from signals import IST


def close_only_breakout(candles, session_date):
    """Evaluate real closed 5m bars against the first nine 5m closes.

    The range ends at 10:00. Its final child belongs to the range, so the
    first possible confirmation is the 10:00–10:05 close. A later inside
    close clears qualification, and a later invalid close clears confirmation.
    """
    result = {"basis": "close", "range_minutes": 45, "confirmation_minutes": 5,
              "range_locked": False, "range_high": None, "range_low": None,
              "direction": None, "confirmed_at": None, "price": None}
    try:
        opening = datetime.combine(date.fromisoformat(session_date), time(9, 15), IST)
    except (TypeError, ValueError):
        return result
    interval = timedelta(minutes=5)
    range_end = opening + timedelta(minutes=45)
    market_end = opening.replace(hour=15, minute=30)
    slots = {}
    for candle in candles:
        try:
            start = datetime.fromisoformat(candle["start_time"])
            end = datetime.fromisoformat(candle["end_time"])
            # Snapshots contain IST timestamps. Normalize other aware offsets
            # while allowing old cache timestamps that omitted their timezone.
            start = start.replace(tzinfo=IST) if start.tzinfo is None else start.astimezone(IST)
            end = end.replace(tzinfo=IST) if end.tzinfo is None else end.astimezone(IST)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if (not candle.get("is_closed") or end - start != interval
                or not opening <= start < end <= market_end
                or (start - opening) % interval):
            continue
        close = candle.get("close")
        valid = (candle.get("is_complete", True) and candle.get("close_is_valid", True)
                 and isinstance(close, (int, float)) and not isinstance(close, bool)
                 and math.isfinite(close) and close > 0)
        slots[start] = (end, close, valid)

    expected = [opening + slot * interval for slot in range(9)]
    if not all(start in slots and slots[start][2] for start in expected):
        return result
    closes = [slots[start][1] for start in expected]
    high, low = max(closes), min(closes)
    result.update(range_locked=True, range_high=high, range_low=low)
    confirmations = [start for start in slots if start >= range_end]
    if not confirmations:
        return result
    end, close, valid = slots[max(confirmations)]
    if valid:
        result.update(direction="BULLISH" if close > high else "BEARISH" if close < low else None,
                      confirmed_at=end.isoformat(), price=close)
    return result
