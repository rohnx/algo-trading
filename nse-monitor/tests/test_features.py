"""Real five-minute coverage, feed gaps, and provider isolation."""

from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb_monitor import DualORBState, MonitorHTTPHandler, MonitorHub, SYMBOL, run_replay_simulation
from signals import Candle, IST


DAY = "2026-10-05"


def at(hour=9, minute=15, second=0, day=DAY):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, second=second, tzinfo=IST)


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def bar(slot, interval=15, *, day=DAY, price=100, complete=True):
    start = at(day=day) + timedelta(minutes=slot * interval)
    return Candle(SYMBOL, price, price + 5, price - 5, price + 1,
                  10 + slot, start, start + timedelta(minutes=interval),
                  is_closed=True, slot=slot, is_complete=complete)


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

    def test_cli_replay_of_legacy_fifteen_minute_bars_does_not_manufacture_fast_setup(self):
        hub = MonitorHub(now=lambda: at(12, 0, day="2026-10-06"), replay=True)
        hub.seed_historical_candles([bar(0), bar(1), bar(2, price=120)])
        with patch("orb_monitor.time.sleep"):
            run_replay_simulation(hub)
        state = hub.snapshot()
        self.assertEqual(len(state["candles"]), 3)
        self.assertEqual(len(state["setups"]["15"]["signals"]), 1)
        self.assertEqual(state["candles_5m"], [])
        self.assertIsNone(state["active_candle_5m"])
        self.assertFalse(state["setups"]["5"]["range_locked"])
        self.assertEqual(state["setups"]["5"]["signals"], [])
        self.assertEqual(state["signals"], [])

    def test_cli_replay_with_native_five_minute_bars_can_confirm_both_setups(self):
        hub = MonitorHub(now=lambda: at(12, 0, day="2026-10-06"), replay=True)
        hub.seed_historical_candles(self.opening(110) + [bar(9, 5, price=120)], interval_minutes=5)
        with patch("orb_monitor.time.sleep"):
            run_replay_simulation(hub)
        state = hub.snapshot()
        self.assertEqual(len(state["candles_5m"]), 10)
        self.assertEqual(len(state["setups"]["15"]["signals"]), 1)
        self.assertEqual(len(state["setups"]["5"]["signals"]), 1)
        self.assertEqual(len(state["signals"]), 1)
        self.assertEqual(state["signals"][0]["timestamp"], at(10, 5).isoformat())

    def test_cli_replay_preserves_incomplete_opening_coverage_and_invalid_fast_close(self):
        for problem in ("incomplete opening", "invalid confirmation"):
            with self.subTest(problem=problem):
                five = self.opening(110) + [bar(9, 5, price=120)]
                if problem == "incomplete opening":
                    five[1].is_complete = False
                else:
                    five[-1].close_is_valid = False
                hub = MonitorHub(now=lambda: at(12, 0, day="2026-10-06"), replay=True)
                hub.seed_historical_candles(five, interval_minutes=5)
                with patch("orb_monitor.time.sleep"):
                    run_replay_simulation(hub)
                state = hub.snapshot()
                self.assertEqual(state["signals"], [])
                self.assertEqual(state["setups"]["5"]["signals"], [])
                if problem == "incomplete opening":
                    self.assertFalse(state["setups"]["15"]["range_locked"])
                    self.assertFalse(state["setups"]["5"]["range_locked"])
                    self.assertFalse(state["candles_5m"][1]["is_complete"])
                else:
                    self.assertFalse(state["candles_5m"][-1]["close_is_valid"])
                    self.assertIsNone(state["setups"]["5"]["confirmation_direction"])

    def test_cli_replay_missing_five_minute_child_cannot_create_valid_parent_confirmation(self):
        five = self.opening() + [bar(9, 5, price=120), bar(11, 5, price=120)]
        hub = MonitorHub(now=lambda: at(12, 0, day="2026-10-06"), replay=True)
        hub.seed_historical_candles(five, interval_minutes=5)
        with patch("orb_monitor.time.sleep"):
            run_replay_simulation(hub)
        state = hub.snapshot()
        self.assertEqual(len(state["candles_5m"]), 11)
        self.assertFalse(state["candles"][-1]["is_complete"])
        self.assertFalse(state["candles"][-1]["close_is_valid"])
        self.assertEqual(state["setups"]["15"]["signals"], [])
        self.assertEqual(len(state["setups"]["5"]["signals"]), 1)
        self.assertEqual(state["signals"], [])


class ProviderIsolationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(11, 0))

    def test_source_switch_isolates_prices_history_and_range_state(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            hub.seed_historical_candles([bar(slot, price=100) for slot in range(3)])
            yahoo = hub.snapshot()
            hub.activate_source("shoonya")
            switched = hub.snapshot()
            self.assertEqual(switched["candles"], [])
            self.assertIsNone(switched["current_price"])
            self.assertFalse(switched["range_locked"])
            self.assertEqual(switched["signals"], [])
            hub.seed_historical_candles([bar(slot, 5, price=200) for slot in range(9)], interval_minutes=5)
            shoonya = hub.snapshot()
            hub.activate_source("yahoo")
            self.assertEqual(hub.snapshot()["candles"], yahoo["candles"])
            self.assertEqual(hub.snapshot()["current_price"], yahoo["current_price"])
            self.assertEqual(hub.snapshot()["candles_5m"], [])
            hub.activate_source("shoonya")
            self.assertEqual(hub.snapshot()["candles"], shoonya["candles"])
            self.assertEqual(hub.snapshot()["candles_5m"], shoonya["candles_5m"])

    def test_restart_keeps_both_provider_histories_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            hub.seed_historical_candles([bar(slot, price=100) for slot in range(3)])
            hub.activate_source("shoonya")
            five = [bar(slot, 5, price=200) for slot in range(9)]
            hub.seed_historical_candles(five, interval_minutes=5)
            restored = MonitorHub(now=self.clock, cache_dir=directory)
            restored.seed_historical_candles([])
            self.assertEqual(restored.snapshot()["candles"][0]["open"], 100)
            self.assertEqual(restored.snapshot()["candles_5m"], [])
            restored.activate_source("shoonya")
            self.assertEqual(restored.snapshot()["candles"][0]["open"], 200)
            self.assertEqual(restored.snapshot()["candles_5m"], [c.to_dict() for c in five])

    def test_legacy_date_cache_is_readable_only_as_yahoo_history(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = [bar(slot, day="2026-10-02") for slot in range(3)]
            path = Path(directory) / "2026-10-02.json"
            path.write_text(json.dumps([c.to_dict() for c in legacy]))
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            self.assertEqual(hub.snapshot("2026-10-02")["candles"], [c.to_dict() for c in legacy])
            hub.activate_source("shoonya")
            self.assertEqual(hub.snapshot("2026-10-02")["candles"], [])
            self.assertEqual(json.loads(path.read_text()), [c.to_dict() for c in legacy])

    def test_invalid_provider_does_not_reset_existing_session(self):
        hub = MonitorHub(now=self.clock)
        hub.seed_historical_candles([bar(slot) for slot in range(3)])
        before = hub.snapshot()
        with self.assertRaises(ValueError):
            hub.activate_source("unknown")
        self.assertEqual(hub.snapshot(), before)


class SourceHTTPTests(unittest.TestCase):
    class Connection:
        def __init__(self, request):
            self.request = io.BytesIO(request)
            self.response = io.BytesIO()

        def makefile(self, *_args, **_kwargs):
            return self.request

        def sendall(self, data):
            self.response.write(data)

    class Manager:
        def __init__(self, hub):
            self.hub = hub
            self.calls = []

        def select_source(self, source):
            self.calls.append(source)
            self.hub.activate_source(source)
            return source

    def setUp(self):
        self.hub = MonitorHub(now=lambda: at(11, 0))
        self.manager = self.Manager(self.hub)
        self.hub.feed_manager = self.manager

    def post(self, payload):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        header = (f"POST /api/source HTTP/1.0\r\nHost: localhost\r\n"
                  f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n").encode()
        connection = self.Connection(header + data)
        MonitorHTTPHandler(connection, ("127.0.0.1", 1234), None, hub=self.hub)
        headers, body = connection.response.getvalue().split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), json.loads(body)

    def test_source_post_switches_active_manager_and_returns_clean_snapshot(self):
        self.hub.seed_historical_candles([bar(slot) for slot in range(3)])
        status, state = self.post({"source": "shoonya"})
        self.assertEqual(status, 200)
        self.assertEqual(self.manager.calls, ["shoonya"])
        self.assertEqual(state["source_id"], "shoonya")
        self.assertEqual(state["candles"], [])
        self.assertEqual(state["candles_5m"], [])
        self.assertFalse(state["range_locked"])

    def test_bad_source_payloads_never_call_manager(self):
        before = self.hub.snapshot()
        for payload in ({"source": "invalid"}, {}, b"{not json}", ["shoonya"]):
            with self.subTest(payload=payload):
                status, _ = self.post(payload)
                self.assertGreaterEqual(status, 400)
                self.assertLess(status, 500)
                self.assertEqual(self.manager.calls, [])
                self.assertEqual(self.hub.snapshot(), before)

    def test_source_post_without_manager_has_explicit_error(self):
        self.hub.feed_manager = None
        status, response = self.post({"source": "shoonya"})
        self.assertGreaterEqual(status, 400)
        self.assertLess(status, 600)
        self.assertIn("message", response)
        self.assertEqual(self.hub.snapshot()["source_id"], "yahoo")


if __name__ == "__main__":
    unittest.main()
