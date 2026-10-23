"""Slow outbox persistence cannot block enqueue or lose accepted alerts."""

import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from orb.notifications import TelegramNotifier

from orb.tests.support.notifications import Clock, response, signal


class NotificationConcurrencyTests(unittest.TestCase):
    def test_enqueue_during_slow_persistence_remains_nonblocking_and_durable(self):
        clock = Clock()
        entered, release, accepted = (threading.Event() for _ in range(3))
        original_fsync = os.fsync
        results = []

        def slow_fsync(descriptor):
            if not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("Test did not release the outbox write")
            return original_fsync(descriptor)

        with tempfile.TemporaryDirectory() as directory, patch("orb.notifications.requests.post") as post:
            notifier = TelegramNotifier(cache_dir=directory, now=clock,
                                        token="synthetic-token", chat_id="synthetic-chat")
            producer = None
            try:
                with patch("orb.notifications.os.fsync", side_effect=slow_fsync):
                    notifier.start()
                    self.assertTrue(notifier.enqueue(signal(), "yahoo", live=True))
                    self.assertTrue(entered.wait(2), "Worker never began persisting the first alert")

                    def enqueue_second():
                        results.append(notifier.enqueue(signal("BEARISH"), "shoonya", live=True))
                        accepted.set()

                    producer = threading.Thread(target=enqueue_second, daemon=True)
                    producer.start()
                    self.assertTrue(accepted.wait(0.5), "Slow disk I/O blocked enqueue")
                    self.assertEqual(results, [True])
                    self.assertFalse(release.is_set())
                    notifier.stop(timeout=0)
                    self.assertFalse(notifier.enqueue(signal(), "yahoo", live=True))
                    post.assert_not_called()
                    release.set()
                    notifier.stop(timeout=3)
                    self.assertFalse(notifier._thread.is_alive())
            finally:
                release.set()
                if producer is not None:
                    producer.join(timeout=3)
                notifier.stop(timeout=3)

            records = json.loads(notifier.outbox_path.read_text())["records"]
            self.assertEqual(set(records), {"2026-10-13:NIFTY:BULLISH", "2026-10-13:NIFTY:BEARISH"})
            self.assertTrue(all(record["state"] == "pending" for record in records.values()))
            delivered, complete = set(), threading.Event()

            def send(*args, **kwargs):
                durable = json.loads(notifier.outbox_path.read_text())["records"]
                record = next(record for record in durable.values() if record["text"] == kwargs["json"]["text"])
                self.assertEqual(record["state"], "pending")
                self.assertGreaterEqual(record["attempts"], 1)
                delivered.add(record["key"])
                if len(delivered) == 2:
                    complete.set()
                return response()

            post.side_effect = send
            restarted = TelegramNotifier(cache_dir=directory, now=clock,
                                         token="synthetic-token", chat_id="synthetic-chat")
            try:
                restarted.start()
                self.assertTrue(complete.wait(3), "Restart did not recover both accepted alerts")
                restarted.stop(timeout=3)
            finally:
                restarted.stop(timeout=3)
            records = json.loads(restarted.outbox_path.read_text())["records"]
            self.assertTrue(all(record["state"] == "sent" for record in records.values()))
            self.assertEqual(post.call_count, 2)
