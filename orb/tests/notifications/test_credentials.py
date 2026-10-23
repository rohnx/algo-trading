"""Telegram configuration uses fresh file values without overriding exported settings."""

import tempfile
import unittest
from unittest.mock import patch

from orb.notifications import read_telegram_credentials, TelegramNotifier

from orb.tests.support.notifications import signal


class CredentialTests(unittest.TestCase):
    def test_root_dotenv_does_not_replace_exported_values(self):
        values = {"TELEGRAM_TOKEN": "file-token", "TELEGRAM_CHAT_ID": "file-chat"}
        with patch("dotenv.dotenv_values", return_value=values), patch.dict("os.environ", {
                "TELEGRAM_TOKEN": "exported-token"}, clear=True):
            self.assertEqual(read_telegram_credentials(), {
                "TELEGRAM_TOKEN": "exported-token", "TELEGRAM_CHAT_ID": "file-chat"})

    def test_incomplete_configuration_is_disabled_without_network_or_disk(self):
        with tempfile.TemporaryDirectory() as directory, patch("orb.notifications.requests.post") as post:
            notifier = TelegramNotifier(cache_dir=directory, token="private-token", chat_id="")
            notifier.start()
            self.assertFalse(notifier.enabled)
            self.assertIn("TELEGRAM_CHAT_ID", notifier.status)
            self.assertNotIn("private-token", notifier.status)
            self.assertFalse(notifier.enqueue(signal(), "shoonya", live=True))
            self.assertFalse(notifier.directory.exists())
            post.assert_not_called()

    def test_dotenv_key_without_value_is_missing_configuration(self):
        with patch("orb.notifications.read_telegram_credentials", return_value={
                "TELEGRAM_TOKEN": "synthetic-token", "TELEGRAM_CHAT_ID": None}):
            notifier = TelegramNotifier()
        self.assertFalse(notifier.enabled)
        self.assertIn("TELEGRAM_CHAT_ID", notifier.status)
