"""Package startup keeps resources, credentials, and writable state separate."""

from contextlib import chdir
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import call, Mock, patch

from dotenv import dotenv_values

from orb import paths
from orb.feeds import read_shoonya_credentials
from orb.notifications import read_telegram_credentials
from orb.orb_monitor import main


class PathPolicyTests(unittest.TestCase):
    def test_checkout_detection_requires_our_package_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "pyproject.toml"
            cases = {
                "unrelated project": '[project]\nname = "other-project"\n',
                "malformed TOML": '[invalid\n',
                "wrong-shaped tool": 'tool = "other"\n',
                "wrong-shaped backend": '[tool.uv]\nbuild-backend = false\n',
                "other package": '[tool.uv.build-backend]\nmodule-name = "other"\nmodule-root = ""\n',
                "other module root": '[tool.uv.build-backend]\nmodule-name = "orb"\nmodule-root = "src"\n',
            }
            with patch.object(paths, "PACKAGE_DIR", root / "orb"):
                self.assertIsNone(paths._checkout_root())
                for name, metadata in cases.items():
                    with self.subTest(name=name):
                        project.write_text(metadata)
                        self.assertIsNone(paths._checkout_root())
                project.write_text('[tool.uv.build-backend]\nmodule-name = "orb"\nmodule-root = ""\n')
                self.assertEqual(paths._checkout_root(), root)

    def test_checkout_paths_ignore_working_directory(self):
        checkout = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory, chdir(directory), \
                patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.config_dir(), checkout)
            self.assertEqual(paths.data_dir(), checkout / "orb" / "data")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_installed_paths_use_working_directory_and_xdg_data(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory), \
                patch.object(paths, "_CHECKOUT_ROOT", None), \
                patch.dict(os.environ, {"XDG_DATA_HOME": str(Path(directory) / "state")}, clear=True):
            self.assertEqual(paths.config_dir(), Path(directory))
            self.assertEqual(paths.data_dir(), Path(directory) / "state" / "algo-trading" / "orb")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_installed_data_defaults_to_home(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory), \
                patch.object(paths, "_CHECKOUT_ROOT", None), \
                patch.dict(os.environ, {"HOME": str(Path(directory) / "home")}, clear=True):
            self.assertEqual(paths.data_dir(),
                             Path(directory) / "home" / ".local" / "share" / "algo-trading" / "orb")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_location_overrides_resolve_without_creating_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working"
            working.mkdir()
            environment = {"HOME": str(root / "home"), "ORB_CONFIG_DIR": "../configuration",
                           "ORB_DATA_DIR": "~/state/cache"}
            with chdir(working), patch.dict(os.environ, environment, clear=True):
                self.assertEqual(paths.config_dir(), root / "configuration")
                self.assertEqual(paths.data_dir(), root / "home" / "state" / "cache")
                self.assertEqual(list(root.iterdir()), [working])


class CredentialLocationTests(unittest.TestCase):
    def test_shoonya_credential_precedence_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shoonya").mkdir()
            (root / ".env").write_text(
                "SHOONYA_CLIENT_ID=root-client\nSHOONYA_USER_ID=root-user\n"
                "SHOONYA_SECRET_CODE=root-secret\nSHOONYA_AUTH_CODE=root-code\n")
            provider_file = root / "shoonya" / ".env"
            provider_file.write_text("SHOONYA_CLIENT_ID=provider-client\nSHOONYA_AUTH_CODE=old-code\n")
            environment = {"ORB_CONFIG_DIR": directory, "SHOONYA_USER_ID": "exported-user"}
            with patch.dict(os.environ, environment, clear=True), \
                    patch("dotenv.dotenv_values", wraps=dotenv_values) as read:
                first = read_shoonya_credentials()
                provider_file.write_text("SHOONYA_CLIENT_ID=provider-client\nSHOONYA_AUTH_CODE=fresh-code\n")
                second = read_shoonya_credentials()
                self.assertEqual(read.call_args_list,
                                 [call(root / ".env"), call(provider_file)] * 2)
                self.assertEqual(first, {"SHOONYA_CLIENT_ID": "provider-client",
                                         "SHOONYA_USER_ID": "exported-user",
                                         "SHOONYA_SECRET_CODE": "root-secret",
                                         "SHOONYA_AUTH_CODE": "old-code"})
                self.assertEqual(second, {**first, "SHOONYA_AUTH_CODE": "fresh-code"})
                self.assertEqual(dict(os.environ), environment)

    def test_telegram_credential_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shoonya").mkdir()
            (root / ".env").write_text("TELEGRAM_TOKEN=root-token\nTELEGRAM_CHAT_ID=root-chat\n")
            (root / "shoonya" / ".env").write_text("TELEGRAM_CHAT_ID=provider-chat\n")
            environment = {"ORB_CONFIG_DIR": directory, "TELEGRAM_TOKEN": "exported-token"}
            with patch.dict(os.environ, environment, clear=True), \
                    patch("dotenv.dotenv_values", wraps=dotenv_values) as read:
                self.assertEqual(read_telegram_credentials(),
                                 {"TELEGRAM_TOKEN": "exported-token", "TELEGRAM_CHAT_ID": "root-chat"})
                read.assert_called_once_with(root / ".env")
                self.assertEqual(dict(os.environ), environment)


class MainStartupTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.cache_dir = Path(directory) / "state"
        self.enterContext(patch.dict(os.environ, {"ORB_DATA_DIR": str(self.cache_dir)}, clear=True))
        self.enterContext(patch.object(sys, "argv", ["orb", "--source", "shoonya", "--port", "9200"]))
        self.feeds = self.enterContext(patch("orb.feeds.FeedManager", autospec=True))
        self.notifier = self.enterContext(patch("orb.notifications.TelegramNotifier", autospec=True))
        self.server = self.enterContext(patch("orb.orb_monitor.run_http_server", autospec=True))
        self.thread = self.enterContext(patch("orb.orb_monitor.threading.Thread", autospec=True))
        self.enterContext(patch("orb.orb_monitor.time.sleep", side_effect=KeyboardInterrupt))
        self.enterContext(patch("sys.stdout", io.StringIO()))
        self.lifecycle = Mock()
        self.lifecycle.attach_mock(self.feeds.return_value.stop, "stop_feed")
        self.lifecycle.attach_mock(self.notifier.return_value.stop, "stop_notifier")

    def test_monitor_startup_and_shutdown(self):
        main()
        self.notifier.assert_called_once_with(cache_dir=self.cache_dir)
        self.feeds.assert_called_once()
        hub = self.feeds.call_args.args[0]
        self.assertEqual(hub.cache_dir, self.cache_dir)
        self.assertIs(hub.notifier, self.notifier.return_value)
        self.assertIs(hub.feed_manager, self.feeds.return_value)
        self.notifier.return_value.start.assert_called_once_with()
        self.feeds.return_value.select_source.assert_called_once_with("shoonya")
        self.thread.assert_called_once_with(target=self.server, args=(hub, 9200), daemon=True)
        self.thread.return_value.start.assert_called_once_with()
        self.server.assert_not_called()
        self.assertEqual(self.lifecycle.mock_calls, [call.stop_feed(), call.stop_notifier()])

    def test_notifier_still_stops_when_the_feed_cannot_stop(self):
        self.feeds.return_value.stop.side_effect = RuntimeError("synthetic stop failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic stop failure"):
            main()
        self.assertEqual(self.lifecycle.mock_calls, [call.stop_feed(), call.stop_notifier()])
