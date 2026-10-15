"""Compose comparison history without changing either provider's bar engine."""

from datetime import datetime, time as dtime, timedelta
import math

from market import QUOTE_MAX_AGE, in_ist


def _time(value):
    try:
        return in_ist(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def _valid(bar):
    return bool(bar and bar.get("is_complete", True) and bar.get("close_is_valid", True))


def _positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _bars(snapshot, interval):
    key = "candles_5m" if interval == 5 else "candles"
    active_key = "active_candle_5m" if interval == 5 else "active_candle"
    return [*snapshot.get(key, []), *([snapshot[active_key]] if snapshot.get(active_key) else [])]


def _parents(five, now):
    """Aggregate actual children; a missing child remains an explicit gap."""
    groups = {}
    for bar in five:
        start = _time(bar["start_time"])
        opening = start.replace(hour=9, minute=15, second=0, microsecond=0)
        slot = int((start - opening).total_seconds() // 900)
        groups.setdefault(opening + timedelta(minutes=slot * 15), []).append(bar)
    parents = []
    for start, parts in sorted(groups.items()):
        parts.sort(key=lambda bar: bar["start_time"])
        end = start + timedelta(minutes=15)
        contiguous = _time(parts[0]["start_time"]) == start and all(
            first["end_time"] == second["start_time"] for first, second in zip(parts, parts[1:]))
        closed = end <= now
        covered = len(parts) == 3 if closed else _time(parts[-1]["end_time"]) >= now
        parents.append({**parts[0], "open": parts[0]["open"],
            "high": max(bar["high"] for bar in parts), "low": min(bar["low"] for bar in parts),
            "close": parts[-1]["close"], "volume": sum(bar["volume"] for bar in parts),
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "slot": parts[0]["slot"] // 3, "is_closed": closed,
            "is_complete": contiguous and covered and all(bar.get("is_complete", True) for bar in parts),
            "close_is_valid": all(bar.get("close_is_valid", True) for bar in parts)
                and (not closed or len(parts) == 3)})
    return parents


def merge_fallback_history(native, fallback, *, now, feed_status):
    """Use completed Yahoo history behind native quotes, retaining provenance.

    Both inputs are ordinary full InstrumentState snapshots. The result is a
    display view only: it must never be seeded or persisted as native history.
    """
    now = in_ist(now)
    result = {**native, "history_source_id": "shoonya", "history_fallback": False,
              "history_delay_seconds": 0, "quote_source_id": "shoonya"}
    day = native["date"]
    delay = fallback.get("quote_delay_seconds", 0)
    source_clock = now - timedelta(seconds=delay)
    # Recheck the source clock even for cached data. A delayed forming candle
    # must never become a supposedly complete Shoonya candle on composition.
    yahoo = {bar["start_time"]: dict(bar) for bar in fallback.get("candles_5m", [])
             if bar.get("is_closed") and _valid(bar) and _time(bar["end_time"]) <= source_clock
             and _time(bar["start_time"]).date().isoformat() == day}
    if not yahoo:
        return result
    chosen = {start: (bar, "yahoo") for start, bar in yahoo.items()}
    for bar in _bars(native, 5):
        start = bar["start_time"]
        if start not in chosen or _valid(bar):
            chosen[start] = (dict(bar), "shoonya")
    used = any(source == "yahoo" for _, source in chosen.values())
    if not used:
        return result
    five = [bar for bar, _ in sorted(chosen.values(), key=lambda item: item[0]["start_time"])]
    result["candles_5m"] = [bar for bar in five if bar.get("is_closed")]
    result["active_candle_5m"] = next((bar for bar in reversed(five) if not bar.get("is_closed")), None)
    parents = {bar["start_time"]: bar for bar in _bars(native, 15)}
    for parent in _parents(five, now):
        existing = parents.get(parent["start_time"])
        # Native 15m history can exist without saved 5m children. Preserve that
        # valid parent instead of replacing it with an incomplete aggregate.
        if existing is None or not _valid(existing) or _valid(parent):
            parents[parent["start_time"]] = parent
    native_active = native.get("active_candle")
    if native_active and result["active_candle_5m"]:
        parents[native_active["start_time"]] = dict(native_active)
    parents = sorted(parents.values(), key=lambda bar: bar["start_time"])
    result["candles"] = [bar for bar in parents if bar.get("is_closed")]
    result["active_candle"] = next((bar for bar in reversed(parents) if not bar.get("is_closed")), None)
    result.update(history_source_id="yahoo", history_fallback=True, history_delay_seconds=delay)

    historical = day != now.date().isoformat()
    offmarket = historical or now.weekday() >= 5 or not dtime(9, 15) <= now.time() < dtime(15, 30)
    native_tick = _time(native.get("last_tick_time")) if not historical else None
    native_samples = _bars(native, 5) or _bars(native, 15)
    native_last = native_samples[-1] if native_samples else None
    native_time = native_tick or _time(native.get("history_coverage_time")) or (
        _time(native_last["end_time"]) if native_last else None)
    if native_last and native_last.get("is_closed") and _valid(native_last):
        native_end = _time(native_last["end_time"])
        native_time = max(native_time, native_end) if native_time else native_end
    native_valid = _positive(native.get("current_price")) and bool(native_tick or _valid(native_last))
    yahoo_last = max(yahoo.values(), key=lambda bar: bar["end_time"])
    yahoo_time = _time(yahoo_last["end_time"])
    use_yahoo_price = not native_valid or native_time is None or yahoo_time > native_time
    opening = native.get("session_open")
    if not _positive(opening):
        opening = fallback.get("session_open")
    result["session_open"] = opening
    if use_yahoo_price:
        result.update(current_price=yahoo_last["close"], last_tick_time=None,
                      history_coverage_time=None, quote_source_id="yahoo", quote_delay_seconds=delay)
    else:
        result.update(quote_source_id="shoonya", quote_delay_seconds=0)

    price = result.get("current_price")
    sample = yahoo_last if use_yahoo_price else native_last
    valid_return = bool(native_tick and not offmarket and not use_yahoo_price) or _valid(sample)
    if not _positive(price) or not _positive(opening) or not valid_return:
        status = "unavailable"
    elif offmarket:
        status = "closed"
    elif (not use_yahoo_price and native_tick and timedelta(0) <= now - native_tick <= QUOTE_MAX_AGE
          and feed_status not in ("connecting", "disconnected", "error")):
        status = "live"
    else:
        status = "stale"
    result.update(comparison_status=status,
                  change_pct=(price / opening - 1) * 100 if status in ("live", "closed") else None)
    return result
