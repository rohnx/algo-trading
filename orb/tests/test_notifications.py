"""Telegram delivery tests use synthetic credentials and never contact a bot."""

from datetime import datetime, timedelta, timezone
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notifications import (DELIVERY_MAX_AGE, REQUEST_TIMEOUT, TelegramNotifier,
                           format_signal, read_telegram_credentials)
from signals import IST


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 13, 10, 5, 1, tzinfo=IST)

    def __call__(self):
        return self.value


def signal(direction="BULLISH", *, timestamp=None):
    timestamp = timestamp or datetime(2026, 10, 13, 10, 5, tzinfo=IST)
    return {"symbol": "^NSEI", "setup_id": "dual_orb", "strategy": "dual_ORB",
            "direction": direction, "signal_type": f"DUAL_ORB_{direction}",
            "timestamp": timestamp.isoformat(), "price": 22550 if direction == "BULLISH" else 22400,
            "confirmations": {
                "15": {"range_minutes": 30, "confirmation_minutes": 15,
                       "confirmed_at": (timestamp - timedelta(minutes=5)).isoformat(),
                       "confirmation_price": 22545 if direction == "BULLISH" else 22405,
                       "orb_high": 22540, "orb_low": 22410},
                "5": {"range_minutes": 45, "confirmation_minutes": 5,
                      "confirmed_at": timestamp.isoformat(),
                      "confirmation_price": 22550 if direction == "BULLISH" else 22400,
                      "orb_high": 22548, "orb_low": 22408}}}


def response(code=200, **body):
    return Mock(status_code=code, json=Mock(return_value=body or {"ok": True, "result": {"message_id": 10}}))


def wait_for(check, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.005)
    raise AssertionError("Telegram worker did not reach expected state")


class FormattingTests(unittest.TestCase):
    def test_message_has_second_confirmation_and_both_setups(self):
        key, timestamp, message = format_signal(signal(), "shoonya")
        self.assertEqual(key, "2026-10-13:NIFTY:BULLISH")
        self.assertEqual(timestamp, datetime(2026, 10, 13, 10, 5, tzinfo=IST))
        for expected in ("Both ORBs BULLISH", "10:05:00 IST", "Provider: Shoonya", "22,550.00",
                         "15m close / 30m ORB", "5m close / 45m ORB", "10:00:00 IST", "22,408.00"):
            self.assertIn(expected, message)

    def test_bearish_levels_and_utc_convert_to_ist(self):
        data = signal("BEARISH")
        data["timestamp"] = datetime.fromisoformat(data["timestamp"]).astimezone(timezone.utc).isoformat()
        _, _, message = format_signal(data, "yahoo")
        self.assertIn("22,405.00 below 22,410.00", message)
        self.assertIn("22,400.00 below 22,408.00", message)
        self.assertIn("13 Oct 2026 10:05:00 IST", message)

    def test_invalid_and_expired_confirmations_are_rejected(self):
        cases = []
        for field, value in (("symbol", "FOCIT.BO"), ("setup_id", "orb_30m"),
                             ("price", float("nan")), ("direction", "NEUTRAL")):
            data = signal()
            data[field] = value
            cases.append(data)
        data = signal()
        data["confirmations"]["5"]["confirmed_at"] = "2026-10-13T09:55:00+05:30"
        cases.append(data)
        data = signal()
        data["confirmations"]["15"]["range_minutes"] = 45
        cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                format_signal(data, "shoonya")


class CredentialTests(unittest.TestCase):
    def test_root_dotenv_does_not_replace_exported_values(self):
        values = {"TELEGRAM_TOKEN": "file-token", "TELEGRAM_CHAT_ID": "file-chat"}
        with patch("dotenv.dotenv_values", return_value=values), patch.dict("os.environ", {
                "TELEGRAM_TOKEN": "exported-token"}, clear=True):
            self.assertEqual(read_telegram_credentials(), {
                "TELEGRAM_TOKEN": "exported-token", "TELEGRAM_CHAT_ID": "file-chat"})

    def test_incomplete_configuration_is_disabled_without_network_or_disk(self):
        with tempfile.TemporaryDirectory() as directory, patch("notifications.requests.post") as post:
            notifier = TelegramNotifier(cache_dir=directory, token="private-token", chat_id="")
            notifier.start()
            self.assertFalse(notifier.enabled)
            self.assertIn("TELEGRAM_CHAT_ID", notifier.status)
            self.assertNotIn("private-token", notifier.status)
            self.assertFalse(notifier.enqueue(signal(), "shoonya", live=True))
            self.assertFalse(notifier.directory.exists())
            post.assert_not_called()

    def test_dotenv_key_without_value_is_missing_configuration(self):
        with patch("notifications.read_telegram_credentials", return_value={
                "TELEGRAM_TOKEN": "synthetic-token", "TELEGRAM_CHAT_ID": None}):
            notifier = TelegramNotifier()
        self.assertFalse(notifier.enabled)
        self.assertIn("TELEGRAM_CHAT_ID", notifier.status)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = Clock()
        self.post = patch("notifications.requests.post", return_value=response()).start()
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
        with patch("notifications.RETRY_DELAYS", (0, 0, 0, 0)):
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
        with patch("notifications.RETRY_DELAYS", (0, 0, 0, 0)):
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
        with patch("notifications.RETRY_DELAYS", (0, 0, 0, 0)):
            self.notifier.start()
            self.notifier.enqueue(signal(), "shoonya", live=True)
            wait_for(lambda: self.state() == "failed")
        self.assertNotIn("synthetic-private-token", stream.getvalue())
        self.assertNotIn("synthetic-private-token", self.notifier.status)
        self.assertIn("TELEGRAM_TOKEN", self.notifier.status)


if __name__ == "__main__":
    unittest.main()
