"""Feed generation and instrument identity control application history admission."""

from dataclasses import replace
from datetime import timedelta
import queue
import unittest
from unittest.mock import Mock

from orb.feeds import FeedManager, shoonya_history, YahooFeed
from orb.market import INSTRUMENTS, PRIMARY_SYMBOL
from orb.orb_monitor import MonitorHub
from orb.signals import Candle

from orb.tests.support.market import five_bars, timestamp
from orb.tests.support.providers import Hub, NOW, row


class FeedHistoryAdmissionTests(unittest.TestCase):
    def test_only_current_generation_can_admit_live_provider_history(self):
        hub = Hub()
        manager = FeedManager(hub)
        feed = YahooFeed(manager, manager._generation)
        feed.seed([], PRIMARY_SYMBOL)
        feed.seed([], "^NSEBANK")
        self.assertEqual(hub.live_histories, [(PRIMARY_SYMBOL, True), ("^NSEBANK", True)])
        manager._generation += 1
        feed.seed([], PRIMARY_SYMBOL)
        self.assertEqual(len(hub.live_histories), 2, "late history from an old source cannot recover alerts")

    def test_real_hub_recovers_primary_alerts_but_comparison_history_never_notifies(self):
        now = timestamp("2026-10-05", 10, 5, 37)
        notifier = Mock(spec=["enqueue"])
        hub = MonitorHub(now=lambda: now, notifier=notifier)
        manager = FeedManager(hub)
        feed = YahooFeed(manager, manager._generation)
        history = five_bars("2026-10-05")
        for candle in history[6:]:
            candle.open = candle.close = 115
            candle.high, candle.low = 120, 110
        start = timestamp("2026-10-05", 10, 0)
        history.append(Candle(PRIMARY_SYMBOL, 125, 125, 125, 125, 0, start,
                              start + timedelta(minutes=5), is_closed=True, slot=9))

        bank = "^NSEBANK"
        feed.seed([replace(candle, symbol=bank) for candle in history], bank)
        self.assertEqual(len(hub.snapshot()["instruments"][bank]["candles_5m"]), 10)
        self.assertEqual(hub.signals, [])
        notifier.enqueue.assert_not_called()

        feed.seed(history, PRIMARY_SYMBOL)
        self.assertEqual(len(hub.signals), 1)
        notifier.enqueue.assert_called_once_with(hub.signals[0], "yahoo", live=True)
        feed.seed(history, PRIMARY_SYMBOL)
        notifier.enqueue.assert_called_once_with(hub.signals[0], "yahoo", live=True)

    def test_backfills_send_only_the_changed_comparison_history(self):
        from orb.orb_monitor import MonitorHub

        hub = MonitorHub(now=lambda: NOW)
        manager = FeedManager(hub)
        feed = YahooFeed(manager, manager._generation)
        client = queue.Queue()
        hub.register_client(client)
        client.get_nowait()
        primary = shoonya_history([row()], now=NOW)
        feed.seed(primary, PRIMARY_SYMBOL)
        event = client.get_nowait()
        self.assertEqual(event["type"], "init")
        self.assertEqual(set(event["instruments"]), set(INSTRUMENTS))
        primary_history = event["instruments"][PRIMARY_SYMBOL]["candles_5m"]

        bank = "^NSEBANK"
        feed.seed(shoonya_history([row()], now=NOW, symbol=bank), bank)
        event = client.get_nowait()
        self.assertEqual(event["type"], "state")
        self.assertEqual(set(event["instruments"]), {bank})
        self.assertEqual(len(event["instruments"][bank]["candles_5m"]), 1)
        self.assertEqual(hub.snapshot()["instruments"][PRIMARY_SYMBOL]["candles_5m"], primary_history)
        self.assertTrue(client.empty())
