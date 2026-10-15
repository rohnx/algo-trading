"""Session boundaries and historical browsing must never contaminate live data."""

from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb_monitor import MonitorHTTPHandler, MonitorHub, SYMBOL
from market import INSTRUMENTS
from signals import Candle, IST


def timestamp(day, hour=9, minute=15, second=0):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, second=second, tzinfo=IST)


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def bar(day, slot=0, *, high=110, low=90, close=100):
    start = timestamp(day) + timedelta(minutes=15 * slot)
    return Candle(SYMBOL, 100, high, low, close, 12, start,
                  start + timedelta(minutes=15), is_closed=True, slot=slot)


def five_bars(day, *, symbol=SYMBOL):
    opening = timestamp(day)
    return [Candle(symbol, 100, 110, 90, 100, 12, opening + timedelta(minutes=5 * slot),
                   opening + timedelta(minutes=5 * (slot + 1)), is_closed=True, slot=slot)
            for slot in range(9)]


class SessionTests(unittest.TestCase):
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

    def test_market_hours_publish_status_without_ticks_or_idle_snapshots(self):
        clock = Clock(timestamp("2026-10-05", 9, 14, 59))
        hub = MonitorHub(now=clock)
        hub.activate_source("shoonya")
        client = queue.Queue()
        hub.register_client(client)
        self.assertEqual(client.get_nowait()["market_status"], "closed")

        clock.value = timestamp("2026-10-05", 9, 15)
        hub.advance_time()
        opened = client.get_nowait()
        self.assertEqual((opened["type"], opened["market_status"]), ("status", "waiting"))
        hub.advance_time()
        self.assertTrue(client.empty())

        clock.value = timestamp("2026-10-05", 10, 5)
        for symbol in INSTRUMENTS:
            hub.seed_historical_candles(five_bars("2026-10-05", symbol=symbol), 5, symbol=symbol)
        self.assertTrue(client.empty())
        clock.value = timestamp("2026-10-05", 15, 30)
        hub.advance_time()
        closed = client.get_nowait()
        self.assertEqual((closed["type"], closed["market_status"]), ("status", "closed"))
        self.assertTrue(all(item["comparison_status"] == "closed" for item in closed["instruments"].values()))
        hub.advance_time()
        self.assertTrue(client.empty())

        clock.value = timestamp("2026-10-06", 9, 15)
        hub.advance_time()
        self.assertEqual(client.get_nowait()["type"], "session")
        self.assertTrue(client.empty(), "date rollover already carries the new market status")

    def test_sse_init_and_tick_include_session_dates(self):
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        self.assertEqual(initial["type"], "init")
        self.assertEqual(initial["date"], "2026-10-05")
        self.assertEqual(initial["today"], "2026-10-05")
        self.hub.process_tick(100, tick_time=self.clock.value)
        tick = client.get_nowait()
        self.assertEqual(tick["type"], "tick")
        self.assertEqual(tick["date"], initial["date"])
        self.assertEqual(tick["today"], initial["today"])

    def test_quote_events_serialize_only_active_bars_and_keep_instrument_summary(self):
        for symbol in INSTRUMENTS:
            self.hub.seed_historical_candles(five_bars("2026-10-05", symbol=symbol), 5, symbol=symbol)
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        closed = {id(candle) for instrument in self.hub.instruments.values()
                  for candle in instrument.candles + instrument.candles_5m}
        serialize = Candle.to_dict

        def active_only(candle):
            self.assertNotIn(id(candle), closed, "A quote must not serialize closed history")
            return serialize(candle)

        for symbol in INSTRUMENTS:
            quote_time = self.hub.instruments[symbol].market_time(self.clock.value)
            with self.subTest(symbol=symbol), patch.object(Candle, "to_dict", active_only):
                self.assertTrue(self.hub.process_tick(115, 2, quote_time, symbol=symbol))
                event = client.get_nowait()
            delta = event["instruments"][symbol]
            self.assertNotIn("candles", delta)
            self.assertNotIn("candles_5m", delta)
            full = self.hub.snapshot()["instruments"][symbol]
            self.assertEqual(delta, {key: value for key, value in full.items()
                                     if key not in ("candles", "candles_5m")})
            self.assertEqual(delta["active_candle_5m"]["close"], 115)
            self.assertEqual(delta["last_tick_time"], quote_time.isoformat())
            self.assertEqual(full["candles_5m"], initial["instruments"][symbol]["candles_5m"])

    def test_shoonya_reported_open_makes_focit_available_without_fake_history(self):
        self.hub.activate_source("shoonya")
        self.hub.set_feed_status("live")
        client = queue.Queue()
        self.hub.register_client(client)
        client.get_nowait()
        self.assertTrue(self.hub.process_tick(35000, tick_time=self.clock.value,
                                             symbol="FOCIT.BO", session_open=34000))
        event = client.get_nowait()
        focit = event["instruments"]["FOCIT.BO"]
        self.assertEqual(focit["session_open"], 34000)
        self.assertEqual(focit["comparison_status"], "live")
        self.assertAlmostEqual(focit["change_pct"], (35000 / 34000 - 1) * 100)
        state = self.hub.snapshot()["instruments"]["FOCIT.BO"]
        self.assertEqual(state["candles"], [])
        self.assertEqual(state["candles_5m"], [])
        self.assertIsNone(self.hub.snapshot()["session_open"])

    def test_candle_close_supplies_full_history_after_quote_delta(self):
        self.hub.seed_historical_candles(five_bars("2026-10-05"), 5)
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        self.hub.process_tick(115, 2, self.clock.value)
        self.assertNotIn("candles_5m", client.get_nowait()["instruments"][SYMBOL])
        self.clock.value = timestamp("2026-10-05", 10, 9, 59)
        self.hub.process_tick(116, 3, self.clock.value)
        client.get_nowait()
        self.clock.value = timestamp("2026-10-05", 10, 10)
        self.hub.advance_time()
        closed = client.get_nowait()
        self.assertEqual((closed["type"], closed["interval_minutes"]), ("candle_close", 5))
        instrument = closed["instruments"][SYMBOL]
        self.assertEqual(instrument["candles_5m"][:-1], initial["candles_5m"])
        self.assertEqual(instrument["candles_5m"][-1], closed["candle"])
        self.assertEqual(instrument, self.hub.snapshot()["instruments"][SYMBOL])

    def test_confirmed_signal_arrives_with_close_without_duplicate_signal_event(self):
        self.clock.value = timestamp("2026-10-05", 10, 0)
        opening = five_bars("2026-10-05")
        for candle in opening[6:]:
            candle.open = candle.close = 115
            candle.high, candle.low = 120, 110
        self.hub.seed_historical_candles(opening, 5)
        for minute in range(5):
            self.clock.value = timestamp("2026-10-05", 10, minute)
            self.hub.process_tick(125, tick_time=self.clock.value)
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        self.assertEqual(initial["last_tick_time"], self.clock.value.isoformat())
        self.assertEqual(initial["signals"], [])
        self.clock.value = timestamp("2026-10-05", 10, 5)
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.hub.advance_time()
        closed = client.get_nowait()
        self.assertEqual((closed["type"], closed["interval_minutes"]), ("candle_close", 5))
        self.assertEqual(len(closed["signals"]), 1)
        signal = closed["signals"][0]
        self.assertEqual(signal["direction"], "BULLISH")
        self.assertEqual(signal["timestamp"], self.clock.value.isoformat())
        self.assertEqual(signal["confirmations"]["15"]["confirmed_at"],
                         timestamp("2026-10-05", 10, 0).isoformat())
        self.assertEqual(signal["confirmations"]["5"]["confirmed_at"], self.clock.value.isoformat())
        self.assertIn(signal["message"], output.getvalue())
        self.assertTrue(client.empty())

    def test_backlogged_sse_client_receives_complete_snapshot(self):
        self.hub.seed_historical_candles(five_bars("2026-10-05"), 5)
        client = queue.Queue(maxsize=1)
        self.hub.register_client(client)
        self.hub.process_tick(100, tick_time=self.clock.value)
        recovered = client.get_nowait()
        self.assertEqual(recovered["type"], "init")
        self.assertEqual(recovered["active_candle"]["close"], 100)
        self.assertEqual(len(recovered["instruments"][SYMBOL]["candles_5m"]), 9)
        self.assertEqual(len(recovered["candles"]), 3)

    def test_backlogged_clients_share_one_complete_recovery_snapshot(self):
        clients = [queue.Queue(maxsize=1), queue.Queue(maxsize=1)]
        for client in clients:
            self.hub.register_client(client)
        with patch.object(self.hub, "_snapshot", wraps=self.hub._snapshot) as snapshot:
            self.hub.process_tick(100, tick_time=self.clock.value)
            self.assertEqual(snapshot.call_count, 1)
        events = [client.get_nowait() for client in clients]
        self.assertEqual(events[0], events[1])
        self.assertEqual(events[0]["type"], "init")
        self.assertEqual(events[0]["active_candle"]["close"], 100)


class NotificationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(timestamp("2026-10-05", 10, 0))
        self.notifier = Mock(spec=["enqueue"])
        self.hub = MonitorHub(now=self.clock, notifier=self.notifier)

    @staticmethod
    def confirming_history(day="2026-10-05", *, both=False):
        candles = five_bars(day)
        for candle in candles[6:]:
            candle.open = candle.close = 115
            candle.high, candle.low = 120, 110
        if both:
            start = timestamp(day, 10, 0)
            candles.append(Candle(SYMBOL, 125, 125, 125, 125, 0, start,
                                  start + timedelta(minutes=5), is_closed=True, slot=9))
        return candles

    def close_live_five_minute_bar(self, *, symbol=SYMBOL):
        for minute in range(5):
            self.clock.value = timestamp("2026-10-05", 10, minute)
            self.assertTrue(self.hub.process_tick(125, tick_time=self.clock(), symbol=symbol))
        self.clock.value = timestamp("2026-10-05", 10, 5)
        with patch("sys.stdout", io.StringIO()):
            self.hub.advance_time()

    def test_new_live_combined_nifty_confirmation_is_enqueued_once(self):
        self.hub.seed_historical_candles(self.confirming_history(), 5)
        self.notifier.enqueue.assert_not_called()
        self.close_live_five_minute_bar()
        self.assertEqual(len(self.hub.signals), 1)
        signal = self.hub.signals[0]
        self.assertEqual(signal.symbol, SYMBOL)
        self.assertEqual(signal.signal_type, "DUAL_ORB_BULLISH")
        self.assertEqual(signal.timestamp, self.clock())
        self.assertEqual(signal.confirmations["15"]["confirmed_at"], timestamp("2026-10-05", 10, 0).isoformat())
        self.assertEqual(signal.confirmations["5"]["confirmed_at"], self.clock().isoformat())
        self.notifier.enqueue.assert_called_once_with(signal, "yahoo", live=True)
        self.hub.advance_time()
        self.hub.snapshot()
        self.hub.history()
        self.hub._rebuild_strategy()
        self.hub.register_client(queue.Queue())
        self.hub.activate_source("shoonya")
        self.hub.activate_source("yahoo")
        self.notifier.enqueue.assert_called_once_with(signal, "yahoo", live=True)

    def test_seeded_history_rebuild_and_replay_snapshots_never_enqueue_old_signals(self):
        self.clock.value = timestamp("2026-10-05", 10, 10)
        history = (self.confirming_history("2026-10-02", both=True)
                   + self.confirming_history(both=True))
        self.hub.seed_historical_candles(history, 5)
        self.assertEqual(len(self.hub.signals), 1, "the fixture contains a historical combined signal")
        self.hub._rebuild_strategy()
        self.assertEqual(len(self.hub.snapshot("2026-10-02")["signals"]), 1)
        # Browser replay consumes saved session snapshots and never posts a signal.
        for _ in range(2):
            self.assertEqual(len(self.hub.snapshot()["signals"]), 1)
            self.hub.history()
        self.hub.register_client(queue.Queue())
        self.hub.activate_source("shoonya")
        self.hub.seed_historical_candles(history, 5)
        self.hub.activate_source("yahoo")
        self.assertEqual(len(self.hub.snapshot()["signals"]), 1)
        self.notifier.enqueue.assert_not_called()

    def test_live_history_recovers_the_recent_confirmation_missed_during_switch(self):
        self.clock.value = timestamp("2026-10-05", 10, 5, 37)
        self.hub.activate_source("shoonya")
        self.hub.seed_historical_candles(self.confirming_history(both=True), 5, live=True)
        signal = self.hub.signals[0]
        self.assertEqual(signal.timestamp, timestamp("2026-10-05", 10, 5))
        self.notifier.enqueue.assert_called_once_with(signal, "shoonya", live=True)
        # A repeated download or source change must not enqueue it again.
        self.hub.seed_historical_candles(self.confirming_history(both=True), 5, live=True)
        self.hub.activate_source("yahoo")
        self.hub.seed_historical_candles(self.confirming_history(both=True), 5, live=True)
        self.notifier.enqueue.assert_called_once_with(signal, "shoonya", live=True)

    def test_fresh_offline_history_and_browsing_do_not_send_alerts(self):
        self.clock.value = timestamp("2026-10-05", 10, 5, 37)
        self.hub.seed_historical_candles(self.confirming_history(both=True), 5)
        self.hub.snapshot()
        self.hub.history()
        self.hub.register_client(queue.Queue())
        self.hub._rebuild_strategy()
        self.notifier.enqueue.assert_not_called()

    def test_live_history_recovery_rejects_old_previous_day_and_future_signals(self):
        for clock, day in ((timestamp("2026-10-05", 10, 7, 1), "2026-10-05"),
                           (timestamp("2026-10-05", 10, 5, 37), "2026-10-02"),
                           (timestamp("2026-10-05", 10, 4, 59), "2026-10-05")):
            with self.subTest(clock=clock, day=day):
                hub = MonitorHub(now=lambda: clock, notifier=self.notifier)
                hub.seed_historical_candles(self.confirming_history(day, both=True), 5, live=True)
                self.notifier.enqueue.assert_not_called()

    def test_live_history_recovery_includes_two_minute_boundary_and_retries_rejection(self):
        self.clock.value = timestamp("2026-10-05", 10, 7)
        self.notifier.enqueue.return_value = False
        self.hub.seed_historical_candles(self.confirming_history(both=True), 5, live=True)
        self.assertEqual(self.notifier.enqueue.call_count, 1)
        self.notifier.enqueue.return_value = True
        self.hub.seed_historical_candles(self.confirming_history(both=True), 5, live=True)
        self.assertEqual(self.notifier.enqueue.call_count, 2)
        self.hub.seed_historical_candles(self.confirming_history(both=True), 5, live=True)
        self.assertEqual(self.notifier.enqueue.call_count, 2)

    def test_notification_status_is_exposed_without_configuration(self):
        self.notifier.status = "Telegram enabled: alert queued"
        self.assertEqual(self.hub.snapshot()["telegram_status"], self.notifier.status)
        self.assertEqual(self.hub._breakout_fields()["telegram_status"], self.notifier.status)

    def test_comparison_bar_closures_never_enqueue_notifications(self):
        symbol = "^NSEBANK"
        self.hub.seed_historical_candles(five_bars("2026-10-05", symbol=symbol), 5, symbol=symbol)
        self.close_live_five_minute_bar(symbol=symbol)
        self.assertEqual(self.hub.instruments[symbol].candles_5m[-1].close, 125)
        self.assertEqual(self.hub.signals, [])
        self.notifier.enqueue.assert_not_called()

    def test_single_setup_confirmation_does_not_enqueue_a_combined_alert(self):
        self.hub.seed_historical_candles(five_bars("2026-10-05"), 5)
        self.close_live_five_minute_bar()
        setups = self.hub.breakouts.get_setups()
        self.assertEqual(len(setups["5"]["signals"]), 1)
        self.assertEqual(setups["15"]["signals"], [])
        self.assertEqual(self.hub.signals, [])
        self.notifier.enqueue.assert_not_called()


class HTTPTests(unittest.TestCase):
    class Connection:
        def __init__(self, request):
            self.request = io.BytesIO(request)
            self.response = io.BytesIO()

        def makefile(self, *_args, **_kwargs):
            return self.request

        def sendall(self, value):
            self.response.write(value)

    def setUp(self):
        self.hub = MonitorHub(now=lambda: timestamp("2026-10-05", 16))
        self.hub.seed_historical_candles([bar("2026-10-02", 0)])

    def request(self, path, method="GET"):
        connection = self.Connection(f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        MonitorHTTPHandler(connection, ("127.0.0.1", 1234), None, hub=self.hub)
        headers, body = connection.response.getvalue().split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), body

    def test_history_endpoint_lists_available_dates(self):
        code, body = self.request("/api/history")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["dates"], ["2026-10-02"])
        self.assertEqual(data["earliest_date"], "2026-10-02")
        self.assertEqual(data["today"], "2026-10-05")

    def test_idle_stream_uses_a_comment_without_serializing_another_snapshot(self):
        initial = {"type": "init", **self.hub.snapshot()}
        client = queue.Queue(maxsize=100)
        with patch("orb_monitor.queue.Queue", return_value=client), \
                patch.object(client, "get", side_effect=[initial, queue.Empty, BrokenPipeError]), \
                patch.object(self.hub, "_snapshot", wraps=self.hub._snapshot) as snapshot:
            code, body = self.request("/api/stream")
        self.assertEqual(code, 200)
        blocks = body.split(b"\n\n")
        self.assertEqual(json.loads(blocks[0].removeprefix(b"data: ")), initial)
        self.assertEqual(blocks[1:], [b": keep-alive", b""])
        self.assertEqual(snapshot.call_count, 1, "only the initial connection needs full history")
        self.assertNotIn(client, self.hub.clients)

    def test_session_endpoint_selects_past_date_or_today(self):
        code, body = self.request("/api/session?date=2026-10-02")
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)["candles"]), 1)
        code, body = self.request("/api/session")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["date"], "2026-10-05")

    def test_invalid_date_is_rejected(self):
        for day in ("bad", "2026-99-99", "20261002"):
            with self.subTest(day=day):
                code, body = self.request(f"/api/session?date={day}")
                self.assertEqual(code, 400)
                self.assertIn("YYYY-MM-DD", json.loads(body)["message"])

    def test_missing_date_returns_empty_snapshot_with_message(self):
        code, body = self.request("/api/session?date=2020-01-01")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["date"], "2020-01-01")
        self.assertEqual(data["candles"], [])
        self.assertIn("message", data)

    def test_removed_mutating_endpoints_are_unavailable(self):
        for endpoint in ("/api/config", "/api/replay"):
            with self.subTest(endpoint=endpoint):
                code, _ = self.request(endpoint, "POST")
                self.assertIn(code, (404, 405, 501))

    def test_shoonya_help_returns_only_the_login_link(self):
        expected = "https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=test-client"
        with patch("feeds.shoonya_login_url", return_value=expected):
            code, body = self.request("/api/shoonya/help")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"login_url": expected})

    def test_public_ip_check_is_bounded_and_runs_from_the_monitor_host(self):
        with patch("orb_monitor.urlopen", return_value=io.BytesIO(b"203.0.113.8\n")) as fetch:
            code, body = self.request("/api/public-ip")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"ip": "203.0.113.8"})
        self.assertEqual(fetch.call_args.args[0].full_url, "https://ifconfig.me/ip")
        self.assertEqual(fetch.call_args.kwargs["timeout"], 3)

    def test_public_ip_failure_keeps_a_manual_recovery_instruction(self):
        for failure in (URLError("offline"), TimeoutError()):
            with self.subTest(failure=type(failure)), patch("orb_monitor.urlopen", side_effect=failure):
                code, body = self.request("/api/public-ip")
            self.assertEqual(code, 503)
            self.assertIn("curl -s ifconfig.me", json.loads(body)["message"])
        with patch("orb_monitor.urlopen", return_value=io.BytesIO(b"<html>service error</html>")):
            code, body = self.request("/api/public-ip")
        self.assertEqual(code, 503)
        self.assertNotIn("<html>", body.decode())


if __name__ == "__main__":
    unittest.main()
