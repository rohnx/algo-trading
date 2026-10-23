"""Yahoo connection replacement and history cutoff retain provider-time isolation."""

from dataclasses import replace
from datetime import timedelta
import threading
import unittest
from unittest.mock import patch

from orb.feeds import FeedManager, HISTORY_CONCURRENCY, shoonya_history, YAHOO_SYMBOLS, YahooFeed
from orb.market import INSTRUMENTS

from orb.tests.support.providers import DummyFeed, FakeYahooSocket, Hub, NOW, row


class YahooLifecycleTests(unittest.TestCase):
    def test_delayed_quotes_cross_history_cutoff_in_provider_time(self):
        hub, manager, socket = self.launch(lambda symbol: shoonya_history([row()], NOW, symbol=symbol))
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        delayed_time = NOW - timedelta(minutes=15)
        socket.callback({"id": "FOCIT.BO", "price": 30000, "time": delayed_time.timestamp() * 1000})
        socket.callback({"id": "FOCIT.BO", "price": 30010,
                         "time": (delayed_time + timedelta(seconds=1)).timestamp() * 1000})
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.tick_symbols, ["FOCIT.BO"])
        self.assertEqual(hub.ticks[0], (30010, 0, delayed_time + timedelta(seconds=1)))

    def test_switch_cancels_pending_dial_and_closes_late_connection_before_replacement(self):
        hub = Hub()

        class DialingSocket(FakeYahooSocket):
            def __init__(self):
                super().__init__()
                self.dialing, self.release = threading.Event(), threading.Event()
                self.connected = False
                self.closes = 0

            def subscribe(self, symbols):
                self.dialing.set()
                self.release.wait(3)
                self.connected = True

            def close(self):
                self.closes += 1
                self.connected = False
                super().close()
                self.release.set()

        socket = DialingSocket()
        self.addCleanup(socket.release.set)
        replacements = []

        def replacement(manager, generation):
            replacements.append((socket.connected, manager._feed.runner.is_alive()))
            return DummyFeed(manager, generation)

        manager = FeedManager(hub, shoonya_factory=replacement,
            yahoo_factory=lambda manager, generation: YahooFeed(manager, generation,
                websocket_factory=lambda: socket, history_fetcher=lambda symbol: self.fail("Stopped dial requested history")))
        self.addCleanup(manager.stop)
        manager.select_source("yahoo")
        old = manager._feed
        self.assertTrue(socket.dialing.wait(1))
        errors = []

        def switch():
            try:
                manager.select_source("shoonya")
            except Exception as exc:
                errors.append(exc)

        switching = threading.Thread(target=switch)
        switching.start()
        self.assertTrue(socket.closed.wait(1), "Stop must reach the socket without waiting for its dial lock")
        switching.join(1)
        self.assertFalse(switching.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(replacements, [(False, False)])
        self.assertGreaterEqual(socket.closes, 2, "A dial completing after stop must also close its new connection")
        self.assertFalse(old.worker.is_alive())
        self.assertFalse(socket.listening.is_set())
        self.assertEqual(hub.ticks, [])

    def test_registry_aliases_route_to_canonical_symbols_with_independent_histories(self):
        symbol = "NIFTY_FIN_SERVICE.NS"
        instrument = replace(INSTRUMENTS[symbol], yahoo_symbol="verified-provider-alias")
        aliases = {provider: canonical for provider, canonical in YAHOO_SYMBOLS.items() if canonical != symbol}
        aliases[instrument.yahoo_symbol] = symbol
        requested = []

        def history(canonical):
            requested.append(canonical)
            return shoonya_history([row()], NOW, symbol=canonical)

        with patch.dict(INSTRUMENTS, {symbol: instrument}), patch.dict(YAHOO_SYMBOLS, aliases, clear=True):
            hub, manager, socket = self.launch(history)
            self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
            self.assertEqual(set(requested), set(INSTRUMENTS))
            self.assertEqual(set(socket.symbols), set(aliases))
            for index, (provider, canonical) in enumerate(aliases.items()):
                socket.callback({"id": provider, "price": 100 + index,
                                 "time": (NOW + timedelta(seconds=index + 1)).timestamp() * 1000})
            socket.callback({"id": symbol, "price": 999, "time": NOW.timestamp() * 1000})
            self.assertTrue(hub.wait(lambda: len(hub.ticks) == len(INSTRUMENTS)))
            self.assertEqual(set(hub.tick_symbols), set(INSTRUMENTS))
            self.assertNotIn(999, [tick[0] for tick in hub.ticks])
            manager.stop()

    def launch(self, history_fetcher, now=lambda: NOW):
        hub, socket = Hub(), FakeYahooSocket()
        manager = FeedManager(hub, shoonya_factory=DummyFeed,
                              yahoo_factory=lambda manager, generation: YahooFeed(
                                  manager, generation, websocket_factory=lambda: socket,
                                  history_fetcher=history_fetcher, now=now))
        self.addCleanup(manager.stop)
        manager.select_source("yahoo")
        self.assertTrue(socket.listening.wait(1))
        return hub, manager, socket

    def test_switch_closes_actual_socket_and_ends_listener(self):
        hub, manager, socket = self.launch(lambda symbol: shoonya_history([row()], NOW, symbol=symbol))
        old = manager._feed
        self.assertEqual(socket.symbols, list(YAHOO_SYMBOLS))
        manager.select_source("shoonya")
        self.assertTrue(socket.closed.is_set())
        self.assertFalse(old.runner.is_alive())
        socket.callback({"id": "^NSEI", "price": 999, "time": NOW.timestamp() * 1000})
        self.assertEqual(hub.ticks, [])

    def test_returning_yahoo_backfills_after_previous_request_finishes(self):
        hub = Hub()
        initial_run = [True]
        release = threading.Event()
        self.addCleanup(release.set)
        calls = {symbol: 0 for symbol in INSTRUMENTS}
        sockets = []

        def socket_factory():
            socket = FakeYahooSocket()
            sockets.append(socket)
            return socket

        def history(symbol):
            with hub.condition:
                calls[symbol] += 1
                first = initial_run[0]
                hub.condition.notify_all()
            if first:
                release.wait(3)
            return shoonya_history([row(intc="105" if first else "106")], NOW, symbol=symbol)

        manager = FeedManager(hub, shoonya_factory=DummyFeed,
            yahoo_factory=lambda manager, generation: YahooFeed(manager, generation,
                websocket_factory=socket_factory, history_fetcher=history, now=lambda: NOW))
        self.addCleanup(manager.stop)
        manager.select_source("yahoo")
        self.assertTrue(hub.wait(lambda: sum(calls.values()) == HISTORY_CONCURRENCY))
        initial = {symbol for symbol, count in calls.items() if count}
        manager.select_source("shoonya")
        initial_run[0] = False
        manager.select_source("yahoo")
        self.assertTrue(sockets[-1].listening.wait(1))
        self.assertEqual(sum(calls.values()), HISTORY_CONCURRENCY)
        self.assertEqual(manager._feed.ready, {})
        release.set()
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual(calls, {symbol: 2 if symbol in initial else 1 for symbol in INSTRUMENTS})
        self.assertEqual(len(hub.histories), len(INSTRUMENTS))
        self.assertEqual({item[0][0].close for item in hub.histories}, {106})
        self.assertEqual(manager._feed.history_errors, {})

    def test_bank_history_failure_and_strict_quote_routing(self):
        def history(symbol):
            if symbol == "^NSEBANK":
                raise RuntimeError("unavailable")
            return shoonya_history([row()], NOW, symbol=symbol)

        hub, manager, socket = self.launch(history)
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual({item[2] for item in hub.histories}, set(INSTRUMENTS) - {"^NSEBANK"})
        self.assertIn("BANKNIFTY", hub.events[-1][2])
        for symbol, price in (("^NSEI", 100), ("^NSEBANK", 200), ("other", 999), (None, 999), (["^NSEI"], 999)):
            socket.callback({"id": symbol, "price": price, "time": (NOW + timedelta(seconds=1)).timestamp() * 1000})
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 2))
        self.assertEqual(set(zip(hub.tick_symbols, (tick[0] for tick in hub.ticks))),
                         {("^NSEI", 100), ("^NSEBANK", 200)})

    def test_backfill_skips_covered_quotes_only_for_the_successful_symbol(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        clock = {"now": NOW}

        def history(symbol):
            if symbol == "^NSEBANK":
                raise RuntimeError("unavailable")
            entered.set()
            release.wait(3)
            return shoonya_history([row(time="08-10-2026 10:05:00")], NOW, symbol=symbol)

        hub, manager, socket = self.launch(history, now=lambda: clock["now"])
        self.assertTrue(entered.wait(1))
        for symbol, price, seconds, volume in (("^NSEI", 99, 0, 8), ("^NSEI", 100, 2, 9),
                                                ("^NSEI", 106, 3, 2), ("^NSEBANK", 200, 0, 4)):
            socket.callback({"id": symbol, "price": price, "last_size": volume,
                             "time": (NOW + timedelta(seconds=seconds)).timestamp() * 1000})
        clock["now"] = NOW + timedelta(seconds=2)
        release.set()
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 2))
        self.assertEqual(set(zip(hub.tick_symbols, (tick[:2] for tick in hub.ticks))),
                         {("^NSEI", (106, 2)), ("^NSEBANK", (200, 4))})
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual(manager._feed.cutoff, {
            symbol: clock["now"] - timedelta(minutes=instrument.yahoo_delay_minutes)
            for symbol, instrument in INSTRUMENTS.items() if symbol != "^NSEBANK"})
        self.assertEqual(hub.histories[0][0][0].close, 105)

    def test_slow_bank_backfill_does_not_delay_primary_or_leak_after_switch(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def history(symbol):
            if symbol == "^NSEBANK":
                entered.set()
                release.wait(3)
                done.set()
            return shoonya_history([row()], NOW, symbol=symbol)

        hub, manager, socket = self.launch(history)
        self.assertTrue(entered.wait(1))
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        socket.callback({"id": "^NSEI", "price": 100, "time": (NOW + timedelta(seconds=1)).timestamp() * 1000})
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.tick_symbols, ["^NSEI"])
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS) - 1))
        manager.select_source("shoonya")
        release.set()
        self.assertTrue(done.wait(1))
        self.assertEqual({item[2] for item in hub.histories}, set(INSTRUMENTS) - {"^NSEBANK"})
