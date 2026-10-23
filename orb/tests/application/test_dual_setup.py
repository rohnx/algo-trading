"""Both ORB setups agree atomically across live closes, source return, and restart."""

import tempfile
import unittest

from orb.orb_monitor import DualORBState, MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bar, DAY


class DualSetupTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 30))
        self.hub = MonitorHub(now=self.clock)

    @staticmethod
    def opening(third_price=100):
        return [bar(slot, 5, price=100 if slot < 6 else third_price) for slot in range(9)]

    @classmethod
    def two_direction_session(cls):
        return cls.opening(110) + [bar(9, 5, price=120), bar(10, 5), bar(11, 5)] + [
            bar(slot, 5, price=90) for slot in range(12, 15)]

    def test_opening_ranges_lock_independently_at_0945_and_1000(self):
        self.clock.value = at(9, 45)
        five = self.opening(110)
        self.hub.seed_historical_candles(five[:6], interval_minutes=5)
        state = self.hub.snapshot()
        primary, fast = state["setups"]["15"], state["setups"]["5"]
        self.assertEqual((primary["range_minutes"], primary["confirmation_minutes"]), (30, 15))
        self.assertEqual((fast["range_minutes"], fast["confirmation_minutes"]), (45, 5))
        self.assertTrue(primary["range_locked"])
        self.assertFalse(fast["range_locked"])
        self.clock.value = at(10, 0)
        self.hub.seed_historical_candles(five[6:], interval_minutes=5)
        state = self.hub.snapshot()
        self.assertTrue(state["setups"]["5"]["range_locked"])
        self.assertEqual((state["orb_high"], state["orb_low"]), (105, 95))
        self.assertEqual((state["setups"]["5"]["orb_high"], state["setups"]["5"]["orb_low"]), (115, 95))
        self.assertEqual(state["setups"]["15"]["confirmation_direction"], "BULLISH")
        self.assertIsNone(state["setups"]["5"]["confirmation_direction"])
        self.assertEqual(state["signals"], [])

    def test_fifteen_minute_only_history_cannot_supply_fast_leg_or_main_signal(self):
        self.clock.value = at(10, 0)
        self.hub.seed_historical_candles([bar(0), bar(1), bar(2, price=120)])
        state = self.hub.snapshot()
        self.assertEqual(len(state["setups"]["15"]["signals"]), 1)
        self.assertEqual(state["setups"]["15"]["signals"][0]["timestamp"], at(10, 0).isoformat())
        self.assertFalse(state["setups"]["5"]["range_locked"])
        self.assertEqual(state["setups"]["5"]["signals"], [])
        self.assertEqual(state["candles_5m"], [])
        self.assertEqual(state["signals"], [])

    def test_live_five_minute_confirmation_can_join_existing_fifteen_minute_confirmation(self):
        self.clock.value = at(10, 0)
        self.hub.seed_historical_candles(self.opening(110), interval_minutes=5)
        for minute in range(5):
            self.clock.value = at(10, minute)
            self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 4, 50)
        self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 5)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertEqual(len(state["setups"]["5"]["signals"]), 1)
        self.assertEqual(state["setups"]["5"]["signals"][0]["timestamp"], at(10, 5).isoformat())
        self.assertEqual(len(state["signals"]), 1)
        signal = state["signals"][0]
        self.assertEqual(signal["direction"], "BULLISH")
        self.assertEqual(signal["timestamp"], at(10, 5).isoformat())
        self.assertEqual(signal["confirmations"]["15"]["confirmed_at"], at(10, 0).isoformat())
        self.assertEqual(signal["confirmations"]["5"]["confirmed_at"], at(10, 5).isoformat())
        self.assertIsNotNone(state["active_candle"])

    def test_disconnect_in_fast_confirmation_bar_blocks_main_even_after_fresh_reconnect_tick(self):
        self.clock.value = at(10, 0)
        self.hub.seed_historical_candles(self.opening(110), interval_minutes=5)
        self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 1)
        self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 2)
        self.hub.set_feed_status("disconnected")
        self.clock.value = at(10, 4, 50)
        self.hub.set_feed_status("connected")
        self.hub.process_tick(120, tick_time=self.clock.value)
        self.clock.value = at(10, 5)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertFalse(state["candles_5m"][-1]["close_is_valid"])
        self.assertEqual(state["setups"]["15"]["confirmation_direction"], "BULLISH")
        self.assertIsNone(state["setups"]["5"]["confirmation_direction"])
        self.assertEqual(state["setups"]["5"]["signals"], [])
        self.assertEqual(state["signals"], [])

    def test_simultaneous_live_closes_do_not_emit_transient_agreement_with_previous_fast_close(self):
        five = self.opening()
        for candle in five[6:]:
            candle.high, candle.low, candle.close = 125, 75, 80
        five.extend([bar(9, 5, price=130), bar(10, 5, price=130)])
        self.clock.value = at(10, 10)
        self.hub.seed_historical_candles(five, interval_minutes=5)
        initial = self.hub.snapshot()
        self.assertEqual(initial["setups"]["15"]["confirmation_direction"], "BEARISH")
        self.assertEqual(initial["setups"]["5"]["confirmation_direction"], "BULLISH")
        for minute in range(10, 15):
            self.clock.value = at(10, minute)
            self.hub.process_tick(110, tick_time=self.clock.value)
        self.clock.value = at(10, 14, 50)
        self.hub.process_tick(110, tick_time=self.clock.value)
        self.clock.value = at(10, 15)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertEqual(state["setups"]["15"]["confirmation_direction"], "BULLISH")
        self.assertEqual(state["setups"]["5"]["confirmation_direction"], "NEUTRAL")
        self.assertEqual(state["signals"], [])

    def test_history_merges_intervals_chronologically_and_updates_latched_arrow_confirmation(self):
        self.hub.seed_historical_candles(list(reversed(self.two_direction_session())), interval_minutes=5)
        state = self.hub.snapshot()
        self.assertEqual([(s["direction"], s["timestamp"]) for s in state["signals"]], [
            ("BEARISH", at(10, 30).isoformat()), ("BULLISH", at(10, 5).isoformat())])
        self.assertEqual([(s["direction"], s["timestamp"]) for s in state["setups"]["15"]["signals"]], [
            ("BEARISH", at(10, 30).isoformat()), ("BULLISH", at(10, 0).isoformat())])
        self.assertEqual([(s["direction"], s["timestamp"]) for s in state["setups"]["5"]["signals"]], [
            ("BEARISH", at(10, 20).isoformat()), ("BULLISH", at(10, 5).isoformat())])
        self.assertEqual(state["setups"]["5"]["confirmed_at"], at(10, 30).isoformat())
        self.assertEqual(state["signals"][0]["confirmations"]["5"]["confirmed_at"], at(10, 30).isoformat())

    def test_skipped_fast_close_cannot_keep_old_confirmation_eligible_for_main(self):
        state = DualORBState()
        events = [(15, bar(0)), (15, bar(1)), (15, bar(2))]
        events.extend((5, c) for c in self.opening())
        events.extend([(5, bar(9, 5, price=120)), (15, bar(3, price=120))])
        state.feed_closed(events)
        self.assertEqual(len(state.get_setups()["5"]["signals"]), 1)
        self.assertEqual(len(state.get_setups()["15"]["signals"]), 1)
        self.assertEqual(state.signals, [])

    def test_next_day_resets_both_setups_and_history_rebuild_cannot_change_live_state(self):
        self.hub.seed_historical_candles(self.two_direction_session(), interval_minutes=5)
        original = self.hub.snapshot()
        self.clock.value = at(9, 20, day="2026-10-06")
        self.hub.advance_time()
        current = self.hub.snapshot()
        for setup in current["setups"].values():
            self.assertFalse(setup["range_locked"])
            self.assertIsNone(setup["confirmation_direction"])
            self.assertEqual(setup["signals"], [])
        self.assertEqual(current["signals"], [])
        past = self.hub.snapshot(DAY)
        self.assertEqual(past["setups"], original["setups"])
        self.assertEqual(past["signals"], original["signals"])
        self.assertEqual(self.hub.snapshot(), current)

    def test_source_switch_and_cache_restart_restore_both_setups_without_cross_provider_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            hub.seed_historical_candles(self.two_direction_session(), interval_minutes=5)
            yahoo = hub.snapshot()
            hub.activate_source("shoonya")
            switched = hub.snapshot()
            self.assertEqual(switched["signals"], [])
            self.assertTrue(all(not setup["range_locked"] and not setup["signals"] for setup in switched["setups"].values()))
            hub.seed_historical_candles(self.opening(), interval_minutes=5)
            shoonya = hub.snapshot()
            restored = MonitorHub(now=self.clock, cache_dir=directory)
            restored.seed_historical_candles([])
            self.assertEqual(restored.snapshot()["setups"], yahoo["setups"])
            self.assertEqual(restored.snapshot()["signals"], yahoo["signals"])
            restored.activate_source("shoonya")
            self.assertEqual(restored.snapshot()["setups"], shoonya["setups"])
            self.assertEqual(restored.snapshot()["signals"], [])
