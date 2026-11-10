"""Saved monthly workflow, using real calculator calls for exit changes."""

from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from risk_tool.journal import Journal


def trade(**changes):
    return {
        "date": "2026-10-30", "ticker": "TEST", "style": "intraday",
        "entry": "100", "direction": "long", "atr": "1", "r_multiplier": "1",
        "risk": "74", "quantity": 3, "status": "open", "exit_price": "97",
        "charges": "1", "pnl": "-10", "exchange": "nse",
    } | changes


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.journal = Journal(self.path)
        self.addCleanup(self.journal.close)
        self.journal.save_month("2026-10", "222", "900", 4)

    def test_month_risk_and_manual_r_are_saved_without_changing_past_trades(self):
        saved = self.journal.add_trade("2026-10", trade())
        self.journal.set_r("2026-10", "0.4")
        quote = self.journal.quote("2026-10")
        self.assertEqual(quote.current_accum, D("3690"))
        self.assertEqual(quote.intraday_risk, D("29.52"))
        self.assertEqual(quote.swing_risk_per_position, D("225"))
        self.assertEqual(self.journal.get_trade(saved["id"])["risk"], "74")
        with Journal(self.path) as reopened:
            self.assertEqual(reopened.settings("2026-10")["r_multiplier"], "0.4")
            self.assertEqual(len(reopened.trades("2026-10")), 1)

    def test_swing_outcome_does_not_change_intraday_or_swing_budget(self):
        self.journal.add_trade("2026-10", trade(style="delivery", pnl="-100"))
        quote = self.journal.quote("2026-10")
        self.assertEqual(quote.intraday_risk, D("74"))
        self.assertEqual(quote.swing_risk_per_position, D("225"))

    def test_exit_updates_reuse_erv_for_recorded_quantity_and_replace_outcomes(self):
        saved = self.journal.add_trade("2026-10", trade())
        self.journal.save_month("2026-10", "999", "900", 4)
        changed = self.journal.update_exit(saved["id"], "101")
        self.assertEqual(changed["quantity"], 3)
        self.assertEqual(changed["risk"], "74")
        self.assertGreater(D(changed["pnl"]), 0)
        self.assertEqual(self.journal.quote("2026-10").current_accum,
                         D("999") / D("0.06") + D(changed["pnl"]))
        completed = self.journal.update_exit(saved["id"], "102", close=True)
        self.assertEqual(completed["status"], "closed")
        self.assertEqual(len(self.journal.trades("2026-10")), 1)
        self.assertEqual(self.journal.quote("2026-10").current_accum,
                         D("999") / D("0.06") + D(completed["pnl"]))

    def test_import_preserves_fee_only_adjustment_and_rejects_duplicate_month(self):
        data = {"month": "2026-11", "intraday_allocation": "222",
                "swing_allocation": "450", "swing_slots": 4, "trades": [trade(pnl="-10")],
                "intraday_adjustments": [{"date": "2026-11-02", "amount": "-2.25",
                                          "label": "Imported fee", "status": "open"}]}
        self.journal.import_month(data)
        self.assertEqual(self.journal.quote("2026-11").current_accum, D("3687.75"))
        self.journal.save_month("2026-11", "222", "450", 5)
        self.assertEqual(self.journal.quote("2026-11").swing_risk_per_position, D("90"))
        self.assertEqual(self.journal.quote("2026-11").current_accum, D("3687.75"))
        with self.assertRaisesRegex(ValueError, "already"):
            self.journal.import_month(data)
        self.assertEqual(len(self.journal.trades("2026-11")), 1)

    def test_bad_import_rolls_back_and_bad_trade_does_not_partially_save(self):
        data = {"month": "2026-12", "intraday_allocation": "222",
                "swing_allocation": "450", "swing_slots": 4,
                "trades": [trade(), trade(quantity=0)]}
        with self.assertRaises(ValueError):
            self.journal.import_month(data)
        self.assertNotIn("2026-12", [month["month"] for month in self.journal.months()])
        with self.assertRaises(ValueError):
            self.journal.add_trade("2026-10", trade(entry="NaN"))
        self.assertEqual(self.journal.trades("2026-10"), [])

    def test_correction_preserves_quantity_and_deletion_removes_its_outcome(self):
        saved = self.journal.add_trade("2026-10", trade())
        corrected = self.journal.update_trade(saved["id"], {"ticker": "CORRECTED", "entry": "99"})
        self.assertEqual(corrected["ticker"], "CORRECTED")
        self.assertEqual(corrected["quantity"], 3)
        self.journal.delete_trade(saved["id"])
        self.assertEqual(self.journal.quote("2026-10").intraday_risk, D("74"))

    def test_renaming_with_unchanged_numeric_defaults_preserves_imported_outcomes(self):
        saved = self.journal.add_trade("2026-10", trade())
        renamed = self.journal.update_trade(saved["id"], {
            "ticker": "RENAMED", "entry": "100.0", "atr": "1.00", "quantity": 3,
        })
        self.assertEqual(renamed["charges"], "1")
        self.assertEqual(renamed["pnl"], "-10")
        self.assertEqual(self.journal.quote("2026-10").intraday_risk, D("73.80"))


if __name__ == "__main__":
    unittest.main()
