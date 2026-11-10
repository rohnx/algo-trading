"""Daily policy through the real terminal command and persisted trades."""

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from daily_risk import RiskPolicy
from risk_tool.journal import Journal


ROOT = Path(__file__).resolve().parents[1]


class DailyTerminalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        (self.home / "config").mkdir()
        shutil.copyfile(ROOT / "config/risk-rules.csv", self.home / "config/risk-rules.csv")
        (self.home / "config/trading-calendar.csv").write_text(
            "date,trading_day,expiry_state,source\n"
            "2026-11-03,yes,nifty_weekly,test fixture\n"
            "2026-11-04,yes,non_expiry,test fixture\n"
            "2026-11-07,no,,test fixture\n")
        self.data = self.home / "data"
        self.policy = RiskPolicy(self.home)

    def launch(self, *arguments, answers=""):
        process = subprocess.run(
            [sys.executable, str(ROOT / "trade.py"), "--risk-home", str(self.home),
             "--data-dir", str(self.data), *arguments], input=answers,
            text=True, capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return process.stdout

    def test_planning_works_before_journal_setup_and_reports_missing_calendar_dates(self):
        output = self.launch("--month", "2026-11", "--plan")
        self.assertIn("NIFTY weekly", output)
        self.assertIn("0.5", output)
        self.assertIn("27 missing", output)
        self.assertNotIn("2026-11-07", output)
        self.assertFalse((self.data / "journal.sqlite3").exists())

    def test_calendar_template_is_editable_csv_without_guessed_exchange_sessions(self):
        output = self.launch("--calendar-template", "2026-11")
        rows = list(csv.DictReader(output.splitlines()))
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[0]["date"], "2026-11-01")
        self.assertTrue(all(row["trading_day"] == row["expiry_state"] == "" for row in rows))
        self.assertFalse(self.data.exists())

    def test_entry_date_controls_r_before_quantity_is_calculated(self):
        with Journal(self.data / "journal.sqlite3") as journal:
            journal.save_month("2026-11", "222", "450", 4)
        self.policy.record_first_orb(datetime.fromisoformat("2026-11-03T10:15:00+05:30"))
        output = self.launch("--month", "2026-11", answers="\n".join([
            "a", "TEST", "i", "l", "100", "1", "2026-11-03", "", "", "97", "", "q", ""]))
        self.assertIn("Saved trade #1", output)
        with Journal(self.data / "journal.sqlite3") as journal:
            saved = journal.get_trade(1)
        self.assertEqual(Decimal(saved["r_multiplier"]), Decimal("0.3"))
        self.assertEqual(Decimal(saved["risk"]), Decimal("22.2"))
        quantity = saved["quantity"]
        self.policy.set_override("2026-11-03", "0.7")
        with Journal(self.data / "journal.sqlite3") as journal:
            self.assertEqual(journal.get_trade(1)["quantity"], quantity)
            self.assertEqual(Decimal(journal.get_trade(1)["risk"]), Decimal("22.2"))

    def test_r_command_is_daily_and_warns_without_rejecting_override(self):
        with Journal(self.data / "journal.sqlite3") as journal:
            journal.save_month("2026-11", "222", "450", 4)
        output = self.launch("--month", "2026-11", answers="r\n2026-11-03\n0.7\nq\n")
        self.assertIn("outside", output)
        self.assertEqual(self.policy.resolve("2026-11-03").r, Decimal("0.7"))
        self.assertEqual(self.policy.resolve("2026-11-04").r, Decimal("1.0"))
        self.launch("--month", "2026-11", answers="r\n2026-11-03\nauto\nq\n")
        self.assertEqual(self.policy.resolve("2026-11-03").source, "expiry")

    def test_replacing_out_of_range_r_with_valid_r_does_not_warn_about_old_value(self):
        with Journal(self.data / "journal.sqlite3") as journal:
            journal.save_month("2026-11", "222", "450", 4)
        self.policy.set_override("2026-11-03", "0.7")
        output = self.launch("--month", "2026-11", answers="\n".join([
            "a", "TEST", "i", "l", "100", "1", "2026-11-03", "0.5", "", "97", "", "q", ""]))
        self.assertNotIn("outside", output)
        self.assertEqual(self.policy.resolve("2026-11-03").r, Decimal("0.5"))

    def test_risk_home_uses_its_shared_state_without_a_custom_journal_directory(self):
        self.policy.set_override("2026-11-03", "0.4")
        process = subprocess.run(
            [sys.executable, str(ROOT / "trade.py"), "--risk-home", str(self.home),
             "--month", "2026-11", "--plan"], text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("manual", process.stdout)
        self.assertIn("0.4", process.stdout)


if __name__ == "__main__":
    unittest.main()
