"""Provider contract, authenticated lifecycle, and source-switch isolation."""

from datetime import datetime, timedelta
from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feeds import (FeedManager, ShoonyaFeed, ShoonyaQuotes, YahooFeed, _shoonya_api,
                   feed_timestamp, shoonya_history)
from signals import IST

NOW = datetime(2026, 10, 8, 10, 5, tzinfo=IST)
CREDENTIALS = {"SHOONYA_CLIENT_ID": "client", "SHOONYA_USER_ID": "user",
               "SHOONYA_SECRET_CODE": "secret", "SHOONYA_AUTH_CODE": "auth"}


def frame(**fields):
    return {"t": "tf", "e": "NSE", "tk": "26000", **fields}


def row(minute=15, **fields):
    return {"time": f"08-10-2026 09:{minute}:00", "into": "100", "inth": "110",
            "intl": "90", "intc": "105", "intv": "12", "v": "9999", **fields}


class Hub:
    def __init__(self):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.events = []
        self.ticks = []
        self.histories = []
        self.feed_status = None

    def activate_source(self, source):
        self.events.append(("activate", source))

    def set_feed_status(self, status, error=None):
        with self.condition:
            self.feed_status = status
            self.events.append(("status", status, error))
            self.condition.notify_all()

    def process_tick(self, *values):
        with self.condition:
            self.ticks.append(values)
            self.condition.notify_all()

    def seed_historical_candles(self, candles, interval_minutes):
        with self.condition:
            self.histories.append((candles, interval_minutes))
            self.condition.notify_all()

    def snapshot(self):
        return {"source": "test"}

    def broadcast(self, event):
        self.events.append(("broadcast", event))

    def wait(self, predicate):
        with self.condition:
            return self.condition.wait_for(predicate, timeout=3)


class DummyFeed:
    def __init__(self, manager, generation):
        self.manager, self.generation = manager, generation
        self.started = self.closed = False

    def start(self):
        self.started = True
        self.manager.hub.events.append(("start", self.generation))

    def stop(self):
        self.closed = True
        self.manager.hub.events.append(("stop", self.generation))


class FakeApi:
    def __init__(self, result=("token", "user", "refresh", "account"), rows=None):
        self.result = result
        self.rows = rows
        self.callbacks = {}
        self.closed = threading.Event()
        self.started = threading.Event()
        self.history_done = threading.Event()
        self.auth_release = None
        self.history_release = None
        self.history_entered = threading.Event()
        self.auth_entered = threading.Event()
        self.history_requests = []
        self.subscriptions = []
        self.auth_calls = 0
        self.start_calls = 0
        self.close_calls = 0

    def getAccessToken(self, **kwargs):
        self.auth_calls += 1
        self.auth_kwargs = kwargs
        self.auth_entered.set()
        if self.auth_release:
            self.auth_release.wait(timeout=5)
        return self.result

    def start_websocket(self, **kwargs):
        self.start_calls += 1
        self.callbacks = kwargs
        self.started.set()
        kwargs["socket_open_callback"]()

    def subscribe(self, instruments):
        self.subscriptions.append(instruments)

    def get_time_price_series(self, **kwargs):
        self.history_requests.append(kwargs)
        self.history_entered.set()
        if self.history_release:
            self.history_release.wait(timeout=5)
        self.history_done.set()
        return self.rows

    def close_websocket(self):
        self.close_calls += 1
        self.closed.set()


class QuoteContractTests(unittest.TestCase):
    def test_snapshot_diffs_and_volume_only_updates(self):
        quotes = ShoonyaQuotes()
        self.assertEqual(quotes.update(frame(t="tk", lp="100", ft=str(NOW.timestamp()),
                                             v="1000", bp1="99")), (100, 0, NOW))
        self.assertIsNone(quotes.update(frame(v="1020", ft=str((NOW + timedelta(seconds=1)).timestamp()))))
        self.assertIsNone(quotes.update(frame(lp="101")))
        timestamp = NOW + timedelta(seconds=2)
        self.assertEqual(quotes.update(frame(lp="102", ft=str(int(timestamp.timestamp() * 1000)))),
                         (102, 20, timestamp))
        self.assertEqual(quotes.state[("NSE", "26000")]["bp1"], "99")
        self.assertEqual(quotes.update(frame(lp="103", ft=str((timestamp + timedelta(seconds=1)).timestamp())))[1], 0)

    def test_rejects_other_tokens_stale_time_and_invalid_prices(self):
        quotes = ShoonyaQuotes()
        quotes.update(frame(t="tk", lp="100", ft=NOW.timestamp(), v="100"))
        for message in [frame(tk="2885", lp="999", ft=NOW.timestamp()),
                        frame(e="BSE", lp="999", ft=NOW.timestamp()),
                        frame(lp="nan", ft=NOW.timestamp()), frame(lp="0", ft=NOW.timestamp()),
                        frame(lp="999", ft=123), frame(lp="999", ft=None),
                        frame(lp="999", ft=(NOW - timedelta(seconds=1)).timestamp(), v="999")]:
            self.assertIsNone(quotes.update(message))
        self.assertEqual(quotes.update(frame(lp="101", ft=(NOW + timedelta(seconds=1)).timestamp(), v="105"))[1], 5)

    def test_cumulative_reset_and_new_session_are_baselines(self):
        quotes = ShoonyaQuotes()
        quotes.update(frame(lp="100", ft=NOW.timestamp(), v="1000"))
        self.assertEqual(quotes.update(frame(lp="101", ft=(NOW + timedelta(seconds=1)).timestamp(), v="2"))[1], 0)
        self.assertEqual(quotes.update(frame(lp="101", ft=(NOW + timedelta(seconds=2)).timestamp(), v="7"))[1], 5)
        tomorrow = NOW + timedelta(days=1)
        self.assertEqual(quotes.update(frame(lp="102", ft=tomorrow.timestamp(), v="300"))[1], 0)

    def test_feed_timestamp_accepts_epoch_units_but_no_receipt_fallback(self):
        self.assertEqual(feed_timestamp(NOW.timestamp()), NOW)
        self.assertEqual(feed_timestamp(NOW.timestamp() * 1000), NOW)
        for value in (0, None, "", "nan", NOW.timestamp() * 1000000):
            with self.assertRaises((ValueError, TypeError)):
                feed_timestamp(value)

    def test_future_feed_time_cannot_poison_subsequent_quotes(self):
        quotes = ShoonyaQuotes(now=lambda: NOW)
        self.assertIsNone(quotes.update(frame(lp="999", ft=(NOW + timedelta(days=1)).timestamp())))
        self.assertEqual(quotes.update(frame(lp="100", ft=NOW.timestamp()))[0], 100)

    def test_history_is_sorted_5m_ohlc_and_interval_volume(self):
        bars = shoonya_history([row(25), row(15), row(20), row(16), row(30, into="nan")], now=NOW)
        self.assertEqual([bar.start_time.minute for bar in bars], [15, 20, 25])
        self.assertTrue(all(bar.volume == 12 for bar in bars))
        self.assertTrue(all(bar.end_time - bar.start_time == timedelta(minutes=5) for bar in bars))
        self.assertTrue(all(bar.start_time.utcoffset() == IST.utcoffset(None) for bar in bars))


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


class ShoonyaLifecycleTests(unittest.TestCase):
    def launch(self, api, credentials=CREDENTIALS):
        hub = Hub()
        manager = FeedManager(hub, yahoo_factory=DummyFeed,
                              shoonya_factory=lambda manager, generation: ShoonyaFeed(
                                  manager, generation, api_factory=lambda: api,
                                  credentials=credentials, now=lambda: NOW))
        self.addCleanup(manager.stop)
        manager.select_source("shoonya")
        return hub, manager

    def test_exact_working_auth_and_nifty_only_subscription(self):
        api = FakeApi()
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual(api.auth_kwargs, {"authcode": "auth", "Secret_Code": "secret",
                                          "client_id": "client", "UID": "user"})
        self.assertEqual(api.subscriptions, [["NSE|26000"]])
        self.assertEqual(api.history_requests[0]["interval"], 5)
        self.assertEqual(api.history_requests[0]["token"], "26000")
        api.callbacks["subscribe_callback"](frame(lp="100", ft=NOW.timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        old_callback = api.callbacks["subscribe_callback"]
        manager.select_source("yahoo")
        self.assertTrue(api.closed.is_set())
        old_callback(frame(lp="999", ft=(NOW + timedelta(seconds=1)).timestamp()))
        self.assertEqual(len(hub.ticks), 1)

    def test_auth_failure_never_opens_socket_or_falls_back(self):
        api = FakeApi(result=None)
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "error"))
        self.assertFalse(api.started.is_set())
        self.assertEqual(manager.source, "shoonya")
        self.assertNotIn("secret", str(hub.events))

    def test_missing_credentials_are_reported_without_auth(self):
        api = FakeApi()
        hub, _ = self.launch(api, {key: "" for key in CREDENTIALS})
        self.assertTrue(hub.wait(lambda: hub.feed_status == "error"))
        self.assertFalse(api.auth_entered.is_set())

    def test_late_auth_completion_cannot_start_old_provider_socket(self):
        api = FakeApi()
        api.auth_release = threading.Event()
        hub, manager = self.launch(api)
        self.assertTrue(api.auth_entered.wait(1))
        old = manager._feed
        manager.select_source("yahoo")
        api.auth_release.set()
        old.worker.join(1)
        self.assertFalse(old.worker.is_alive())
        self.assertFalse(api.started.is_set())
        self.assertEqual(manager.source, "yahoo")

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
        api.callbacks["socket_close_callback"]()
        self.assertTrue(hub.wait(lambda: hub.feed_status == "disconnected"))
        api.callbacks["subscribe_callback"](frame(lp="999", ft=(NOW + timedelta(seconds=1)).timestamp()))
        api.callbacks["socket_open_callback"]()
        self.assertTrue(hub.wait(lambda: len(hub.histories) == 2 and hub.feed_status == "live"))
        self.assertEqual(api.subscriptions, [["NSE|26000"], ["NSE|26000"]])
        api.callbacks["subscribe_callback"](frame(lp="100", ft=(NOW + timedelta(seconds=1)).timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.ticks[0][0], 100)

    def test_history_cutoff_uses_requested_end_not_response_receipt_time(self):
        api = FakeApi(rows=[row()])
        api.history_release = threading.Event()
        hub = Hub()
        clock = {"now": NOW}
        manager = FeedManager(hub, yahoo_factory=DummyFeed,
                              shoonya_factory=lambda manager, generation: ShoonyaFeed(
                                  manager, generation, api_factory=lambda: api,
                                  credentials=CREDENTIALS, now=lambda: clock["now"]))
        self.addCleanup(manager.stop)
        manager.select_source("shoonya")
        self.assertTrue(api.history_entered.wait(1))
        api.callbacks["subscribe_callback"](frame(lp="100", ft=NOW.timestamp()))
        api.callbacks["subscribe_callback"](frame(lp="101", ft=(NOW + timedelta(seconds=1)).timestamp()))
        clock["now"] += timedelta(seconds=30)
        api.history_release.set()
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.ticks[0], (101, 0, NOW + timedelta(seconds=1)))

    def test_installed_sdk_disconnected_stop_still_stops_reconnect_thread(self):
        api = _shoonya_api()
        api._NorenApi__stop_event = threading.Event()

        class Socket:
            closed = False

            def close(self):
                self.closed = True

        socket = Socket()
        api._NorenApi__websocket = socket
        api._NorenApi__websocket_connected = False
        thread = threading.Thread(target=lambda: api._NorenApi__stop_event.wait(2))
        api._NorenApi__ws_thread = thread
        thread.start()
        api.close_websocket()
        self.assertTrue(socket.closed)
        self.assertTrue(api._NorenApi__stop_event.is_set())
        self.assertFalse(thread.is_alive())

    def test_same_day_toggle_reuses_authentication_after_closing_old_socket(self):
        api = FakeApi()
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        old_tick = api.callbacks["subscribe_callback"]
        manager.select_source("yahoo")
        self.assertEqual(api.close_calls, 1)
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual(api.auth_calls, 1)
        self.assertEqual(api.start_calls, 2)
        old_tick(frame(lp="999", ft=NOW.timestamp()))
        self.assertEqual(hub.ticks, [])

    def test_changed_credentials_or_new_ist_day_invalidate_cached_session(self):
        hub = Hub()
        apis = [FakeApi(), FakeApi(), FakeApi()]
        available = iter(apis)
        credentials, clock = dict(CREDENTIALS), {"now": NOW}
        manager = FeedManager(hub, yahoo_factory=DummyFeed,
                              shoonya_factory=lambda manager, generation: ShoonyaFeed(
                                  manager, generation, api_factory=lambda: next(available),
                                  credentials=credentials, now=lambda: clock["now"]))
        self.addCleanup(manager.stop)
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        manager.select_source("yahoo")
        credentials["SHOONYA_AUTH_CODE"] = "new-auth"
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual(apis[1].auth_kwargs["authcode"], "new-auth")
        manager.select_source("yahoo")
        clock["now"] += timedelta(days=1)
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual([api.auth_calls for api in apis], [1, 1, 1])

    def test_auth_error_discards_cached_session_and_can_retry(self):
        api = FakeApi()
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        api.callbacks["socket_error_callback"]({"t": "ak", "s": "Not_Ok"})
        self.assertTrue(hub.wait(lambda: hub.feed_status == "error"))
        self.assertIsNone(manager._shoonya_session)
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual(api.auth_calls, 2)

    def test_auth_error_followed_immediately_by_close_stays_terminal_until_selected_again(self):
        api = FakeApi()
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        feed = manager._feed
        entered, release = threading.Event(), threading.Event()
        process_control = feed._control

        def blocked_control(*control):
            entered.set()
            release.wait(3)
            process_control(*control)

        with patch.object(feed, "_control", side_effect=blocked_control):
            api.callbacks["socket_error_callback"]({"t": "ak", "s": "Not_Ok"})
            self.assertTrue(entered.wait(1))
            # The server can close the rejected socket before our worker drains
            # the authentication event. A transport close must not erase it.
            api.callbacks["socket_close_callback"]()
            release.set()
            self.assertTrue(hub.wait(lambda: hub.feed_status == "error"))
        self.assertTrue(api.closed.wait(1))
        self.assertIsNone(manager._shoonya_session)
        api.callbacks["socket_open_callback"]()
        api.callbacks["subscribe_callback"](frame(lp="999", ft=NOW.timestamp()))
        with hub.condition:
            resumed = hub.condition.wait_for(lambda: hub.feed_status != "error" or hub.ticks, timeout=.1)
        self.assertFalse(resumed)
        self.assertEqual(api.subscriptions, [["NSE|26000"]])
        self.assertEqual(api.auth_calls, 1)
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual(api.auth_calls, 2)
        self.assertEqual(api.subscriptions, [["NSE|26000"], ["NSE|26000"]])

    def test_credentials_reload_env_files_without_mutating_process_environment(self):
        manager = FeedManager(Hub(), yahoo_factory=DummyFeed, shoonya_factory=DummyFeed)
        feed = ShoonyaFeed(manager, 0)
        values = [{"SHOONYA_AUTH_CODE": "old", "SHOONYA_USER_ID": "file-user"},
                  {}, {"SHOONYA_AUTH_CODE": "fresh", "SHOONYA_USER_ID": "file-user"}, {}]
        with patch("dotenv.dotenv_values", side_effect=values), patch.dict("os.environ", {"SHOONYA_USER_ID": "exported-user"}, clear=True):
            first, second = feed._credentials(), feed._credentials()
            self.assertEqual(first["SHOONYA_AUTH_CODE"], "old")
            self.assertEqual(second["SHOONYA_AUTH_CODE"], "fresh")
            self.assertEqual(second["SHOONYA_USER_ID"], "exported-user")
            import os
            self.assertNotIn("SHOONYA_AUTH_CODE", os.environ)

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
        manager._feed.incoming = queue.Queue(maxsize=1)
        tick = frame(lp="100", ft=NOW.timestamp())
        api.callbacks["subscribe_callback"](tick)
        api.callbacks["subscribe_callback"](tick)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "disconnected"))
        self.assertTrue(socket_closed.wait(1))
        self.assertLessEqual(manager._feed.incoming.qsize(), 1)
        self.assertEqual(hub.ticks, [])
        api.history_release.set()


class YahooLifecycleTests(unittest.TestCase):
    def test_switch_closes_actual_socket_and_ends_listener(self):
        hub = Hub()
        listening, close = threading.Event(), threading.Event()

        class Socket:
            def subscribe(self, symbols):
                self.symbols = symbols

            def listen(self, callback):
                self.callback = callback
                listening.set()
                close.wait(5)

            def close(self):
                close.set()

        socket = Socket()
        manager = FeedManager(hub, shoonya_factory=DummyFeed,
                              yahoo_factory=lambda manager, generation: YahooFeed(
                                  manager, generation, websocket_factory=lambda: socket,
                                  history_fetcher=lambda: shoonya_history([row()], NOW)))
        self.addCleanup(manager.stop)
        manager.select_source("yahoo")
        old = manager._feed
        self.assertTrue(listening.wait(1))
        manager.select_source("shoonya")
        self.assertTrue(close.is_set())
        self.assertFalse(old.runner.is_alive())
        socket.callback({"id": "^NSEI", "price": 999, "time": NOW.timestamp() * 1000})
        self.assertEqual(hub.ticks, [])


if __name__ == "__main__":
    unittest.main()
