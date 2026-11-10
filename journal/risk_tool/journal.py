"""Local monthly journal; stored trade inputs survive changes to future risk."""

from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3

from .calculator import calculate_trade
from .risk import MonthlyRiskSettings, accum_from_outcomes, calculate_risk, estimate_opening_accum


def number(value, field, *, positive=False, nonnegative=False):
    try:
        amount = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a number") from None
    if not amount.is_finite() or (positive and amount <= 0) or (nonnegative and amount < 0):
        raise ValueError(f"{field} must be {'positive' if positive else 'nonnegative' if nonnegative else 'finite'}")
    return amount


def _trade_payload(record):
    payload = {key: record.get(key) for key in (
        "date", "ticker", "style", "entry", "direction", "atr", "r_multiplier",
        "risk", "quantity", "status", "exit_price", "charges", "pnl", "exchange",
    )}
    try:
        payload["date"] = date.fromisoformat(str(payload["date"])).isoformat()
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD") from None
    payload["ticker"] = str(payload["ticker"] or "").strip()
    if not payload["ticker"]:
        raise ValueError("ticker is required")
    if payload["style"] not in ("intraday", "delivery"):
        raise ValueError("style must be intraday or delivery")
    if payload["direction"] not in ("long", "short"):
        raise ValueError("direction must be long or short")
    if payload["style"] == "delivery" and payload["direction"] == "short":
        raise ValueError("delivery supports long positions only")
    if payload["status"] not in ("open", "closed"):
        raise ValueError("status must be open or closed")
    quantity = payload["quantity"]
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    payload["exchange"] = payload["exchange"] or "nse"
    if payload["exchange"] not in ("nse", "bse"):
        raise ValueError("exchange must be nse or bse")
    for field in ("entry", "atr"):
        payload[field] = str(number(payload[field], field, positive=True))
    for field in ("risk", "r_multiplier"):
        payload[field] = str(number(payload[field] if payload[field] is not None else "1", field, nonnegative=True))
    for field in ("exit_price", "charges", "pnl"):
        value = payload[field]
        payload[field] = None if value is None or value == "" else str(number(
            value, field, positive=field == "exit_price", nonnegative=field == "charges"))
    if payload["status"] == "closed" and payload["exit_price"] is None:
        raise ValueError("a closed trade needs an exit price")
    return payload


def _pricing_values(record):
    numeric = {"entry", "atr", "risk", "exit_price"}
    return tuple(Decimal(record[field]) if field in numeric and record[field] is not None else record[field]
                 for field in ("entry", "atr", "risk", "exit_price", "quantity", "style", "direction", "exchange"))


class Journal:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS months (month TEXT PRIMARY KEY, settings TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY, month TEXT NOT NULL REFERENCES months(month), payload TEXT NOT NULL
            );
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    def settings(self, month):
        row = self.db.execute("SELECT settings FROM months WHERE month = ?", (month,)).fetchone()
        if row is None:
            raise ValueError(f"Month {month} has not been set up")
        return json.loads(row["settings"])

    def months(self):
        return [json.loads(row["settings"]) for row in self.db.execute(
            "SELECT settings FROM months ORDER BY month DESC")]

    def _month_payload(self, month, intraday, swing, slots, r_multiplier="1", adjustments=None):
        settings = MonthlyRiskSettings(month, number(intraday, "intraday allocation", nonnegative=True),
                                       number(swing, "swing allocation", nonnegative=True), slots)
        multiplier = number(r_multiplier, "R", nonnegative=True)
        return {"month": settings.month, "intraday_allocation": str(settings.intraday_allocation),
                "swing_allocation": str(settings.swing_allocation), "swing_slots": settings.swing_slots,
                "r_multiplier": str(multiplier), "intraday_adjustments": adjustments or []}

    def _write_month(self, payload):
        self.db.execute("INSERT INTO months VALUES (?, ?) ON CONFLICT(month) DO UPDATE SET settings=excluded.settings",
                        (payload["month"], json.dumps(payload)))

    def save_month(self, month, intraday_allocation, swing_allocation, swing_slots, r_multiplier=None):
        old = next((item for item in self.months() if item["month"] == month), {})
        payload = self._month_payload(month, intraday_allocation, swing_allocation, swing_slots,
                                      r_multiplier if r_multiplier is not None else old.get("r_multiplier", "1"),
                                      old.get("intraday_adjustments", []))
        with self.db:
            self._write_month(payload)
        return payload

    def set_r(self, month, multiplier):
        settings = self.settings(month)
        settings["r_multiplier"] = str(number(multiplier, "R", nonnegative=True))
        with self.db:
            self._write_month(settings)

    def quote(self, month, r_multiplier=None):
        saved = self.settings(month)
        settings = MonthlyRiskSettings(month, Decimal(saved["intraday_allocation"]),
                                       Decimal(saved["swing_allocation"]), saved["swing_slots"])
        outcomes = [Decimal(row["pnl"]) for row in self.trades(month)
                    if row["style"] == "intraday" and row["pnl"] is not None]
        outcomes.extend(Decimal(row["amount"]) for row in saved["intraday_adjustments"])
        accum = accum_from_outcomes(estimate_opening_accum(settings.intraday_allocation), outcomes)
        return calculate_risk(settings, current_accum=accum, intraday_r_multiplier=number(
            saved["r_multiplier"] if r_multiplier is None else r_multiplier, "R", nonnegative=True))

    def trades(self, month):
        return [{"id": row["id"], "month": row["month"], **json.loads(row["payload"])}
                for row in self.db.execute("SELECT * FROM trades WHERE month = ? ORDER BY id", (month,))]

    def get_trade(self, trade_id):
        row = self.db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"Trade #{trade_id} does not exist")
        return {"id": row["id"], "month": row["month"], **json.loads(row["payload"])}

    def add_trade(self, month, record):
        self.settings(month)
        payload = _trade_payload(record)
        with self.db:
            cursor = self.db.execute("INSERT INTO trades(month,payload) VALUES (?,?)", (month, json.dumps(payload)))
        return self.get_trade(cursor.lastrowid)

    def update_trade(self, trade_id, changes, *, recalculate=False):
        old = self.get_trade(trade_id)
        payload = _trade_payload(old | changes)
        if recalculate or _pricing_values(payload) != _pricing_values(old):
            if payload["exit_price"] is not None:
                result = calculate_trade(payload["entry"], payload["risk"], payload["atr"],
                                         payload["style"], payload["direction"], payload["exchange"],
                                         exit_price=payload["exit_price"], quantity=payload["quantity"])
                payload.update(charges=result["charges"], pnl=result["pnl"])
            else:
                payload.update(charges=None, pnl=None)
        with self.db:
            self.db.execute("UPDATE trades SET payload=? WHERE id=?", (json.dumps(payload), trade_id))
        return self.get_trade(trade_id)

    def update_exit(self, trade_id, exit_price, *, close=False):
        changes = {"exit_price": exit_price}
        if close:
            changes["status"] = "closed"
        return self.update_trade(trade_id, changes, recalculate=True)

    def delete_trade(self, trade_id):
        self.get_trade(trade_id)
        with self.db:
            self.db.execute("DELETE FROM trades WHERE id=?", (trade_id,))

    def import_month(self, data):
        month = data["month"]
        old = next((item for item in self.months() if item["month"] == month), None)
        if old and (self.trades(month) or old.get("intraday_adjustments")):
            raise ValueError(f"{month} already has journal entries; existing data was left intact")
        adjustments = [{"date": date.fromisoformat(item["date"]).isoformat(),
                        "amount": str(number(item["amount"], "adjustment")),
                        "label": str(item.get("label", "Imported adjustment")),
                        "status": item.get("status", "open")}
                       for item in data.get("intraday_adjustments", [])]
        payload = self._month_payload(month, data["intraday_allocation"], data["swing_allocation"],
                                      data["swing_slots"], adjustments=adjustments)
        records = [_trade_payload(record) for record in data["trades"]]
        with self.db:
            self._write_month(payload)
            self.db.executemany("INSERT INTO trades(month,payload) VALUES (?,?)",
                                [(month, json.dumps(record)) for record in records])
        return month
