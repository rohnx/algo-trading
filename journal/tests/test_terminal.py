"""Exercise the actual launch command and the everyday keyboard workflow."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from risk_tool.journal import Journal


LAUNCHER = Path(__file__).resolve().parents[1] / "trade.py"


class TerminalTests(unittest.TestCase):
    def test_new_month_launch_shows_risk_and_can_quit_without_a_traceback(self):
        with TemporaryDirectory() as directory:
            process = subprocess.run(
                [sys.executable, str(LAUNCHER), "--data-dir", directory, "--month", "2026-10"],
                input="n\n\n222\n900\n4\nq\n", text=True, capture_output=True, timeout=20,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertIn("October 2026", process.stdout)
            self.assertIn("74.00", process.stdout)
            self.assertIn("225.00", process.stdout)
            self.assertNotIn("Traceback", process.stderr)

    def test_entry_limit_close_and_restart_persist_real_user_actions(self):
        with TemporaryDirectory() as directory:
            with Journal(Path(directory) / "journal.sqlite3") as journal:
                journal.save_month("2026-10", "222", "900", 4)
            answers = ["a", "TEST", "i", "l", "100", "1", "2026-10-30", "", "3", "97",
                       "", "x", "1", "101", "y", "q"]
            process = subprocess.run(
                [sys.executable, str(LAUNCHER), "--data-dir", directory, "--month", "2026-10"],
                input="\n".join(answers) + "\n", text=True, capture_output=True, timeout=20,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertIn("Saved trade #1", process.stdout)
            self.assertIn("Closed trade #1", process.stdout)
            with Journal(Path(directory) / "journal.sqlite3") as journal:
                record = journal.get_trade(1)
                self.assertEqual(record["quantity"], 3)
                self.assertEqual(record["status"], "closed")
                self.assertEqual(record["exit_price"], "101")
            restarted = subprocess.run(
                [sys.executable, str(LAUNCHER), "--data-dir", directory, "--show"],
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            self.assertIn("TEST", restarted.stdout)
            self.assertIn("OUT", restarted.stdout)

    def test_ticker_only_correction_keeps_imported_fee_and_pnl_snapshots(self):
        with TemporaryDirectory() as directory:
            with Journal(Path(directory) / "journal.sqlite3") as journal:
                journal.save_month("2026-10", "222", "900", 4)
                saved = journal.add_trade("2026-10", {
                    "date": "2026-10-30", "ticker": "ORIGINAL", "style": "intraday",
                    "entry": "100", "direction": "long", "atr": "1", "r_multiplier": "1",
                    "risk": "74", "quantity": 3, "status": "open", "exit_price": "97",
                    "charges": "1", "pnl": "-10", "exchange": "nse",
                })
            process = subprocess.run(
                [sys.executable, str(LAUNCHER), "--data-dir", directory, "--month", "2026-10"],
                input="u\n1\nRENAMED\n\n\n\nq\n", text=True, capture_output=True, timeout=20,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            with Journal(Path(directory) / "journal.sqlite3") as journal:
                changed = journal.get_trade(saved["id"])
                self.assertEqual(changed["ticker"], "RENAMED")
                self.assertEqual(changed["charges"], "1")
                self.assertEqual(changed["pnl"], "-10")


if __name__ == "__main__":
    unittest.main()
