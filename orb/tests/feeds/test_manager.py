"""Source selection serializes socket replacement and cancels old work."""

import threading
import unittest
from unittest.mock import patch

from orb.feeds import FeedManager, HISTORY_CONCURRENCY, ShoonyaFeed, YahooFeed
from orb.market import INSTRUMENTS, PRIMARY_SYMBOL

from orb.tests.support.providers import DummyFeed, FakeApi, Hub, row


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()
        self.manager = FeedManager(self.hub, yahoo_factory=DummyFeed, shoonya_factory=DummyFeed)
        self.addCleanup(self.manager.stop)

    def test_switch_closes_previous_feed_and_invalidates_every_old_write(self):
        self.manager.select_source("yahoo")
        old = self.manager._feed
        self.manager.select_source("shoonya")
        self.assertTrue(old.closed)
        self.assertLess(self.hub.events.index(("stop", old.generation)),
                        self.hub.events.index(("activate", "shoonya")))
        self.assertFalse(self.manager._apply(old.generation, lambda: self.hub.process_tick(999)))
        self.assertEqual(self.hub.ticks, [])
        self.assertTrue(self.manager._apply(self.manager._generation, lambda: self.hub.process_tick(100)))
        self.manager.stop()
        self.assertFalse(self.manager._apply(self.manager._generation - 1,
                                              lambda: self.hub.process_tick(999)))

    def test_idempotent_healthy_selection_but_error_can_retry(self):
        self.manager.select_source("shoonya")
        old = self.manager._feed
        self.manager.select_source("shoonya")
        self.assertIs(self.manager._feed, old)
        self.hub.feed_status = "error"
        self.manager.select_source("shoonya")
        self.assertTrue(old.closed)
        self.assertIsNot(self.manager._feed, old)

    def test_invalid_source_leaves_current_feed_active(self):
        self.manager.select_source("yahoo")
        old = self.manager._feed
        with self.assertRaises(ValueError):
            self.manager.select_source("other")
        self.assertIs(self.manager._feed, old)
        self.assertFalse(old.closed)

    def test_history_locks_are_shared_across_feed_instances_but_not_providers(self):
        first = ShoonyaFeed(self.manager, 1)
        second = ShoonyaFeed(self.manager, 2)
        yahoo = YahooFeed(self.manager, 3)
        self.assertIs(first.history_locks, second.history_locks)
        self.assertIs(first.history_slots, second.history_slots)
        self.assertIsNot(first.history_slots, yahoo.history_slots)
        for symbol in INSTRUMENTS:
            self.assertIsNot(first.history_locks[symbol], yahoo.history_locks[symbol])

    def test_waiting_for_provider_capacity_cancels_without_starting_an_http_request(self):
        for provider in (YahooFeed, ShoonyaFeed):
            for stopped in (True, False):
                with self.subTest(provider=provider.__name__, stopped=stopped):
                    feed = provider(self.manager, 0)
                    feed.connected, feed.connection = True, 1
                    requested, entered = [], threading.Event()
                    if provider is YahooFeed:
                        feed.history_fetcher = lambda symbol: requested.append(symbol)
                    else:
                        feed.api = FakeApi(rows=[row()])
                    for _ in range(HISTORY_CONCURRENCY):
                        feed.history_slots.acquire()
                    original = feed._is_connected
                    checks = [0]
                    def checked(connection):
                        checks[0] += 1
                        if checks[0] >= 2:
                            entered.set()
                        return original(connection)
                    worker = threading.Thread(target=feed._refresh_history, args=(1, PRIMARY_SYMBOL))
                    try:
                        with patch.object(feed, "_is_connected", side_effect=checked):
                            worker.start()
                            self.assertTrue(entered.wait(1))
                            if stopped:
                                feed.stopped.set()
                            else:
                                feed.connection += 1
                            worker.join(1)
                        self.assertFalse(worker.is_alive())
                        self.assertFalse(feed.history_locks[PRIMARY_SYMBOL].locked())
                        self.assertEqual(requested, [])
                        if provider is ShoonyaFeed:
                            self.assertEqual(feed.api.history_requests, [])
                    finally:
                        feed.stopped.set()
                        for _ in range(HISTORY_CONCURRENCY):
                            feed.history_slots.release()
                        worker.join(1)

    def test_waiting_history_cancels_when_feed_stops_or_connection_changes(self):
        for provider in (YahooFeed, ShoonyaFeed):
            for stopped in (True, False):
                with self.subTest(provider=provider.__name__, stopped=stopped):
                    requested = []
                    feed = provider(self.manager, 0)
                    feed.connected, feed.connection = True, 1
                    if provider is YahooFeed:
                        feed.history_fetcher = lambda symbol: requested.append(symbol)
                    else:
                        feed.api = FakeApi(rows=[row()])
                    lock = feed.history_locks[PRIMARY_SYMBOL]
                    lock.acquire()
                    entered = threading.Event()
                    is_connected = feed._is_connected

                    def checked(connection):
                        entered.set()
                        return is_connected(connection)

                    worker = threading.Thread(target=feed._refresh_history, args=(1, PRIMARY_SYMBOL))
                    try:
                        with patch.object(feed, "_is_connected", side_effect=checked):
                            worker.start()
                            self.assertTrue(entered.wait(1))
                            if stopped:
                                feed.stopped.set()
                            else:
                                feed.connection += 1
                            worker.join(1)
                            self.assertFalse(worker.is_alive())
                        self.assertTrue(lock.locked())
                        self.assertEqual(requested, [])
                        if provider is ShoonyaFeed:
                            self.assertEqual(feed.api.history_requests, [])
                    finally:
                        feed.stopped.set()
                        lock.release()
                        worker.join(1)

    def test_switches_are_serialized_while_old_socket_closes(self):
        entered, release = threading.Event(), threading.Event()
        manager = self.manager
        manager.select_source("yahoo")
        old = manager._feed
        original = old.stop

        def stop():
            entered.set()
            release.wait(3)
            original()

        old.stop = stop
        first = threading.Thread(target=manager.select_source, args=("shoonya",))
        second = threading.Thread(target=manager.select_source, args=("yahoo",))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        self.assertEqual([event for event in self.hub.events if event[0] == "start"], [("start", 1)])
        release.set()
        first.join(3)
        second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(manager.source, "yahoo")
        self.assertEqual([event[0] for event in self.hub.events],
                         ["activate", "start", "stop", "activate", "start", "stop", "activate", "start"])
