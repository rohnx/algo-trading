"""HTTP parsing and SSE transport preserve their wire and failure contracts."""

import io
import json
import queue
import unittest
from unittest.mock import patch
from urllib.error import URLError

from orb.orb_monitor import MonitorHub

from orb.tests.support.http import request as http_request
from orb.tests.support.market import session_bar as bar, timestamp


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.hub = MonitorHub(now=lambda: timestamp("2026-10-05", 16))
        self.hub.seed_historical_candles([bar("2026-10-02", 0)])

    def request(self, path, method="GET"):
        content = f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode()
        return http_request(self.hub, content)

    def test_history_endpoint_lists_available_dates(self):
        code, body = self.request("/api/history")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["dates"], ["2026-10-02"])
        self.assertEqual(data["earliest_date"], "2026-10-02")
        self.assertEqual(data["today"], "2026-10-05")

    def test_idle_stream_uses_a_comment_without_serializing_another_snapshot(self):
        initial = {"type": "init", **self.hub.snapshot()}
        client = queue.Queue(maxsize=100)
        with patch("orb.orb_monitor.queue.Queue", return_value=client), \
                patch.object(client, "get", side_effect=[initial, queue.Empty, BrokenPipeError]), \
                patch.object(self.hub, "_snapshot", wraps=self.hub._snapshot) as snapshot:
            code, body = self.request("/api/stream")
        self.assertEqual(code, 200)
        blocks = body.split(b"\n\n")
        self.assertEqual(json.loads(blocks[0].removeprefix(b"data: ")), initial)
        self.assertEqual(blocks[1:], [b": keep-alive", b""])
        self.assertEqual(snapshot.call_count, 1, "only the initial connection needs full history")
        self.assertNotIn(client, self.hub.clients)

    def test_session_endpoint_selects_past_date_or_today(self):
        code, body = self.request("/api/session?date=2026-10-02")
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)["candles"]), 1)
        code, body = self.request("/api/session")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["date"], "2026-10-05")

    def test_invalid_date_is_rejected(self):
        for day in ("bad", "2026-99-99", "20261002"):
            with self.subTest(day=day):
                code, body = self.request(f"/api/session?date={day}")
                self.assertEqual(code, 400)
                self.assertIn("YYYY-MM-DD", json.loads(body)["message"])

    def test_missing_date_returns_empty_snapshot_with_message(self):
        code, body = self.request("/api/session?date=2020-01-01")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["date"], "2020-01-01")
        self.assertEqual(data["candles"], [])
        self.assertIn("message", data)

    def test_removed_mutating_endpoints_are_unavailable(self):
        for endpoint in ("/api/config", "/api/replay"):
            with self.subTest(endpoint=endpoint):
                code, _ = self.request(endpoint, "POST")
                self.assertIn(code, (404, 405, 501))

    def test_shoonya_help_returns_only_the_login_link(self):
        expected = "https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=test-client"
        with patch("orb.feeds.shoonya_login_url", return_value=expected):
            code, body = self.request("/api/shoonya/help")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"login_url": expected})

    def test_public_ip_check_is_bounded_and_runs_from_the_monitor_host(self):
        with patch("orb.orb_monitor.urlopen", return_value=io.BytesIO(b"203.0.113.8\n")) as fetch:
            code, body = self.request("/api/public-ip")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"ip": "203.0.113.8"})
        self.assertEqual(fetch.call_args.args[0].full_url, "https://ifconfig.me/ip")
        self.assertEqual(fetch.call_args.kwargs["timeout"], 3)

    def test_public_ip_failure_keeps_a_manual_recovery_instruction(self):
        for failure in (URLError("offline"), TimeoutError()):
            with self.subTest(failure=type(failure)), patch("orb.orb_monitor.urlopen", side_effect=failure):
                code, body = self.request("/api/public-ip")
            self.assertEqual(code, 503)
            self.assertIn("curl -s ifconfig.me", json.loads(body)["message"])
        with patch("orb.orb_monitor.urlopen", return_value=io.BytesIO(b"<html>service error</html>")):
            code, body = self.request("/api/public-ip")
        self.assertEqual(code, 503)
        self.assertNotIn("<html>", body.decode())
