"""Application journeys preserve day boundaries, history browsing, and restart behavior."""

from datetime import timedelta
import queue
import tempfile
import unittest

from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import session_bar as bar, timestamp


class SessionJourneyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(timestamp("2026-10-05", 10, 5))
        self.hub = MonitorHub(now=self.clock)

    def test_monday_start_does_not_seed_friday_into_live_chart(self):
        self.hub.seed_historical_candles([bar("2026-10-02", slot) for slot in range(25)])
        state = self.hub.snapshot()
        self.assertEqual(state["date"], "2026-10-05")
        self.assertEqual(state["candles"], [])
        self.assertIsNone(state["active_candle"])
        self.assertIsNone(state["orb_high"])
        self.assertEqual(self.hub.history()["dates"], ["2026-10-02"])

    def test_same_slot_on_new_day_starts_new_candle(self):
        self.clock.value = timestamp("2026-10-02", 9, 18)
        self.hub.advance_time()
        self.hub.process_tick(100, 1, self.clock.value)
        self.clock.value = timestamp("2026-10-05", 9, 18)
        self.hub.process_tick(200, 2, self.clock.value)
        state = self.hub.snapshot()
        self.assertEqual(state["date"], "2026-10-05")
        self.assertEqual(state["candles"], [])
        self.assertEqual(state["active_candle"]["slot"], 0)
        self.assertEqual(state["active_candle"]["open"], 200)
        self.assertEqual(state["active_candle"]["low"], 200)
        self.assertEqual(state["active_candle"]["volume"], 2)
        self.assertEqual(self.hub.snapshot("2026-10-02")["candles"][0]["close"], 100)

    def test_midnight_rolls_day_without_waiting_for_quote(self):
        self.hub.seed_historical_candles([bar("2026-10-05", slot) for slot in range(3)])
        client = queue.Queue()
        self.hub.register_client(client)
        client.get_nowait()
        self.clock.value = timestamp("2026-10-06", 0, 0)
        self.hub.advance_time()
        event = client.get_nowait()
        self.assertEqual(event["type"], "session")
        self.assertEqual(event["date"], "2026-10-06")
        self.assertEqual(event["today"], "2026-10-06")
        self.assertEqual(event["candles"], [])
        self.assertEqual(event["signals"], [])
        self.assertIsNone(event["current_price"])
        self.assertFalse(event["range_locked"])
        self.assertEqual(event["market_status"], "closed")

    def test_weekend_still_has_today_date_and_empty_chart(self):
        self.clock.value = timestamp("2026-10-10", 10, 30)
        state = self.hub.snapshot()
        self.assertEqual(state["date"], "2026-10-10")
        self.assertEqual(state["today"], "2026-10-10")
        self.assertEqual(state["market_status"], "closed")
        self.assertEqual(state["candles"], [])
        self.assertFalse(self.hub.process_tick(100, tick_time=self.clock.value))

    def test_partial_seed_remains_active_until_its_close(self):
        self.clock.value = timestamp("2026-10-05", 9, 50)
        self.hub.seed_historical_candles([bar("2026-10-05", slot) for slot in range(3)])
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles"]), 2)
        self.assertEqual(state["active_candle"]["slot"], 2)
        self.assertFalse(state["active_candle"]["is_closed"])
        self.assertTrue(state["range_locked"])
        self.assertFalse(state["setups"]["5"]["range_locked"])
        self.assertEqual(state["orb_high"], 110)
        for minute in range(50, 60):
            self.clock.value = timestamp("2026-10-05", 9, minute)
            self.hub.process_tick(100, tick_time=self.clock.value)
        self.clock.value = timestamp("2026-10-05", 10, 0)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles"]), 3)
        self.assertIsNone(state["active_candle"])
        self.assertTrue(state["range_locked"])

    def test_final_candle_closes_once_without_next_quote(self):
        self.clock.value = timestamp("2026-10-05", 15, 17)
        self.hub.process_tick(100, 1, self.clock.value)
        self.clock.value = timestamp("2026-10-05", 15, 29, 59)
        self.hub.process_tick(105, 1, self.clock.value)
        self.clock.value = timestamp("2026-10-05", 15, 30)
        self.hub.advance_time()
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles"]), 1)
        self.assertEqual(state["candles"][0]["slot"], 24)
        self.assertEqual(state["candles"][0]["close"], 105)
        self.assertTrue(state["candles"][0]["is_closed"])
        self.assertIsNone(state["active_candle"])
        self.assertEqual(state["market_status"], "closed")
        self.assertFalse(self.hub.process_tick(999, tick_time=self.clock.value))

    def test_closing_timer_can_confirm_breakout(self):
        self.hub.seed_historical_candles([bar("2026-10-05", slot) for slot in range(3)])
        self.clock.value = timestamp("2026-10-05", 10, 14, 59)
        self.hub.process_tick(115, tick_time=self.clock.value)
        self.assertEqual(self.hub.snapshot()["setups"]["15"]["signals"], [])
        self.clock.value = timestamp("2026-10-05", 10, 15)
        self.hub.advance_time()
        state = self.hub.snapshot()
        signals = state["setups"]["15"]["signals"]
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["price"], 115)
        self.assertEqual(signals[0]["timestamp"], self.clock.value.isoformat())
        self.assertEqual(state["signals"], [])

    def test_starting_midway_through_opening_bar_cannot_confirm_range(self):
        self.clock.value = timestamp("2026-10-05", 9, 20)
        self.hub.process_tick(100, tick_time=self.clock.value)
        self.clock.value = timestamp("2026-10-05", 9, 30)
        self.hub.process_tick(105, tick_time=self.clock.value)
        self.clock.value = timestamp("2026-10-05", 9, 45)
        self.hub.process_tick(110, tick_time=self.clock.value)
        self.clock.value = timestamp("2026-10-05", 10)
        self.hub.advance_time()
        state = self.hub.snapshot()
        self.assertEqual(len(state["candles"]), 3)
        self.assertFalse(state["candles"][0]["is_complete"])
        self.assertFalse(state["candles"][1]["is_complete"])
        self.assertFalse(state["candles"][2]["is_complete"])
        self.assertFalse(state["range_locked"])
        self.hub.process_tick(200, tick_time=self.clock.value)
        self.clock.value = timestamp("2026-10-05", 10, 15)
        self.hub.advance_time()
        self.assertEqual(self.hub.signals, [])

    def test_old_out_of_order_off_session_and_future_quotes_are_ignored(self):
        self.hub.process_tick(100, 1, self.clock.value)
        before = self.hub.snapshot()
        rejected = [timestamp("2026-10-02", 10, 5), timestamp("2026-10-05", 10, 4),
                    timestamp("2026-10-05", 8), timestamp("2026-10-05", 16),
                    timestamp("2026-10-06", 10), timestamp("2026-10-05", 10, 10)]
        for tick_time in rejected:
            with self.subTest(time=tick_time):
                self.assertFalse(self.hub.process_tick(999, 50, tick_time))
        self.assertEqual(self.hub.snapshot(), before)

    def test_tick_for_already_closed_bar_cannot_reopen_it(self):
        self.hub.seed_historical_candles([bar("2026-10-05", slot) for slot in range(3)])
        self.assertFalse(self.hub.process_tick(999, tick_time=timestamp("2026-10-05", 9, 59)))
        self.assertIsNone(self.hub.active_candle)
        self.assertEqual(len(self.hub.candles), 3)

    def test_today_history_and_live_quotes_never_duplicate_seeded_bar(self):
        self.hub.seed_historical_candles([bar("2026-10-05", slot) for slot in range(4)])
        self.hub.process_tick(105, tick_time=self.clock.value)
        self.clock.value = timestamp("2026-10-05", 10, 15)
        self.hub.process_tick(106, tick_time=self.clock.value)
        starts = [c.start_time for c in self.hub.candles]
        self.assertEqual(len(starts), 4)
        self.assertEqual(len(set(starts)), 4)
        self.assertEqual(self.hub.active_candle.slot, 4)

    def test_history_snapshot_has_signals_but_never_changes_live_state(self):
        history = [bar("2026-10-02", slot) for slot in range(3)]
        history += [bar("2026-10-02", 3, high=120, close=115)]
        self.hub.seed_historical_candles(history + [bar("2026-10-05", 0, high=250, low=200, close=225)])
        before = self.hub.snapshot()
        past = self.hub.snapshot("2026-10-02")
        self.assertEqual(past["orb_high"], 110)
        self.assertEqual(past["orb_low"], 90)
        self.assertTrue(past["range_locked"])
        self.assertEqual(len(past["setups"]["15"]["signals"]), 1)
        self.assertEqual(past["setups"]["15"]["signals"][0]["price"], 115)
        self.assertEqual(past["setups"]["5"]["signals"], [])
        self.assertEqual(past["signals"], [])
        self.assertEqual(past["market_status"], "closed")
        self.assertEqual(self.hub.snapshot(), before)

    def test_missing_history_is_explicit_and_stays_on_requested_date(self):
        state = self.hub.snapshot("2020-01-01")
        self.assertEqual(state["date"], "2020-01-01")
        self.assertEqual(state["candles"], [])
        self.assertIn("message", state)
        self.assertIn("60 calendar days", state["history_note"])
        self.assertEqual(self.hub.date, "2026-10-05")

    def test_cached_completed_bars_restore_offline_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            hub.seed_historical_candles([bar("2026-10-05", slot) for slot in range(3)])
            restored = MonitorHub(now=self.clock, cache_dir=directory)
            restored.seed_historical_candles([])
            self.assertEqual(restored.snapshot()["candles"], hub.snapshot()["candles"])
            self.assertTrue(restored.snapshot()["range_locked"])
            self.clock.value = timestamp("2026-10-06", 9, 20)
            next_day = MonitorHub(now=self.clock, cache_dir=directory)
            next_day.seed_historical_candles([])
            self.assertEqual(next_day.snapshot()["candles"], [])
            self.assertEqual(len(next_day.snapshot("2026-10-05")["candles"]), 3)

    def test_downloaded_completed_history_persists_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            hub.seed_historical_candles([bar("2026-10-02", slot) for slot in range(3)])
            restored = MonitorHub(now=self.clock, cache_dir=directory)
            restored.seed_historical_candles([])
            self.assertEqual(len(restored.snapshot("2026-10-02")["candles"]), 3)

    def test_feed_status_distinguishes_stale_feed_from_recent_quote(self):
        self.assertEqual(self.hub.snapshot()["market_status"], "waiting")
        self.hub.process_tick(100, tick_time=self.clock.value)
        self.assertEqual(self.hub.snapshot()["market_status"], "live")
        self.clock.value += timedelta(seconds=91)
        self.assertEqual(self.hub.snapshot()["market_status"], "waiting")
