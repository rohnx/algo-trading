"""CSV-driven daily policy and independently persisted daily choices."""

import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, getcontext, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from daily_risk import RiskPolicy


RULES = {
    "non_expiry": (("0.8", "1.0"), ("0.7", "0.9"), ("0.2", "0.6")),
    "sensex_weekly": (("0.4", "0.6"), ("0.3", "0.5"), ("0.1", "0.3")),
    "nifty_weekly": (("0.1", "0.5"), ("0.1", "0.3"), ("0", "0.2")),
    "sensex_monthly": (("0.1", "0.5"), ("0.1", "0.3"), ("0", "0.1")),
    "nifty_monthly": (("0.1", "0.3"), ("0.1", "0.2"), ("0", "0.1")),
}
BANDS = ("before_1015", "at_1015", "after_1015")


class DailyRiskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        self.rules = self.root / "config/risk-rules.csv"
        with self.rules.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("expiry_state", "timing_band", "r_min", "r_max"))
            for state, ranges in RULES.items():
                for band, (low, high) in zip(BANDS, ranges):
                    writer.writerow((state, band, low, high))
        self.calendar = self.root / "config/trading-calendar.csv"
        self.write_calendar([
            (f"2026-11-{day:02}", "yes", state, "test fixture")
            for day, state in enumerate(RULES, 2)
        ] + [("2026-11-07", "no", "", "test fixture"),
             ("2026-11-08", "", "", "")])
        self.policy = RiskPolicy(self.root)

    def write_calendar(self, rows):
        with self.calendar.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("date", "trading_day", "expiry_state", "source"))
            writer.writerows(rows)

    def test_expiry_only_uses_highest_r_for_each_expiry_state(self):
        for day, state in enumerate(RULES, 2):
            with self.subTest(state=state):
                decision = self.policy.resolve(f"2026-11-{day:02}")
                self.assertEqual(decision.r, max(Decimal(high) for low, high in RULES[state]))
                self.assertEqual(decision.expiry_state, state)
                self.assertEqual(decision.source, "expiry")
                self.assertIsNone(decision.timing_band)

    def test_exact_1015_boundary_and_upper_limit_ranges(self):
        for clock, band, expected in (("10:14:59", "before_1015", "0.5"),
                                      ("10:15:00", "at_1015", "0.3"),
                                      ("10:15:01", "after_1015", "0.2")):
            with self.subTest(clock=clock):
                policy = RiskPolicy(self.root, self.root / f"{band}.sqlite3")
                decision = policy.record_first_orb(datetime.fromisoformat(f"2026-11-04T{clock}+05:30"))
                self.assertEqual(decision.timing_band, band)
                self.assertEqual(decision.r, Decimal(expected))
                self.assertEqual(decision.source, "orb")
                if band == "after_1015":
                    self.assertEqual(decision.r_min, Decimal("0"))

    def test_manual_overrides_warn_outside_inclusive_range_without_blocking(self):
        self.policy.record_first_orb(datetime.fromisoformat("2026-11-03T10:05:00+05:30"))
        for value, warning in (("0.39", True), ("0.7", True), ("0.4", False), ("0.6", False)):
            with self.subTest(value=value):
                decision = self.policy.set_override("2026-11-03", value)
                self.assertEqual(decision.r, Decimal(value))
                self.assertEqual(decision.source, "manual")
                self.assertEqual(bool(decision.warning), warning)
        self.assertEqual(self.policy.set_override("2026-11-03", None).r, Decimal("0.6"))

    def test_first_signal_survives_restarts_and_later_signals(self):
        early = datetime.fromisoformat("2026-11-03T10:05:00+05:30")
        self.policy.record_first_orb(early)
        restarted = RiskPolicy(self.root)
        restarted.record_first_orb(datetime.fromisoformat("2026-11-03T11:00:00+05:30"))
        restarted.record_first_orb(early)
        self.assertEqual(restarted.resolve("2026-11-03").first_orb_at, early.isoformat())
        self.assertEqual(restarted.resolve("2026-11-03").r, Decimal("0.6"))
        restarted.set_override("2026-11-03", "0")
        self.assertEqual(self.policy.resolve("2026-11-03").r, Decimal("0"))
        self.assertEqual(self.policy.resolve("2026-11-04").source, "expiry")

    def test_missing_closed_and_unclassified_dates_never_invent_auto_r(self):
        for day, source in (("2026-11-07", "closed"), ("2026-11-08", "unavailable"),
                            ("2026-12-01", "unavailable")):
            with self.subTest(day=day):
                decision = self.policy.resolve(day)
                self.assertIsNone(decision.r)
                self.assertEqual(decision.source, source)
                self.assertTrue(decision.warning)
                manual = self.policy.set_override(day, "0.4")
                self.assertEqual(manual.r, Decimal("0.4"))
                self.assertTrue(manual.warning)

    def test_csv_edits_are_used_on_the_next_resolution(self):
        self.assertEqual(self.policy.resolve("2026-11-03").r, Decimal("0.6"))
        self.rules.write_text(self.rules.read_text().replace("sensex_weekly,before_1015,0.4,0.6", "sensex_weekly,before_1015,0.4,0.65"))
        self.assertEqual(self.policy.resolve("2026-11-03").r, Decimal("0.65"))

    def test_earliest_recovered_signal_wins_across_concurrent_writers(self):
        clocks = ("11:00", "10:15", "10:05", "10:30", "10:05")
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda clock: RiskPolicy(self.root).record_first_orb(
                datetime.fromisoformat(f"2026-11-03T{clock}:00+05:30")), clocks))
        decision = self.policy.resolve("2026-11-03")
        self.assertEqual(decision.first_orb_at, "2026-11-03T10:05:00+05:30")
        self.assertEqual(decision.r, Decimal("0.6"))

    def test_utc_and_naive_timestamps_are_interpreted_in_ist(self):
        result = self.policy.record_first_orb(datetime(2026, 11, 3, 4, 35, tzinfo=timezone.utc))
        self.assertEqual(result.first_orb_at, "2026-11-03T10:05:00+05:30")
        result = self.policy.record_first_orb(datetime(2026, 11, 4, 10, 15))
        self.assertEqual(result.timing_band, "at_1015")
        for clock in ((9, 14), (15, 31)):
            with self.subTest(clock=clock), self.assertRaisesRegex(ValueError, "09:15"):
                self.policy.record_first_orb(datetime(2026, 11, 3, *clock))

    def test_month_calendar_is_sorted_and_keeps_closed_unknown_and_source(self):
        self.write_calendar([("2026-12-01", "yes", "non_expiry", "other month"),
                             ("2026-11-03", "", "", "pending"),
                             ("2026-11-01", "no", "", "manual")])
        rows = self.policy.calendar_month("2026-11")
        self.assertEqual([row.date for row in rows], ["2026-11-01", "2026-11-03"])
        self.assertEqual([row.trading_day for row in rows], [False, None])
        self.assertEqual(rows[1].source, "pending")

    def test_invalid_rule_rows_fail_with_file_and_row_context(self):
        original = self.rules.read_text()
        for replacement in ("non_expiry,before_1015,NaN,1.0", "non_expiry,before_1015,1.1,1.0",
                            "unknown,before_1015,0.8,1.0", "non_expiry,unknown,0.8,1.0"):
            with self.subTest(replacement=replacement):
                self.rules.write_text(original.replace("non_expiry,before_1015,0.8,1.0", replacement))
                with self.assertRaisesRegex(ValueError, "risk-rules.csv:2:"):
                    self.policy.resolve("2026-11-02")
        self.rules.write_text(original + "non_expiry,before_1015,0.8,1.0\n")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.policy.resolve("2026-11-02")
        self.rules.write_text("\n".join(original.splitlines()[:-1]) + "\n")
        with self.assertRaisesRegex(ValueError, "missing 1"):
            self.policy.resolve("2026-11-02")

    def test_invalid_calendar_rows_are_not_inferred_or_silently_skipped(self):
        bad_rows = [("20261102", "yes", "non_expiry", ""),
                    ("2026-11-02", "yes", "", ""),
                    ("2026-11-02", "no", "nifty_weekly", ""),
                    ("2026-11-02", "maybe", "", "")]
        for row in bad_rows:
            with self.subTest(row=row):
                self.write_calendar([row])
                with self.assertRaisesRegex(ValueError, "trading-calendar.csv:2:"):
                    self.policy.resolve("2026-11-02")
        self.write_calendar([bad_rows[1], bad_rows[1]])
        with self.assertRaises(ValueError):
            self.policy.calendar_month("2026-11")

    def test_manual_choice_remains_usable_when_csv_data_is_invalid(self):
        self.rules.write_text("bad header\n")
        with self.assertRaisesRegex(ValueError, "risk-rules.csv"):
            self.policy.resolve("2026-11-03")
        decision = self.policy.set_override("2026-11-03", "0.4")
        self.assertEqual(decision.r, Decimal("0.4"))
        self.assertEqual(decision.source, "manual")
        self.assertIn("risk-rules.csv", decision.warning)
        self.calendar.write_text("bad header\n")
        self.assertEqual(self.policy.resolve("2026-11-03").r, Decimal("0.4"))
        with self.assertRaisesRegex(ValueError, "trading-calendar.csv"):
            self.policy.resolve("2026-11-02")

    def test_invalid_manual_input_preserves_previous_value_and_decimal_context(self):
        self.policy.set_override("2026-11-03", "0.4")
        with localcontext() as context:
            context.clear_flags()
            before = str(getcontext())
            for value in ("NaN", "Infinity", "-0.1", "invalid"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.policy.set_override("2026-11-03", value)
                self.assertEqual(str(getcontext()), before)
        self.assertEqual(self.policy.resolve("2026-11-03").r, Decimal("0.4"))

    def test_seeded_rules_match_all_reference_ranges(self):
        seeded = Path(__file__).resolve().parents[1] / "config/risk-rules.csv"
        self.rules.write_text(seeded.read_text())
        for day, (state, ranges) in enumerate(RULES.items(), 2):
            for band, (clock, (lower, upper)) in enumerate(zip(("10:05", "10:15", "10:20"), ranges)):
                with self.subTest(state=state, clock=clock):
                    policy = RiskPolicy(self.root, self.root / f"seed-{day}-{band}.sqlite3")
                    decision = policy.record_first_orb(datetime.fromisoformat(f"2026-11-{day:02}T{clock}:00+05:30"))
                    self.assertEqual((decision.r_min, decision.r_max), (Decimal(lower), Decimal(upper)))

    def test_headers_duplicates_missing_files_and_bad_dates_are_explicit(self):
        for day in ("20261102", "2026-2-01", "2026-02-30"):
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                self.policy.resolve(day)
        for month in ("2026-1", "2026-13", "202611"):
            with self.subTest(month=month), self.assertRaises(ValueError):
                self.policy.calendar_month(month)
        row = ("2026-11-02", "yes", "non_expiry", "manual")
        self.write_calendar([row, row])
        with self.assertRaisesRegex(ValueError, "Duplicate calendar date"):
            self.policy.resolve("2026-11-02")
        self.calendar.unlink()
        self.assertIsNone(self.policy.resolve("2026-11-02").r)
        self.write_calendar([row])
        self.rules.unlink()
        with self.assertRaisesRegex(ValueError, "Missing risk-rules.csv"):
            self.policy.resolve("2026-11-02")


if __name__ == "__main__":
    unittest.main()
