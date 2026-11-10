"""Portable workbook interchange, independent of the user's original file."""

from pathlib import Path
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from risk_tool.workbook import list_sheets, read_month, write_month


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "month.xlsx"
        self.settings = {"month": "2026-11", "intraday_allocation": "222",
                         "swing_allocation": "450", "swing_slots": 4}
        self.trade = {"date": "2026-11-02", "ticker": "A&B <test>",
                      "style": "intraday", "entry": "100", "direction": "short",
                      "atr": "2", "r_multiplier": "0.5", "risk": "37",
                      "quantity": 11, "status": "open", "exit_price": "98",
                      "charges": "1.25", "pnl": "20.75"}

    def test_export_is_real_xlsx_and_round_trips_inputs_cached_charges_and_outcome(self):
        self.assertEqual(write_month(self.path, self.settings, [self.trade]), self.path)
        self.assertEqual(list_sheets(self.path), ["Nov 2026"])
        month = read_month(self.path, "Nov 2026")
        self.assertEqual(month["month"], "2026-11")
        self.assertEqual(month["intraday_allocation"], "222")
        self.assertEqual(month["swing_allocation"], "450")
        self.assertEqual(month["swing_slots"], 4)
        self.assertEqual(month["trades"], [self.trade])

    def test_export_has_editable_formulas_with_fee_snapshots_and_preserves_held_size(self):
        write_month(self.path, self.settings, [self.trade])
        with ZipFile(self.path) as workbook:
            root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            cells = {cell.attrib["r"]: cell for cell in root.findall("s:sheetData/s:row/s:c", ns)}
            self.assertIsNotNone(cells["P6"].find("s:f", ns))
            self.assertIsNotNone(cells["C7"].find("s:f", ns))
            self.assertEqual(cells["M6"].find("s:v", ns).text, "11")
            self.assertIsNone(cells["M6"].find("s:f", ns))
            self.assertEqual(cells["O6"].find("s:v", ns).text, "1.25")
            self.assertIsNone(cells["O6"].find("s:f", ns))
            self.assertEqual(cells["J6"].find("s:v", ns).text, "37")
            self.assertIn("All charges", workbook.read("xl/worksheets/sheet1.xml").decode())

    def test_shared_strings_and_cached_formulas_from_an_existing_sheet_are_read(self):
        write_month(self.path, self.settings, [self.trade])
        with ZipFile(self.path) as original:
            parts = {name: original.read(name) for name in original.namelist()}
        root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        cell = root.find(f".//{{{namespace}}}c[@r='D6']")
        cell.set("t", "s")
        cell.remove(cell.find(f"{{{namespace}}}is"))
        ET.SubElement(cell, f"{{{namespace}}}v").text = "0"
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(root)
        parts["xl/sharedStrings.xml"] = b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>A&amp;B &lt;test&gt;</t></si></sst>'
        with ZipFile(self.path, "w") as revised:
            for name, content in parts.items():
                revised.writestr(name, content)
        self.assertEqual(read_month(self.path, "Nov 2026")["trades"][0], self.trade)

    def test_fee_only_adjustment_is_counted_once_and_not_invented_as_a_trade(self):
        adjustment = {"date": "2026-11-03", "amount": "-2.253467581",
                      "label": "Imported sheet row18", "status": "open"}
        settings = self.settings | {"intraday_adjustments": [adjustment]}
        write_month(self.path, settings, [self.trade])
        month = read_month(self.path, "Nov 2026")
        self.assertEqual(month["trades"], [self.trade])
        self.assertEqual(month["intraday_adjustments"], [adjustment])

    def test_blank_month_has_no_calendar_placeholders_and_retains_next_risk(self):
        write_month(self.path, self.settings, [])
        self.assertEqual(read_month(self.path, "Nov 2026")["trades"], [])
        with ZipFile(self.path) as workbook:
            root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            cells = {cell.attrib["r"]: cell for cell in root.findall("s:sheetData/s:row/s:c", ns)}
            self.assertNotIn("A6", cells)
            self.assertEqual(cells["C6"].find("s:f", ns).text, "C5")
            self.assertIsNone(root.find("s:conditionalFormatting", ns))
            self.assertIn("0.02", workbook.read("xl/worksheets/sheet1.xml").decode())

    def test_export_uses_next_r_without_changing_saved_trade_risk(self):
        settings = self.settings | {"r_multiplier": "0.4"}
        write_month(self.path, settings, [])
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with ZipFile(self.path) as workbook:
            root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        cells = {cell.attrib["r"]: cell for cell in root.findall("s:sheetData/s:row/s:c", ns)}
        self.assertEqual(Decimal(cells["M2"].find("s:v", ns).text), Decimal("29.6"))
        self.assertEqual(cells["K1"].find("s:is/s:t", ns).text, "Next R")
        self.assertEqual(Decimal(cells["K2"].find("s:v", ns).text), Decimal("0.4"))
        self.assertIn("*$E$2*$K$2", cells["M2"].find("s:f", ns).text)
        self.assertNotIn("A6", cells)
        self.assertEqual(Decimal(cells["C6"].find("s:v", ns).text), Decimal("3700"))

        write_month(self.path, settings, [self.trade])
        with ZipFile(self.path) as workbook:
            root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        cells = {cell.attrib["r"]: cell for cell in root.findall("s:sheetData/s:row/s:c", ns)}
        self.assertEqual(cells["B6"].find("s:v", ns).text, "0.5")
        self.assertIsNone(cells["B6"].find("s:f", ns))
        self.assertEqual(cells["J6"].find("s:v", ns).text, "37")
        self.assertIsNone(cells["J6"].find("s:f", ns))
        self.assertNotIn("A7", cells)

    def test_unknown_sheet_or_invalid_workbook_fails_clearly(self):
        write_month(self.path, self.settings, [])
        with self.assertRaisesRegex(ValueError, "sheet"):
            read_month(self.path, "Other")
        self.path.write_text("not a spreadsheet")
        with self.assertRaisesRegex(ValueError, "xlsx"):
            list_sheets(self.path)


if __name__ == "__main__":
    unittest.main()
