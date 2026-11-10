"""The journal bridge must match the standalone calculator."""

from decimal import Decimal
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import unittest

from risk_tool.calculator import calculate_trade


ERV = Path(__file__).resolve().parents[2] / "erv" / "erv.py"


def standalone(style, direction, exit_price, risk="205"):
    result = subprocess.run(
        [sys.executable, str(ERV), "1900.1", risk, "40.97", style, direction, "nse"],
        input=f"y\n{exit_price}\n", text=True, capture_output=True, check=True,
    )
    return {
        label: match.group(1).strip()
        for label in ("Stoploss", "Breakeven", "Take-Profit", "PNL", "Total charges")
        if (match := re.search(rf"^{re.escape(label)}:\s*(.+)$", result.stdout, re.MULTILINE))
    }


class CalculatorTests(unittest.TestCase):
    def test_exit_calculations_match_standalone_erv_for_all_supported_trade_types(self):
        before = hashlib.sha256(ERV.read_bytes()).digest()
        for style, direction in (("intraday", "long"), ("intraday", "short"), ("delivery", "long")):
            for exit_price in ("1880", "1920"):
                with self.subTest(style=style, direction=direction, exit_price=exit_price):
                    expected = standalone(style, direction, exit_price)
                    actual = calculate_trade("1900.1", "205", "40.97", style, direction,
                                             exit_price=exit_price)
                    self.assertEqual(actual["quantity"], 2 if style == "delivery" else 3)
                    for key, label in (("stop_loss", "Stoploss"), ("breakeven", "Breakeven"),
                                       ("take_profit", "Take-Profit"), ("pnl", "PNL"),
                                       ("charges", "Total charges")):
                        self.assertEqual(Decimal(actual[key]), Decimal(expected[label]))
                    self.assertEqual(actual["exit_price"], exit_price)
        self.assertEqual(hashlib.sha256(ERV.read_bytes()).digest(), before)

    def test_changing_exit_preserves_recorded_quantity_despite_new_risk_budget(self):
        before = calculate_trade("1900.1", "205", "40.97", "intraday", "long",
                                 exit_price="1880", quantity=7)
        after = calculate_trade("1900.1", "1", "40.97", "intraday", "long",
                                exit_price="1920", quantity=7)
        expected = standalone("intraday", "long", "1920", risk="487.543")
        self.assertEqual((before["quantity"], after["quantity"]), (7, 7))
        self.assertEqual(Decimal(after["pnl"]), Decimal(expected["PNL"]))
        self.assertEqual(Decimal(after["charges"]), Decimal(expected["Total charges"]))
        self.assertNotEqual(before["pnl"], after["pnl"])

    def test_entry_preview_has_prices_without_inventing_an_exit_outcome(self):
        preview = calculate_trade("1900.1", "205", "40.97", "intraday", "short")
        self.assertEqual(preview["quantity"], 3)
        self.assertIsNone(preview["exit_price"])
        self.assertIsNone(preview["charges"])
        self.assertIsNone(preview["pnl"])
        self.assertGreater(Decimal(preview["stop_loss"]), Decimal("1900.1"))

    def test_invalid_trade_inputs_report_field_and_do_not_hang_on_prompts(self):
        examples = (("entry", "0"), ("risk", "-1"), ("atr", "0"),
                    ("entry", "NaN"), ("exit_price", "Infinity"),
                    ("style", "unknown"), ("direction", "unknown"),
                    ("exchange", "unknown"), ("quantity", 0), ("quantity", True))
        for field, value in examples:
            arguments = {"entry": "1900.1", "risk": "205", "atr": "40.97",
                         "style": "intraday", "direction": "long", "exit_price": "1880"}
            arguments[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                calculate_trade(**arguments)
        with self.assertRaisesRegex(ValueError, "delivery"):
            calculate_trade("1900.1", "205", "40.97", "delivery", "short")

    def test_risk_too_small_for_a_share_has_a_clear_result(self):
        with self.assertRaisesRegex(ValueError, "one share"):
            calculate_trade("1900.1", "1", "40.97", "intraday", "long")


if __name__ == "__main__":
    unittest.main()
