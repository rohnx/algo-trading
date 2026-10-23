"""Source requests validate input before selecting a provider."""

import json
import unittest

from orb.orb_monitor import MonitorHub

from orb.tests.support.http import request as http_request
from orb.tests.support.market import at, bar


class SourceHTTPTests(unittest.TestCase):
    class Manager:
        def __init__(self, hub):
            self.hub = hub
            self.calls = []

        def select_source(self, source):
            self.calls.append(source)
            self.hub.activate_source(source)
            return source

    def setUp(self):
        self.hub = MonitorHub(now=lambda: at(11, 0))
        self.manager = self.Manager(self.hub)
        self.hub.feed_manager = self.manager

    def post(self, payload):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        header = (f"POST /api/source HTTP/1.0\r\nHost: localhost\r\n"
                  f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n").encode()
        status, body = http_request(self.hub, header + data)
        return status, json.loads(body)

    def test_source_post_switches_active_manager_and_returns_clean_snapshot(self):
        self.hub.seed_historical_candles([bar(slot) for slot in range(3)])
        status, state = self.post({"source": "shoonya"})
        self.assertEqual(status, 200)
        self.assertEqual(self.manager.calls, ["shoonya"])
        self.assertEqual(state["source_id"], "shoonya")
        self.assertEqual(state["candles"], [])
        self.assertEqual(state["candles_5m"], [])
        self.assertFalse(state["range_locked"])

    def test_bad_source_payloads_never_call_manager(self):
        before = self.hub.snapshot()
        for payload in ({"source": "invalid"}, {}, b"{not json}", ["shoonya"]):
            with self.subTest(payload=payload):
                status, _ = self.post(payload)
                self.assertGreaterEqual(status, 400)
                self.assertLess(status, 500)
                self.assertEqual(self.manager.calls, [])
                self.assertEqual(self.hub.snapshot(), before)

    def test_source_post_without_manager_has_explicit_error(self):
        self.hub.feed_manager = None
        status, response = self.post({"source": "shoonya"})
        self.assertGreaterEqual(status, 400)
        self.assertLess(status, 600)
        self.assertIn("message", response)
        self.assertEqual(self.hub.snapshot()["source_id"], "yahoo")
