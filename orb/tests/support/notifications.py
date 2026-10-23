"""Synthetic alerts, responses, and clock for durable-delivery contracts."""

from datetime import datetime, timedelta
import time
from unittest.mock import Mock

from orb.signals import IST

class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 13, 10, 5, 1, tzinfo=IST)

    def __call__(self):
        return self.value


def signal(direction="BULLISH", *, timestamp=None):
    timestamp = timestamp or datetime(2026, 10, 13, 10, 5, tzinfo=IST)
    return {"symbol": "^NSEI", "setup_id": "dual_orb", "strategy": "dual_ORB",
            "direction": direction, "signal_type": f"DUAL_ORB_{direction}",
            "timestamp": timestamp.isoformat(), "price": 22550 if direction == "BULLISH" else 22400,
            "confirmations": {
                "15": {"range_minutes": 30, "confirmation_minutes": 15,
                       "confirmed_at": (timestamp - timedelta(minutes=5)).isoformat(),
                       "confirmation_price": 22545 if direction == "BULLISH" else 22405,
                       "orb_high": 22540, "orb_low": 22410},
                "5": {"range_minutes": 45, "confirmation_minutes": 5,
                      "confirmed_at": timestamp.isoformat(),
                      "confirmation_price": 22550 if direction == "BULLISH" else 22400,
                      "orb_high": 22548, "orb_low": 22408}}}


def response(code=200, **body):
    return Mock(status_code=code, json=Mock(return_value=body or {"ok": True, "result": {"message_id": 10}}))


def wait_for(check, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.005)
    raise AssertionError("Telegram worker did not reach expected state")
