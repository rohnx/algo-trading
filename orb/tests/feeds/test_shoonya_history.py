"""Shoonya backfill, Yahoo fallback, and periodic refresh preserve quote availability."""

from datetime import timedelta
import threading
import unittest
from unittest.mock import patch

from orb.feeds import FeedManager, HISTORY_CONCURRENCY, shoonya_history, ShoonyaFeed, TOKEN_SYMBOLS
from orb.market import INSTRUMENTS, PRIMARY_SYMBOL

from orb.tests.support.providers import (
    CREDENTIALS,
    DummyFeed,
    FakeApi,
    frame,
    Hub,
    missing_focit_api,
    NOW,
    row,
    SHOONYA_SYMBOLS,
    SUBSCRIPTIONS,
)


class ShoonyaHistoryTests(unittest.TestCase):
    def test_history_notices_summarize_large_groups_but_keep_up_to_three_names(self):
        hub = Hub()
        feed = ShoonyaFeed(FeedManager(hub), 0)
        feed.ready[PRIMARY_SYMBOL] = 1
        fallback_names = {symbol: instrument.label for symbol, instrument in INSTRUMENTS.items()
                          if instrument.shoonya_history_fallback == "yahoo"}
        self.assertEqual(len(fallback_names), 16)
        feed.history_errors = dict(fallback_names)
        feed._history_status()
        self.assertEqual(hub.events[-1], ("status", "live",
                         "Shoonya 5-minute history unavailable: 16 indices"))
        self.assertLess(len(hub.events[-1][2]), 80)

        feed.history_errors.clear()
        feed.history_fallbacks = dict(fallback_names)
        feed._history_status()
        self.assertEqual(hub.events[-1], ("status", "live",
                         "16 indices: Yahoo history backfill; Shoonya quotes"))
        self.assertLess(len(hub.events[-1][2]), 80)
        self.assertEqual(feed.history_fallbacks, fallback_names,
                         "compact global status must retain each instrument's provenance")

        feed.history_fallbacks = {"FOCIT.BO": "FOCIT", "^CNXPHARMA": "PHARMA", "^CNXAUTO": "AUTO"}
        feed._history_status()
        self.assertEqual(hub.events[-1][2], "FOCIT, PHARMA, AUTO: Yahoo history backfill; Shoonya quotes")
        feed.history_fallbacks.clear()
        feed.history_errors = {"^CNXPHARMA": "PHARMA", "^CNXAUTO": "AUTO"}
        feed._history_status()
        self.assertEqual(hub.events[-1][2], "Shoonya 5-minute history unavailable: PHARMA, AUTO")

    def test_sector_backfills_share_bounded_yahoo_capacity_while_all_native_quotes_flow(self):
        sectors = {symbol for symbol, instrument in INSTRUMENTS.items()
                   if instrument.comparison_group == "sectors"}
        expected_fallbacks = sectors | {"FOCIT.BO"}
        rows = {instrument.shoonya_token: None if symbol in expected_fallbacks else [row()]
                for symbol, instrument in INSTRUMENTS.items()}
        release, capacity_filled = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        guard = threading.Lock()
        activity = {"active": 0, "maximum": 0}
        requested = []

        def fallback(symbol):
            with guard:
                activity["active"] += 1
                activity["maximum"] = max(activity["maximum"], activity["active"])
                requested.append(symbol)
                if activity["active"] == HISTORY_CONCURRENCY:
                    capacity_filled.set()
            try:
                release.wait(3)
                return shoonya_history([row()], NOW, symbol=symbol)
            finally:
                with guard:
                    activity["active"] -= 1

        api = FakeApi(rows=rows)
        hub, manager = self.launch(api, fallback_history_fetcher=fallback)
        self.assertTrue(capacity_filled.wait(1))
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        callback = api.callbacks["subscribe_callback"]
        for symbol in sectors:
            callback(frame(t="tk", tk=INSTRUMENTS[symbol].shoonya_token, lp="500", o="490", ft=NOW.timestamp()))
        self.assertTrue(hub.wait(lambda: set(hub.tick_symbols) == sectors))
        self.assertEqual(hub.fallback_histories, [])
        release.set()
        self.assertTrue(hub.wait(lambda: set(manager._feed.history_fallbacks) == expected_fallbacks))
        self.assertEqual(set(requested), expected_fallbacks)
        self.assertEqual(activity["maximum"], HISTORY_CONCURRENCY)
        self.assertEqual({item[1] for item in hub.fallback_histories}, expected_fallbacks)
        self.assertTrue(all(item[2] == "yahoo" for item in hub.fallback_histories))
        self.assertTrue(expected_fallbacks.isdisjoint(manager._feed.cutoff))
        self.assertEqual(api.start_calls, 1)
        self.assertEqual(api.subscriptions, [SUBSCRIPTIONS])
        self.assertEqual(len(SUBSCRIPTIONS), 21)

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

    def test_focit_backfill_refreshes_without_reopening_native_history_or_socket(self):
        clock = [NOW]
        requested = []

        def fallback(symbol):
            requested.append(symbol)
            return shoonya_history([row()], clock[0], symbol=symbol)

        api = missing_focit_api()
        hub, manager = self.launch(api, fallback_history_fetcher=fallback, now=lambda: clock[0])
        self.assertTrue(hub.wait(lambda: len(hub.fallback_histories) == 1))
        clock[0] += timedelta(minutes=4, seconds=59)
        manager._feed.wake.set()
        self.assertEqual(requested, ["FOCIT.BO"])
        clock[0] += timedelta(seconds=1)
        manager._feed.wake.set()
        self.assertTrue(hub.wait(lambda: len(hub.fallback_histories) == 2))
        self.assertEqual(requested, ["FOCIT.BO", "FOCIT.BO"])
        self.assertEqual(len(api.history_requests), len(INSTRUMENTS))
        self.assertEqual(api.start_calls, 1)
        self.assertEqual(api.subscriptions, [SUBSCRIPTIONS])
        self.assertNotIn("FOCIT.BO", manager._feed.cutoff)

    def test_slow_periodic_backfill_keeps_quotes_flowing_and_cannot_cross_switch(self):
        clock = [NOW]
        requested = []
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def fallback(symbol):
            requested.append(symbol)
            if len(requested) > 1:
                entered.set()
                release.wait(3)
                finished.set()
            return shoonya_history([row()], clock[0], symbol=symbol)

        api = missing_focit_api()
        hub, manager = self.launch(api, fallback_history_fetcher=fallback, now=lambda: clock[0])
        self.assertTrue(hub.wait(lambda: len(hub.fallback_histories) == 1))
        feed = manager._feed
        clock[0] += timedelta(minutes=5)
        feed.wake.set()
        self.assertTrue(entered.wait(1))
        api.callbacks["subscribe_callback"](frame(t="tk", e="BSE", tk="75", lp="35000", o="34000", ft=clock[0].timestamp()))
        self.assertTrue(hub.wait(lambda: hub.tick_symbols == ["FOCIT.BO"]))
        clock[0] += timedelta(minutes=5)
        feed.wake.set()
        self.assertEqual(len(requested), 2, "a pending refresh cannot pile up requests")
        manager.select_source("yahoo")
        release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(len(hub.fallback_histories), 1)
        feed._poll_fallback_history()
        self.assertEqual(len(requested), 2)

    def test_periodic_backfill_retries_initial_failure_and_stops_overnight(self):
        clock = [NOW]
        requested = []

        def fallback(symbol):
            requested.append(symbol)
            if len(requested) == 1:
                raise RuntimeError("temporarily unavailable")
            return shoonya_history([row()], clock[0], symbol=symbol)

        hub, manager = self.launch(missing_focit_api(), fallback_history_fetcher=fallback, now=lambda: clock[0])
        self.assertTrue(hub.wait(lambda: "FOCIT.BO" in manager._feed.fallback_due))
        self.assertTrue(hub.wait(lambda: "FOCIT" in manager._feed.history_errors.values()))
        self.assertTrue(hub.wait(lambda: len(requested) == 1 and not manager._feed.fallback_pending))
        clock[0] += timedelta(minutes=5)
        manager._feed.wake.set()
        self.assertTrue(hub.wait(lambda: len(hub.fallback_histories) == 1))
        self.assertTrue(hub.wait(lambda: "FOCIT.BO" in manager._feed.history_fallbacks))
        clock[0] = NOW.replace(hour=15, minute=45)
        manager._feed.wake.set()
        self.assertTrue(hub.wait(lambda: len(hub.fallback_histories) == 2))
        clock[0] = NOW.replace(hour=18)
        manager._feed._poll_fallback_history()
        self.assertEqual(len(requested), 3, "completed sessions do not poll all night")

    def test_periodic_backfill_uses_a_small_same_day_history_request(self):
        feed = ShoonyaFeed(FeedManager(Hub()), 0)
        with patch("orb.feeds.fetch_yahoo_5m_history", return_value=[]) as fetch:
            feed.fallback_recent_fetcher("FOCIT.BO")
        fetch.assert_called_once_with("FOCIT.BO", period="1d")

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

    def test_bank_history_failure_does_not_apply_primary_cutoff_or_block_quotes(self):
        api = FakeApi(rows={"26000": [row()], "26009": RuntimeError("unavailable")})
        hub, manager = self.launch(api)
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertEqual([history[2] for history in hub.histories], ["^NSEI"])
        self.assertEqual(manager._feed.history_errors["^NSEBANK"], "BANKNIFTY")
        self.assertIn("Shoonya 5-minute history unavailable", hub.events[-1][2])
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

    def test_returning_source_waits_for_pending_history_then_backfills_once(self):
        api = FakeApi(rows=[row()])
        api.history_release = threading.Event()
        self.addCleanup(api.history_release.set)
        hub, manager = self.launch(api)
        self.assertTrue(api.wait_for_history(HISTORY_CONCURRENCY))
        self.assertEqual(len(api.history_requests), HISTORY_CONCURRENCY)
        old_callback = api.callbacks["subscribe_callback"]
        manager.select_source("yahoo")
        manager.select_source("shoonya")
        self.assertTrue(hub.wait(lambda: manager._feed.connected))
        self.assertEqual(manager._feed.ready, {})
        self.assertEqual(len(api.history_requests), HISTORY_CONCURRENCY)
        timestamp = (NOW + timedelta(seconds=1)).timestamp()
        old_callback(frame(lp="999", ft=timestamp))
        api.callbacks["subscribe_callback"](frame(lp="101", ft=timestamp))
        self.assertEqual(hub.ticks, [])
        api.history_release.set()
        self.assertTrue(hub.wait(lambda: len(manager._feed.ready) == len(INSTRUMENTS)))
        self.assertTrue(hub.wait(lambda: len(hub.ticks) == 1))
        self.assertEqual(hub.ticks[0][0], 101)
        self.assertEqual(len(api.history_requests), len(INSTRUMENTS) + HISTORY_CONCURRENCY)
        self.assertEqual(len(hub.histories), len(INSTRUMENTS))
        self.assertEqual(manager._feed.history_errors, {})

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
