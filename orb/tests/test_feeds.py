"""Provider contract, authenticated lifecycle, and source-switch isolation."""

from dataclasses import replace
from datetime import datetime, timedelta
import gc
import hashlib
import json
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feeds import (FeedManager, ShoonyaFeed, ShoonyaQuotes, YahooFeed, _shoonya_api,
                   _yahoo_websocket, feed_timestamp, shoonya_history, fetch_yahoo_5m_history,
                   read_shoonya_credentials, shoonya_login_url, REST_TIMEOUT,
                   SOCKET_OPEN_TIMEOUT, SOCKET_CLOSE_TIMEOUT, TOKEN_SYMBOLS, YAHOO_SYMBOLS)
from market import INSTRUMENTS, PRIMARY_SYMBOL
from signals import IST

NOW = datetime(2026, 10, 8, 10, 5, tzinfo=IST)
SHOONYA_SYMBOLS = set(TOKEN_SYMBOLS.values())
SUBSCRIPTIONS = [f"{exchange}|{token}" for exchange, token in TOKEN_SYMBOLS]
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
        self.tick_symbols = []
        self.tick_openings = []
        self.histories = []
        self.live_histories = []
        self.fallback_histories = []
        self.feed_status = None

    def activate_source(self, source):
        self.events.append(("activate", source))

    def set_feed_status(self, status, error=None):
        with self.condition:
            self.feed_status = status
            self.events.append(("status", status, error))
            self.condition.notify_all()

    def process_tick(self, *values, symbol=PRIMARY_SYMBOL, session_open=None):
        with self.condition:
            self.ticks.append(values)
            self.tick_symbols.append(symbol)
            self.tick_openings.append(session_open)
            self.condition.notify_all()

    def seed_historical_candles(self, candles, interval_minutes, *, symbol=PRIMARY_SYMBOL, live=False):
        with self.condition:
            self.histories.append((candles, interval_minutes, symbol))
            self.live_histories.append((symbol, live))
            self.condition.notify_all()

    def seed_fallback_history(self, candles, *, symbol, history_source_id="yahoo"):
        with self.condition:
            self.fallback_histories.append((candles, symbol, history_source_id))
            self.condition.notify_all()
        return bool(candles)

    def snapshot(self):
        return {"source": "test"}

    def _instrument_event(self, symbol):
        return {"symbol": symbol, "instruments": {symbol: {}}}

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
        self.history_releases = {}
        self.history_entries = {token: threading.Event() for _, token in TOKEN_SYMBOLS}
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
        self.history_entries[kwargs["token"]].set()
        if release := self.history_releases.get(kwargs["token"]):
            release.wait(timeout=5)
        if self.history_release:
            self.history_release.wait(timeout=5)
        self.history_done.set()
        result = self.rows.get(kwargs["token"]) if isinstance(self.rows, dict) else self.rows
        if isinstance(result, Exception):
            raise result
        return result

    def close_websocket(self):
        self.close_calls += 1
        self.closed.set()


class QuoteContractTests(unittest.TestCase):
    def test_only_current_generation_primary_history_enables_fresh_alert_recovery(self):
        hub = Hub()
        manager = FeedManager(hub)
        feed = YahooFeed(manager, manager._generation)
        feed.seed([], PRIMARY_SYMBOL)
        feed.seed([], "^NSEBANK")
        self.assertEqual(hub.live_histories, [(PRIMARY_SYMBOL, True), ("^NSEBANK", False)])
        manager._generation += 1
        feed.seed([], PRIMARY_SYMBOL)
        self.assertEqual(len(hub.live_histories), 2, "late history from an old source cannot recover alerts")

    def test_backfills_send_only_the_changed_comparison_history(self):
        from orb_monitor import MonitorHub

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

    def test_login_link_reads_fresh_client_id_and_encodes_no_other_credentials(self):
        credentials = {**CREDENTIALS, "SHOONYA_CLIENT_ID": "client & id/one"}
        with patch("feeds.read_shoonya_credentials", side_effect=[credentials, {"SHOONYA_CLIENT_ID": ""}]):
            self.assertEqual(shoonya_login_url(),
                             "https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=client+%26+id%2Fone")
            self.assertIsNone(shoonya_login_url())

    def test_yahoo_transport_bounds_dial_and_close_waits(self):
        with patch("websockets.sync.client.connect") as connect:
            socket = _yahoo_websocket()
            socket.subscribe(["^NSEI"])
            connect.assert_called_once_with(socket.url, open_timeout=SOCKET_OPEN_TIMEOUT,
                                            close_timeout=SOCKET_CLOSE_TIMEOUT)
            socket.close()
            connect.return_value.close.assert_called_once_with()

    def test_shoonya_transport_preserves_auth_and_history_contract_with_timeouts(self):
        api = _shoonya_api()
        result = {"access_token": "token", "USERID": "user", "refresh_token": "refresh",
                  "actid": "account", "susertoken": "session"}
        with patch("requests.post", side_effect=[SimpleNamespace(text=json.dumps(result)),
                                                SimpleNamespace(text=json.dumps([row()]))]) as post:
            self.assertEqual(api.getAccessToken("auth", "secret", "client", "user"),
                             ("token", "user", "refresh", "account"))
            self.assertEqual(api.get_time_price_series("NSE", "26000", 100, 200, 5), [row()])
        auth, history = post.call_args_list
        self.assertEqual(auth.args, ("https://api.shoonya.com/NorenWClientAPI//GenAcsTok",))
        self.assertEqual(auth.kwargs, {"data": "jData=" + json.dumps({
            "code": "auth", "checksum": hashlib.sha256(b"clientsecretauth").hexdigest(), "uid": "user"}),
            "timeout": REST_TIMEOUT})
        self.assertEqual(history.args, ("https://api.shoonya.com/NorenWClientAPI//TPSeries",))
        self.assertEqual(history.kwargs, {"data": "jData=" + json.dumps({
            "ordersource": "API", "uid": "user", "exch": "NSE", "token": "26000",
            "st": "100", "et": "200", "intrv": "5"}), "timeout": REST_TIMEOUT,
            "headers": {"Authorization": "Bearer token", "Content-Type": "application/json; charset=utf-8"}})
        self.assertEqual(api._NorenApi__access_token, "token")
        self.assertEqual(api._NorenApi__susertoken, "session")

    def test_shoonya_transport_keeps_unsuccessful_response_contract(self):
        api = _shoonya_api()
        api.injectOAuthHeader("token", "user", "account")
        with patch("requests.post", return_value=SimpleNamespace(text='{"stat":"Not_Ok"}')) as post:
            self.assertIsNone(api.getAccessToken("auth", "secret", "client", "user"))
            self.assertIsNone(api.get_time_price_series("NSE", "26000", 100, 200, 5))
            self.assertTrue(all(call.kwargs["timeout"] == REST_TIMEOUT for call in post.call_args_list))

    def test_every_registered_history_uses_yahoo_identifier_and_canonical_candles(self):
        history = SimpleNamespace(iterrows=lambda: iter([(SimpleNamespace(to_pydatetime=lambda: NOW),
            {"Open": 100, "High": 110, "Low": 90, "Close": 105, "Volume": 12})]))
        for symbol, instrument in INSTRUMENTS.items():
            with self.subTest(symbol=symbol), patch("yfinance.Ticker") as ticker, patch("feeds.datetime", wraps=datetime) as clock:
                clock.now.return_value = NOW + timedelta(minutes=instrument.yahoo_delay_minutes)
                ticker.return_value.history.return_value = history
                candles = fetch_yahoo_5m_history(symbol)
                ticker.assert_called_once_with(instrument.yahoo_symbol)
                ticker.return_value.history.assert_called_once_with(period="60d", interval="5m")
                self.assertEqual([candle.symbol for candle in candles], [symbol])

    def test_snapshot_diffs_and_volume_only_updates(self):
        quotes = ShoonyaQuotes()
        self.assertEqual(quotes.update(frame(t="tk", lp="100", ft=str(NOW.timestamp()),
                                             v="1000", bp1="99")), (100, 0, NOW))
        self.assertIsNone(quotes.update(frame(v="1020", ft=str((NOW + timedelta(seconds=1)).timestamp()))))
        self.assertIsNone(quotes.update(frame(lp="101")))
        timestamp = NOW + timedelta(seconds=2)
        self.assertEqual(quotes.update(frame(lp="102", ft=str(int(timestamp.timestamp() * 1000)))),
                         (102, 20, timestamp))
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

    def test_bse_quotes_require_their_own_exchange_and_token(self):
        quotes = ShoonyaQuotes(now=lambda: NOW, exchange="BSE", token="75")
        self.assertIsNone(quotes.update(frame(tk="75", lp="999", ft=NOW.timestamp())))
        self.assertIsNone(quotes.update(frame(e="BSE", lp="999", ft=NOW.timestamp())))
        self.assertEqual(quotes.update(frame(e="BSE", tk="75", lp="30000", ft=NOW.timestamp(), v="100")),
                         (30000, 0, NOW))
        self.assertEqual(quotes.update(frame(e="BSE", tk="75", lp="30010", ft=NOW.timestamp(), v="105")),
                         (30010, 5, NOW))

    def test_touchline_open_is_retained_across_deltas_without_inventing_tick_times(self):
        quotes = ShoonyaQuotes(now=lambda: NOW, exchange="BSE", token="75")
        quotes.update(frame(t="tk", e="BSE", tk="75", lp="30000", o="29000", ft=NOW.timestamp()))
        self.assertEqual(quotes.session_open, 29000)
        quotes.update(frame(e="BSE", tk="75", lp="30010", ft=NOW.timestamp()))
        self.assertEqual(quotes.session_open, 29000)
        self.assertIsNone(quotes.update(frame(e="BSE", tk="75", o="99999")))
        self.assertEqual(quotes.session_open, 29000)
        quotes.update(frame(t="tk", e="BSE", tk="75", lp="30010", o="nan", ft=NOW.timestamp()))
        self.assertIsNone(quotes.session_open)

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

    def test_bank_history_and_quote_state_keep_their_symbol(self):
        bars = shoonya_history([row()], now=NOW, symbol="^NSEBANK")
        self.assertEqual(bars[0].symbol, "^NSEBANK")
        nifty, bank = ShoonyaQuotes(now=lambda: NOW), ShoonyaQuotes(now=lambda: NOW, token="26009")
        nifty.update(frame(lp="100", ft=NOW.timestamp(), v="1000"))
        bank_time = NOW - timedelta(seconds=1)
        self.assertEqual(bank.update(frame(tk="26009", lp="200", ft=bank_time.timestamp(), v="20")),
                         (200, 0, bank_time))
        self.assertIsNone(bank.update(frame(lp="999", ft=NOW.timestamp(), v="9999")))
        self.assertEqual(nifty.update(frame(lp="101", ft=NOW.timestamp(), v="1007"))[1], 7)
        self.assertEqual(bank.update(frame(tk="26009", lp="201", ft=NOW.timestamp(), v="23"))[1], 3)


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
        for symbol in INSTRUMENTS:
            self.assertIsNot(first.history_locks[symbol], yahoo.history_locks[symbol])

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


class ShoonyaLifecycleTests(unittest.TestCase):
    def test_missing_focit_history_uses_an_explicit_separate_yahoo_backfill(self):
        rows = {instrument.shoonya_token: [row()] for instrument in INSTRUMENTS.values()}
        rows["75"] = None
        requested = []

        def fallback(symbol):
            requested.append(symbol)
            return shoonya_history([row()], NOW, symbol=symbol)

        hub, manager = self.launch(FakeApi(rows=rows), fallback_history_fetcher=fallback)
        self.assertTrue(hub.wait(lambda: bool(hub.fallback_histories)))
        self.assertEqual(requested, ["FOCIT.BO"])
        self.assertEqual(hub.fallback_histories[0][1:], ("FOCIT.BO", "yahoo"))
        self.assertNotIn("FOCIT.BO", [history[2] for history in hub.histories])
        self.assertTrue(hub.wait(lambda: "FOCIT.BO" in manager._feed.history_fallbacks))
        self.assertIn("Yahoo history backfill", hub.events[-1][2])
        self.assertNotIn("FOCIT.BO", manager._feed.cutoff, "fallback cannot reject fresh native quotes")

    def test_slow_fallback_allows_live_focit_quotes_and_cannot_cross_a_source_switch(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def fallback(symbol):
            entered.set()
            release.wait(3)
            finished.set()
            return shoonya_history([row()], NOW, symbol=symbol)

        hub, manager = self.launch(FakeApi(), fallback_history_fetcher=fallback)
        self.assertTrue(entered.wait(1))
        self.assertTrue(hub.wait(lambda: "FOCIT.BO" in manager._feed.ready))
        manager._feed.api.callbacks["subscribe_callback"](
            frame(t="tk", e="BSE", tk="75", lp="35000", o="34000", ft=NOW.timestamp()))
        self.assertTrue(hub.wait(lambda: hub.tick_symbols == ["FOCIT.BO"]))
        self.assertEqual(hub.tick_openings, [34000])
        manager.select_source("yahoo")
        release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(hub.fallback_histories, [])

    def test_failed_fallback_keeps_the_native_history_warning_and_quotes_available(self):
        attempted = threading.Event()

        def fallback(symbol):
            attempted.set()
            raise RuntimeError("unavailable")

        hub, manager = self.launch(FakeApi(), fallback_history_fetcher=fallback)
        self.assertTrue(attempted.wait(1))
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual(hub.fallback_histories, [])
        self.assertIn("FOCIT", manager._feed.history_errors.values())
        self.assertEqual(manager._feed.history_fallbacks, {})

    def test_focit_live_open_and_ticks_work_without_provider_history(self):
        api = FakeApi()
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        callback = api.callbacks["subscribe_callback"]
        callback(frame(t="tk", e="BSE", tk="75", lp="35000", o="34000", ft=NOW.timestamp()))
        callback(frame(e="BSE", tk="75", lp="35010", ft=(NOW + timedelta(seconds=1)).timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 2))
        self.assertEqual(hub.tick_symbols, ["FOCIT.BO", "FOCIT.BO"])
        self.assertEqual(hub.tick_openings, [34000, 34000])
        self.assertEqual(hub.histories, [])

    def test_all_registered_indices_route_independently(self):
        api = FakeApi(rows=[row()])
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual(api.subscriptions, [SUBSCRIPTIONS])
        self.assertEqual({(request["exchange"], request["token"]) for request in api.history_requests}, set(TOKEN_SYMBOLS))
        self.assertEqual({item[2] for item in hub.histories}, SHOONYA_SYMBOLS)
        self.assertEqual(manager._feed.history_errors, {})
        self.assertEqual(set(manager._feed.cutoff), set(INSTRUMENTS))
        callback = api.callbacks["subscribe_callback"]
        expected = {}
        for index, ((exchange, token), symbol) in enumerate(TOKEN_SYMBOLS.items()):
            price = 100 + index * 100
            timestamp = NOW + timedelta(seconds=index + 1)
            callback(frame(t="tk", e=exchange, tk=token, lp=str(price), ft=timestamp.timestamp(), v="100"))
            callback(frame(e=exchange, tk=token, lp=str(price + 1), ft=timestamp.timestamp(), v=str(101 + index)))
            expected[symbol] = [(price, 0), (price + 1, 1 + index)]
        for token in (None, "None", "unregistered"):
            callback(frame(tk=token, lp="999", ft=NOW.timestamp()))
        callback(frame(tk="75", lp="999", ft=NOW.timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 2 * len(TOKEN_SYMBOLS)))
        self.assertEqual({symbol: [tick[:2] for tick, routed in zip(hub.ticks, hub.tick_symbols)
                                  if routed == symbol] for symbol in SHOONYA_SYMBOLS}, expected)

    def launch(self, api, credentials=CREDENTIALS, *, fallback_history_fetcher=lambda symbol: []):
        hub = Hub()
        manager = FeedManager(hub, yahoo_factory=DummyFeed,
                              shoonya_factory=lambda manager, generation: ShoonyaFeed(
                                  manager, generation, api_factory=lambda: api,
                                  credentials=credentials, now=lambda: NOW,
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

    def test_bank_history_failure_does_not_apply_primary_cutoff_or_block_quotes(self):
        api = FakeApi(rows={"26000": [row()], "26009": RuntimeError("unavailable")})
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual([history[2] for history in hub.histories], ["^NSEI"])
        self.assertIn("BANKNIFTY", hub.events[-1][2])
        callback = api.callbacks["subscribe_callback"]
        callback(frame(lp="999", ft=NOW.timestamp()))
        callback(frame(tk="26009", lp="200", ft=NOW.timestamp()))
        callback(frame(lp="101", ft=(NOW + timedelta(seconds=1)).timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 2))
        self.assertEqual(set(zip(hub.tick_symbols, (tick[0] for tick in hub.ticks))),
                         {("^NSEI", 101), ("^NSEBANK", 200)})

    def test_slow_bank_history_does_not_delay_primary_live_quotes(self):
        api = FakeApi(rows=[row()])
        release = api.history_releases["26009"] = threading.Event()
        self.addCleanup(release.set)
        hub, manager = self.launch(api)
        self.assertTrue(api.history_entries["26009"].wait(1))
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        callback = api.callbacks["subscribe_callback"]
        callback(frame(lp="101", ft=(NOW + timedelta(seconds=1)).timestamp()))
        callback(frame(tk="26009", lp="201", ft=(NOW + timedelta(seconds=1)).timestamp()))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.tick_symbols, ["^NSEI"])
        release.set()
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 2))
        self.assertEqual(hub.tick_symbols, ["^NSEI", "^NSEBANK"])
        self.assertTrue(hub.wait(lambda: {history[2] for history in hub.histories} == SHOONYA_SYMBOLS))

    def test_auth_failure_never_opens_socket_or_falls_back(self):
        api = FakeApi(result=None)
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "error"))
        self.assertFalse(api.started.is_set())
        self.assertEqual(manager.source, "shoonya")
        self.assertNotIn("secret", str(hub.events))

    def test_installed_sdk_auth_timeout_never_opens_socket_or_caches_session(self):
        import requests
        api = _shoonya_api()
        with patch("requests.post", side_effect=requests.Timeout), patch.object(api, "start_websocket") as start:
            hub, manager = self.launch(api)
            self.assertTrue(hub.wait(lambda: hub.feed_status == "error"))
            start.assert_not_called()
            self.assertIsNone(manager._shoonya_session)

    def test_returning_source_waits_for_pending_history_then_backfills_once(self):
        api = FakeApi(rows=[row()])
        api.history_release = threading.Event()
        self.addCleanup(api.history_release.set)
        hub, manager = self.launch(api)
        self.assertTrue(all(event.wait(1) for event in api.history_entries.values()))
        old_callback = api.callbacks["subscribe_callback"]
        manager.select_source("yahoo")
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: manager._feed.connected))
        self.assertEqual(manager._feed.ready, {})
        self.assertEqual(len(api.history_requests), len(INSTRUMENTS))
        timestamp = (NOW + timedelta(seconds=1)).timestamp()
        old_callback(frame(lp="999", ft=timestamp))
        api.callbacks["subscribe_callback"](frame(lp="101", ft=timestamp))
        self.assertEqual(hub.ticks, [])
        api.history_release.set()
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.ticks[0][0], 101)
        self.assertEqual(len(api.history_requests), 2 * len(INSTRUMENTS))
        self.assertEqual(len(hub.histories), len(INSTRUMENTS))
        self.assertEqual(manager._feed.history_errors, {})

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

    def test_history_cutoff_uses_requested_end_not_response_receipt_time(self):
        api = FakeApi(rows=[row()])
        api.history_release = threading.Event()
        hub = Hub()
        clock = {"now": NOW}
        manager = FeedManager(hub, yahoo_factory=DummyFeed,
                              shoonya_factory=lambda manager, generation: ShoonyaFeed(
                                  manager, generation, api_factory=lambda: api,
                                  credentials=CREDENTIALS, now=lambda: clock["now"],
                                  fallback_history_fetcher=lambda symbol: []))
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
                                  credentials=credentials, now=lambda: clock["now"],
                                  fallback_history_fetcher=lambda symbol: []))
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
        self.assertEqual(api.subscriptions, [SUBSCRIPTIONS])
        self.assertEqual(api.auth_calls, 1)
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: hub.feed_status == "live"))
        self.assertEqual(api.auth_calls, 2)
        self.assertEqual(api.subscriptions, [SUBSCRIPTIONS, SUBSCRIPTIONS])

    def test_credentials_reload_env_files_without_mutating_process_environment(self):
        manager = FeedManager(Hub(), yahoo_factory=DummyFeed, shoonya_factory=DummyFeed)
        feed = ShoonyaFeed(manager, 0)
        values = [{"SHOONYA_AUTH_CODE": "old", "SHOONYA_USER_ID": "file-user"},
                  {}, {"SHOONYA_AUTH_CODE": "fresh", "SHOONYA_USER_ID": "file-user"}, {}]
        with patch("dotenv.dotenv_values", side_effect=values), patch.dict("os.environ", {"SHOONYA_USER_ID": "exported-user"}, clear=True):
            first, second = feed._credentials(), read_shoonya_credentials()
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
        manager._feed.incoming[PRIMARY_SYMBOL] = queue.Queue(maxsize=1)
        tick = frame(lp="100", ft=NOW.timestamp())
        api.callbacks["subscribe_callback"](tick)
        api.callbacks["subscribe_callback"](tick)
        self.assertTrue(hub.wait(lambda: hub.feed_status == "disconnected"))
        self.assertTrue(socket_closed.wait(1))
        self.assertLessEqual(manager._feed.incoming[PRIMARY_SYMBOL].qsize(), 1)
        self.assertEqual(hub.ticks, [])
        api.history_release.set()


class FakeYahooSocket:
    def __init__(self):
        self.listening, self.closed = threading.Event(), threading.Event()

    def subscribe(self, symbols):
        self.symbols = symbols

    def listen(self, callback):
        self.callback = callback
        self.listening.set()
        self.closed.wait(5)

    def close(self):
        self.closed.set()


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
        entered = {symbol: threading.Event() for symbol in INSTRUMENTS}
        release = threading.Event()
        self.addCleanup(release.set)
        calls = {symbol: 0 for symbol in INSTRUMENTS}
        sockets = []

        def socket_factory():
            socket = FakeYahooSocket()
            sockets.append(socket)
            return socket

        def history(symbol):
            calls[symbol] += 1
            first = calls[symbol] == 1
            if first:
                entered[symbol].set()
                release.wait(3)
            return shoonya_history([row(intc="105" if first else "106")], NOW, symbol=symbol)

        manager = FeedManager(hub, shoonya_factory=DummyFeed,
            yahoo_factory=lambda manager, generation: YahooFeed(manager, generation,
                websocket_factory=socket_factory, history_fetcher=history, now=lambda: NOW))
        self.addCleanup(manager.stop)
        manager.select_source("yahoo")
        self.assertTrue(all(event.wait(1) for event in entered.values()))
        manager.select_source("shoonya")
        manager.select_source("yahoo")
        self.assertTrue(sockets[-1].listening.wait(1))
        self.assertEqual(set(calls.values()), {1})
        self.assertEqual(manager._feed.ready, {})
        release.set()
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual(set(calls.values()), {2})
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


if __name__ == "__main__":
    unittest.main()
