"""Shoonya socket routing, reconnect, and overflow reject obsolete work."""

from datetime import timedelta
import gc
import queue
import threading
from types import SimpleNamespace
import unittest
import weakref

from orb.feeds import _shoonya_api, FeedManager, ShoonyaFeed, TOKEN_SYMBOLS
from orb.market import INSTRUMENTS, PRIMARY_SYMBOL

from orb.tests.support.providers import (
    CREDENTIALS,
    DummyFeed,
    FakeApi,
    frame,
    Hub,
    NOW,
    row,
    SHOONYA_SYMBOLS,
    SUBSCRIPTIONS,
)


class ShoonyaLifecycleTests(unittest.TestCase):
    def launch(self, api, credentials=CREDENTIALS, *, fallback_history_fetcher=lambda symbol: [], now=lambda: NOW):
        hub = Hub()
        manager = FeedManager(hub, yahoo_factory=DummyFeed,
                              shoonya_factory=lambda manager, generation: ShoonyaFeed(
                                  manager, generation, api_factory=lambda: api,
                                  credentials=credentials, now=now,
                                  fallback_history_fetcher=fallback_history_fetcher))
        self.addCleanup(manager.stop)
        manager.select_source("shoonya")
        return hub, manager

    def test_exact_working_auth_and_supported_indices_on_one_socket(self):
        api = FakeApi()
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual(api.auth_kwargs, {"authcode": "auth", "Secret_Code": "secret",
                                          "client_id": "client", "UID": "user"})
        self.assertEqual(api.subscriptions, [SUBSCRIPTIONS])
        self.assertTrue(all(event.wait(1) for event in api.history_entries.values()))
        self.assertEqual({(request["exchange"], request["token"]) for request in api.history_requests}, set(TOKEN_SYMBOLS))
        self.assertTrue(all(request["interval"] == 5 for request in api.history_requests))
        self.assertEqual(api.start_calls, 1)
        api.callbacks["subscribe_callback"](frame(lp="100", ft=NOW.timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        old_callback = api.callbacks["subscribe_callback"]
        manager.select_source("yahoo")
        self.assertTrue(api.closed.is_set())
        old_callback(frame(lp="999", ft=(NOW + timedelta(seconds=1)).timestamp()))
        self.assertEqual(len(hub.ticks), 1)

    def test_routes_quotes_by_exchange_and_token_with_independent_volume_and_time(self):
        api = FakeApi()
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        callback = api.callbacks["subscribe_callback"]
        callback(frame(t="tk", lp="100", ft=NOW.timestamp(), v="1000"))
        callback(frame(t="tk", tk="26009", lp="200", ft=(NOW - timedelta(seconds=1)).timestamp(), v="20"))
        callback(frame(lp="101", ft=NOW.timestamp(), v="1007"))
        callback(frame(tk="26009", lp="201", ft=NOW.timestamp(), v="23"))
        for message in (frame(tk="unknown", lp="999", ft=NOW.timestamp()),
                        frame(e="BSE", tk="26009", lp="999", ft=NOW.timestamp()),
                        {"t": "tf", "e": "NSE", "lp": "999", "ft": NOW.timestamp()}):
            callback(message)
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 4))
        by_symbol = {symbol: [tick[:2] for tick, routed in zip(hub.ticks, hub.tick_symbols) if routed == symbol]
                     for symbol in INSTRUMENTS}
        self.assertEqual(by_symbol, {symbol: {"^NSEI": [(100, 0), (101, 7)], "^NSEBANK": [(200, 0), (201, 3)]}.get(symbol, []) for symbol in INSTRUMENTS})

    def test_late_history_completion_and_disconnected_ticks_are_ignored(self):
        api = FakeApi(rows=[row()])
        api.history_release = threading.Event()
        hub, manager = self.launch(api)
        self.assertTrue(api.history_entered.wait(1))
        old = manager._feed
        manager.select_source("yahoo")
        api.history_release.set()
        self.assertTrue(api.history_done.wait(1))
        old.worker.join(1)
        self.assertEqual(hub.histories, [])
        self.assertEqual(hub.ticks, [])

    def test_disconnect_reconnect_resubscribes_and_backfills_before_drain(self):
        api = FakeApi(rows=[row()])
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertTrue(hub.wait(lambda: len(hub.histories) == len(SHOONYA_SYMBOLS)))
        api.callbacks["socket_close_callback"]()
        self.assertTrue(hub.wait(lambda: hub.feed_status == "disconnected"))
        api.callbacks["subscribe_callback"](frame(lp="999", ft=(NOW + timedelta(seconds=1)).timestamp()))
        api.callbacks["socket_open_callback"]()
        self.assertTrue(hub.wait(lambda: len(hub.histories) == 2 * len(SHOONYA_SYMBOLS) and hub.feed_status == "live"))
        self.assertEqual(api.subscriptions, [SUBSCRIPTIONS, SUBSCRIPTIONS])
        api.callbacks["subscribe_callback"](frame(lp="100", ft=(NOW + timedelta(seconds=1)).timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.ticks[0][0], 100)

    def test_installed_sdk_disconnected_stop_still_stops_reconnect_thread(self):
        api = _shoonya_api()
        api._NorenApi__stop_event = threading.Event()
        feed = ShoonyaFeed(FeedManager(Hub()), 0)
        stopped_feed = weakref.ref(feed)
        api._NorenApi__on_open = feed._on_open
        api._NorenApi__on_disconnect = feed._on_close
        api._NorenApi__on_error = feed._on_error
        api._NorenApi__subscribe_callback = feed._on_tick
        api._NorenApi__access_token = "saved-token"
        del feed
        self.assertIsNotNone(stopped_feed())

        class Socket:
            closed = False

            def close(self):
                self.closed = True

        socket = Socket()
        api._NorenApi__websocket = socket
        api._NorenApi__websocket_connected = False
        api._NorenApi__ws_thread = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: True)
        with self.assertRaisesRegex(RuntimeError, "Shoonya socket did not stop"):
            api.close_websocket()
        self.assertIsNotNone(stopped_feed())
        api._NorenApi__stop_event.clear()
        thread = threading.Thread(target=lambda: api._NorenApi__stop_event.wait(2))
        api._NorenApi__ws_thread = thread
        thread.start()
        api.close_websocket()
        self.assertTrue(socket.closed)
        self.assertTrue(api._NorenApi__stop_event.is_set())
        self.assertFalse(thread.is_alive())
        gc.collect()
        self.assertIsNone(stopped_feed())
        self.assertEqual(api._NorenApi__access_token, "saved-token")
        for callback in ("on_open", "on_disconnect", "on_error", "subscribe_callback"):
            self.assertIsNone(getattr(api, "_NorenApi__" + callback))

    def test_bounded_queue_overflow_invalidates_feed_and_forces_reconnect(self):
        api = FakeApi(rows=[row()])
        api.history_release = threading.Event()
        socket_closed = threading.Event()

        class Socket:
            def close(self):
                socket_closed.set()

        api._NorenApi__websocket = Socket()
        hub, manager = self.launch(api)
        self.assertTrue(api.history_entered.wait(1))
        manager._feed.incoming[PRIMARY_SYMBOL] = queue.Queue(maxsize=1)
        tick = frame(lp="100", ft=NOW.timestamp())
        api.callbacks["subscribe_callback"](tick)
        api.callbacks["subscribe_callback"](tick)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "disconnected"))
        self.assertTrue(socket_closed.wait(1))
        self.assertLessEqual(manager._feed.incoming[PRIMARY_SYMBOL].qsize(), 1)
        self.assertEqual(hub.ticks, [])
        api.history_release.set()
