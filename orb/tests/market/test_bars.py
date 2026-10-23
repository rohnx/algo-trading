"""Real five-minute coverage and its fifteen-minute aggregation."""

import unittest

from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bar


class FiveMinuteTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(11, 0))
        self.hub = MonitorHub(now=self.clock)

    def test_real_five_minute_history_aggregates_ohlc_volume_and_locks_both_opening_ranges(self):
        five = [bar(slot, 5, price=100 + slot * 3) for slot in range(9)]
        self.hub.seed_historical_candles(five, interval_minutes=5)
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles_5m"]), 9)
        self.assertEqual(len(state["candles"]), 3)
        for slot, result in enumerate(state["candles"]):
            group = five[slot * 3:slot * 3 + 3]
            self.assertEqual(result["start_time"], group[0].start_time.isoformat())
            self.assertEqual(result["end_time"], group[-1].end_time.isoformat())
            self.assertEqual((result["open"], result["high"], result["low"], result["close"], result["volume"]),
                             (group[0].open, max(c.high for c in group), min(c.low for c in group),
                              group[-1].close, sum(c.volume for c in group)))
            self.assertTrue(result["is_complete"])
        self.assertTrue(state["range_locked"])
        self.assertEqual((state["orb_high"], state["orb_low"]), (120, 95))
        self.assertEqual((state["setups"]["5"]["orb_high"], state["setups"]["5"]["orb_low"]), (129, 95))
        self.assertTrue(state["setups"]["5"]["range_locked"])

    def test_missing_five_minute_segment_cannot_create_complete_opening_range(self):
        self.hub.seed_historical_candles([bar(slot, 5) for slot in range(9) if slot != 1], interval_minutes=5)
        state = self.hub.snapshot()
        self.assertFalse(state["range_locked"])
        first = [c for c in state["candles"] if c["slot"] == 0]
        self.assertTrue(not first or not first[0]["is_complete"])
        self.assertEqual(len(state["candles_5m"]), 8)

    def test_incomplete_five_minute_segment_invalidates_aggregate(self):
        five = [bar(slot, 5, complete=slot != 4) for slot in range(9)]
        self.hub.seed_historical_candles(five, interval_minutes=5)
        state = self.hub.snapshot()
        self.assertFalse(state["range_locked"])
        middle = next(c for c in state["candles"] if c["slot"] == 1)
        self.assertFalse(middle["is_complete"])

    def test_fifteen_minute_history_never_invents_five_minute_prices(self):
        self.hub.seed_historical_candles([bar(slot) for slot in range(3)])
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles"]), 3)
        self.assertEqual(state["candles_5m"], [])
        self.assertIsNone(state["active_candle_5m"])
        self.assertTrue(state["range_locked"])
        self.assertFalse(state["setups"]["5"]["range_locked"])
        self.assertEqual(state["setups"]["5"]["signals"], [])

    def test_unsorted_duplicate_backfill_does_not_double_count_five_minute_volume(self):
        five = [bar(slot, 5, price=100 + slot) for slot in range(9)]
        self.hub.seed_historical_candles(list(reversed(five)) + [five[1]], interval_minutes=5)
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles_5m"]), 9)
        self.assertEqual(state["candles"][0]["open"], five[0].open)
        self.assertEqual(state["candles"][0]["close"], five[2].close)
        self.assertEqual(state["candles"][0]["volume"], sum(c.volume for c in five[:3]))

    def test_five_minute_close_outside_range_does_not_override_fifteen_minute_confirmation(self):
        five = [bar(slot, 5) for slot in range(12)]
        five[9] = bar(9, 5, price=120)
        self.hub.seed_historical_candles(five, interval_minutes=5)
        state = self.hub.snapshot()
        self.assertTrue(state["range_locked"])
        self.assertGreater(state["candles_5m"][9]["close"], state["orb_high"])
        self.assertLessEqual(state["candles"][3]["close"], state["orb_high"])
        self.assertEqual(state["setups"]["15"]["signals"], [])
        self.assertEqual(len(state["setups"]["5"]["signals"]), 1)
        self.assertEqual(state["setups"]["5"]["signals"][0]["timestamp"], at(10, 5).isoformat())
        self.assertEqual(state["signals"], [])

    def test_live_five_minute_close_keeps_fifteen_minute_bar_open(self):
        self.clock.value = at()
        self.hub.process_tick(100, 2, self.clock.value)
        self.clock.value = at(9, 19, 50)
        self.hub.process_tick(104, 3, self.clock.value)
        self.clock.value = at(9, 20)
        self.hub.process_tick(102, 4, self.clock.value)
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles_5m"]), 1)
        self.assertEqual(state["candles"], [])
        self.assertEqual(state["candles_5m"][0]["volume"], 5)
        self.assertEqual(state["active_candle_5m"]["start_time"], at(9, 20).isoformat())
        self.assertEqual(state["active_candle"]["start_time"], at().isoformat())
        self.assertEqual(state["active_candle"]["high"], 104)
        self.assertEqual(state["active_candle"]["close"], 102)
        self.assertEqual(state["active_candle"]["volume"], 9)
        self.assertEqual(state["signals"], [])

    def test_delayed_quote_cannot_reopen_a_seeded_five_minute_bar(self):
        self.clock.value = at(10, 12)
        self.hub.seed_historical_candles([bar(slot, 5) for slot in range(11)], interval_minutes=5)
        before = self.hub.snapshot()
        self.assertEqual(before["candles_5m"][-1]["end_time"], at(10, 10).isoformat())
        self.assertEqual(before["active_candle"]["start_time"], at(10, 0).isoformat())
        self.assertFalse(self.hub.process_tick(999, tick_time=at(10, 9)))
        self.assertEqual(self.hub.snapshot(), before)
        self.assertTrue(self.hub.process_tick(102, tick_time=at(10, 11)))
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles_5m"]), 11)
        self.assertEqual(state["active_candle_5m"]["start_time"], at(10, 10).isoformat())
        self.assertEqual(state["active_candle"]["close"], 102)
