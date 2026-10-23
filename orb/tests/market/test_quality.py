"""Observed coverage, disconnect gaps, and whether a close is valid."""

import unittest

from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bar


class FeedQualityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 0))
        self.hub = MonitorHub(now=self.clock)
        self.hub.seed_historical_candles([bar(slot) for slot in range(3)])

    def test_stale_price_cannot_confirm_a_timer_closed_breakout(self):
        self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 15)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertEqual(state["signals"], [])
        self.assertFalse(state["candles"][-1]["is_complete"])
        self.assertFalse(state["candles"][-1]["close_is_valid"])

    def test_fresh_observed_close_can_confirm_breakout(self):
        for minute in range(15):
            self.clock.value = at(10, minute)
            self.hub.process_tick(110, tick_time=self.clock.value)
        self.clock.value = at(10, 14, 50)
        self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 15)
        self.hub.advance_time()
        state = self.hub.snapshot()
        signals = state["setups"]["15"]["signals"]
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["price"], 120)
        self.assertEqual(signals[0]["timestamp"], self.clock.value.isoformat())
        self.assertEqual(state["signals"], [])

    def test_reconnected_fresh_tick_does_not_erase_gap_in_open_bars(self):
        self.hub.process_tick(110, tick_time=self.clock.value)
        self.clock.value = at(10, 2)
        self.hub.set_feed_status("disconnected", error="Fixture disconnect")
        self.clock.value = at(10, 4, 50)
        self.hub.set_feed_status("connected")
        self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 5)
        self.hub.advance_time()
        self.assertFalse(self.hub.snapshot()["candles_5m"][-1]["is_complete"])
        self.clock.value = at(10, 14, 50)
        self.hub.process_tick(125, tick_time=self.clock.value)
        self.clock.value = at(10, 15)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertFalse(state["candles"][-1]["is_complete"])
        self.assertFalse(state["candles"][-1]["close_is_valid"])
        self.assertEqual(state["signals"], [])

    def test_three_stale_opening_bars_cannot_lock_the_range(self):
        self.clock.value = at()
        self.hub = MonitorHub(now=self.clock)
        for hour, minute in ((9, 15), (9, 30), (9, 45)):
            self.clock.value = at(hour, minute)
            self.hub.process_tick(100, tick_time=self.clock.value)
        self.clock.value = at(10, 0)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertFalse(state["range_locked"])
        self.assertTrue(all(not c["is_complete"] for c in state["candles"]))

    def observe_opening_from_0922(self):
        for minute in range(22, 60):
            self.clock.value = at(9, minute)
            self.hub.process_tick(100, tick_time=self.clock.value)
        self.clock.value = at(9, 59, 50)
        self.hub.process_tick(100, tick_time=self.clock.value)
        self.clock.value = at(10, 0)
        self.hub.advance_time()
        return self.hub.snapshot()

    def test_active_history_missing_current_five_minute_coverage_cannot_complete_parent(self):
        self.clock.value = at(9, 22)
        self.hub = MonitorHub(now=self.clock)
        self.hub.seed_historical_candles([bar(0, 5)], interval_minutes=5)
        initial = self.hub.snapshot()
        self.assertEqual(initial["active_candle"]["start_time"], at().isoformat())
        self.assertFalse(initial["active_candle"]["is_complete"])
        state = self.observe_opening_from_0922()
        self.assertFalse(state["candles"][0]["is_complete"])
        self.assertFalse(state["range_locked"])
        self.assertEqual(state["signals"], [])

    def test_first_live_quote_after_gap_from_historical_coverage_invalidates_parent(self):
        self.clock.value = at(9, 20)
        self.hub = MonitorHub(now=self.clock)
        self.hub.seed_historical_candles([bar(0, 5)], interval_minutes=5)
        initial = self.hub.snapshot()
        self.assertTrue(initial["active_candle"]["is_complete"])
        self.assertEqual(initial["market_status"], "waiting")
        self.assertIsNone(initial["last_tick_time"])
        # No live observation arrives during 09:20–09:22. Historical coverage
        # must serve as a continuity boundary without impersonating a live tick.
        self.clock.value = at(9, 22)
        self.hub.process_tick(100, tick_time=self.clock.value)
        self.assertFalse(self.hub.snapshot()["active_candle"]["is_complete"])
        state = self.observe_opening_from_0922()
        self.assertFalse(state["candles"][0]["is_complete"])
        self.assertFalse(state["range_locked"])
        self.assertEqual(state["signals"], [])

    def test_late_first_tick_of_uncovered_five_minute_child_invalidates_parent(self):
        self.clock.value = at(9, 20)
        self.hub = MonitorHub(now=self.clock)
        self.hub.seed_historical_candles([bar(0, 5)], interval_minutes=5)
        self.clock.value = at(9, 21)
        self.hub.process_tick(100, tick_time=self.clock.value)
        state = self.hub.snapshot()
        self.assertFalse(state["active_candle_5m"]["is_complete"])
        self.assertFalse(state["active_candle"]["is_complete"])

    def test_authoritative_fifteen_minute_history_preserves_parent_despite_missing_five_minute_history(self):
        self.clock.value = at(9, 18)
        self.hub = MonitorHub(now=self.clock)
        self.hub.seed_historical_candles([bar(0)])
        self.clock.value = at(9, 18, 1)
        self.hub.process_tick(102, tick_time=self.clock.value)
        state = self.hub.snapshot()
        self.assertFalse(state["active_candle_5m"]["is_complete"])
        self.assertTrue(state["active_candle"]["is_complete"])
        self.assertEqual(state["active_candle"]["high"], 105)
