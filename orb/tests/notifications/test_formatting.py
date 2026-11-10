"""Alert formatting validates the dual confirmation and converts timestamps."""

from datetime import datetime, timezone
import unittest

from orb.notifications import format_signal
from orb.signals import IST

from orb.tests.support.notifications import signal


class FormattingTests(unittest.TestCase):
    def test_message_has_second_confirmation_and_both_setups(self):
        data = signal()
        data.update(daily_r="0.6", daily_r_source="orb")
        key, timestamp, message = format_signal(data, "shoonya")
        self.assertEqual(key, "2026-10-13:NIFTY:BULLISH")
        self.assertEqual(timestamp, datetime(2026, 10, 13, 10, 5, tzinfo=IST))
        self.assertEqual(message, "\n".join([
            "BULLISH | NIFTY 50",
            "Both ORBs Confirmed @10:05:00 IST",
            "Provider: Shoonya",
            "---",
            "R Multiple: 0.6",
            "---",
            "15m close / 30m ORB: Confirmed 10:00:00 IST",
            "5m close / 45m ORB: Confirmed 10:05:00 IST",
        ]))

    def test_bearish_message_and_utc_convert_to_ist(self):
        data = signal("BEARISH")
        data["timestamp"] = datetime.fromisoformat(data["timestamp"]).astimezone(timezone.utc).isoformat()
        _, _, message = format_signal(data, "yahoo")
        self.assertEqual(message.splitlines(), [
            "BEARISH | NIFTY 50",
            "Both ORBs Confirmed @10:05:00 IST",
            "Provider: Yahoo Finance",
            "---",
            "R Multiple: unavailable",
            "---",
            "15m close / 30m ORB: Confirmed 10:00:00 IST",
            "5m close / 45m ORB: Confirmed 10:05:00 IST",
        ])

    def test_r_multiple_is_numeric_for_manual_and_zero_values_and_unavailable_if_invalid(self):
        for value, expected in (("0.7", "0.7"), ("0", "0"), ("NaN", "unavailable"),
                                ("-1", "unavailable"), (None, "unavailable")):
            with self.subTest(value=value):
                data = signal()
                data.update(daily_r=value, daily_r_source="manual")
                self.assertEqual(format_signal(data, "yahoo")[2].splitlines()[4], f"R Multiple: {expected}")

    def test_invalid_and_expired_confirmations_are_rejected(self):
        cases = []
        for field, value in (("symbol", "FOCIT.BO"), ("setup_id", "orb_30m"),
                             ("price", float("nan")), ("direction", "NEUTRAL")):
            data = signal()
            data[field] = value
            cases.append(data)
        data = signal()
        data["confirmations"]["5"]["confirmed_at"] = "2026-10-13T09:55:00+05:30"
        cases.append(data)
        data = signal()
        data["confirmations"]["15"]["range_minutes"] = 45
        cases.append(data)
        for field, value in (("confirmation_price", 0), ("orb_high", float("nan")), ("orb_low", -1)):
            data = signal()
            data["confirmations"]["5"][field] = value
            cases.append(data)
        data = signal()
        data["confirmations"]["5"].update(orb_high=100, orb_low=200)
        cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                format_signal(data, "shoonya")
