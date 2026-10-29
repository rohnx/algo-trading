"""Read-only Yahoo availability probe. Run with the workspace Python environment.

No account credentials are loaded. Market-data calls are bounded to 10 seconds
per HTTP request. Actual option endpoint responses are observed through the
installed yfinance adapter, including status and sanitized structural metadata.
"""

import argparse
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yfinance as yf
from yfinance.data import YfData


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True, help="Write a new sanitized JSON observation here.")
OUT = parser.parse_args().output
result = {
    "queried_at_utc": datetime.now(timezone.utc).isoformat(),
    "queried_at_ist": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    "yfinance_version": yf.__version__,
    "request_timeout_seconds": 10,
    "option_http_responses": [],
    "symbols": {},
}
original_get = YfData.get


def error(exc):
    return {"type": type(exc).__name__, "message": str(exc)[:350]}


@wraps(original_get)
def observed_get(self, url, params=None, timeout=10):
    is_options = "/finance/options/" in url
    try:
        response = original_get(self, url=url, params=params, timeout=10)
    except Exception as exc:
        if is_options:
            result["option_http_responses"].append({
                "endpoint": urlsplit(url).path,
                "error": error(exc),
            })
        raise
    if is_options:
        entry = {"endpoint": urlsplit(url).path, "http_status": response.status_code}
        try:
            payload = response.json()
            chain = payload.get("optionChain", {})
            rows = chain.get("result") or []
            entry.update({"api_error": chain.get("error"), "result_count": len(rows)})
            if rows:
                row = rows[0]
                entry["expiration_count"] = len(row.get("expirationDates", []))
                entry["expirations_utc"] = [
                    datetime.fromtimestamp(ts, timezone.utc).isoformat()
                    for ts in row.get("expirationDates", [])
                ]
                contracts = row.get("options") or []
                entry["option_expiry_groups"] = len(contracts)
                entry["call_count"] = sum(len(group.get("calls", [])) for group in contracts)
                entry["put_count"] = sum(len(group.get("puts", [])) for group in contracts)
                entry["contract_fields"] = sorted({
                    key for group in contracts for side in ("calls", "puts")
                    for contract in group.get(side, []) for key in contract
                })
                quote = row.get("quote") or {}
                entry["underlying_market_time"] = quote.get("regularMarketTime")
                if quote.get("regularMarketTime"):
                    entry["underlying_market_time_ist"] = datetime.fromtimestamp(
                        quote["regularMarketTime"], ZoneInfo("Asia/Kolkata")
                    ).isoformat()
                entry["underlying_symbol"] = quote.get("symbol")
            else:
                entry["top_level_keys"] = sorted(payload)
        except Exception as exc:
            entry["json_error"] = error(exc)
        result["option_http_responses"].append(entry)
    return response


YfData.get = observed_get

for symbol in ("^NSEI", "^BSESN", "SPY"):
    info = result["symbols"][symbol] = {}
    ticker = yf.Ticker(symbol)
    try:
        expiries = ticker.options
        info["options_property"] = {"expiry_count": len(expiries), "expiries": list(expiries)}
        if expiries:
            chain = ticker.option_chain(expiries[0])
            info["nearest_option_chain"] = {}
            for label in ("calls", "puts"):
                frame = getattr(chain, label)
                item = {"rows": len(frame), "fields": list(frame.columns)}
                for column in ("openInterest", "volume"):
                    if column in frame:
                        item[column + "_non_null"] = int(frame[column].notna().sum())
                        item[column + "_sum"] = float(frame[column].sum())
                if "lastTradeDate" in frame and not frame.empty:
                    item["lastTradeDate_min"] = str(frame["lastTradeDate"].min())
                    item["lastTradeDate_max"] = str(frame["lastTradeDate"].max())
                info["nearest_option_chain"][label] = item
        else:
            chain = ticker.option_chain()
            info["nearest_option_chain"] = {
                "calls": None if chain.calls is None else len(chain.calls),
                "puts": None if chain.puts is None else len(chain.puts),
                "underlying": chain.underlying,
            }
    except Exception as exc:
        info["options_error"] = error(exc)
    OUT.write_text(json.dumps(result, indent=2) + "\n")

try:
    frame = yf.Ticker("^NSEI").history(period="5d", interval="5m", timeout=10)
    result["nifty_5m_history"] = {
        "rows": len(frame),
        "fields": list(frame.columns),
        "first_timestamp": str(frame.index.min()) if not frame.empty else None,
        "last_timestamp": str(frame.index.max()) if not frame.empty else None,
    }
except Exception as exc:
    result["nifty_5m_history"] = {"error": error(exc)}

result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
OUT.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
