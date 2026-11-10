"""Reuse erv.py in a fresh worker, including its current charge configuration."""

import ast
import builtins
from contextlib import redirect_stdout
from decimal import Decimal, InvalidOperation
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys


_ERV = Path(__file__).resolve().parents[2] / "erv" / "erv.py"


def _number(value, field, *, allow_zero=False):
    try:
        number = Decimal(str(value))
        converted = float(number)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise ValueError(f"{field} must be a finite positive number") from None
    if not number.is_finite() or not math.isfinite(converted):
        raise ValueError(f"{field} must be a finite positive number")
    if converted < 0 or (not allow_zero and converted == 0):
        raise ValueError(f"{field} must be greater than zero")
    return str(number)


def calculate_trade(
    entry: str, risk: str, atr: str, style: str = "intraday", direction: str = "long",
    exchange: str = "nse", exit_price: str | None = None, quantity: int | None = None,
) -> dict:
    """Return sizing/price levels, and charges/net P&L for an optional exit limit.

    A supplied quantity is an existing holding; it is preserved on exit edits.
    No fees or P&L formulas are maintained separately from erv.py.
    """
    if style not in ("intraday", "delivery"):
        raise ValueError("style must be intraday or delivery")
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    if style == "delivery" and direction == "short":
        raise ValueError("delivery short positions are not supported")
    if exchange not in ("nse", "bse"):
        raise ValueError("exchange must be nse or bse")
    if quantity is not None and (not isinstance(quantity, int)
                                 or isinstance(quantity, bool) or quantity <= 0):
        raise ValueError("quantity must be a positive integer")
    request = {
        "entry": _number(entry, "entry"),
        "risk": _number(risk, "risk", allow_zero=quantity is not None),
        "atr": _number(atr, "atr"), "style": style, "direction": direction,
        "exchange": exchange,
        "exit_price": None if exit_price is None else _number(exit_price, "exit_price"),
        "quantity": quantity,
    }
    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            input=json.dumps(request), text=True, capture_output=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Calculator took too long; try again") from None
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError:
        raise ValueError("Could not read the result from erv.py") from None
    if process.returncode or "error" in result:
        raise ValueError(result.get("error", "Could not calculate this trade"))
    return result


def _run_original(request):
    tree = ast.parse(_ERV.read_text(), filename=str(_ERV))
    split = next((index + 1 for index, statement in enumerate(tree.body)
                  if isinstance(statement, ast.FunctionDef)
                  and statement.name == "position_size"), None)
    if split is None:
        raise ValueError("erv.py no longer exposes position_size")

    # The original top-level prefix loads rates/arguments and defines sizing.
    # The original branches then own every price, fee, and P&L calculation.
    answers = iter(("y", request["exit_price"] or request["entry"]))
    namespace = {
        "__file__": str(_ERV), "__name__": "_journal_erv",
        "__builtins__": vars(builtins) | {"input": lambda _prompt="": next(answers)},
    }
    sys.argv = [str(_ERV), request["entry"], request["risk"], request["atr"],
                request["style"], request["direction"], request["exchange"]]
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            exec(compile(ast.Module(tree.body[:split], []), str(_ERV), "exec"), namespace)
            if request["quantity"] is not None:
                namespace["position_size"] = lambda *_args: request["quantity"]
            exec(compile(ast.Module(tree.body[split:], []), str(_ERV), "exec"), namespace)
    except SystemExit:
        raise ValueError("risk is too small for one share at this ATR") from None

    quantity = int(namespace["size"])
    if quantity <= 0:
        raise ValueError("risk is too small for one share at this ATR")
    rounder = namespace["round_down" if request["style"] == "intraday"
                        and request["direction"] == "short" else "round_up"]
    result = {
        "quantity": quantity,
        "stop_loss": str(rounder(namespace["stoploss"], 2)),
        "breakeven": str(rounder(namespace["breakeven"], 2)),
        "take_profit": str(rounder(namespace["takeprofit"], 2)),
        "charges": None, "pnl": None, "exit_price": request["exit_price"],
    }
    if request["exit_price"] is not None:
        match = re.search(r"^Total charges:\s*(.+)$", output.getvalue(), re.MULTILINE)
        if match is None:
            raise ValueError("erv.py did not return total charges")
        result.update(charges=match.group(1).strip(), pnl=str(namespace["pnl"]))
    return result


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Use calculate_trade from the trading journal")
    try:
        print(json.dumps(_run_original(json.load(sys.stdin))))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
