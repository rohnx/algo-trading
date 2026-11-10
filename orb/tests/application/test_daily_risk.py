"""Live ORB risk is independent of Telegram and never written by browsing."""

from datetime import timedelta
from decimal import Decimal
import io
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orb.notifications import format_signal
from orb.orb_monitor import ConfluenceSignal, MonitorHub, SYMBOL
from orb.signals import Candle, SignalDirection
from orb.tests.support.clock import Clock
from orb.tests.support.market import five_bars, timestamp
from orb.tests.support.notifications import signal


class RecordingPolicy:
    def __init__(self):
        self.times = []
        self.first = None
        self.override = None

    def record_first_orb(self, value):
        self.times.append(value)
        self.first = min(self.first or value, value)
        selected = Decimal("0.6") if self.first.hour == 10 and self.first.minute < 15 else Decimal("0.3")
        return SimpleNamespace(r=self.override if self.override is not None else selected,
                               source="manual" if self.override is not None else "orb")


def main_signal(hour, minute, direction="BULLISH", day="2026-10-05"):
    data = signal(direction, timestamp=timestamp(day, hour, minute))
    return ConfluenceSignal(strategy="dual_ORB", symbol=SYMBOL,
                            direction=SignalDirection(direction), signal_type=data["signal_type"],
                            price=data["price"], level=100, diff=1,
                            timestamp=timestamp(day, hour, minute), message="Both ORBs",
                            setup_id="dual_orb", confirmations=data["confirmations"])


class DailyRiskIntegrationTests(unittest.TestCase):
    def hub(self, now, policy, notifier=None):
        directory = self.enterContext(TemporaryDirectory())
        return MonitorHub(now=now if callable(now) else lambda: now, cache_dir=Path(directory),
                          notifier=notifier, daily_risk=policy)

    def test_accepted_future_boundary_records_risk_when_wall_clock_catches_up(self):
        for switch_source in (False, True):
            with self.subTest(switch_source=switch_source):
                clock, policy = Clock(timestamp("2026-10-05", 10)), RecordingPolicy()
                notifier = Mock(spec=["enqueue"])
                hub = self.hub(clock, policy, notifier)
                hub.seed_historical_candles(five_bars("2026-10-05"), 5)
                for minute in range(15):
                    clock.value = timestamp("2026-10-05", 10, minute)
                    self.assertTrue(hub.process_tick(100 if minute < 10 else 125, tick_time=clock()))
                clock.value = timestamp("2026-10-05", 10, 14, 30)
                hub.process_tick(125, tick_time=clock())
                with patch("sys.stdout", io.StringIO()):
                    hub.process_tick(999, tick_time=timestamp("2026-10-05", 10, 15))
                self.assertEqual(len(hub.signals), 1)
                self.assertEqual(policy.times, [], "future confirmations cannot write R yet")
                if switch_source:
                    hub.activate_source("shoonya")
                clock.value = timestamp("2026-10-05", 10, 15)
                hub.advance_time()
                self.assertEqual(policy.times, [clock()])
                clock.value = timestamp("2026-10-05", 10, 20)
                hub.advance_time()
                self.assertEqual(len(policy.times), 1, "a recorded pending hit is removed")
                notifier.enqueue.assert_not_called()

    def test_deferred_live_risk_does_not_admit_offline_or_previous_day_signals(self):
        clock, policy = Clock(timestamp("2026-10-05", 10, 14, 30)), RecordingPolicy()
        hub = self.hub(clock, policy)
        hub.breakouts.signals = [main_signal(10, 15)]
        clock.value = timestamp("2026-10-05", 10, 15)
        hub.snapshot()
        self.assertEqual(policy.times, [], "read-only history did not admit the signal")
        clock.value = timestamp("2026-10-05", 15, 29, 30)
        future = main_signal(15, 30)
        hub.breakouts.signals = [future]
        hub._notify_signals([future])
        clock.value = timestamp("2026-10-06", 9, 15)
        hub.advance_time()
        self.assertEqual(policy.times, [], "expired pending risk cannot carry into another date")

    def test_live_history_records_old_first_signal_when_telegram_is_disabled(self):
        now = timestamp("2026-10-05", 12)
        policy = RecordingPolicy()
        hub = self.hub(now, policy)
        candles = five_bars("2026-10-05")
        for candle in candles[6:]:
            candle.open = candle.close = 115
            candle.high, candle.low = 120, 110
        start = timestamp("2026-10-05", 10, 0)
        candles.append(Candle(SYMBOL, 125, 125, 125, 125, 0, start,
                              start + timedelta(minutes=5), is_closed=True, slot=9))
        hub.seed_historical_candles(candles, 5)
        self.assertEqual(policy.times, [], "offline history is read-only")
        hub.snapshot()
        hub.history()
        hub.snapshot("2026-10-02")
        self.assertEqual(policy.times, [])
        hub.seed_historical_candles(candles, 5, live=True)
        self.assertEqual(policy.first, timestamp("2026-10-05", 10, 5))
        self.assertEqual(hub.signals[0].daily_r, "0.6")

    def test_opposite_signal_uses_first_time_and_current_manual_override(self):
        now = timestamp("2026-10-05", 10, 30, 30)
        policy, notifier = RecordingPolicy(), Mock(spec=["enqueue"])
        hub = self.hub(now, policy, notifier)
        first, second = main_signal(10, 5), main_signal(10, 30, "BEARISH")
        hub.breakouts.signals = [second, first]
        hub._notify_signals(hub.signals)
        self.assertEqual(policy.times, [first.timestamp, second.timestamp])
        self.assertEqual(second.daily_r, "0.6")
        notifier.enqueue.assert_called_once_with(second, "yahoo", live=True)
        self.assertIn("R Multiple: 0.6", format_signal(second, "yahoo")[2])
        policy.override = Decimal("0.7")
        notifier.enqueue.return_value = False
        hub._notification_keys.clear()
        hub._notify_signals([second])
        self.assertEqual(second.daily_r, "0.7")
        self.assertIn("R Multiple: 0.7", format_signal(second, "yahoo")[2])

    def test_previous_day_and_future_signals_do_not_write_risk(self):
        now = timestamp("2026-10-05", 10, 5)
        policy = RecordingPolicy()
        hub = self.hub(now, policy)
        hub.breakouts.signals = [main_signal(10, 10), main_signal(10, 5, day="2026-10-02")]
        hub._notify_signals(hub.signals)
        self.assertEqual(policy.times, [])

    def test_risk_failure_preserves_the_original_signal_and_alert(self):
        policy, notifier = Mock(), Mock(spec=["enqueue"])
        policy.record_first_orb.side_effect = RuntimeError("unavailable calendar")
        hub = self.hub(timestamp("2026-10-05", 10, 5), policy, notifier)
        item = main_signal(10, 5)
        hub.breakouts.signals = [item]
        with patch("sys.stdout", io.StringIO()):
            hub._notify_signals([item])
        notifier.enqueue.assert_called_once_with(item, "yahoo", live=True)
        message = format_signal(item, "yahoo")[2]
        self.assertIn("BULLISH | NIFTY 50", message)
        self.assertIn("R Multiple: unavailable", message)

    def test_malformed_optional_risk_metadata_does_not_reject_a_valid_alert(self):
        item = signal()
        item.update(daily_r="NaN", daily_r_source="orb")
        self.assertIn("R Multiple: unavailable", format_signal(item, "yahoo")[2])


class RiskAdapterTests(unittest.TestCase):
    def test_real_shared_policy_preserves_first_signal_across_adapter_restarts(self):
        from orb.risk import DailyRisk
        source = Path(__file__).resolve().parents[3] / "journal"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            shutil.copyfile(source / "daily_risk.py", root / "daily_risk.py")
            shutil.copyfile(source / "config/risk-rules.csv", root / "config/risk-rules.csv")
            (root / "config/trading-calendar.csv").write_text(
                "date,trading_day,expiry_state,source\n2026-10-05,yes,sensex_weekly,test fixture\n")
            state = root / "state.sqlite3"
            first = DailyRisk(root=root, state_path=state).record_first_orb(timestamp("2026-10-05", 10, 5))
            self.assertEqual(first.r, Decimal("0.6"))
            restarted = DailyRisk(root=root, state_path=state)
            later = restarted.record_first_orb(timestamp("2026-10-05", 10, 30))
            self.assertEqual(later.r, Decimal("0.6"))
            self.assertEqual(later.first_orb_at, timestamp("2026-10-05", 10, 5).isoformat())

    def test_missing_or_broken_installation_is_optional(self):
        from orb.risk import DailyRisk
        with TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = DailyRisk(root=root)
            self.assertIsNone(adapter.record_first_orb(timestamp("2026-10-05", 10, 5)))
            (root / "daily_risk.py").write_text("this is not valid python!")
            self.assertIsNone(adapter.record_first_orb(timestamp("2026-10-05", 10, 5)))

    def test_explicit_config_root_loads_standalone_module_without_changing_import_path(self):
        from orb.risk import DailyRisk
        with TemporaryDirectory(prefix="daily risk ") as directory:
            root = Path(directory)
            state_path = root / "separate-state.sqlite3"
            (root / "daily_risk.py").write_text(
                "from dataclasses import dataclass\n"
                "@dataclass\n"
                "class Decision:\n    r: str\n    source: str\n"
                "class RiskPolicy:\n"
                "    def __init__(self, root=None, state_path=None):\n"
                "        self.root, self.state_path = root, state_path\n"
                "    def record_first_orb(self, timestamp):\n"
                "        assert self.root.name.startswith('daily risk ')\n"
                "        assert self.state_path.name == 'separate-state.sqlite3'\n"
                "        return Decision('0.4', 'orb')\n")
            before = sys.path[:]
            with patch.dict(os.environ, {"TRADING_RISK_HOME": str(root)}, clear=True):
                adapter = DailyRisk(state_path=state_path)
                value = adapter.record_first_orb(timestamp("2026-10-05", 10, 5))
            self.assertEqual(value.r, "0.4")
            self.assertEqual(sys.path, before)


if __name__ == "__main__":
    unittest.main()
