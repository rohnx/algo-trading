"""The provider-session owner composes and repairs isolated history."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from orb.market import INSTRUMENTS, InstrumentState
from orb.signals import Candle, IST


FOCIT = "FOCIT.BO"


OPEN = datetime(2026, 10, 5, 9, 15, tzinfo=IST)


def candles(symbol, count, price=500):
    return [Candle(symbol, price + slot, price + slot + 5, price + slot - 5,
                   price + slot + 1, 10 + slot, OPEN + timedelta(minutes=slot * 5),
                   OPEN + timedelta(minutes=(slot + 1) * 5), is_closed=True, slot=slot)
            for slot in range(count)]


class ProviderSessionTests(unittest.TestCase):
    def test_composed_history_uses_displayed_child_precision_without_rewriting_native_cache(self):
        now = OPEN.replace(hour=18, minute=0)
        with tempfile.TemporaryDirectory() as directory:
            state = InstrumentState(INSTRUMENTS[FOCIT], now=lambda: now, cache_root=directory)
            state.activate_source("shoonya")
            native = replace(candles(FOCIT, 3, 800)[2], open=800.006, high=805.006,
                             low=795.006, close=801.006, volume=0.9)
            state.seed_historical_candles([native], interval_minutes=5)
            native_path = Path(directory) / "shoonya" / FOCIT / "5m" / "2026-10-05.json"
            native_bytes = native_path.read_bytes()
            fallback = [replace(c, open=c.open + 0.004, high=c.high + 0.004,
                                low=c.low + 0.004, close=c.close + 0.004, volume=0.9)
                        for c in candles(FOCIT, 4)]

            self.assertTrue(state.seed_fallback_history(fallback))
            view = state.snapshot(now, "live")
            first = view["candles"][0]
            self.assertEqual((first["open"], first["high"], first["low"], first["close"]),
                             (500.0, 805.01, 495.0, 801.01))
            self.assertEqual(first["volume"], 0)
            self.assertEqual(view["candles_5m"][2]["close"], 801.01)
            self.assertEqual(view["current_price"], 504.0)
            self.assertEqual(view["quote_source_id"], "yahoo")
            self.assertEqual(native_path.read_bytes(), native_bytes)

    def test_fallback_admission_uses_source_clock_and_preserves_newer_native_quotes(self):
        now = OPEN.replace(hour=10, minute=12)
        for symbol, count, delay in ((FOCIT, 8, 900), ("^CNXPHARMA", 11, 0)):
            with self.subTest(symbol=symbol), tempfile.TemporaryDirectory() as directory:
                state = InstrumentState(INSTRUMENTS[symbol], now=lambda: now, cache_root=directory)
                state.activate_source("shoonya")
                self.assertTrue(state.set_session_open(600, now))
                self.assertTrue(state.process_tick(700, 7, now))
                before = state.snapshot(now, "live")

                self.assertTrue(state.seed_fallback_history(candles(symbol, 12)))
                view = state.snapshot(now, "live")
                self.assertEqual(len(view["candles_5m"]), count)
                self.assertTrue(all(c["is_closed"] for c in view["candles_5m"]))
                for key in ("current_price", "last_tick_time", "session_open",
                            "active_candle", "active_candle_5m"):
                    self.assertEqual(view[key], before[key], key)
                self.assertEqual(view["comparison_status"], "live")
                self.assertEqual(view["history_source_id"], "yahoo")
                self.assertTrue(view["history_fallback"])
                self.assertEqual(view["history_delay_seconds"], delay)
                self.assertEqual(view["quote_source_id"], "shoonya")
                self.assertEqual(view["quote_delay_seconds"], 0)
                self.assertEqual(state.snapshot(now, "live", include_history=False),
                                 {key: value for key, value in view.items()
                                  if key not in ("candles", "candles_5m")})
                native_cache = Path(directory) / "shoonya" / symbol.lstrip("^") / "5m"
                self.assertFalse((native_cache / "2026-10-05.json").exists())

    def test_repairs_survive_source_return_restart_rollover_and_retention(self):
        now = OPEN.replace(hour=18, minute=0)
        with tempfile.TemporaryDirectory() as directory:
            state = InstrumentState(INSTRUMENTS[FOCIT], now=lambda: now, cache_root=directory)
            state.activate_source("shoonya")
            native = candles(FOCIT, 6, 800)
            native[1] = replace(native[1], is_complete=False, close_is_valid=False)
            state.seed_historical_candles(native, interval_minutes=5)
            native_path = Path(directory) / "shoonya" / FOCIT / "5m" / "2026-10-05.json"
            native_bytes = native_path.read_bytes()

            self.assertTrue(state.seed_fallback_history(candles(FOCIT, 9)))
            expected = state.snapshot(now, "live")
            self.assertEqual(len(expected["candles_5m"]), 9)
            self.assertEqual([c["open"] for c in expected["candles_5m"][:3]], [800, 501, 802])
            self.assertTrue(expected["candles_5m"][1]["is_complete"])
            self.assertTrue(expected["candles_5m"][1]["close_is_valid"])
            self.assertEqual(expected["current_price"], 509)
            self.assertEqual(expected["quote_source_id"], "yahoo")
            self.assertEqual(native_path.read_bytes(), native_bytes)
            self.assertEqual(len(json.loads(native_bytes)), 6)
            self.assertFalse(json.loads(native_bytes)[1]["close_is_valid"])

            state.activate_source("yahoo")
            yahoo = state.snapshot(now, "live")
            self.assertEqual(len(yahoo["candles_5m"]), 9)
            self.assertEqual(yahoo["candles_5m"][0]["open"], 500)
            self.assertFalse(yahoo["history_fallback"])
            self.assertFalse(state.seed_fallback_history(candles(FOCIT, 9, 999)))
            state.activate_source("shoonya")
            self.assertEqual(state.snapshot(now, "live"), expected)

            restored = InstrumentState(INSTRUMENTS[FOCIT], now=lambda: now, cache_root=directory)
            restored.activate_source("shoonya")
            self.assertEqual(restored.snapshot(now, "live"), expected)
            self.assertEqual(native_path.read_bytes(), native_bytes)

            now += timedelta(days=1)
            restored.reset(now.date().isoformat())
            today = restored.snapshot(now, "live")
            self.assertEqual(today["date"], "2026-10-06")
            self.assertEqual(today["candles_5m"], [])
            self.assertIsNone(today["current_price"])
            self.assertFalse(today["history_fallback"])
            self.assertEqual(restored.snapshot(now, "live", "2026-10-05"), expected)
            restored.prune_history("2026-10-06")
            expired = restored.snapshot(now, "live", "2026-10-05")
            self.assertEqual(expired["candles_5m"], [])
            self.assertIsNone(expired["current_price"])
            self.assertFalse(expired["history_fallback"])
