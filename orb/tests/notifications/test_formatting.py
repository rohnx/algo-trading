"""Alert formatting validates the dual confirmation and converts timestamps."""

from datetime import datetime, timezone
import unittest

from orb.notifications import format_signal
from orb.signals import IST

from orb.tests.support.notifications import signal


class FormattingTests(unittest.TestCase):
    def test_message_has_second_confirmation_and_both_setups(self):
        key, timestamp, message = format_signal(signal(), "shoonya")
        self.assertEqual(key, "2026-10-13:NIFTY:BULLISH")
        self.assertEqual(timestamp, datetime(2026, 10, 13, 10, 5, tzinfo=IST))
        for expected in ("Both ORBs BULLISH", "10:05:00 IST", "Provider: Shoonya", "22,550.00",
                         "15m close / 30m ORB", "5m close / 45m ORB", "10:00:00 IST", "22,408.00"):
            self.assertIn(expected, message)

    def test_bearish_levels_and_utc_convert_to_ist(self):
        data = signal("BEARISH")
        data["timestamp"] = datetime.fromisoformat(data["timestamp"]).astimezone(timezone.utc).isoformat()
        _, _, message = format_signal(data, "yahoo")
        self.assertIn("22,405.00 below 22,410.00", message)
        self.assertIn("22,400.00 below 22,408.00", message)
        self.assertIn("13 Oct 2026 10:05:00 IST", message)

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
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                format_signal(data, "shoonya")
