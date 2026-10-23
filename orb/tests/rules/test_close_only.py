"""Close-only comparison rules have no dependency on monitor state."""

from datetime import timedelta
import unittest

from orb.comparison import close_only_breakout

from orb.tests.support.market import at
from orb.tests.support.market import close_only_bars as bars


BANK = "^NSEBANK"


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
