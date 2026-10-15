"""Comparison emphasis uses closed five-minute prices, never candle whiskers."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import queue
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comparison import close_only_breakout
from market import PRIMARY_SYMBOL
from orb_monitor import MonitorHub
from test_features import Clock, at, bar

BANK = "^NSEBANK"
MID = "NIFTY_MID_SELECT.NS"
FOCIT = "FOCIT.BO"


def bars(closes=None, *, symbol=BANK, day="2026-10-05"):
    closes = list(range(100, 109)) if closes is None else closes
    return [replace(bar(slot, 5, day=day, price=close), symbol=symbol,
                    high=max(1000, close), low=1, close=close)
            for slot, close in enumerate(closes)]


def evaluate(candles, day="2026-10-05"):
    return close_only_breakout([candle.to_dict() for candle in candles], day)


class CloseOnlyRangeTests(unittest.TestCase):
    def test_nine_closes_lock_range_ignoring_open_high_and_low(self):
        state = evaluate(bars())
        self.assertEqual((state["basis"], state["range_minutes"], state["confirmation_minutes"]),
                         ("close", 45, 5))
        self.assertTrue(state["range_locked"])
        self.assertEqual((state["range_high"], state["range_low"]), (108, 100))
        self.assertIsNone(state["direction"])
        self.assertIsNone(state["confirmed_at"])
        self.assertIsNone(state["price"])

    def test_first_confirmation_is_1005_and_forming_close_cannot_confirm(self):
        candles = bars([*range(100, 109), 109])
        candles[-1].is_closed = False
        self.assertIsNone(evaluate(candles)["direction"])
        candles[-1].is_closed = True
        state = evaluate(candles)
        self.assertEqual(state["direction"], "BULLISH")
        self.assertEqual(state["confirmed_at"], at(10, 5).isoformat())
        self.assertEqual(state["price"], 109)

    def test_missing_unclosed_incomplete_or_invalid_opening_child_never_locks(self):
        for kind in ("missing", "is_closed", "is_complete", "close_is_valid", "nan", "zero"):
            with self.subTest(kind=kind):
                candles = bars([*range(100, 109), 120])
                if kind == "missing":
                    candles.pop(4)
                elif kind in ("nan", "zero"):
                    candles[4].close = float("nan") if kind == "nan" else 0
                else:
                    setattr(candles[4], kind, False)
                state = evaluate(candles)
                self.assertFalse(state["range_locked"])
                self.assertIsNone(state["range_high"])
                self.assertIsNone(state["range_low"])
                self.assertIsNone(state["direction"])

    def test_strict_boundaries_inside_close_clears_and_outside_requalifies(self):
        expected = [(109, "BULLISH"), (108, None), (104, None),
                    (100, None), (99, "BEARISH"), (107, None), (110, "BULLISH")]
        closes = list(range(100, 109))
        for close, direction in expected:
            closes.append(close)
            with self.subTest(close=close, count=len(closes)):
                state = evaluate(bars(closes))
                self.assertEqual(state["direction"], direction)
                self.assertEqual(state["price"], close)
                self.assertEqual(state["confirmed_at"],
                                 (at() + timedelta(minutes=5 * len(closes))).isoformat())

    def test_latest_invalid_close_clears_previous_qualification(self):
        for field in ("is_complete", "close_is_valid"):
            with self.subTest(field=field):
                candles = bars([*range(100, 109), 120, 121])
                setattr(candles[-1], field, False)
                state = evaluate(candles)
                self.assertTrue(state["range_locked"])
                self.assertIsNone(state["direction"])
                self.assertIsNone(state["confirmed_at"])
                self.assertIsNone(state["price"])

    def test_postrange_gap_is_not_fabricated_and_later_real_close_can_qualify(self):
        candles = bars([*range(100, 109), 120, 105, 99])
        candles.pop(10)
        state = evaluate(list(reversed(candles)))
        self.assertEqual(state["direction"], "BEARISH")
        self.assertEqual(state["confirmed_at"], at(10, 15).isoformat())

    def test_wrong_date_interval_or_unaligned_opening_bar_cannot_fill_a_slot(self):
        for kind in ("date", "interval", "alignment"):
            with self.subTest(kind=kind):
                candles = bars([*range(100, 109), 120])
                if kind == "date":
                    candles[4] = bars(day="2026-10-02")[4]
                elif kind == "interval":
                    candles[4].end_time += timedelta(minutes=10)
                else:
                    candles[4].start_time += timedelta(seconds=1)
                    candles[4].end_time += timedelta(seconds=1)
                self.assertFalse(evaluate(candles)["range_locked"])

    def test_single_price_range_still_uses_strict_comparisons(self):
        self.assertIsNone(evaluate(bars([100] * 10))["direction"])
        self.assertEqual(evaluate(bars([100] * 9 + [101]))["direction"], "BULLISH")
        self.assertEqual(evaluate(bars([100] * 9 + [99]))["direction"], "BEARISH")


class ComparisonHubTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(11, 0))
        self.hub = MonitorHub(now=self.clock)

    def comparison(self, symbol=BANK, day=None):
        return self.hub.snapshot(day)["instruments"][symbol]

    def seed(self, closes=None, *, symbol=BANK, day="2026-10-05"):
        self.hub.seed_historical_candles(bars(closes, symbol=symbol, day=day), 5, symbol=symbol)

    def test_comparisons_are_independent_and_never_change_primary_or_notify(self):
        before = self.hub.snapshot()["instruments"][PRIMARY_SYMBOL]
        self.seed([*range(100, 109), 110])
        self.seed([*range(100, 109), 99], symbol=MID)
        snapshot = self.hub.snapshot()
        self.assertEqual(snapshot["instruments"][BANK]["breakout"]["direction"], "BULLISH")
        self.assertEqual(snapshot["instruments"][MID]["breakout"]["direction"], "BEARISH")
        self.assertEqual(snapshot["instruments"][PRIMARY_SYMBOL], before)
        self.assertNotIn("breakout", before)
        self.assertEqual(snapshot["signals"], [])
        self.assertFalse(snapshot["setups"]["5"]["range_locked"])

    def test_only_fifteen_minute_history_and_session_open_cannot_lock_comparison(self):
        self.hub.seed_historical_candles([replace(bar(slot), symbol=BANK) for slot in range(4)],
                                         symbol=BANK)
        self.hub.process_tick(110, symbol=BANK, session_open=100)
        self.assertFalse(self.comparison()["breakout"]["range_locked"])

    def test_tick_deltas_reuse_confirmation_without_serializing_closed_history(self):
        self.clock.value = at(10, 5)
        self.seed([*range(100, 109), 110])
        with patch("orb_monitor.close_only_breakout", wraps=close_only_breakout) as evaluator:
            first = self.hub._instrument_snapshot(BANK, include_history=False)
            self.assertEqual(evaluator.call_count, 1)
            self.assertEqual(first["breakout"]["direction"], "BULLISH")
            self.assertNotIn("candles_5m", first)
            self.hub.process_tick(103, symbol=BANK)
            self.clock.value = at(10, 5, 1)
            self.hub.process_tick(104, symbol=BANK)
            delta = self.hub._instrument_snapshot(BANK, include_history=False)
            self.assertEqual(delta["breakout"], first["breakout"])
            self.assertEqual(evaluator.call_count, 1)
            self.assertNotIn("candles", delta)
            self.assertNotIn("candles_5m", delta)

    def test_valid_timer_close_clears_and_next_close_can_requalify(self):
        self.clock.value = at(10, 5)
        self.seed([*range(100, 109), 110])
        self.hub.process_tick(103, symbol=BANK)
        for minute in range(6, 10):
            self.clock.value = at(10, minute)
            self.hub.process_tick(103, symbol=BANK)
        self.clock.value = at(10, 9, 59)
        self.hub.process_tick(104, symbol=BANK)
        client = queue.Queue()
        self.hub.register_client(client)
        client.get_nowait()
        self.clock.value = at(10, 10)
        self.hub.advance_time()
        event = client.get_nowait()["instruments"][BANK]
        self.assertIsNone(event["breakout"]["direction"])
        self.assertEqual(event["breakout"]["confirmed_at"], at(10, 10).isoformat())
        self.hub.process_tick(98, symbol=BANK)
        for minute in range(11, 15):
            self.clock.value = at(10, minute)
            self.hub.process_tick(98, symbol=BANK)
        self.clock.value = at(10, 14, 59)
        self.hub.process_tick(99, symbol=BANK)
        self.clock.value = at(10, 15)
        self.hub.advance_time()
        self.assertEqual(self.comparison()["breakout"]["direction"], "BEARISH")

    def test_invalid_live_close_clears_cached_qualification(self):
        self.clock.value = at(10, 7)
        self.seed([*range(100, 109), 110])
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.hub.process_tick(120, symbol=BANK)  # Started too late for a complete5m bar.
        self.clock.value = at(10, 9, 59)
        self.hub.process_tick(121, symbol=BANK)
        self.clock.value = at(10, 10)
        self.hub.advance_time()
        state = self.hub._instrument_snapshot(BANK, include_history=False)["breakout"]
        self.assertIsNone(state["direction"])
        self.assertIsNone(state["confirmed_at"])

    def test_backfill_replacement_invalidates_light_delta_cache(self):
        self.seed([*range(100, 109), 110])
        before = self.hub._instrument_snapshot(BANK, include_history=False)["breakout"]
        self.assertEqual(before["direction"], "BULLISH")
        self.seed([*range(100, 109), 105])
        self.assertIsNone(self.hub._instrument_snapshot(BANK, include_history=False)["breakout"]["direction"])

    def test_yahoo_backfill_is_composed_before_close_only_evaluation(self):
        self.clock.value = at(10, 30)
        self.hub.activate_source("shoonya")
        self.hub.seed_fallback_history(bars([*range(100, 109), 110], symbol=FOCIT), symbol=FOCIT)
        fallback = self.comparison(FOCIT)
        self.assertTrue(fallback["history_fallback"])
        self.assertEqual(fallback["breakout"]["direction"], "BULLISH")
        self.assertEqual(self.hub.instruments[FOCIT].candles_5m, [])
        # A later invalid native close blocks a valid but older fallback confirmation.
        native = replace(bars([*range(100, 109), 110, 120], symbol=FOCIT)[-1],
                         is_complete=False, close_is_valid=False)
        self.hub.seed_historical_candles([native], 5, symbol=FOCIT)
        self.assertIsNone(self.comparison(FOCIT)["breakout"]["direction"])
        # Native valid replacements win over fallback values in the range too.
        self.hub.seed_historical_candles(bars([100] * 9 + [99, 98], symbol=FOCIT), 5, symbol=FOCIT)
        native = self.comparison(FOCIT)["breakout"]
        self.assertEqual((native["range_high"], native["range_low"]), (100, 100))
        self.assertEqual(native["direction"], "BEARISH")

    def test_delayed_fallback_does_not_promote_a_future_five_minute_close(self):
        self.clock.value = at(10, 17)  # Yahoo history is complete only through10:00.
        self.hub.activate_source("shoonya")
        self.hub.seed_fallback_history(bars([*range(100, 109), 110], symbol=FOCIT), symbol=FOCIT)
        state = self.comparison(FOCIT)["breakout"]
        self.assertTrue(state["range_locked"])
        self.assertIsNone(state["direction"])
        self.assertIsNone(state["confirmed_at"])

    def test_history_source_switch_and_new_day_do_not_leak_comparison_state(self):
        self.seed([*range(100, 109), 110])
        self.seed([*range(100, 109), 99], day="2026-10-02")
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.assertEqual(self.comparison(day="2026-10-02")["breakout"]["direction"], "BEARISH")
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.hub.activate_source("shoonya")
        self.assertFalse(self.comparison()["breakout"]["range_locked"])
        self.seed([100] * 9 + [99])
        self.assertEqual(self.comparison()["breakout"]["direction"], "BEARISH")
        self.hub.activate_source("yahoo")
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.clock.value = at(10, 10, day="2026-10-06")
        self.assertFalse(self.comparison()["breakout"]["range_locked"])
        self.assertEqual(self.comparison(day="2026-10-05")["breakout"]["direction"], "BULLISH")


if __name__ == "__main__":
    unittest.main()
