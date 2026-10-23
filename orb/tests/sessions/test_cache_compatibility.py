"""Existing cache files survive restoration without an unsolicited rewrite."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at


class CacheCompatibilityTests(unittest.TestCase):
    def test_original_session_format_survives_restart_and_source_switches_without_rewrite(self):
        original = json.dumps([{
            "symbol": "^NSEI", "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0,
            "volume": 12, "start_time": at().isoformat(), "end_time": at(9, 30).isoformat(),
            "is_closed": True, "slot": 0, "is_complete": True, "close_is_valid": True,
        }])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yahoo" / "2026-10-05.json"
            path.parent.mkdir()
            path.write_text(original)
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
            clock = Clock(at(11, 0))
            hub = MonitorHub(now=clock, cache_dir=directory)
            for source in ("yahoo", "shoonya", "yahoo"):
                hub.activate_source(source)
            self.assertEqual(hub.snapshot()["candles"][0]["close"], 105)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(path.stat().st_mtime_ns, 1_000_000_000)
