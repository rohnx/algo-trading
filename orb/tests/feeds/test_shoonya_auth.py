"""Shoonya credential reload and authentication reuse respect source and day ownership."""

from datetime import timedelta
import threading
import unittest
from unittest.mock import patch

from orb.feeds import _shoonya_api, FeedManager, read_shoonya_credentials, ShoonyaFeed

from orb.tests.support.providers import CREDENTIALS, DummyFeed, FakeApi, frame, Hub, NOW, SUBSCRIPTIONS


class ShoonyaAuthenticationTests(unittest.TestCase):
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
