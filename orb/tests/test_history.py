"""Yahoo comparison backfills must not become Shoonya quotes or saved bars."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market import INSTRUMENTS, PRIMARY_SYMBOL
from orb_monitor import MonitorHub
from test_features import Clock, at, bar

FOCIT = "FOCIT.BO"


def history(count=75, *, day="2026-10-05", price=500):
    return [replace(bar(slot, 5, day=day, price=price + slot), symbol=FOCIT)
            for slot in range(count)]


class FallbackHistoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(18, 0))
        self.hub = MonitorHub(now=self.clock)
        self.hub.activate_source("shoonya")
        self.hub.set_feed_status("live")

    def focit(self, day=None):
        return self.hub.snapshot(day)["instruments"][FOCIT]

    def seed(self, candles=None):
        return self.hub.seed_fallback_history(history() if candles is None else candles, symbol=FOCIT)

    def test_all_sectors_compose_yahoo_history_with_native_quotes_without_changing_nifty(self):
        self.clock.value = at(10, 6)
        primary_before = self.hub.snapshot()["instruments"][PRIMARY_SYMBOL]
        for symbol, instrument in INSTRUMENTS.items():
            if instrument.comparison_group != "sectors":
                continue
            with self.subTest(symbol=symbol):
                backfill = [replace(candle, symbol=symbol) for candle in history(10)]
                native = [replace(candle, symbol=symbol) for candle in history(10, price=800)[9:]]
                self.hub.seed_historical_candles(native, 5, symbol=symbol)
                self.hub.process_tick(900, symbol=symbol, session_open=700)
                native_before = self.hub.instruments[symbol].snapshot(self.clock(), "live")
                self.assertTrue(self.hub.seed_fallback_history(backfill, symbol=symbol))
                state = self.hub.snapshot()["instruments"][symbol]
                self.assertEqual(state["comparison_group"], "sectors")
                self.assertTrue(state["history_fallback"])
                self.assertEqual(state["history_source_id"], "yahoo")
                self.assertEqual(state["history_delay_seconds"], 0)
                self.assertEqual(state["quote_source_id"], "shoonya")
                self.assertEqual(state["quote_delay_seconds"], 0)
                self.assertEqual(state["current_price"], 900)
                self.assertEqual(state["last_tick_time"], at(10, 6).isoformat())
                self.assertEqual(len(state["candles_5m"]), 10)
                self.assertEqual(state["candles_5m"][-1]["close"], native[-1].close)
                self.assertEqual(state["breakout"]["direction"], "BULLISH")
                self.assertEqual(state["breakout"]["range_high"], 509)
                self.assertEqual(self.hub.instruments[symbol].snapshot(self.clock(), "live"), native_before)
        self.assertEqual(self.hub.snapshot()["instruments"][PRIMARY_SYMBOL], primary_before)
        self.assertEqual(self.hub.signals, [])

    def test_after_hours_fallback_supplies_full_session_in_both_intervals(self):
        primary_before = self.hub.snapshot()["instruments"][PRIMARY_SYMBOL]
        self.assertTrue(self.seed())
        result = self.focit()
        self.assertEqual(len(result["candles_5m"]), 75)
        self.assertEqual(len(result["candles"]), 25)
        self.assertTrue(all(item["is_complete"] and item["close_is_valid"] for item in result["candles"]))
        self.assertIsNone(result["active_candle"])
        self.assertIsNone(result["active_candle_5m"])
        self.assertEqual(result["session_open"], 500)
        self.assertEqual(result["current_price"], 575)
        self.assertEqual(result["comparison_status"], "closed")
        self.assertAlmostEqual(result["change_pct"], 15)
        self.assertEqual(result["history_source_id"], "yahoo")
        self.assertEqual(result["quote_source_id"], "yahoo")
        self.assertTrue(result["history_fallback"])
        self.assertEqual(result["history_delay_seconds"], 900)
        self.assertEqual(result["quote_delay_seconds"], 900)
        self.assertIsNone(result["last_tick_time"])
        self.assertEqual(self.hub.snapshot()["instruments"][PRIMARY_SYMBOL], primary_before)
        self.assertEqual(self.hub.instruments[FOCIT].candles_5m, [])
        self.assertEqual(self.hub.signals, [])

    def test_late_fallback_preserves_newer_native_quote_and_active_candles(self):
        self.clock.value = at(10, 12)
        self.hub.advance_time()
        self.assertTrue(self.hub.process_tick(700, 7, symbol=FOCIT, session_open=600))
        native = self.hub.instruments[FOCIT].snapshot(self.clock(), "live")
        self.seed(history(12))
        result = self.focit()
        for key in ("current_price", "last_tick_time", "session_open", "active_candle", "active_candle_5m"):
            self.assertEqual(result[key], native[key], key)
        self.assertEqual(result["comparison_status"], "live")
        self.assertEqual(result["quote_source_id"], "shoonya")
        self.assertEqual(result["quote_delay_seconds"], 0)
        self.assertEqual(result["history_delay_seconds"], 900)
        self.assertEqual(result["history_source_id"], "yahoo")
        self.assertEqual(len(result["candles_5m"]), 8)
        self.assertEqual(self.hub.instruments[FOCIT].last_tick_time, at(10, 12))
        delta = self.hub._instrument_snapshot(FOCIT, include_history=False)
        self.assertNotIn("candles", delta)
        self.assertNotIn("candles_5m", delta)
        self.assertEqual(delta["active_candle_5m"], result["active_candle_5m"])
        self.assertEqual(delta["history_source_id"], "yahoo")

    def test_live_history_only_is_stale_and_excludes_delayed_partial_bar(self):
        self.clock.value = at(10, 12)
        candles = history(9)
        candles[-1] = replace(candles[-1], is_closed=False)
        self.seed(candles)
        result = self.focit()
        self.assertEqual(len(result["candles_5m"]), 8)
        self.assertEqual(result["candles_5m"][-1]["end_time"], at(9, 55).isoformat())
        self.assertIsNone(result["active_candle_5m"])
        self.assertEqual(result["comparison_status"], "stale")
        self.assertIsNone(result["last_tick_time"])
        self.assertIsNone(result["change_pct"])
        # Receipt or elapsed wall time must not promote that partial close.
        self.clock.value = at(10, 20)
        result = self.focit()
        self.assertEqual(len(result["candles_5m"]), 8)
        self.assertFalse(result["candles"][-1]["is_complete"])
        self.assertFalse(result["candles"][-1]["close_is_valid"])

    def test_incomplete_native_parent_does_not_replace_valid_child_aggregate(self):
        self.clock.value = at(10, 12)
        self.hub.advance_time()
        self.hub.seed_historical_candles(history(12, price=600)[9:], 5, symbol=FOCIT)
        self.hub.process_tick(700, symbol=FOCIT, session_open=600)
        native = self.hub.instruments[FOCIT]
        native.active_candle.is_complete = False
        native.active_candle.close_is_valid = False
        self.seed(history(12))
        result = self.focit()
        self.assertTrue(result["active_candle"]["is_complete"])
        self.assertTrue(result["active_candle"]["close_is_valid"])
        self.assertEqual(result["active_candle"]["close"], 700)
        self.assertEqual(result["quote_source_id"], "shoonya")
        self.assertFalse(native.active_candle.is_complete, "composition cannot alter native bar validity")
        self.assertFalse(native.active_candle.close_is_valid)

    def test_missing_five_minute_children_remain_invalid_fifteen_minute_gaps(self):
        candles = history(9)
        self.seed([item for item in candles if item.slot != 4])
        result = self.focit()
        self.assertEqual(len(result["candles_5m"]), 8)
        self.assertEqual(len(result["candles"]), 3)
        self.assertTrue(result["candles"][0]["is_complete"])
        self.assertFalse(result["candles"][1]["is_complete"])
        self.assertFalse(result["candles"][1]["close_is_valid"])
        self.assertTrue(result["candles"][2]["is_complete"])

    def test_later_backfill_repairs_the_handoff_gap_without_replacing_live_prices(self):
        self.clock.value = at(10, 12)
        self.hub.advance_time()
        self.hub.process_tick(700, symbol=FOCIT, session_open=600)
        self.seed(history(12))
        initial = self.focit()
        self.assertNotIn(at(10, 0).isoformat(), [c["start_time"] for c in initial["candles_5m"]])
        for minute in range(1, 24):
            self.clock.value = at(10, 12) + timedelta(minutes=minute)
            self.hub.process_tick(700 + minute, symbol=FOCIT)
        native = self.hub.instruments[FOCIT].snapshot(self.clock(), "live")
        self.seed(history(13))
        result = self.focit()
        self.assertIn(at(10, 0).isoformat(), [c["start_time"] for c in result["candles_5m"]])
        self.assertEqual(result["current_price"], 723)
        self.assertEqual(result["last_tick_time"], at(10, 35).isoformat())
        self.assertEqual(result["quote_source_id"], "shoonya")
        self.assertEqual(result["active_candle_5m"], native["active_candle_5m"])
        native_valid = {c["start_time"]: c for c in native["candles_5m"] if c["is_complete"] and c["close_is_valid"]}
        for candle in result["candles_5m"]:
            if candle["start_time"] in native_valid:
                self.assertEqual(candle, native_valid[candle["start_time"]])
        self.assertEqual(self.hub.instruments[FOCIT].snapshot(self.clock(), "live"), native)

    def test_valid_native_bars_win_and_invalid_native_bars_use_valid_fallback(self):
        native = history(6, price=800)
        native[1] = replace(native[1], is_complete=False, close_is_valid=False)
        self.hub.seed_historical_candles(native, 5, symbol=FOCIT)
        self.seed(history(9))
        result = self.focit()
        self.assertEqual(result["candles_5m"][0]["open"], 800)
        self.assertEqual(result["candles_5m"][1]["open"], 501)
        self.assertEqual(result["candles_5m"][2]["open"], 802)
        self.assertEqual(result["candles"][0]["open"], 800)
        self.assertEqual(result["candles"][0]["close"], 803)
        self.assertTrue(result["candles"][0]["is_complete"])
        self.assertEqual(result["current_price"], 509)
        self.assertEqual(result["quote_source_id"], "yahoo")
        self.assertEqual(self.hub.instruments[FOCIT].candles_5m[1].open, 801)
        self.assertFalse(self.hub.instruments[FOCIT].candles_5m[1].is_complete)

    def test_equal_full_native_history_does_not_claim_fallback_or_replace_prices(self):
        self.hub.seed_historical_candles(history(price=800), 5, symbol=FOCIT)
        self.seed()
        result = self.focit()
        self.assertFalse(result["history_fallback"])
        self.assertEqual(result["history_source_id"], "shoonya")
        self.assertEqual(result["quote_source_id"], "shoonya")
        self.assertEqual(result["current_price"], 875)

    def test_historical_date_isolated_from_todays_native_quote(self):
        self.clock.value = at(10, 12)
        self.hub.process_tick(900, symbol=FOCIT, session_open=800)
        self.seed(history(day="2026-10-02"))
        past = self.focit("2026-10-02")
        self.assertEqual(past["current_price"], 575)
        self.assertEqual(past["session_open"], 500)
        self.assertEqual(past["comparison_status"], "closed")
        self.assertIsNone(past["last_tick_time"])
        self.assertIsNone(past["active_candle_5m"])
        self.assertEqual(len(past["candles_5m"]), 75)
        today = self.focit()
        self.assertEqual(today["current_price"], 900)
        self.assertEqual(today["session_open"], 800)
        self.assertFalse(today["history_fallback"])

    def test_cache_provider_and_source_switch_isolation_with_latest_yahoo_history(self):
        with tempfile.TemporaryDirectory() as directory:
            self.hub = MonitorHub(now=self.clock, cache_dir=directory)
            self.hub.activate_source("shoonya")
            self.seed()
            yahoo_path = Path(directory) / "yahoo" / FOCIT / "5m" / "2026-10-05.json"
            self.assertEqual(len(json.loads(yahoo_path.read_text())), 75)
            self.assertFalse((Path(directory) / "shoonya" / FOCIT / "5m" / "2026-10-05.json").exists())
            self.hub.activate_source("yahoo")
            self.hub.seed_historical_candles(history(price=600), 5, symbol=FOCIT)
            self.assertFalse(self.focit()["history_fallback"])
            self.assertFalse(self.seed(history(price=999)))
            self.hub.activate_source("shoonya")
            self.assertEqual(self.focit()["current_price"], 675)
            self.assertEqual(self.focit()["session_open"], 600)
            restored = MonitorHub(now=self.clock, cache_dir=directory)
            restored.activate_source("shoonya")
            self.assertEqual(restored.snapshot()["instruments"][FOCIT]["current_price"], 675)

    def test_new_day_keeps_past_fallback_without_leaking_it_into_today(self):
        self.seed()
        self.clock.value = at(10, 12, day="2026-10-06")
        today = self.focit()
        self.assertIsNone(today["current_price"])
        self.assertEqual(today["candles_5m"], [])
        self.assertFalse(today["history_fallback"])
        self.assertEqual(self.focit("2026-10-05")["current_price"], 575)

    def test_revisiting_shoonya_keeps_completed_fallback_over_inactive_invalid_yahoo_cache(self):
        self.seed(history(9))
        self.hub.activate_source("yahoo")
        candles = history(9, price=600)
        candles[1] = replace(candles[1], is_complete=False, close_is_valid=False)
        self.hub.seed_historical_candles(candles, 5, symbol=FOCIT)
        self.hub.activate_source("shoonya")
        result = self.focit()
        self.assertEqual(result["candles_5m"][0]["open"], 600)
        self.assertEqual(result["candles_5m"][1]["open"], 501)
        self.assertTrue(result["candles_5m"][1]["is_complete"])
        self.assertTrue(result["candles"][0]["is_complete"])

    def test_switching_to_yahoo_reuses_fallback_without_an_upstream_response_or_shorter_save(self):
        self.clock.value = at(13, 0)
        with tempfile.TemporaryDirectory() as directory:
            self.hub = MonitorHub(now=self.clock, cache_dir=directory)
            self.hub.activate_source("shoonya")
            self.seed(history(40))
            before = self.focit()
            path = Path(directory) / "yahoo" / FOCIT / "5m" / "2026-10-05.json"
            self.assertEqual(len(json.loads(path.read_text())), 40)
            self.hub.activate_source("yahoo")
            restored = self.focit()
            self.assertEqual(restored["candles_5m"], before["candles_5m"])
            self.assertEqual(restored["current_price"], before["current_price"])
            self.assertEqual(restored["session_open"], before["session_open"])
            self.assertFalse(restored["history_fallback"])
            self.assertEqual(len(json.loads(path.read_text())), 40)
            # Yahoo's native bar engine can save its next live close without
            # replacing the longer fallback cache with its former empty cache.
            self.assertTrue(self.hub.process_tick(700, symbol=FOCIT))
            self.clock.value = at(13, 4, 59)
            self.assertTrue(self.hub.process_tick(701, symbol=FOCIT))
            self.clock.value = at(13, 5)
            self.assertTrue(self.hub.process_tick(702, symbol=FOCIT))
            saved = json.loads(path.read_text())
            self.assertEqual(len(saved), 41)
            self.assertEqual(saved[:40], before["candles_5m"])
            self.assertFalse((Path(directory) / "shoonya" / FOCIT / "5m" / "2026-10-05.json").exists())

    def test_switching_to_yahoo_preserves_valid_cached_overlaps_and_repairs_invalid_ones(self):
        self.hub.activate_source("yahoo")
        candles = history(9, price=600)
        candles[1] = replace(candles[1], is_complete=False, close_is_valid=False)
        self.hub.seed_historical_candles(candles, 5, symbol=FOCIT)
        self.hub.activate_source("shoonya")
        self.seed(history(12))
        self.hub.activate_source("yahoo")
        result = self.focit()
        self.assertEqual(len(result["candles_5m"]), 12)
        self.assertEqual(result["candles_5m"][0]["open"], 600)
        self.assertEqual(result["candles_5m"][1]["open"], 501)
        self.assertTrue(result["candles_5m"][1]["is_complete"])
        self.assertEqual(result["candles_5m"][8]["open"], 608)
        self.assertEqual(result["candles_5m"][9]["open"], 509)

    def test_comparison_backfill_does_not_notify_or_rebuild_primary_strategies(self):
        notifier = Mock()
        self.hub.notifier = notifier
        before = self.hub.snapshot()["setups"]
        self.seed()
        self.assertEqual(self.hub.snapshot()["setups"], before)
        notifier.enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
