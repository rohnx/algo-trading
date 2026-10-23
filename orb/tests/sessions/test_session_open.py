"""Reported session openings persist by provider and symbol."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orb.market import history_cutoff, INSTRUMENTS, InstrumentState, prune_session_files

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bar


BANK = "^NSEBANK"


FOCIT = "FOCIT.BO"


class SessionOpenPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(13, 40))

    def state(self, cache_root=None, source="shoonya", symbol=FOCIT):
        state = InstrumentState(INSTRUMENTS[symbol], now=self.clock, cache_root=cache_root)
        state.activate_source(source)
        return state

    def capture_midday_bar(self, state, opening=500):
        self.assertTrue(state.process_tick(525, tick_time=state.market_time()))
        self.assertTrue(state.set_session_open(opening, state.market_time()))
        for minute, second in ((41, 0), (42, 0), (43, 0), (44, 0), (44, 59)):
            self.clock.value = at(13, minute, second)
            self.assertTrue(state.process_tick(526, tick_time=state.market_time()))
        self.clock.value = at(13, 45)
        state._close_elapsed(state.market_time())

    def test_midday_open_survives_restart_and_historical_view_without_fake_opening_candle(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            self.capture_midday_bar(state)
            path = Path(directory) / "shoonya" / "FOCIT.BO" / "opens" / "2026-10-05.json"
            metadata = json.loads(path.read_text())
            self.assertEqual(metadata["session_open"], 500)
            self.assertEqual(metadata["symbol"], FOCIT)
            self.assertEqual(metadata["source_id"], "shoonya")
            restored = self.state(directory)
            self.assertEqual(restored.session_open, 500)
            self.assertTrue(all(c.start_time >= at(13, 40) for c in restored.candles_5m))
            self.assertEqual(len(restored.candles_5m), 1)
            self.clock.value = at(10, 0, day="2026-10-06")
            restored = self.state(directory)
            past = restored.snapshot(self.clock(), "live", "2026-10-05")
            self.assertEqual(past["session_open"], 500)
            self.assertEqual(past["current_price"], 526)
            self.assertAlmostEqual(past["change_pct"], 5.2)
            self.assertEqual(past["comparison_status"], "closed")
            self.assertIsNone(restored.session_open)

    def test_baselines_follow_provider_and_symbol_in_memory_and_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory, source="yahoo")
            self.assertTrue(state.set_session_open(500, state.market_time()))
            state.activate_source("shoonya")
            self.assertIsNone(state.session_open)
            self.assertTrue(state.set_session_open(600, state.market_time()))
            state.activate_source("yahoo")
            self.assertEqual(state.session_open, 500)
            state.activate_source("shoonya")
            self.assertEqual(state.session_open, 600)
            bank = self.state(directory, symbol=BANK)
            self.assertIsNone(bank.session_open)
            self.assertTrue(bank.set_session_open(700, bank.market_time()))
            for source, opening in (("yahoo", 500), ("shoonya", 600)):
                self.assertEqual(self.state(directory, source=source).session_open, opening)
            self.assertEqual(self.state(directory, symbol=BANK).session_open, 700)
        state = self.state()
        self.assertTrue(state.set_session_open(800, state.market_time()))
        state.reset("2026-10-06")
        self.assertIsNone(state.session_open)
        self.assertEqual(state.snapshot(self.clock(), "live", "2026-10-05")["session_open"], 800)

    def test_native_opening_candle_overrides_reported_baseline_after_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            self.assertTrue(state.set_session_open(500, state.market_time()))
            state.seed_historical_candles([replace(bar(0, 5, price=600), symbol=FOCIT)], 5)
            self.assertEqual(state.session_open, 600)
            self.assertEqual(self.state(directory).session_open, 600)
            self.clock.value = at(10, 0, day="2026-10-06")
            restored = self.state(directory)
            self.assertEqual(restored.snapshot(self.clock(), "live", "2026-10-05")["session_open"], 600)

    def test_opening_is_saved_only_once_after_validated_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            with patch.object(Path, "write_text", autospec=True, wraps=Path.write_text) as write:
                self.assertFalse(state.set_session_open(-1, state.market_time()))
                self.assertTrue(state.set_session_open(500, state.market_time()))
                self.assertFalse(state.set_session_open(501, state.market_time()))
                self.assertTrue(state.process_tick(525, tick_time=state.market_time()))
                self.assertEqual(write.call_count, 1)

    def test_native_fifteen_minute_open_wins_when_five_minute_open_is_missing(self):
        state = self.state()
        self.assertTrue(state.set_session_open(500, state.market_time()))
        state.seed_historical_candles([replace(bar(0, 15, price=600), symbol=FOCIT)], 15)
        late = replace(bar(53, 5, price=700), symbol=FOCIT)
        state.seed_historical_candles([late], 5)
        self.assertEqual(state.session_open, 600)

    def test_reported_openings_share_actual_day_retention_across_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in ("yahoo", "shoonya"):
                for day in ("2026-08-13", "2026-08-14"):
                    self.clock.value = at(13, 40, day=day)
                    state = self.state(directory, source=source)
                    self.assertTrue(state.set_session_open(500, state.market_time()))
            self.clock.value = at(0, 5, day="2026-10-13")
            restored = self.state(directory, source="yahoo")
            restored.activate_source("shoonya")
            self.assertEqual(set(restored.session_opens), {"2026-08-14"})
            state.prune_history(history_cutoff(self.clock()))
            self.assertTrue(all("2026-08-13" not in values for values in state._source_opens.values()))
            prune_session_files(directory, history_cutoff(self.clock()))
            self.assertEqual({p.stem for p in Path(directory).rglob("opens/*.json")}, {"2026-08-14"})
