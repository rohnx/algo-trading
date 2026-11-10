"""Portable XLSX interchange for the monthly trading ledger.

Only saved cell values are imported. Exported fee amounts are snapshots from
the journal; Excel can recalculate risk and net outcomes without a second fee
implementation or a Codex-specific runtime dependency.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import posixpath
import re
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

from .risk import MonthlyRiskSettings, estimate_opening_accum


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"s": _MAIN}
ET.register_namespace("", _MAIN)


def _xml(workbook, member):
    if workbook.getinfo(member).file_size > 32 * 1024 * 1024:
        raise ValueError("xlsx worksheet is too large")
    return ET.fromstring(workbook.read(member))


def _sheets(workbook):
    relations = {item.attrib["Id"]: item.attrib["Target"]
                 for item in _xml(workbook, "xl/_rels/workbook.xml.rels")}
    sheets = {}
    for sheet in _xml(workbook, "xl/workbook.xml").find("s:sheets", _NS):
        target = relations[sheet.attrib[f"{{{_REL}}}id"]]
        sheets[sheet.attrib["name"]] = (target.lstrip("/") if target.startswith("/")
                                       else posixpath.normpath("xl/" + target))
    return sheets


def list_sheets(path):
    """List sheet names in workbook order without executing formulas."""
    try:
        with ZipFile(path) as workbook:
            return list(_sheets(workbook))
    except (BadZipFile, ET.ParseError, KeyError) as error:
        raise ValueError("Could not read the xlsx workbook") from error


def _decimal(value, label):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"Missing or invalid {label} in the worksheet") from error
    if not number.is_finite():
        raise ValueError(f"Invalid {label} in the worksheet")
    return number


def _month(sheet_name):
    match = re.fullmatch(r"([A-Za-z]+)\s+([0-9]{4})", sheet_name.strip())
    if match:
        names = {calendar.month_abbr[index].lower(): index for index in range(1, 13)}
        number = names.get(match.group(1)[:3].lower())
        if number:
            return date(int(match.group(2)), number, 1).strftime("%Y-%m")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}", sheet_name):
        return date.fromisoformat(sheet_name + "-01").strftime("%Y-%m")
    raise ValueError("Choose a month sheet such as Nov 2026")


def _trade_date(value, epoch, row):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return epoch + timedelta(days=int(_decimal(value, f"date A{row}")))


def read_month(path, sheet_name):
    """Read the source monthly layout, preserving cached outcomes and charges."""
    try:
        with ZipFile(path) as workbook:
            sheets = _sheets(workbook)
            if sheet_name not in sheets:
                raise ValueError(f"Unknown sheet: {sheet_name}")
            shared = (["".join(item.itertext()) for item in _xml(workbook, "xl/sharedStrings.xml")]
                      if "xl/sharedStrings.xml" in workbook.namelist() else [])
            root = _xml(workbook, sheets[sheet_name])
            cells = {}
            for cell in root.findall("s:sheetData/s:row/s:c", _NS):
                value = cell.find("s:v", _NS)
                value = value.text if value is not None else None
                if cell.attrib.get("t") == "s" and value is not None:
                    value = shared[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", _NS).itertext())
                cells[cell.attrib["r"]] = value
            properties = _xml(workbook, "xl/workbook.xml").find("s:workbookPr", _NS)
            epoch = date(1904, 1, 1) if properties is not None and properties.attrib.get("date1904") in ("1", "true") else date(1899, 12, 30)
    except (BadZipFile, ET.ParseError, KeyError, IndexError) as error:
        raise ValueError("Could not read the xlsx month worksheet") from error

    intraday = _decimal(cells.get("D2"), "intraday allocation D2")
    swing = _decimal(cells.get("F2"), "swing allocation F2")
    share = _decimal(cells.get("G2"), "swing proportion G2")
    if share <= 0:
        raise ValueError("Swing proportion G2 must be positive")
    slots = int((1 / share).to_integral_value())
    settings = MonthlyRiskSettings(_month(sheet_name), intraday, swing, slots)
    trades = []
    adjustments = []
    row_numbers = sorted({int(re.search(r"[0-9]+$", address).group()) for address in cells})
    for row in row_numbers:
        if row < 6:
            continue
        ticker = str(cells.get(f"D{row}") or "").strip()
        entry_value = cells.get(f"F{row}")
        entry = _decimal(entry_value, f"entry F{row}") if entry_value not in (None, "") else Decimal(0)
        if entry <= 0:
            # The source includes an intraday fee-only row with no ticker/entry.
            # It affects Accum and must survive migration as an adjustment.
            raw_pnl = cells.get(f"P{row}")
            if (str(cells.get(f"E{row}") or "").strip().lower() == "intraday"
                    and raw_pnl not in (None, "") and cells.get(f"A{row}") is not None):
                amount = _decimal(raw_pnl, f"adjustment P{row}")
                if amount:
                    label = ticker.removeprefix("(adjustment) ") if ticker else f"Imported sheet row{row}"
                    adjustments.append({"date": _trade_date(cells[f"A{row}"], epoch, row).isoformat(),
                                        "amount": str(amount), "label": label,
                                        "status": "closed" if str(cells.get(f"I{row}")).strip().lower() in ("out", "closed") else "open"})
            continue
        ticker = ticker or "(unnamed)"
        raw_date = cells.get(f"A{row}")
        trade_date = _trade_date(raw_date, epoch, row)
        style_text = str(cells.get(f"E{row}") or "Intraday").strip().lower()
        if style_text not in ("intraday", "swing", "delivery", "investing"):
            raise ValueError(f"Unknown trading style in E{row}: {style_text}")
        trade = {
            "date": trade_date.isoformat(), "ticker": ticker,
            "style": "intraday" if style_text == "intraday" else "delivery",
            "entry": str(entry),
            "direction": "short" if str(cells.get(f"G{row}")).lower() == "short" else "long",
            "atr": str(_decimal(cells.get(f"H{row}"), f"ATR H{row}")),
            "r_multiplier": str(_decimal(cells.get(f"B{row}") or "1", f"R B{row}")),
            "risk": str(_decimal(cells.get(f"J{row}"), f"risk J{row}")),
            "quantity": int(_decimal(cells.get(f"M{row}") or "0", f"quantity M{row}")),
            "status": "closed" if str(cells.get(f"I{row}")).strip().lower() in ("out", "closed") else "open",
        }
        for key, column in (("exit_price", "N"), ("charges", "O"), ("pnl", "P")):
            value = cells.get(f"{column}{row}")
            trade[key] = None if value in (None, "") else str(_decimal(value, f"{key} {column}{row}"))
        trades.append(trade)
    return {"month": settings.month, "intraday_allocation": str(intraday),
            "swing_allocation": str(swing), "swing_slots": slots, "trades": trades,
            "intraday_adjustments": adjustments}


def _cell(address, value=None, *, formula=None, style=0):
    attributes = {"r": address, "s": str(style)}
    is_text = isinstance(value, str) and formula is None
    if is_text:
        attributes["t"] = "inlineStr"
    cell = ET.Element(f"{{{_MAIN}}}c", attributes)
    if formula is not None:
        ET.SubElement(cell, f"{{{_MAIN}}}f").text = formula
    if is_text:
        ET.SubElement(ET.SubElement(cell, f"{{{_MAIN}}}is"), f"{{{_MAIN}}}t").text = value
    elif value is not None:
        ET.SubElement(cell, f"{{{_MAIN}}}v").text = str(value)
    return cell


def _numeric(value):
    return None if value in (None, "") else Decimal(str(value))


_STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="dd-mmm-yyyy"/></numFmts>
<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><sz val="11"/><color rgb="FF165DAD"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF24364B"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="9"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" horizontal="center" wrapText="1"/></xf><xf numFmtId="2" fontId="2" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="164" fontId="2" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="1" fontId="2" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
<dxfs count="2"><dxf><font><color rgb="FF197243"/></font></dxf><dxf><font><color rgb="FFB33535"/></font></dxf></dxfs>
</styleSheet>'''


def write_month(path, settings, trades):
    """Export one editable month with frozen held quantities and fee snapshots."""
    adjustments = list(settings.get("intraday_adjustments", []))
    manual_r = _decimal(settings.get("r_multiplier", "1"), "manual R")
    settings = MonthlyRiskSettings(settings["month"], Decimal(str(settings["intraday_allocation"])),
                                   Decimal(str(settings["swing_allocation"])), int(settings["swing_slots"]))
    title = date.fromisoformat(settings.month + "-01").strftime("%b %Y")
    actual = [dict(trade) for trade in trades]
    actual.extend({"date": adjustment["date"], "ticker": "(adjustment) " + adjustment["label"],
                   "style": "intraday", "entry": "0", "atr": "0", "quantity": 0,
                   "r_multiplier": "1", "risk": "0", "status": adjustment.get("status", "open"),
                   "pnl": adjustment["amount"], "_adjustment": True}
                  for adjustment in adjustments)
    rows = sorted(actual, key=lambda trade: trade["date"])
    root = ET.Element(f"{{{_MAIN}}}worksheet")
    views = ET.SubElement(root, f"{{{_MAIN}}}sheetViews")
    view = ET.SubElement(views, f"{{{_MAIN}}}sheetView", {"workbookViewId": "0", "showGridLines": "0"})
    ET.SubElement(view, f"{{{_MAIN}}}pane", {"xSplit": "1", "ySplit": "5", "topLeftCell": "B6", "activePane": "bottomRight", "state": "frozen"})
    ET.SubElement(root, f"{{{_MAIN}}}sheetFormatPr", {"defaultRowHeight": "20"})
    columns = ET.SubElement(root, f"{{{_MAIN}}}cols")
    for index, width in enumerate((15, 9, 15, 18, 12, 13, 10, 12, 12, 14, 13, 13, 8, 19, 24, 15, 12, 17), 1):
        ET.SubElement(columns, f"{{{_MAIN}}}col", {"min": str(index), "max": str(index), "width": str(width), "customWidth": "1"})
    data = ET.SubElement(root, f"{{{_MAIN}}}sheetData")

    def add_row(number, cells, height=None):
        attributes = {"r": str(number)}
        if height:
            attributes |= {"ht": str(height), "customHeight": "1"}
        row = ET.SubElement(data, f"{{{_MAIN}}}row", attributes)
        row.extend(cells)

    close_row = 6 + len(rows)
    opening = estimate_opening_accum(settings.intraday_allocation)
    accum = opening
    final_accum = opening + sum((_numeric(trade.get("pnl")) or Decimal(0) for trade in actual if trade.get("style") == "intraday"), Decimal(0))
    add_row(1, [_cell(address, value, style=1) for address, value in
                (("A1", "Total Risk"), ("D1", "Intraday"), ("E1", "intraday f"),
                 ("F1", "Swing"), ("G1", "delivery f"), ("I1", "per swing"),
                 ("K1", "Next R"), ("M1", "Next intraday"))], 28)
    add_row(2, [_cell("A2", settings.intraday_allocation + settings.swing_allocation, formula="D2+F2", style=3),
                _cell("D2", settings.intraday_allocation, style=2), _cell("E2", Decimal("0.02"), style=7),
                _cell("F2", settings.swing_allocation, style=2), _cell("G2", Decimal(1) / settings.swing_slots, formula=f"1/{settings.swing_slots}", style=7),
                _cell("I2", settings.swing_allocation / settings.swing_slots, formula="F2*G2", style=3),
                _cell("K2", manual_r, style=2),
                _cell("M2", final_accum * Decimal("0.02") * manual_r, formula=f"C{close_row}*$E$2*$K$2", style=3)])
    headers = ("Date", "R mul", "Accum", "TICK", "Style", "Entry", "type", "ATR", "Position",
               "sizing ~r", "space", "Size", "Qty", "Exit limit / actual", "All charges (snapshot)", "Net P&L", "Risk %", "total entry")
    add_row(4, [_cell(f"{chr(65 + index)}4", header, style=1) for index, header in enumerate(headers)], 36)
    add_row(5, [_cell("C5", opening, formula="D2/0.06", style=3)])
    previous = None
    for number, trade in enumerate(rows, 6):
        if previous and previous.get("style") == "intraday" and previous.get("pnl") is not None:
            accum += _numeric(previous["pnl"])
        formula = "C5" if number == 6 else f'C{number-1}+IF(AND(E{number-1}="Intraday",ISNUMBER(P{number-1})),P{number-1},0)'
        style = "Swing" if trade.get("style") == "delivery" else "Intraday"
        multiplier = Decimal(3) if style == "Swing" else Decimal("1.7")
        risk = _numeric(trade.get("risk"))
        atr = _numeric(trade.get("atr"))
        space = atr * multiplier if atr is not None else None
        raw_size = risk / space if space else None
        entry = _numeric(trade.get("entry"))
        quantity = int(trade["quantity"])
        charges = _numeric(trade.get("charges"))
        pnl = _numeric(trade.get("pnl"))
        cells = [_cell(f"A{number}", (date.fromisoformat(trade["date"]) - date(1899, 12, 30)).days, style=4),
                 _cell(f"B{number}", _numeric(trade.get("r_multiplier", "1")), style=2),
                 _cell(f"C{number}", accum, formula=formula, style=3),
                 _cell(f"D{number}", trade.get("ticker", ""), style=5), _cell(f"E{number}", style, style=5),
                 _cell(f"F{number}", entry, style=2), _cell(f"G{number}", trade.get("direction", "long").capitalize(), style=5),
                 _cell(f"H{number}", atr, style=2), _cell(f"I{number}", "Out" if trade.get("status") == "closed" else "In", style=5),
                 _cell(f"J{number}", risk, style=2),
                 _cell(f"K{number}", space, formula=f'IF(H{number}="","",IF(E{number}="Swing",3,1.7)*H{number})', style=3),
                 _cell(f"L{number}", raw_size, formula=f'IF(K{number}="","",J{number}/K{number})', style=3),
                 _cell(f"M{number}", quantity, style=8),
                 _cell(f"N{number}", _numeric(trade.get("exit_price")), style=2), _cell(f"O{number}", charges, style=2),
                 _cell(f"P{number}", pnl, formula=None if trade.get("_adjustment") else f'IF(OR(N{number}="",F{number}="",M{number}="",O{number}=""),"",IF(G{number}="Long",(N{number}-F{number})*M{number}-O{number},(F{number}-N{number})*M{number}-O{number}))', style=3),
                 _cell(f"Q{number}", space * quantity / (settings.intraday_allocation + settings.swing_allocation) if space and quantity and settings.intraday_allocation + settings.swing_allocation else None,
                       formula=f'IF(OR(K{number}="",M{number}="",$A$2=0),"",K{number}*M{number}/$A$2)', style=7),
                 _cell(f"R{number}", entry * quantity + charges if entry is not None and charges is not None else None,
                       formula=f'IF(OR(F{number}="",M{number}="",O{number}=""),"",F{number}*M{number}+O{number})', style=3)]
        add_row(number, cells)
        previous = trade
    end = close_row - 1
    add_row(close_row, [_cell(f"B{close_row}", "Next Accum"), _cell(f"C{close_row}", final_accum,
                formula=f'C{end}+IF(AND(E{end}="Intraday",ISNUMBER(P{end})),P{end},0)' if rows else "C5", style=3)])
    for offset, label, condition in ((2, "net", None), (3, "intraday", ("E", "Intraday")),
                                      (4, "swing", ("E", "Swing")), (5, "realized", ("I", "Out"))):
        row = close_row + offset
        selected = [trade for trade in actual if condition is None or
                    (condition[0] == "E" and (trade.get("style") == "intraday") == (condition[1] == "Intraday")) or
                    (condition[0] == "I" and trade.get("status") == "closed")]
        net = sum((_numeric(trade.get("pnl")) or Decimal(0) for trade in selected), Decimal(0))
        formula = f"SUM(P6:P{end})" if condition is None else f'SUMIF({condition[0]}6:{condition[0]}{end},"{condition[1]}",P6:P{end})'
        if not rows:
            formula = "0"
        add_row(row, [_cell(f"O{row}", label), _cell(f"P{row}", net, formula=formula, style=3)])
    if rows:
        formatting = ET.SubElement(root, f"{{{_MAIN}}}conditionalFormatting", {"sqref": f"P6:P{end}"})
        for priority, operator in ((1, "greaterThan"), (2, "lessThan")):
            rule = ET.SubElement(formatting, f"{{{_MAIN}}}cfRule", {"type": "cellIs", "dxfId": str(priority - 1), "priority": str(priority), "operator": operator})
            ET.SubElement(rule, f"{{{_MAIN}}}formula").text = "0"
    ET.SubElement(root, f"{{{_MAIN}}}pageMargins", {"left": "0.25", "right": "0.25", "top": "0.5", "bottom": "0.5", "header": "0.2", "footer": "0.2"})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{_PACKAGE}"><Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets><sheet name="{title}" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="0" fullCalcOnLoad="1"/></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{_PACKAGE}"><Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{_REL}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": _STYLES,
        "xl/worksheets/sheet1.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
    }
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as workbook:
        for member, content in parts.items():
            workbook.writestr(member, content)
    return path
