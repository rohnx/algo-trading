"""The everyday terminal: risk, entries, exit limits, and a saved month view."""

import argparse
import calendar
import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
import os
import shutil
import sys
from zoneinfo import ZoneInfo

from daily_risk import Decision, RiskPolicy, STATE_LABELS
from .calculator import calculate_trade
from .journal import Journal, number
from .risk import format_money
from .workbook import list_sheets, read_month, write_month


ROOT = Path(__file__).resolve().parents[1]


def today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


def ask(label, default=None):
    suffix = f" [{default}]" if default is not None else ""
    answer = input(f"{label}{suffix}: ").strip()
    return str(default) if not answer and default is not None else answer


def confirm(label, default=False):
    return ask(label, "y" if default else "n").lower() in ("y", "yes")


def amount(label, default=None, *, positive=False, nonnegative=False):
    return str(number(ask(label, default), label, positive=positive, nonnegative=nonnegative))


def integer(label, default, error):
    try:
        return int(ask(label, default))
    except ValueError:
        raise ValueError(error) from None


def money(value):
    if value is None:
        return "—"
    value = Decimal(str(value))
    return format(value, ".2E") if value.adjusted() > 12 else format_money(value)


def clean(value):
    return "".join(character if character.isprintable() else " " for character in str(value))


def title(month):
    return date.fromisoformat(month + "-01").strftime("%B %Y")


def entry_day(month):
    return today() if today().startswith(month + "-") else month + "-01"


def daily_decision(policy, day):
    try:
        return policy.resolve(day)
    except (ValueError, OSError, sqlite3.Error) as error:
        return Decision(day, None, None, None, None, None, "unavailable", None,
                        f"Automatic R unavailable: {clean(error)}; enter R manually.")


def planning_view(policy, month):
    first = date.fromisoformat(month + "-01")
    if first.strftime("%Y-%m") != month:
        raise ValueError("Month must be YYYY-MM")
    rows = policy.calendar_month(month)
    missing = calendar.monthrange(first.year, first.month)[1] - len(rows)
    closed = sum(row.trading_day is False for row in rows)
    unknown = sum(row.trading_day is None for row in rows)
    lines = [f"{title(month)} · Risk planning", "",
             "Date        Expiry             R       Range       Basis",
             "─" * 65]
    for row in rows:
        if row.trading_day is False:
            continue
        decision = daily_decision(policy, row.date)
        bounds = f"{decision.r_min}–{decision.r_max}" if decision.r_min is not None else "—"
        value = str(decision.r) if decision.r is not None else "—"
        label = STATE_LABELS.get(row.expiry_state, "Unclassified")
        basis = decision.source
        if decision.first_orb_at:
            basis += " " + datetime.fromisoformat(decision.first_orb_at).strftime("%H:%M")
        lines.append(f"{row.date}  {label:<17}  {value:<6}  {bounds:<10}  {basis}")
        if decision.warning:
            lines.append("            " + clean(decision.warning))
    if not rows:
        lines.append("No calendar dates entered for this month.")
    lines.extend(["", f"{len(rows) - closed - unknown} trading · {closed} closed · "
                  f"{unknown} unclassified · {missing} missing",
                  "R uses expiry until the first main ORB signal; a daily manual R takes priority."])
    return "\n".join(lines)


def calendar_template(month):
    first = date.fromisoformat(month + "-01")
    if first.strftime("%Y-%m") != month:
        raise ValueError("Month must be YYYY-MM")
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(("date", "trading_day", "expiry_state", "source"))
    for day in range(1, calendar.monthrange(first.year, first.month)[1] + 1):
        writer.writerow((date(first.year, first.month, day).isoformat(), "", "", ""))


def month_view(journal, month, *, all_rows=False, policy=None):
    settings = journal.settings(month)
    decision = daily_decision(policy, entry_day(month)) if policy else None
    multiplier = decision.r if decision and decision.r is not None else settings["r_multiplier"]
    quote = journal.quote(month, multiplier)
    trades = journal.trades(month)
    lines = [title(month), "─" * 76,
             f"Risk now   Intraday ₹{money(quote.intraday_risk)}  (R {multiplier})"
             f"    Swing ₹{money(quote.swing_risk_per_position)} / trade · {settings['swing_slots']} allocations",
             ""]
    if decision:
        label = STATE_LABELS.get(decision.expiry_state, "Calendar unavailable")
        lines.insert(3, f"{entry_day(month)} · {label} · {decision.source}")
        if decision.warning:
            lines.insert(4, clean(decision.warning))
    widths = (3, 5, 12, 5, 4, 4, 9, 9, 4, 10)
    headings = ("#", "Date", "Ticker", "Mode", "Side", "Qty", "Entry", "Limit/exit", "State", "Net P&L")
    lines.append(" ".join(value.ljust(width) for value, width in zip(headings, widths)))
    lines.append("─" * 76)
    visible = trades
    if sys.stdout.isatty() and not all_rows:
        count = max(5, shutil.get_terminal_size(fallback=(100, 32)).lines - 16)
        visible = trades[-count:]
    for trade in visible:
        cells = (str(trade["id"]), date.fromisoformat(trade["date"]).strftime("%d/%m"), clean(trade["ticker"])[:12],
                 "Day" if trade["style"] == "intraday" else "Swing", trade["direction"][0].upper(),
                 str(trade["quantity"]), money(trade["entry"]), money(trade["exit_price"]),
                 "OPEN" if trade["status"] == "open" else "OUT", money(trade["pnl"]))
        lines.append(" ".join(value.ljust(width) for value, width in zip(cells, widths)))
    if not trades:
        lines.append("No trades yet. Press a to enter one.")
    elif len(visible) < len(trades):
        lines.append(f"Latest {len(visible)} of {len(trades)} trades · v shows the full month")
    adjustments = settings.get("intraday_adjustments", [])
    for item in adjustments:
        lines.append(f"    {date.fromisoformat(item['date']).strftime('%d/%m')}  {clean(item['label'])}  ₹{money(item['amount'])}")
    working = sum((Decimal(item["pnl"]) for item in trades if item["pnl"] is not None), Decimal("0"))
    working += sum((Decimal(item["amount"]) for item in adjustments), Decimal("0"))
    closed = sum((Decimal(item["pnl"]) for item in trades
                  if item["status"] == "closed" and item["pnl"] is not None), Decimal("0"))
    closed += sum((Decimal(item["amount"]) for item in adjustments if item["status"] == "closed"), Decimal("0"))
    open_count = sum(item["status"] == "open" for item in trades)
    lines.extend(["─" * 76, f"Net at limits ₹{money(working)}    Closed ₹{money(closed)}    Open positions {open_count}"])
    return "\n".join(lines)


def setup_month(journal, month):
    existing = next((item for item in journal.months() if item["month"] == month), {})
    month = ask("Month (YYYY-MM)", month)
    intraday = amount("Monthly intraday allocation", existing.get("intraday_allocation", "222"), nonnegative=True)
    swing = amount("Starting swing pool", existing.get("swing_allocation", "450"), nonnegative=True)
    slots = integer("Number of equal swing allocations", existing.get("swing_slots", 4),
                    "Swing allocation count must be a positive integer")
    journal.save_month(month, intraday, swing, slots)
    return month


def import_workbook(journal, path=None, sheet=None):
    if path is None:
        candidate = Path.home() / "Downloads" / "Trading Spreadsheet-2026.xlsx"
        path = ask("Workbook path", str(candidate) if candidate.exists() else None)
    path = Path(path).expanduser()
    sheets = list_sheets(path)
    if sheet is None:
        print("Sheets: " + ", ".join(sheets))
        default = next((name for name in sheets if name.lower() == date.fromisoformat(today()).strftime("%b %Y").lower()),
                       next((name for name in sheets if name[:3].lower() == "nov"), sheets[0]))
        selection = ask("Month sheet", default)
        sheet = next((name for name in sheets if name.lower() == selection.lower()), selection)
    data = read_month(path, sheet)
    month = journal.import_month(data)
    print(f"Imported {sheet}: {len(data['trades'])} trades. The original workbook is unchanged.")
    return month


def choose_trade(journal, month):
    trades = journal.trades(month)
    if not trades:
        raise ValueError("There are no trades in this month yet")
    default = next((trade["id"] for trade in reversed(trades) if trade["status"] == "open"), trades[-1]["id"])
    trade_id = integer("Trade #", default, "Enter the trade number shown in the month view")
    trade = journal.get_trade(trade_id)
    if trade["month"] != month:
        raise ValueError("Choose a trade from the displayed month")
    return trade


def add_entry(journal, month, policy=None):
    ticker = ask("Ticker")
    if not ticker:
        raise ValueError("Ticker is required")
    mode = ask("Mode: i intraday / s swing", "i").lower()
    if mode not in ("i", "intraday", "s", "swing", "d", "delivery"):
        raise ValueError("Choose i for intraday or s for swing")
    style = "intraday" if mode in ("i", "intraday") else "delivery"
    side = ask("Side: l long / s short", "l").lower()
    if side not in ("l", "long", "s", "short"):
        raise ValueError("Choose l for long or s for short")
    direction = "long" if side in ("l", "long") else "short"
    entry = amount("Entry price", positive=True)
    atr = amount("ATR", positive=True)
    trade_date = date.fromisoformat(ask("Entry date", entry_day(month))).isoformat()
    if not trade_date.startswith(month + "-"):
        raise ValueError("Entry date must be in the displayed month")
    manual = False
    decision = daily_decision(policy, trade_date) if policy and style == "intraday" else None
    multiplier = "1"
    if style == "intraday":
        fallback = journal.settings(month)["r_multiplier"]
        if decision and decision.r is not None:
            multiplier = str(decision.r)
            print(f"R {multiplier} · {STATE_LABELS.get(decision.expiry_state, 'unclassified')} · {decision.source}")
            override = ask("R override (Enter keeps default)")
            if override:
                multiplier, manual = str(number(override, "R", nonnegative=True)), True
        else:
            multiplier, manual = amount("Manual R", fallback, nonnegative=True), True
        warning = decision.warning if decision else None
        if decision and decision.r_min is not None:
            warning = None if decision.r_min <= Decimal(multiplier) <= decision.r_max else (
                f"R {multiplier} is outside the day's range {decision.r_min}–{decision.r_max}.")
        if warning:
            print(clean(warning))
    quote = journal.quote(month, multiplier)
    risk = str(quote.intraday_risk if style == "intraday" else quote.swing_risk_per_position)
    preview = calculate_trade(entry, risk, atr, style, direction)
    print(f"Risk ₹{money(risk)} · suggested quantity {preview['quantity']}")
    quantity = integer("Quantity", preview["quantity"], "Quantity must be a positive integer")
    if quantity != preview["quantity"]:
        preview = calculate_trade(entry, risk, atr, style, direction, quantity=quantity)
    limit = amount("Exit limit (order price)", preview["stop_loss"], positive=True)
    result = calculate_trade(entry, risk, atr, style, direction, exit_price=limit, quantity=quantity)
    print(f"Net at limit ₹{money(result['pnl'])} · charges ₹{money(result['charges'])}")
    if not confirm("Save this trade?", True):
        return "Entry preview discarded."
    saved = journal.add_trade(month, {
        "date": trade_date, "ticker": ticker, "style": style, "direction": direction,
        "entry": entry, "atr": atr, "r_multiplier": multiplier, "risk": risk,
        "quantity": quantity, "status": "open", "exit_price": limit,
        "charges": result["charges"], "pnl": result["pnl"], "exchange": "nse",
    })
    if policy and manual:
        try:
            policy.set_override(trade_date, multiplier)
        except (ValueError, OSError, sqlite3.Error) as error:
            return f"Saved trade #{saved['id']}; daily R could not be saved: {clean(error)}"
    return f"Saved trade #{saved['id']} · net at limit ₹{money(saved['pnl'])}."


def change_daily_r(journal, month, policy):
    day = date.fromisoformat(ask("Date for R", entry_day(month))).isoformat()
    if not day.startswith(month + "-"):
        raise ValueError("R date must be in the displayed month")
    decision = daily_decision(policy, day)
    current = decision.r if decision.r is not None else journal.settings(month)["r_multiplier"]
    value = ask("R value or auto to clear override", current)
    updated = policy.set_override(day, None if value.lower() == "auto" else value)
    result = f"{day}: R {updated.r if updated.r is not None else 'unavailable'} · {updated.source}"
    return result + ("\n" + clean(updated.warning) if updated.warning else "")


def change_exit(journal, month):
    trade = choose_trade(journal, month)
    print(f"{trade['ticker']} · {trade['quantity']} shares · entry ₹{money(trade['entry'])}")
    limit = amount("New limit / actual exit price", trade["exit_price"], positive=True)
    close = trade["status"] == "closed" or confirm("Has this trade actually closed?")
    saved = journal.update_exit(trade["id"], limit, close=close)
    action = "Closed" if close else "Updated limit for"
    return f"{action} trade #{saved['id']} · net ₹{money(saved['pnl'])} · charges ₹{money(saved['charges'])}."


def correct_trade(journal, month):
    trade = choose_trade(journal, month)
    ticker = ask("Ticker", trade["ticker"])
    entry = amount("Entry price", trade["entry"], positive=True)
    atr = amount("ATR", trade["atr"], positive=True)
    quantity = integer("Recorded quantity", trade["quantity"], "Quantity must be a positive integer")
    saved = journal.update_trade(trade["id"], {"ticker": ticker, "entry": entry, "atr": atr, "quantity": quantity})
    return f"Corrected trade #{saved['id']}."


def trade_details(journal, month):
    trade = choose_trade(journal, month)
    print(f"\n#{trade['id']} {trade['ticker']} · {trade['style']} {trade['direction']} · {trade['status']}")
    for label, field in (("Entry", "entry"), ("ATR", "atr"), ("Risk at entry", "risk"),
                         ("Exit limit / price", "exit_price"), ("All charges", "charges"), ("Net P&L", "pnl")):
        print(f"{label}: {money(trade[field])}")
    print(f"Quantity: {trade['quantity']}  · R at entry: {trade['r_multiplier']}")
    input("Enter to return to the month: ")


def run(journal, args, policy=None):
    month = import_workbook(journal, args.workbook, args.sheet) if args.workbook else None
    if not journal.months():
        print("Trading journal — start with your workbook or a fresh month.")
        action = ask("n New month / i Import Excel / q Quit", "n").lower()
        if action == "q":
            return 0
        month = import_workbook(journal) if action == "i" else setup_month(journal, args.month or today()[:7])
    known = {settings["month"] for settings in journal.months()}
    month = args.month or month or (today()[:7] if today()[:7] in known else journal.months()[0]["month"])
    if month not in known:
        month = setup_month(journal, month)
    if args.show:
        print(month_view(journal, month, policy=policy))
        return 0
    notice = ""
    while True:
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        print(month_view(journal, month, policy=policy))
        if notice:
            print("\n" + notice)
        notice = ""
        action = ask("\na Entry · x Exit/close · r R · m Month · e Excel · v All · ? More · q Quit").lower()
        try:
            if action in ("q", "quit"):
                print("Saved. See you.")
                return 0
            if action == "a":
                notice = add_entry(journal, month, policy)
            elif action == "x":
                notice = change_exit(journal, month)
            elif action == "r":
                notice = change_daily_r(journal, month, policy)
            elif action == "m":
                print("Months: " + ", ".join(settings["month"] for settings in journal.months()))
                requested = ask("Month (YYYY-MM)", month)
                month = requested if requested in {item["month"] for item in journal.months()} else setup_month(journal, requested)
            elif action == "e":
                default = ROOT / "exports" / (date.fromisoformat(month + "-01").strftime("%b %Y") + ".xlsx")
                path = Path(ask("Excel export path", default)).expanduser()
                if path.exists() and not confirm("That file exists. Replace it?"):
                    notice = "Export cancelled."
                else:
                    settings = journal.settings(month)
                    decision = daily_decision(policy, entry_day(month))
                    if decision.r is not None:
                        settings["r_multiplier"] = str(decision.r)
                    write_month(path, settings, journal.trades(month))
                    notice = f"Exported {path}"
            elif action == "i":
                month = import_workbook(journal)
            elif action == "s":
                month = setup_month(journal, month)
            elif action == "u":
                notice = correct_trade(journal, month)
            elif action == "d":
                trade_details(journal, month)
            elif action == "v":
                print("\n" + month_view(journal, month, all_rows=True, policy=policy))
                input("Enter to return: ")
            elif action == "p":
                print("\n" + planning_view(policy, month))
                input("Enter to return: ")
            elif action == "z":
                trade = choose_trade(journal, month)
                if confirm(f"Remove trade #{trade['id']} {trade['ticker']}?"):
                    journal.delete_trade(trade["id"])
                    notice = "Trade removed."
            else:
                notice = "More: p Plan month · s Month setup · i Import workbook · u Correct entry · d Details · z Remove trade"
        except (ValueError, OSError, sqlite3.Error) as error:
            notice = str(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Your saved monthly trading journal")
    parser.add_argument("--data-dir", type=Path, help="journal folder (default: journal/data)")
    parser.add_argument("--month", help="month to open, e.g. 2026-10")
    parser.add_argument("--import", dest="workbook", type=Path, help="import a month from an xlsx workbook")
    parser.add_argument("--sheet", help="worksheet to import, e.g. Oct 2026")
    parser.add_argument("--show", action="store_true", help="print the month and exit")
    parser.add_argument("--risk-home", type=Path, help="folder containing config/risk-rules.csv and config/trading-calendar.csv")
    parser.add_argument("--plan", action="store_true", help="show the month's expiry and R planning table")
    parser.add_argument("--calendar-template", metavar="YYYY-MM", help="print a blank calendar CSV for manual or AI-assisted entry")
    args = parser.parse_args(argv)
    try:
        if args.calendar_template:
            calendar_template(args.calendar_template)
            return 0
        state_path = os.environ.get("TRADING_RISK_STATE_PATH")
        if state_path is None and args.data_dir is not None:
            state_path = args.data_dir / "daily-risk.sqlite3"
        policy = RiskPolicy(root=args.risk_home, state_path=state_path)
        if args.plan:
            print(planning_view(policy, args.month or today()[:7]))
            return 0
        args.data_dir = args.data_dir or ROOT / "data"
        with Journal(args.data_dir / "journal.sqlite3") as journal:
            return run(journal, args, policy)
    except (EOFError, KeyboardInterrupt):
        print("\nSaved changes are kept. See you.")
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"Could not open the journal: {error}", file=sys.stderr)
        return 1
