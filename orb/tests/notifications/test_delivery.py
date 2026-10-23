"""Durable alert delivery owns deduplication, retry, and restart recovery."""

from datetime import timedelta
import io
import json
import logging
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests

from orb.notifications import DELIVERY_MAX_AGE, REQUEST_TIMEOUT, TelegramNotifier

from orb.tests.support.notifications import Clock, response, signal, wait_for


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = Clock()
        self.post = patch("orb.notifications.requests.post", return_value=response()).start()
        self.addCleanup(patch.stopall)
        self.notifier = self.new_notifier()

    def new_notifier(self):
        notifier = TelegramNotifier(cache_dir=self.temporary.name, now=self.clock,
                                    token="synthetic-private-token", chat_id="12345")
        self.addCleanup(notifier.stop)
        return notifier

    def records(self):
        try:
            return json.loads(self.notifier.outbox_path.read_text())["records"]
        except FileNotFoundError:
            return {}

    def state(self, direction="BULLISH"):
        return self.records().get(f"2026-10-13:NIFTY:{direction}", {}).get("state")

    def test_posts_plain_text_with_timeouts_and_persists_before_http(self):
        def send(*args, **kwargs):
            self.assertEqual(self.state(), "pending")
            return response()
        self.post.side_effect = send
        self.notifier.start()
        self.assertTrue(self.notifier.enqueue(signal(), "shoonya", live=True))
        wait_for(lambda: self.state() == "sent")
        self.post.assert_called_once()
        _, request = self.post.call_args
        self.assertEqual(request["timeout"], REQUEST_TIMEOUT)
        self.assertFalse(request["allow_redirects"])
        self.assertEqual(request["json"]["chat_id"], "12345")
        self.assertNotIn("parse_mode", request["json"])
        contents = self.notifier.outbox_path.read_text()
        self.assertNotIn("synthetic-private-token", contents)
        self.assertNotIn("12345", contents)
        self.assertEqual(self.notifier.outbox_path.stat().st_mode & 0o777, 0o600)

    def test_only_live_fresh_current_day_confluence_is_accepted(self):
        self.notifier.start()
        self.assertFalse(self.notifier.enqueue(signal(), "shoonya"))
        old = signal(timestamp=self.clock.value - timedelta(minutes=3))
        self.assertFalse(self.notifier.enqueue(old, "shoonya", live=True))
        prior_day = signal(timestamp=self.clock.value - timedelta(days=1))
        self.assertFalse(self.notifier.enqueue(prior_day, "shoonya", live=True))
        future = signal(timestamp=self.clock.value + timedelta(minutes=2))
        self.assertFalse(self.notifier.enqueue(future, "shoonya", live=True))
        incomplete = signal()
        del incomplete["confirmations"]["5"]
        self.assertFalse(self.notifier.enqueue(incomplete, "shoonya", live=True))
        self.post.assert_not_called()

    def test_dedup_survives_provider_switch_and_clean_restart(self):
        self.notifier.start()
        self.assertTrue(self.notifier.enqueue(signal(), "yahoo", live=True))
        self.assertFalse(self.notifier.enqueue(signal(), "shoonya", live=True))
        wait_for(lambda: self.state() == "sent")
        self.notifier.stop()
        restarted = self.new_notifier()
        restarted.start()
        self.assertFalse(restarted.enqueue(signal(), "shoonya", live=True))
        self.assertTrue(restarted.enqueue(signal("BEARISH"), "shoonya", live=True))
        wait_for(lambda: self.state("BEARISH") == "sent")
        self.assertEqual(self.post.call_count, 2)

    def test_slow_request_does_not_block_enqueue(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def send(*args, **kwargs):
            entered.set()
            release.wait(3)
            return response()
        self.post.side_effect = send
        self.notifier.start()
        self.assertTrue(self.notifier.enqueue(signal(), "shoonya", live=True))
        self.assertTrue(entered.wait(2))
        start = time.monotonic()
        self.assertTrue(self.notifier.enqueue(signal("BEARISH"), "shoonya", live=True))
        self.assertLess(time.monotonic() - start, 0.1)
        release.set()
        wait_for(lambda: self.state("BEARISH") == "sent")

    def test_network_and_server_errors_retry_with_a_finite_budget(self):
        self.post.side_effect = [requests.Timeout("token-bearing URL"), response(503, ok=False), response()]
        with patch("orb.notifications.RETRY_DELAYS", (0, 0, 0, 0)):
            self.notifier.start()
            self.notifier.enqueue(signal(), "shoonya", live=True)
            wait_for(lambda: self.state() == "sent")
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(next(iter(self.records().values()))["attempts"], 3)

    def test_rate_limit_honors_telegram_retry_after(self):
        self.post.side_effect = [response(429, ok=False, error_code=429, parameters={"retry_after": 30}), response()]
        self.notifier.start()
        self.notifier.enqueue(signal(), "shoonya", live=True)
        wait_for(lambda: "retrying" in self.notifier.status)
        self.assertEqual(self.post.call_count, 1)
        record = next(iter(self.records().values()))
        self.assertEqual(record["next_attempt"] - self.clock().timestamp(), 30)
        self.clock.value += timedelta(seconds=29)
        self.notifier._wake.set()
        time.sleep(0.03)
        self.assertEqual(self.post.call_count, 1)
        self.clock.value += timedelta(seconds=1)
        self.notifier._wake.set()
        wait_for(lambda: self.state() == "sent")
        self.assertEqual(self.post.call_count, 2)

    def test_retry_budget_and_permanent_failures_are_not_requeued(self):
        self.post.side_effect = requests.ConnectionError("secret URL")
        with patch("orb.notifications.RETRY_DELAYS", (0, 0, 0, 0)):
            self.notifier.start()
            self.notifier.enqueue(signal(), "shoonya", live=True)
            wait_for(lambda: self.state() == "failed")
        self.assertEqual(self.post.call_count, 5)
        self.assertFalse(self.notifier.enqueue(signal(), "shoonya", live=True))
        self.post.side_effect = None
        self.post.return_value = response(403, ok=False, description="synthetic-private-token")
        self.notifier.enqueue(signal("BEARISH"), "shoonya", live=True)
        wait_for(lambda: self.state("BEARISH") == "failed")
        self.assertEqual(self.post.call_count, 6)
        self.assertIn("unblock", self.notifier.status)

    def test_pending_outbox_recovers_after_restart_without_requeue(self):
        self.post.return_value = response(429, ok=False, parameters={"retry_after": 30})
        self.notifier.start()
        self.notifier.enqueue(signal(), "shoonya", live=True)
        wait_for(lambda: "retrying" in self.notifier.status)
        self.notifier.stop()
        self.post.return_value = response()
        self.clock.value += timedelta(seconds=30)
        restarted = self.new_notifier()
        restarted.start()
        wait_for(lambda: self.state() == "sent")
        self.assertEqual(self.post.call_count, 2)
        self.assertFalse(restarted.enqueue(signal(), "yahoo", live=True))

    def test_expired_pending_outbox_is_never_delivered(self):
        self.post.return_value = response(429, ok=False, parameters={"retry_after": 30})
        self.notifier.start()
        self.notifier.enqueue(signal(), "shoonya", live=True)
        wait_for(lambda: "retrying" in self.notifier.status)
        self.notifier.stop()
        self.clock.value += DELIVERY_MAX_AGE + timedelta(seconds=1)
        restarted = self.new_notifier()
        restarted.start()
        wait_for(lambda: self.state() == "expired")
        self.assertEqual(self.post.call_count, 1)

    def test_cannot_post_without_a_durable_outbox(self):
        with patch.object(self.notifier, "_persist", side_effect=OSError("sensitive-storage-message")):
            self.notifier.start()
            self.notifier.enqueue(signal(), "shoonya", live=True)
            wait_for(lambda: not self.notifier.enabled)
        self.post.assert_not_called()
        self.assertIn("storage", self.notifier.status)
        self.assertNotIn("sensitive-storage-message", self.notifier.status)

    def test_corrupt_outbox_and_concurrent_monitor_fail_closed(self):
        self.notifier.directory.mkdir()
        self.notifier.outbox_path.write_text("{invalid JSON")
        self.notifier.start()
        self.assertFalse(self.notifier.enabled)
        self.post.assert_not_called()
        self.notifier.outbox_path.unlink()
        first = self.new_notifier()
        first.start()
        second = self.new_notifier()
        second.start()
        self.assertFalse(second.enabled)
        self.assertTrue(first.enabled)

    def test_request_exceptions_and_server_descriptions_do_not_leak_token(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("notifications")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.post.side_effect = [requests.Timeout("https://example/botsynthetic-private-token/sendMessage"),
                                 response(401, ok=False, description="synthetic-private-token")]
        with patch("orb.notifications.RETRY_DELAYS", (0, 0, 0, 0)):
            self.notifier.start()
            self.notifier.enqueue(signal(), "shoonya", live=True)
            wait_for(lambda: self.state() == "failed")
        self.assertNotIn("synthetic-private-token", stream.getvalue())
        self.assertNotIn("synthetic-private-token", self.notifier.status)
        self.assertIn("TELEGRAM_TOKEN", self.notifier.status)
