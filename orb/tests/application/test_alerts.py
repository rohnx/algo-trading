"""Only eligible combined live confirmations enter the durable notifier."""

from datetime import timedelta
import io
import queue
import unittest
from unittest.mock import Mock, patch

from orb.orb_monitor import MonitorHub, SYMBOL
from orb.signals import Candle

from orb.tests.support.clock import Clock
from orb.tests.support.market import five_bars, timestamp


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
