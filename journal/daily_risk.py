"""Editable daily risk policy, independent of the journal and ORB processes."""

import csv
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import os
from pathlib import Path
import sqlite3


IST = timezone(timedelta(hours=5, minutes=30))
STATE_LABELS = {
    "non_expiry": "Non-expiry",
    "sensex_weekly": "Sensex weekly",
    "nifty_weekly": "NIFTY weekly",
    "sensex_monthly": "Sensex monthly",
    "nifty_monthly": "NIFTY monthly",
}
TIMING_LABELS = {
    "before_1015": "Before 10:15",
    "at_1015": "At 10:15",
    "after_1015": "After 10:15",
}
STATES = tuple(STATE_LABELS)
BANDS = tuple(TIMING_LABELS)


@dataclass(frozen=True)
class CalendarDay:
    date: str
    trading_day: bool | None
    expiry_state: str | None
    source: str


@dataclass(frozen=True)
class Decision:
    date: str
    expiry_state: str | None
    timing_band: str | None
    r_min: Decimal | None
    r_max: Decimal | None
    r: Decimal | None
    source: str
    first_orb_at: str | None
    warning: str | None


def _day(value):
    text = str(value)
    try:
        if date.fromisoformat(text).isoformat() == text:
            return text
    except (ValueError, TypeError):
        pass
    raise ValueError("Date must be YYYY-MM-DD") from None


def _number(value):
    try:
        with localcontext():
            result = Decimal(str(value).strip())
        if result.is_finite() and result >= 0:
            return result
    except (InvalidOperation, ValueError):
        pass
    raise ValueError("R must be a finite nonnegative number") from None


def _rows(path, columns, *, optional=False):
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if (reader.fieldnames is None or len(reader.fieldnames) != len(columns)
                    or set(reader.fieldnames) != set(columns)):
                raise ValueError(f"{path.name}: expected columns {','.join(columns)}")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"{path.name}:{reader.line_num}: wrong number of columns")
                yield reader.line_num, {key: value.strip() for key, value in row.items()}
    except FileNotFoundError:
        if not optional:
            raise ValueError(f"Missing {path.name}") from None
    except (OSError, csv.Error) as error:
        raise ValueError(f"Cannot read {path.name}: {error}") from None


class RiskPolicy:
    def __init__(self, root=None, state_path=None):
        self.root = Path(root if root is not None else
                         os.environ.get("TRADING_RISK_HOME", Path(__file__).resolve().parent)).expanduser()
        self.state_path = Path(state_path if state_path is not None else
                               os.environ.get("TRADING_RISK_STATE_PATH", self.root / "data/daily-risk.sqlite3")).expanduser()

    def _rules(self):
        path = self.root / "config/risk-rules.csv"
        result = {}
        for line, row in _rows(path, ("expiry_state", "timing_band", "r_min", "r_max")):
            key = row["expiry_state"], row["timing_band"]
            try:
                if key[0] not in STATES or key[1] not in BANDS:
                    raise ValueError("Unknown expiry state or timing band")
                if key in result:
                    raise ValueError("Duplicate expiry/timing rule")
                lower, upper = _number(row["r_min"]), _number(row["r_max"])
                if lower > upper:
                    raise ValueError("r_min must not exceed r_max")
                result[key] = lower, upper
            except ValueError as error:
                raise ValueError(f"{path.name}:{line}: {error}") from None
        missing = {(state, band) for state in STATES for band in BANDS} - result.keys()
        if missing:
            raise ValueError(f"{path.name}: missing {len(missing)} expiry/timing rules")
        return result

    def _calendar(self):
        path = self.root / "config/trading-calendar.csv"
        result = {}
        for line, row in _rows(path, ("date", "trading_day", "expiry_state", "source"), optional=True):
            try:
                day = _day(row["date"])
                if day in result:
                    raise ValueError("Duplicate calendar date")
                if row["trading_day"] not in ("yes", "no", ""):
                    raise ValueError("trading_day must be yes, no, or blank")
                trading = {"yes": True, "no": False, "": None}[row["trading_day"]]
                state = row["expiry_state"] or None
                if trading is True and state not in STATES:
                    raise ValueError("Trading date needs a known expiry_state")
                if trading is not True and state is not None:
                    raise ValueError("Closed or unclassified date cannot have an expiry_state")
                result[day] = CalendarDay(day, trading, state, row["source"])
            except ValueError as error:
                raise ValueError(f"{path.name}:{line}: {error}") from None
        return result

    def calendar_month(self, month):
        _day(str(month) + "-01")
        return sorted((row for row in self._calendar().values() if row.date.startswith(month + "-")),
                      key=lambda row: row.date)

    @contextmanager
    def _database(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.state_path, timeout=3)
        try:
            with connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS daily_risk (
                    date TEXT PRIMARY KEY, first_orb_at TEXT, manual_r TEXT)""")
                yield connection
        finally:
            connection.close()

    def _saved(self, day):
        if not self.state_path.exists():
            return None, None
        with self._database() as connection:
            row = connection.execute("SELECT first_orb_at, manual_r FROM daily_risk WHERE date = ?", (day,)).fetchone()
        return row if row else (None, None)

    def _automatic(self, day, first_orb_at):
        calendar_day = self._calendar().get(day)
        state = calendar_day.expiry_state if calendar_day else None
        lower = upper = band = None
        if calendar_day is None or calendar_day.trading_day is None:
            source, warning = "unavailable", f"Calendar not classified for {day}; enter R manually."
        elif calendar_day.trading_day is False:
            source, warning = "closed", f"Calendar marks {day} closed; check the date before trading."
        else:
            rules = self._rules()
            if first_orb_at:
                clock = datetime.fromisoformat(first_orb_at).astimezone(IST).time()
                boundary = time(10, 15)
                band = "before_1015" if clock < boundary else "after_1015" if clock > boundary else "at_1015"
                lower, upper = rules[state, band]
                source = "orb"
            else:
                ranges = [rules[state, item] for item in BANDS]
                lower, upper = min(low for low, high in ranges), max(high for low, high in ranges)
                source = "expiry"
            warning = None
        return Decision(day, state, band, lower, upper, upper, source, first_orb_at, warning)

    def resolve(self, day):
        day = _day(day)
        first_orb_at, manual = self._saved(day)
        try:
            decision = self._automatic(day, first_orb_at)
        except ValueError as error:
            if manual is None:
                raise
            return Decision(day, None, None, None, None, _number(manual), "manual", first_orb_at,
                            f"Risk data unavailable: {error}. Using manual R.")
        if manual is not None:
            value, warning = _number(manual), decision.warning
            if decision.r_min is not None and not decision.r_min <= value <= decision.r_max:
                warning = f"R {value} is outside the day's range {decision.r_min}–{decision.r_max}."
            return replace(decision, r=value, source="manual", warning=warning)
        return decision

    def set_override(self, day, value):
        day = _day(day)
        amount = None if value is None else str(_number(value))
        with self._database() as connection:
            connection.execute("""INSERT INTO daily_risk (date, manual_r) VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET manual_r = excluded.manual_r""", (day, amount))
        return self.resolve(day)

    def record_first_orb(self, timestamp):
        """Record the earliest main signal, treating a naive datetime as IST."""
        if not isinstance(timestamp, datetime):
            raise ValueError("ORB timestamp must be a datetime")
        instant = timestamp.replace(tzinfo=IST) if timestamp.tzinfo is None else timestamp.astimezone(IST)
        if not time(9, 15) <= instant.time() <= time(15, 30):
            raise ValueError("ORB timestamp must be within 09:15–15:30 IST")
        day = instant.date().isoformat()
        with self._database() as connection:
            connection.execute("""INSERT INTO daily_risk (date, first_orb_at) VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET first_orb_at = CASE
                    WHEN daily_risk.first_orb_at IS NULL OR excluded.first_orb_at < daily_risk.first_orb_at
                    THEN excluded.first_orb_at ELSE daily_risk.first_orb_at END""", (day, instant.isoformat()))
        return self.resolve(day)
