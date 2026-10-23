"""Expired session files are pruned by session date without harming live state."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orb import market
from orb.market import INSTRUMENTS, InstrumentState, PRIMARY_SYMBOL
from orb.orb_monitor import MonitorHub
from orb.signals import Candle, IST

from orb.tests.support.clock import Clock


def candle(day, interval=15, symbol=PRIMARY_SYMBOL):
    start = datetime.fromisoformat(day).replace(hour=9, minute=15, tzinfo=IST)
    return Candle(symbol, 100, 110, 90, 105, 12, start,
                  start + timedelta(minutes=interval), is_closed=True, slot=0)


def save(path, day=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([candle(day or path.stem).to_dict()]))
    return path


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.clock = Clock(datetime(2026, 10, 12, 10, tzinfo=IST))

    def test_startup_prunes_all_session_layouts_by_date_not_file_age(self):
        locations = ("", "yahoo", "yahoo/5m", "yahoo/NSEBANK", "yahoo/NSEBANK/5m",
                     "shoonya", "shoonya/5m", "shoonya/NSEBANK", "shoonya/NSEBANK/5m")
        expired, retained = [], []
        for location in locations:
            directory = self.root / location
            expired.append(save(directory / "2026-08-12.json"))
            retained.append(save(directory / "2026-08-13.json"))
            # A newly rewritten old session still expires; an old file at the boundary stays.
            os.utime(expired[-1], (2_000_000_000, 2_000_000_000))
            os.utime(retained[-1], (1, 1))
        hub = MonitorHub(now=self.clock, cache_dir=self.root)
        self.assertEqual(market.history_cutoff(self.clock()), "2026-08-13")
        self.assertTrue(all(not path.exists() for path in expired))
        self.assertTrue(all(path.exists() for path in retained))
        self.assertEqual(hub.history()["dates"], ["2026-08-13"])

    def test_unrelated_invalid_future_and_symlink_files_are_untouched(self):
        untouched = [self.root / name for name in (
            "notes.json", "2026-02-30.json", "2026-8-12.json", "2026-08-12.tmp",
            "2026-10-13.json", "2026-08-13.json")]
        self.root.mkdir(parents=True)
        for path in untouched:
            path.write_text("keep me")
        external = Path(self.temp.name) / "external"
        outside_session = save(external / "2026-08-12.json")
        directory_link = self.root / "linked-provider"
        directory_link.symlink_to(external, target_is_directory=True)
        file_link = self.root / "2026-08-12.json"
        file_link.symlink_to(outside_session)
        market.prune_session_files(self.root, "2026-08-13")
        self.assertTrue(all(path.read_text() == "keep me" for path in untouched))
        self.assertTrue(directory_link.is_symlink())
        self.assertTrue(file_link.is_symlink())
        self.assertTrue(outside_session.exists())

    def test_scan_runs_at_startup_and_next_ist_day_only(self):
        self.clock.value = datetime(2026, 10, 12, 18, 29, tzinfo=timezone.utc)
        with patch("orb.orb_monitor.prune_session_files", wraps=market.prune_session_files) as prune:
            hub = MonitorHub(now=self.clock, cache_dir=self.root)
            self.assertEqual(prune.call_count, 1)
            for _ in range(3):
                hub.advance_time()
                hub.snapshot()
                hub.history()
            self.assertEqual(prune.call_count, 1)
            self.clock.value += timedelta(minutes=1)
            hub.advance_time()
            self.assertEqual(prune.call_count, 2)
            self.assertEqual(market.history_cutoff(self.clock()), "2026-08-14")
            hub.snapshot()
            self.assertEqual(prune.call_count, 2)

    def test_rollover_prunes_active_and_inactive_provider_history_and_hashes(self):
        hub = MonitorHub(now=self.clock, cache_dir=self.root)
        for source in ("yahoo", "shoonya"):
            if source != hub.source_id:
                hub.activate_source(source)
            for symbol in (PRIMARY_SYMBOL, "^NSEBANK"):
                for day in ("2026-08-13", "2026-08-14"):
                    hub.seed_historical_candles([candle(day, 5, symbol)], 5, symbol=symbol)
        self.assertTrue(list(self.root.rglob("2026-08-13.json")))
        self.clock.value += timedelta(days=1)
        hub.advance_time()
        self.assertFalse(list(self.root.rglob("2026-08-13.json")))
        for source in ("yahoo", "shoonya"):
            hub.activate_source(source)
            self.assertEqual(hub.history()["dates"], ["2026-08-14"])
            for symbol in (PRIMARY_SYMBOL, "^NSEBANK"):
                state = hub.instruments[symbol]
                self.assertNotIn("2026-08-13", state.sessions)
                self.assertNotIn("2026-08-13", state.sessions_5m)
                self.assertTrue(state.sessions["2026-08-14"])
                self.assertTrue(state.sessions_5m["2026-08-14"])
                self.assertFalse(any(path.stem == "2026-08-13" for path in state._saved_content))

    def test_source_switch_at_midnight_cannot_skip_daily_retention(self):
        hub = MonitorHub(now=self.clock, cache_dir=self.root)
        hub.seed_historical_candles([candle("2026-08-13")])
        self.clock.value += timedelta(days=1)
        hub.activate_source("shoonya")
        hub.advance_time()
        self.assertFalse(list(self.root.rglob("2026-08-13.json")))
        hub.activate_source("yahoo")
        self.assertEqual(hub.history()["dates"], [])

    def test_expired_backfill_and_save_cannot_recreate_pruned_sessions(self):
        state = InstrumentState(INSTRUMENTS[PRIMARY_SYMBOL], now=self.clock, cache_root=self.root)
        for interval in (5, 15):
            with self.subTest(interval=interval):
                state.seed_historical_candles([candle("2026-08-12", interval),
                                               candle("2026-08-13", interval)], interval)
                state._save_candles("2026-08-12", [candle("2026-08-12", interval)], interval)
                self.assertNotIn("2026-08-12", state.sessions)
                self.assertNotIn("2026-08-12", state.sessions_5m)
                self.assertFalse(list(self.root.rglob("2026-08-12.json")))
                self.assertTrue(state.sessions["2026-08-13"])
        self.assertTrue(state.sessions_5m["2026-08-13"])

    def test_failed_deletion_is_reported_and_expired_file_is_not_loaded(self):
        expired = save(self.root / "yahoo" / "2026-08-12.json")
        save(self.root / "yahoo" / "2026-08-13.json")
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == expired:
                raise PermissionError("retention test denied")
            return original_unlink(path, *args, **kwargs)

        errors = io.StringIO()
        with patch.object(Path, "unlink", unlink), patch("sys.stderr", errors):
            hub = MonitorHub(now=self.clock, cache_dir=self.root)
        self.assertTrue(expired.exists())
        self.assertIn(expired.name, errors.getvalue())
        self.assertIn("retention test denied", errors.getvalue())
        self.assertEqual(hub.history()["dates"], ["2026-08-13"])
        self.assertNotIn(expired, hub.instruments[PRIMARY_SYMBOL]._saved_content)
