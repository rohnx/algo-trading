"""Optional journal access for the standalone ERV terminal flow."""

from datetime import date, datetime
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo


JOURNAL_ROOT = Path(__file__).resolve().parents[1] / "journal"
sys.path.insert(0, str(JOURNAL_ROOT))

from daily_risk import RiskPolicy
from risk_tool.journal import Journal, number


def today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


def open_journal():
    path = JOURNAL_ROOT / "data/journal.sqlite3"
    if not path.is_file():
        raise ValueError("Journal not set up; run uv run journal first")
    return Journal(path)


def current_risk(style):
    """Return the current cash-risk and R snapshots, without saving a trade."""
    day = today()
    try:
        with open_journal() as journal:
            month = day[:7]
            journal.settings(month)
            multiplier = "1"
            if style == "intraday":
                decision = RiskPolicy().resolve(day)
                if decision.r is None:
                    raise ValueError(decision.warning or f"Daily R unavailable for {day}")
                if decision.warning:
                    print(decision.warning)
                multiplier = str(decision.r)
            quote = journal.quote(month, multiplier)
            risk = quote.intraday_risk if style == "intraday" else quote.swing_risk_per_position
            if risk <= 0:
                raise ValueError("Journal risk must be greater than zero")
            print(f"Journal {style} risk for {day}: {risk:.2f}")
            return str(risk), multiplier
    except (ValueError, OSError, sqlite3.Error) as error:
        raise ValueError(f"Journal risk unavailable: {error}. Supply a numeric risk or update uv run journal.") from None


def save_entry(record):
    """Save this calculation as OPEN after the user explicitly opts in."""
    try:
        ticker = input("Ticker: ").strip()
        if not ticker:
            raise ValueError("Ticker is required")
        day = today()
        trade_date = date.fromisoformat(input(f"Entry date [{day}]: ").strip() or day).isoformat()
        month = trade_date[:7]
        with open_journal() as journal:
            settings = journal.settings(month)
            multiplier = record["r_multiplier"]
            if record["style"] == "intraday" and multiplier is None:
                default = settings["r_multiplier"]
                try:
                    decision = RiskPolicy().resolve(trade_date)
                    if decision.r is not None:
                        default = str(decision.r)
                    if decision.warning:
                        print(decision.warning)
                except (ValueError, OSError, sqlite3.Error) as error:
                    print(f"Daily R unavailable: {error}")
                multiplier = str(number(input(f"R at entry [{default}]: ").strip() or default,
                                        "R", nonnegative=True))
            saved = journal.add_trade(month, record | {
                "date": trade_date, "ticker": ticker, "status": "open",
                "r_multiplier": multiplier if multiplier is not None else "1",
            })
        print(f"Saved trade #{saved['id']} as OPEN · exit limit {saved['exit_price']}.")
    except EOFError:
        print("\nJournal entry skipped.")
    except (ValueError, OSError, sqlite3.Error) as error:
        raise SystemExit(f"Could not save journal entry: {error}") from None
