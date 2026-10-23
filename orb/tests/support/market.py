"""Synthetic session timestamps and explicit candle shapes shared by contracts."""

from dataclasses import replace
from datetime import datetime, timedelta

from orb.market import PRIMARY_SYMBOL
from orb.signals import Candle, IST

SYMBOL = PRIMARY_SYMBOL
DAY = "2026-10-05"
BANK = "^NSEBANK"

def at(hour=9, minute=15, second=0, day=DAY):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, second=second, tzinfo=IST)


def bar(slot, interval=15, *, day=DAY, price=100, complete=True):
    start = at(day=day) + timedelta(minutes=slot * interval)
    return Candle(SYMBOL, price, price + 5, price - 5, price + 1,
                  10 + slot, start, start + timedelta(minutes=interval),
                  is_closed=True, slot=slot, is_complete=complete)


def timestamp(day, hour=9, minute=15, second=0):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, second=second, tzinfo=IST)


def session_bar(day, slot=0, *, high=110, low=90, close=100):
    start = timestamp(day) + timedelta(minutes=15 * slot)
    return Candle(SYMBOL, 100, high, low, close, 12, start,
                  start + timedelta(minutes=15), is_closed=True, slot=slot)


def five_bars(day, *, symbol=SYMBOL):
    opening = timestamp(day)
    return [Candle(symbol, 100, 110, 90, 100, 12, opening + timedelta(minutes=5 * slot),
                   opening + timedelta(minutes=5 * (slot + 1)), is_closed=True, slot=slot)
            for slot in range(9)]


def bank_bar(slot=0, interval=5, **kwargs):
    return replace(bar(slot, interval, **kwargs), symbol=BANK)


def close_only_bars(closes=None, *, symbol=BANK, day="2026-10-05"):
    closes = list(range(100, 109)) if closes is None else closes
    return [replace(bar(slot, 5, day=day, price=close), symbol=symbol,
                    high=max(1000, close), low=1, close=close)
            for slot, close in enumerate(closes)]
