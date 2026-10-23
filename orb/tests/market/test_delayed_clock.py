"""Delayed instruments close and age against their provider clock."""

from dataclasses import replace
from datetime import timedelta
import unittest

from orb.market import INSTRUMENTS, InstrumentState

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bar


FOCIT = "FOCIT.BO"


class DelayedInstrumentTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 12))
        self.state = InstrumentState(INSTRUMENTS[FOCIT], now=self.clock)

    def history(self, count=9):
        return [replace(bar(slot, 5, price=500), symbol=FOCIT) for slot in range(count)]

    def snapshot(self, feed_status="live"):
        return self.state.snapshot(self.clock(), feed_status)

    def test_delayed_history_uses_source_clock_for_active_bars_and_coverage(self):
        self.state.seed_historical_candles(self.history(), 5)
        result = self.snapshot()
        self.assertEqual(self.state.quote_delay, timedelta(minutes=15))
        self.assertEqual(self.state.market_time(), at(9, 57))
        self.assertEqual(len(result["candles_5m"]), 8)
        self.assertEqual(len(result["candles"]), 2)
        self.assertEqual(result["active_candle_5m"]["start_time"], at(9, 55).isoformat())
        self.assertEqual(result["active_candle"]["start_time"], at(9, 45).isoformat())
        self.assertFalse(result["active_candle_5m"]["is_closed"])
        self.assertTrue(result["active_candle"]["is_complete"])
        self.assertEqual(result["history_coverage_time"], at(9, 57).isoformat())
        self.assertEqual(result["session_open"], 500)
        self.assertEqual(result["quote_delay_seconds"], 900)
        self.assertEqual(result["comparison_status"], "stale")
        self.assertIsNone(result["last_tick_time"])

    def test_delayed_quote_remains_in_its_native_bar_and_closes_at_delayed_boundary(self):
        self.state.seed_historical_candles(self.history(), 5)
        self.assertTrue(self.state.process_tick(520, tick_time=at(9, 57)))
        self.state._close_elapsed(self.state.market_time())
        self.assertEqual(self.state.active_candle_5m.start_time, at(9, 55))
        for quote_time in (at(9, 58, 15), at(9, 59, 30), at(9, 59, 59)):
            self.clock.value = quote_time + timedelta(minutes=15)
            self.assertTrue(self.state.process_tick(522, tick_time=quote_time))
        self.assertEqual(self.state.active_candle_5m.end_time, at(10, 0))
        self.clock.value = at(10, 15)
        closed = self.state._close_elapsed(self.state.market_time())
        self.assertEqual({interval for interval, _ in closed}, {5, 15})
        self.assertTrue(all(candle.close_is_valid for _, candle in closed))
        self.assertEqual(self.state.candles_5m[-1].close, 522)
        self.assertEqual(len(self.state.candles_5m), 9)

    def test_delayed_freshness_does_not_make_repeated_old_quotes_fresh(self):
        self.state.seed_historical_candles(self.history(), 5)
        self.assertTrue(self.state.process_tick(525, tick_time=at(9, 57)))
        result = self.snapshot()
        self.assertEqual(result["comparison_status"], "delayed")
        self.assertEqual(result["last_tick_time"], at(9, 57).isoformat())
        self.assertAlmostEqual(result["change_pct"], 5)
        self.assertEqual(self.snapshot("disconnected")["comparison_status"], "stale")
        self.clock.value = at(10, 13, 30)
        self.assertEqual(self.snapshot()["comparison_status"], "delayed")
        self.clock.value += timedelta(seconds=1)
        self.assertTrue(self.state.process_tick(525, tick_time=at(9, 57)))
        self.assertEqual(self.snapshot()["comparison_status"], "stale")
        self.assertIsNone(self.snapshot()["change_pct"])
        self.assertFalse(self.state.process_tick(526, tick_time=at(10, 13)))

    def test_delayed_source_is_open_until_its_last_quote_window_ends(self):
        self.clock.value = at(15, 30)
        self.assertTrue(self.state.set_session_open(500, at(15, 15)))
        self.state.seed_historical_candles(self.history(75), 5)
        self.assertTrue(self.state.process_tick(525, tick_time=at(15, 15)))
        self.assertEqual(self.snapshot()["comparison_status"], "delayed")
        self.clock.value = at(15, 44, 59)
        self.state.seed_historical_candles(self.history(75), 5)
        self.assertTrue(self.state.process_tick(530, tick_time=at(15, 29, 59)))
        self.assertEqual(self.snapshot()["comparison_status"], "delayed")
        self.clock.value = at(15, 45)
        self.state._close_elapsed(self.state.market_time())
        self.assertEqual(self.snapshot()["comparison_status"], "closed")
        self.assertFalse(self.state.process_tick(531, tick_time=at(15, 30)))

    def test_source_switch_removes_delay_without_carrying_quotes_or_baseline(self):
        self.assertTrue(self.state.set_session_open(500, at(9, 57)))
        self.assertTrue(self.state.process_tick(525, tick_time=at(9, 57)))
        self.state.activate_source("shoonya")
        self.assertEqual(self.state.quote_delay, timedelta(0))
        self.assertEqual(self.state.market_time(), at(10, 12))
        self.assertEqual(self.snapshot()["quote_delay_seconds"], 0)
        self.assertIsNone(self.state.current_price)
        self.assertIsNone(self.state.session_open)
        self.assertTrue(self.state.set_session_open(600, at(10, 12)))
        self.assertTrue(self.state.process_tick(630, tick_time=at(10, 12)))
        self.assertEqual(self.snapshot()["comparison_status"], "live")
        self.assertAlmostEqual(self.snapshot()["change_pct"], 5)
        self.state.activate_source("yahoo")
        self.assertEqual(self.state.quote_delay, timedelta(minutes=15))
        self.assertIsNone(self.state.current_price)
        self.assertEqual(self.state.session_open, 500)

    def test_upstream_open_requires_fresh_same_session_metadata_and_creates_no_bar(self):
        for opening, timestamp in ((0, at(9, 57)), (float("nan"), at(9, 57)),
                                   (500, at(9, 55)), (500, at(9, 57, 1)),
                                   (500, at(9, 57, day="2026-10-02")), (500, at(9, 14))):
            with self.subTest(opening=opening, timestamp=timestamp):
                self.assertFalse(self.state.set_session_open(opening, timestamp))
        self.assertTrue(self.state.set_session_open(500, at(9, 57)))
        self.assertFalse(self.state.set_session_open(999, at(9, 57)))
        self.assertEqual(self.state.session_open, 500)
        self.assertIsNone(self.state.active_candle)
        self.assertIsNone(self.state.active_candle_5m)
        self.assertEqual(self.state.candles, [])
        self.assertIsNone(self.state.current_price)

    def test_retention_and_session_date_use_actual_day_at_midnight(self):
        self.clock.value = at(0, 5, day="2026-10-13")
        state = InstrumentState(INSTRUMENTS[FOCIT], now=self.clock)
        self.assertEqual(state.date, "2026-10-13")
        self.assertEqual(state.market_time().date().isoformat(), "2026-10-12")
        old = replace(bar(0, 5, day="2026-08-13"), symbol=FOCIT)
        retained = replace(old, start_time=at(day="2026-08-14"), end_time=at(9, 20, day="2026-08-14"))
        state.seed_historical_candles([old, retained], 5)
        self.assertNotIn("2026-08-13", state.sessions_5m)
        self.assertIn("2026-08-14", state.sessions_5m)
        self.assertEqual(state.date, "2026-10-13")
