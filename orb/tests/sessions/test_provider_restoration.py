"""Provider returns and restarts retain native history without leaking live state."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orb.market import INSTRUMENTS, InstrumentState, PRIMARY_SYMBOL
from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bank_bar, bar


BANK = "^NSEBANK"


class InstrumentRestorationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 0))
        self.hub = MonitorHub(now=self.clock)

    def test_provider_and_instrument_caches_restore_without_cross_contamination(self):
        self.clock.value = at(10, 15)
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            for source, offset in (("yahoo", 0), ("shoonya", 1000)):
                if source != "yahoo":
                    hub.activate_source(source)
                for index, symbol in enumerate(INSTRUMENTS):
                    price = 100 + index * 400 + offset
                    candles = [replace(bar(slot, 5, price=price), symbol=symbol) for slot in range(9)]
                    hub.seed_historical_candles(candles, 5, symbol=symbol)
            for source, offset in (("yahoo", 0), ("shoonya", 1000)):
                restored = MonitorHub(now=self.clock, cache_dir=directory)
                restored.activate_source(source)
                state = restored.snapshot()["instruments"]
                for index, symbol in enumerate(INSTRUMENTS):
                    self.assertEqual(state[symbol]["session_open"], 100 + index * 400 + offset)
                    self.assertTrue(all(candle["symbol"] == symbol for candle in state[symbol]["candles"]))
                    self.assertEqual(len(state[symbol]["candles_5m"]), 9)
            self.assertTrue((Path(directory) / "yahoo" / "2026-10-05.json").exists())
            self.assertTrue((Path(directory) / "yahoo" / "NSEBANK" / "5m" / "2026-10-05.json").exists())

    def test_revisiting_sources_reuses_history_without_rewriting_past_sessions(self):
        self.clock.value = at(10, 15)
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            expected = {}
            for source, offset in (("yahoo", 0), ("shoonya", 1000)):
                if source != "yahoo":
                    hub.activate_source(source)
                for index, symbol in enumerate(INSTRUMENTS):
                    candles = [replace(bar(slot, 5, day=day, price=100 + index * 400 + offset + slot),
                                       symbol=symbol)
                               for day in ("2026-10-02", "2026-10-05") for slot in range(9)]
                    hub.seed_historical_candles(candles, 5, symbol=symbol)
                expected[source] = hub.snapshot()
            with patch.object(InstrumentState, "_load_sessions", side_effect=AssertionError("history reread")), \
                    patch.object(InstrumentState, "_save_candles", autospec=True) as save:
                for source in ("yahoo", "shoonya", "yahoo"):
                    hub.activate_source(source)
                    state = hub.snapshot()
                    for key in ("instruments", "candles", "candles_5m", "setups", "signals", "orb_high", "orb_low"):
                        self.assertEqual(state[key], expected[source][key])
                    self.assertEqual(hub.snapshot("2026-10-02")["session_open"],
                                     expected[source]["session_open"])
                    for symbol, instrument in hub.instruments.items():
                        path = Path(directory) / source
                        if symbol != PRIMARY_SYMBOL:
                            path /= symbol.lstrip("^")
                        self.assertEqual(instrument.cache_dir, path)
                self.assertTrue(save.called)
                self.assertEqual({call.args[1] for call in save.call_args_list}, {"2026-10-05"})

    def test_identical_backfill_does_not_rewrite_cache_but_quality_changes_do(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InstrumentState(INSTRUMENTS[BANK], now=self.clock, cache_root=directory)
            candles = [bank_bar(slot, day=day) for day in ("2026-10-02", "2026-10-05")
                       for slot in range(9)]
            state.seed_historical_candles(candles, 5)
            with patch.object(Path, "write_text", side_effect=AssertionError("unchanged cache rewrite")):
                state.seed_historical_candles(candles, 5)
                restored = InstrumentState(INSTRUMENTS[BANK], now=self.clock, cache_root=directory)
                restored.activate_source("yahoo")
                restored.seed_historical_candles(candles, 5)
            revised = replace(candles[0], high=candles[0].high + 10, close_is_valid=False)
            state.seed_historical_candles([revised], 5)
            native = json.loads((state.cache_dir / "5m" / "2026-10-02.json").read_text())
            parent = json.loads((state.cache_dir / "2026-10-02.json").read_text())
            self.assertEqual(native[0]["high"], revised.high)
            self.assertFalse(native[0]["close_is_valid"])
            self.assertEqual(parent[0]["high"], revised.high)
            self.assertFalse(parent[0]["close_is_valid"])

    def test_failed_cache_replace_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InstrumentState(INSTRUMENTS[BANK], now=self.clock, cache_root=directory)
            candles = [bank_bar()]
            destination = state.cache_dir / "5m" / "2026-10-05.json"
            with patch.object(Path, "replace", side_effect=OSError("temporary write failure")), \
                    patch("sys.stderr"):
                state._save_candles("2026-10-05", candles, 5)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".tmp").exists())
            state._save_candles("2026-10-05", candles, 5)
            self.assertEqual(json.loads(destination.read_text()), [candles[0].to_dict()])

    def test_revisited_source_rebuilds_partial_parent_without_old_live_tick(self):
        for count, tick_time in ((1, at(9, 22)), (10, at(10, 7))):
            with self.subTest(children=count):
                self.clock.value = tick_time
                hub = MonitorHub(now=self.clock)
                hub.seed_historical_candles([bank_bar(slot, price=500 + slot) for slot in range(count)],
                                            5, symbol=BANK)
                before = hub.snapshot()["instruments"][BANK]
                self.assertTrue(hub.process_tick(999, 50, symbol=BANK))
                hub.activate_source("shoonya")
                self.clock.value += timedelta(seconds=10)
                hub.activate_source("yahoo")
                state = hub.snapshot()["instruments"][BANK]
                self.assertEqual(state["active_candle"], before["active_candle"])
                self.assertEqual(state["candles_5m"], before["candles_5m"])
                self.assertEqual(state["current_price"], 500 + count)
                self.assertIsNone(state["active_candle_5m"])
                self.assertIsNone(state["last_tick_time"])
                self.assertEqual(state["comparison_status"], "stale")
                self.assertIsNone(state["change_pct"])

    def test_revisited_source_does_not_restore_unsaved_tick_only_bars(self):
        self.assertTrue(self.hub.process_tick(999, symbol=BANK))
        self.hub.activate_source("shoonya")
        self.hub.activate_source("yahoo")
        state = self.hub.snapshot()["instruments"][BANK]
        for key in ("current_price", "last_tick_time", "history_coverage_time", "active_candle", "active_candle_5m"):
            self.assertIsNone(state[key])
        self.assertEqual(state["comparison_status"], "unavailable")

    def test_inactive_source_revisited_on_new_date_keeps_history_and_clears_live(self):
        self.clock.value = at(10, 15)
        for source, offset in (("yahoo", 0), ("shoonya", 1000)):
            if source != "yahoo":
                self.hub.activate_source(source)
            for symbol in INSTRUMENTS:
                candles = [replace(bar(slot, 5, price=100 + offset), symbol=symbol) for slot in range(9)]
                self.hub.seed_historical_candles(candles, 5, symbol=symbol)
        self.clock.value = at(10, 0, day="2026-10-06")
        for source, opening in (("yahoo", 100), ("shoonya", 1100)):
            self.hub.activate_source(source)
            state = self.hub.snapshot()
            self.assertEqual(state["date"], "2026-10-06")
            self.assertFalse(state["range_locked"])
            self.assertEqual(state["signals"], [])
            for instrument in state["instruments"].values():
                self.assertEqual(instrument["candles"], [])
                self.assertEqual(instrument["candles_5m"], [])
                for key in ("current_price", "session_open", "last_tick_time", "history_coverage_time",
                            "active_candle", "active_candle_5m"):
                    self.assertIsNone(instrument[key])
            past = self.hub.snapshot("2026-10-05")
            self.assertEqual({item["session_open"] for item in past["instruments"].values()}, {opening})

    def test_first_source_visit_still_aggregates_native_five_minute_only_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in ("shoonya", "yahoo"):
                with self.subTest(source=source):
                    cache = Path(directory) / source / "NSEBANK" / "5m"
                    cache.mkdir(parents=True)
                    for day in ("2026-10-02", "2026-10-05"):
                        candles = [bank_bar(slot, day=day, price=500 + slot) for slot in range(9)]
                        (cache / f"{day}.json").write_text(json.dumps([c.to_dict() for c in candles]))
                    hub = MonitorHub(now=self.clock, cache_dir=directory)
                    hub.activate_source(source)
                    state = hub.snapshot()["instruments"][BANK]
                    self.assertEqual((len(state["candles"]), len(state["candles_5m"])), (3, 9))
                    self.assertEqual(state["session_open"], 500)
                    past = hub.snapshot("2026-10-02")["instruments"][BANK]
                    self.assertEqual((len(past["candles"]), len(past["candles_5m"])), (3, 9))
                    self.assertEqual(past["candles"][0]["close"], 503)

    def test_inactive_source_rollover_rebuilds_its_partial_historical_parent(self):
        self.clock.value = at(10, 7)
        history = [bank_bar(slot, price=500 + slot) for slot in range(10)]
        self.hub.seed_historical_candles(history, 5, symbol=BANK)
        self.assertEqual(self.hub.snapshot()["instruments"][BANK]["active_candle"]["start_time"],
                         at(10, 0).isoformat())
        self.hub.activate_source("shoonya")
        self.clock.value = at(10, 0, day="2026-10-06")
        self.hub.activate_source("yahoo")
        state = self.hub.snapshot("2026-10-05")["instruments"][BANK]
        self.assertEqual(len(state["candles_5m"]), 10)
        self.assertEqual(len(state["candles"]), 4)
        parent = state["candles"][-1]
        self.assertEqual((parent["start_time"], parent["end_time"]),
                         (at(10, 0).isoformat(), at(10, 15).isoformat()))
        self.assertEqual((parent["open"], parent["high"], parent["low"], parent["close"], parent["volume"]),
                         (history[-1].open, history[-1].high, history[-1].low,
                          history[-1].close, history[-1].volume))
        self.assertTrue(parent["is_closed"])
        self.assertFalse(parent["is_complete"])
        self.assertFalse(parent["close_is_valid"])
        self.assertIsNone(self.hub.snapshot()["instruments"][BANK]["current_price"])
        self.hub.activate_source("shoonya")
        self.hub.activate_source("yahoo")
        self.assertEqual(self.hub.snapshot("2026-10-05")["instruments"][BANK], state)

    def test_history_and_rollover_preserve_comparator_date_without_changing_live(self):
        self.hub.seed_historical_candles([bank_bar(day="2026-10-02", price=500)], 5, symbol=BANK)
        before = self.hub.snapshot()
        past = self.hub.snapshot("2026-10-02")["instruments"][BANK]
        self.assertEqual(past["session_open"], 500)
        self.assertEqual(past["comparison_status"], "closed")
        self.assertIsNone(past["history_coverage_time"])
        self.assertEqual(before, self.hub.snapshot())
        self.hub.process_tick(600, symbol=BANK)
        self.clock.value = at(day="2026-10-06")
        self.hub.advance_time()
        current = self.hub.snapshot()["instruments"][BANK]
        self.assertIsNone(current["current_price"])
        self.assertIsNone(current["session_open"])


class ProviderIsolationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(11, 0))

    def test_source_switch_isolates_prices_history_and_range_state(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            hub.seed_historical_candles([bar(slot, price=100) for slot in range(3)])
            yahoo = hub.snapshot()
            hub.activate_source("shoonya")
            switched = hub.snapshot()
            self.assertEqual(switched["candles"], [])
            self.assertIsNone(switched["current_price"])
            self.assertFalse(switched["range_locked"])
            self.assertEqual(switched["signals"], [])
            hub.seed_historical_candles([bar(slot, 5, price=200) for slot in range(9)], interval_minutes=5)
            shoonya = hub.snapshot()
            hub.activate_source("yahoo")
            self.assertEqual(hub.snapshot()["candles"], yahoo["candles"])
            self.assertEqual(hub.snapshot()["current_price"], yahoo["current_price"])
            self.assertEqual(hub.snapshot()["candles_5m"], [])
            hub.activate_source("shoonya")
            self.assertEqual(hub.snapshot()["candles"], shoonya["candles"])
            self.assertEqual(hub.snapshot()["candles_5m"], shoonya["candles_5m"])

    def test_restart_keeps_both_provider_histories_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            hub.seed_historical_candles([bar(slot, price=100) for slot in range(3)])
            hub.activate_source("shoonya")
            five = [bar(slot, 5, price=200) for slot in range(9)]
            hub.seed_historical_candles(five, interval_minutes=5)
            restored = MonitorHub(now=self.clock, cache_dir=directory)
            restored.seed_historical_candles([])
            self.assertEqual(restored.snapshot()["candles"][0]["open"], 100)
            self.assertEqual(restored.snapshot()["candles_5m"], [])
            restored.activate_source("shoonya")
            self.assertEqual(restored.snapshot()["candles"][0]["open"], 200)
            self.assertEqual(restored.snapshot()["candles_5m"], [c.to_dict() for c in five])

    def test_legacy_date_cache_is_readable_only_as_yahoo_history(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = [bar(slot, day="2026-10-02") for slot in range(3)]
            path = Path(directory) / "2026-10-02.json"
            path.write_text(json.dumps([c.to_dict() for c in legacy]))
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            self.assertEqual(hub.snapshot("2026-10-02")["candles"], [c.to_dict() for c in legacy])
            hub.activate_source("shoonya")
            self.assertEqual(hub.snapshot("2026-10-02")["candles"], [])
            self.assertEqual(json.loads(path.read_text()), [c.to_dict() for c in legacy])

    def test_invalid_provider_does_not_reset_existing_session(self):
        hub = MonitorHub(now=self.clock)
        hub.seed_historical_candles([bar(slot) for slot in range(3)])
        before = hub.snapshot()
        with self.assertRaises(ValueError):
            hub.activate_source("unknown")
        self.assertEqual(hub.snapshot(), before)
