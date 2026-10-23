"""Session closes reach disk and clients before an admitted quote changes prices."""

import io
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from orb.orb_monitor import MonitorHub, SYMBOL

from orb.tests.support.clock import Clock
from orb.tests.support.market import DAY, at, five_bars


class RecordingClient(queue.Queue):
    def __init__(self, directory, effects):
        super().__init__()
        self.directory, self.effects = directory, effects

    def put_nowait(self, event):
        saved = {interval: json.loads(path.read_text()) for interval, path in (
            (15, self.directory / "yahoo" / f"{DAY}.json"),
            (5, self.directory / "yahoo" / "5m" / f"{DAY}.json"),
        )}
        self.effects.append((event["type"], event, saved))
        super().put_nowait(event)


class RecordingNotifier:
    def __init__(self, effects):
        self.effects = effects

    def enqueue(self, signal, source, *, live):
        self.effects.append(("alert", signal, source, live))
        return True


class SessionTransitionOrderTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock = Clock(at(10, 0))
        self.effects = []
        self.hub = MonitorHub(now=self.clock, cache_dir=self.directory,
                              notifier=RecordingNotifier(self.effects))
        self.hub.seed_historical_candles(five_bars(DAY), 5)
        for minute in range(15):
            self.clock.value = at(10, minute)
            self.assertTrue(self.hub.process_tick(100 if minute < 10 else 125,
                                                  tick_time=self.clock()))
        self.clock.value = at(10, 14, 30)
        self.assertTrue(self.hub.process_tick(125, tick_time=self.clock()))
        self.assertEqual(self.hub.signals, [])
        self.client = RecordingClient(self.directory, self.effects)
        self.hub.register_client(self.client)
        self.client.get_nowait()
        self.effects.clear()

    def assert_closed_before_quote(self):
        closes = self.effects[:2]
        self.assertEqual([(kind, event["interval_minutes"]) for kind, event, _ in closes],
                         [("candle_close", 5), ("candle_close", 15)])
        for _, event, saved in closes:
            for interval in (5, 15):
                self.assertEqual(saved[interval][-1]["end_time"], at(10, 15).isoformat())
                self.assertEqual(saved[interval][-1]["close"], 125)
            instrument = event["instruments"][SYMBOL]
            self.assertEqual(instrument["current_price"], 125)
            self.assertEqual(instrument["last_tick_time"], at(10, 14, 30).isoformat())
            self.assertEqual(instrument["session_open"], 100)
            self.assertIsNone(instrument["active_candle"])
            self.assertIsNone(instrument["active_candle_5m"])
            self.assertEqual(event["candle"]["close"], 125)
            self.assertTrue(event["candle"]["is_complete"])
            self.assertTrue(event["candle"]["close_is_valid"])
            self.assertEqual(len(event["signals"]), 1)
            self.assertEqual(event["signals"][0]["timestamp"], at(10, 15).isoformat())

    def test_admitted_boundary_quote_publishes_saved_closes_before_its_price(self):
        with patch("sys.stdout", io.StringIO()):
            accepted = self.hub.process_tick(999, tick_time=at(10, 15), session_open=90)
        self.assertTrue(accepted)
        self.assertEqual([effect[0] for effect in self.effects],
                         ["candle_close", "candle_close", "tick"])
        self.assert_closed_before_quote()
        tick = self.effects[-1][1]
        instrument = tick["instruments"][SYMBOL]
        self.assertEqual((tick["price"], instrument["current_price"]), (999, 999))
        self.assertEqual(instrument["last_tick_time"], at(10, 15).isoformat())
        self.assertEqual(instrument["active_candle"]["close"], 999)
        self.assertEqual(instrument["active_candle_5m"]["close"], 999)
        self.assertEqual(instrument["session_open"], 100,
                         "a reported open cannot replace the established opening")
        self.assertEqual(len(self.hub.signals), 1)
        self.assertEqual(self.hub.signals[0].price, 125)
        self.assertEqual(self.client.qsize(), 3,
                         "a future confirmation is displayed but is not eligible for an alert")

    def test_too_future_quote_has_no_tick_clock_or_persistence_effects(self):
        before = self.hub.snapshot()
        files = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in self.directory.rglob("*.json")}
        self.assertFalse(self.hub.process_tick(999, tick_time=at(10, 16), session_open=90))
        self.assertEqual(self.hub.snapshot(), before)
        self.assertEqual(self.effects, [])
        self.assertTrue(self.client.empty())
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns)
                          for path in self.directory.rglob("*.json")}, files)

    def test_invalid_quote_still_allows_wall_clock_closes_and_an_eligible_alert(self):
        self.clock.value = at(10, 15)
        with patch("sys.stdout", io.StringIO()):
            accepted = self.hub.process_tick(0, tick_time=self.clock(), session_open=90)
        self.assertFalse(accepted)
        self.assertEqual([effect[0] for effect in self.effects],
                         ["candle_close", "candle_close", "alert"])
        self.assert_closed_before_quote()
        _, signal, source, live = self.effects[-1]
        self.assertIs(signal, self.hub.signals[0])
        self.assertEqual((source, live, signal.timestamp), ("yahoo", True, self.clock()))
        self.assertEqual(self.hub.current_price, 125)
        self.assertEqual(self.hub.last_tick_time, at(10, 14, 30))
        self.assertEqual(self.client.qsize(), 2, "the invalid quote cannot publish a tick")
