"""Exercise the real calculator command against an isolated journal checkout."""

from datetime import datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "journal"))

from daily_risk import IST, RiskPolicy
from risk_tool.journal import Journal


class ErvCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.erv = self.root / "erv"
        self.erv.mkdir()
        source = ROOT / "erv"
        for path in source.glob("*.py"):
            shutil.copyfile(path, self.erv / path.name)
        for path in source.glob("*.csv"):
            shutil.copyfile(path, self.erv / path.name)
        self.home = self.root / "journal"
        shutil.copytree(ROOT / "journal/risk_tool", self.home / "risk_tool",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "journal/daily_risk.py", self.home / "daily_risk.py")
        self.path = self.home / "data/journal.sqlite3"
        self.day = datetime.now(IST).date().isoformat()
        self.month = self.day[:7]

    def setup_journal(self, *, month=None, multiplier="0.4", outcome=None):
        month = month or self.month
        with Journal(self.path) as journal:
            journal.save_month(month, "222", "450", 4, r_multiplier="0.7")
            if outcome is not None:
                journal.add_trade(month, {
                    "date": self.day, "ticker": "PREVIOUS", "style": "intraday",
                    "entry": "100", "atr": "1", "direction": "long",
                    "risk": "74", "r_multiplier": "1", "quantity": 3,
                    "status": "open", "exit_price": "97", "charges": "1",
                    "pnl": outcome, "exchange": "nse",
                })
        config = self.home / "config"
        config.mkdir()
        shutil.copyfile(ROOT / "journal/config/risk-rules.csv", config / "risk-rules.csv")
        (config / "trading-calendar.csv").write_text(
            "date,trading_day,expiry_state,source\n"
            f"{self.day},yes,non_expiry,test fixture\n", encoding="utf-8")
        if multiplier is not None:
            RiskPolicy(self.home, self.home / "data/daily-risk.sqlite3").set_override(
                self.day, multiplier)

    def launch(self, *arguments, answers="", success=True, inside_erv=False):
        environment = dict(os.environ)
        environment.pop("TRADING_RISK_HOME", None)
        environment.pop("TRADING_RISK_STATE_PATH", None)
        script = "erv.py" if inside_erv else "erv/erv.py"
        process = subprocess.run(
            [sys.executable, script, *arguments], input=answers, text=True,
            capture_output=True, timeout=15, env=environment,
            cwd=self.erv if inside_erv else self.root)
        output = process.stdout + process.stderr
        if success:
            self.assertEqual(process.returncode, 0, output)
        else:
            self.assertNotEqual(process.returncode, 0, output)
            self.assertNotIn("Traceback", output)
        return process, output

    def printed_number(self, output, label):
        match = re.search(rf"^{re.escape(label)}:\s*([^\n]+)", output, re.MULTILINE)
        self.assertIsNotNone(match, output)
        return Decimal(match.group(1).strip())

    def test_r_uses_current_cash_risk_for_each_style_and_latest_outcomes(self):
        self.setup_journal(outcome="-10")
        for style, direction, token, expected in (
                ("d", "l", "r", "112.5"),
                ("i", "l", "R", "29.52"),
                ("i", "s", "r", "29.52")):
            with self.subTest(style=style, direction=direction):
                _, output = self.launch("57.7", token, "2.1", style, direction,
                                        answers="y\n60\nn\n")
                self.assertEqual(self.printed_number(output, "Risk"), Decimal(expected))
        with Journal(self.path) as journal:
            self.assertEqual(len(journal.trades(self.month)), 1)

    def test_numeric_risk_works_without_journal_and_eof_declines_saving(self):
        for answers in ("y\n60\nn\n", "y\n60\n"):
            with self.subTest(answers=answers):
                _, output = self.launch("57.7", "112.50", "2.1", "d", answers=answers)
                self.assertEqual(self.printed_number(output, "Risk"), Decimal("112.50"))
                self.assertIn("Add to journal?", output)
                self.assertFalse(self.path.exists())

    def test_all_exit_branches_save_open_positions_with_calculated_snapshots(self):
        self.setup_journal()
        for style, direction, quantity in (("d", "l", 18), ("i", "l", 32), ("i", "s", 32)):
            with self.subTest(style=style, direction=direction):
                answers = "y\n60\ny\nENTERED\n\n"
                if style == "i":
                    answers += "\n"  # Keep the resolved daily R for numeric-risk metadata.
                _, output = self.launch("57.7", "112.50", "2.1", style, direction, "bse",
                                        answers=answers)
                self.assertIn("Saved trade #", output)
                with Journal(self.path) as journal:
                    saved = journal.trades(self.month)[-1]
                self.assertEqual(saved["status"], "open")
                self.assertEqual(saved["date"], self.day)
                self.assertEqual(saved["ticker"], "ENTERED")
                self.assertEqual(saved["style"], "delivery" if style == "d" else "intraday")
                self.assertEqual(saved["direction"], "short" if direction == "s" else "long")
                self.assertEqual(saved["quantity"], quantity)
                self.assertEqual(saved["exchange"], "bse")
                for key, expected in (("entry", "57.7"), ("atr", "2.1"),
                                      ("risk", "112.50"), ("exit_price", "60")):
                    self.assertEqual(Decimal(saved[key]), Decimal(expected))
                self.assertEqual(Decimal(saved["pnl"]), self.printed_number(output, "PNL"))
                self.assertEqual(Decimal(saved["charges"]),
                                 self.printed_number(output, "Total charges"))
                self.assertEqual(Decimal(saved["r_multiplier"]),
                                 Decimal("1" if style == "d" else "0.4"))

    def test_r_entry_keeps_quoted_r_without_another_r_prompt(self):
        self.setup_journal(outcome="-10")
        _, output = self.launch("57.7", "r", "2.1", "i", answers="y\n60\ny\nENTERED\n\n")
        self.assertIn("Saved trade #", output)
        self.assertNotIn("R at entry", output)
        with Journal(self.path) as journal:
            saved = journal.trades(self.month)[-1]
        self.assertEqual(Decimal(saved["risk"]), Decimal("29.52"))
        self.assertEqual(Decimal(saved["r_multiplier"]), Decimal("0.4"))
        self.assertEqual(saved["quantity"], 8)

    def test_blank_interactive_risk_uses_style_selected_afterward(self):
        self.setup_journal()
        _, output = self.launch(answers="57.7\n\n2.1\nn\nd\nbse\nl\ny\n60\nn\n")
        self.assertIn("Delivery Long", output)
        self.assertEqual(self.printed_number(output, "Risk"), Decimal("112.5"))

    def test_no_exit_and_no_reward_finishes_without_a_save_prompt(self):
        for style, direction in (("d", "l"), ("i", "l"), ("i", "s")):
            with self.subTest(style=style, direction=direction):
                _, output = self.launch("57.7", "112.50", "2.1", style, direction,
                                        answers="n\nn\n")
                self.assertNotIn("Add to journal?", output)
                self.assertFalse(self.path.exists())

    def test_missing_current_month_does_not_use_previous_month(self):
        previous = (datetime.now(IST).date().replace(day=1) - timedelta(days=1)).isoformat()[:7]
        self.setup_journal(month=previous)
        _, output = self.launch("57.7", "r", "2.1", "d", success=False)
        self.assertIn(self.month, output)
        with Journal(self.path) as journal:
            self.assertEqual([month["month"] for month in journal.months()], [previous])

    def test_unknown_daily_r_blocks_intraday_default_but_not_delivery(self):
        self.setup_journal(multiplier=None)
        (self.home / "config/trading-calendar.csv").write_text(
            "date,trading_day,expiry_state,source\n", encoding="utf-8")
        _, output = self.launch("57.7", "r", "2.1", "i", success=False)
        self.assertIn(self.day, output)
        _, output = self.launch("57.7", "r", "2.1", "d", answers="y\n60\nn\n")
        self.assertEqual(self.printed_number(output, "Risk"), Decimal("112.5"))

    def test_numeric_intraday_entry_can_keep_month_r_when_daily_r_is_unknown(self):
        self.setup_journal(multiplier=None)
        (self.home / "config/trading-calendar.csv").write_text(
            "date,trading_day,expiry_state,source\n", encoding="utf-8")
        _, output = self.launch("57.7", "112.50", "2.1", "i",
                                answers="y\n60\ny\nENTERED\n\n\n")
        self.assertIn("Saved trade #", output)
        with Journal(self.path) as journal:
            saved = journal.trades(self.month)[0]
        self.assertEqual(Decimal(saved["risk"]), Decimal("112.50"))
        self.assertEqual(Decimal(saved["r_multiplier"]), Decimal("0.7"))

    def test_zero_daily_r_does_not_size_a_new_position(self):
        self.setup_journal(multiplier="0")
        _, output = self.launch("57.7", "r", "2.1", "i", success=False)
        self.assertRegex(output.lower(), r"risk.*(positive|greater than zero|zero)")
        with Journal(self.path) as journal:
            self.assertEqual(journal.trades(self.month), [])

    def test_eof_after_accepting_save_does_not_write_a_partial_trade(self):
        self.setup_journal()
        for answers in ("y\n60\ny\n", "y\n60\ny\nENTERED\n"):
            with self.subTest(answers=answers):
                self.launch("57.7", "112.50", "2.1", "d", answers=answers)
                with Journal(self.path) as journal:
                    self.assertEqual(journal.trades(self.month), [])

    def test_saved_position_can_be_closed_through_relocated_journal_calculator(self):
        self.setup_journal()
        self.launch("57.7", "112.50", "2.1", "d", answers="y\n60\ny\nENTERED\n\n")
        with Journal(self.path) as journal:
            saved = journal.trades(self.month)[0]
            changed = journal.update_exit(saved["id"], "61", close=True)
        self.assertEqual(changed["status"], "closed")
        self.assertEqual(changed["quantity"], 18)
        self.assertEqual(Decimal(changed["risk"]), Decimal("112.50"))
        self.assertGreater(Decimal(changed["pnl"]), Decimal(saved["pnl"]))

    def test_command_also_runs_from_inside_erv_directory(self):
        _, output = self.launch("57.7", "112.50", "2.1", "d", answers="y\n60\nn\n",
                                inside_erv=True)
        self.assertEqual(self.printed_number(output, "Risk"), Decimal("112.50"))


if __name__ == "__main__":
    unittest.main()
