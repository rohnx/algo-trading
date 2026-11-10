"""Workbook-derived risk contracts; no workbook or market data is required."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, MAX_EMAX, MIN_EMIN, ROUND_DOWN, Rounded, getcontext, localcontext
import unittest

from risk_tool.risk import (
    MonthlyRiskSettings,
    RiskQuote,
    accum_from_outcomes,
    calculate_risk,
    estimate_opening_accum,
    format_money,
)


D = Decimal


def monthly_settings(**changes):
    values = {
        "month": "2026-10",
        "intraday_allocation": D("222"),
        "swing_allocation": D("900"),
        "swing_slots": 4,
    }
    return MonthlyRiskSettings(**(values | changes))


class RiskQuoteTests(unittest.TestCase):
    def test_october_quote_exposes_inputs_and_exact_raw_components(self):
        settings = monthly_settings()
        quote = calculate_risk(
            settings, current_accum=D("3658.2715"), intraday_r_multiplier=D("1")
        )

        self.assertIsInstance(quote, RiskQuote)
        self.assertEqual(quote.settings, settings)
        self.assertEqual(quote.current_accum, D("3658.2715"))
        self.assertEqual(quote.intraday_r_multiplier, D("1"))
        self.assertEqual(quote.combined_monthly_allocation, D("1122"))
        self.assertEqual(quote.intraday_base_risk, D("73.165430"))
        self.assertEqual(quote.intraday_risk, D("73.165430"))
        self.assertEqual(quote.swing_risk_per_position, D("225"))
        self.assertEqual(estimate_opening_accum(D("222")), D("3700"))

    def test_manual_r_scales_only_intraday_and_has_no_implicit_ceiling(self):
        for multiplier, expected in (("0", "0"), ("0.4", "29.2661720"), ("2", "146.330860")):
            with self.subTest(multiplier=multiplier):
                quote = calculate_risk(
                    monthly_settings(),
                    current_accum=D("3658.2715"),
                    intraday_r_multiplier=D(multiplier),
                )
                self.assertEqual(quote.intraday_risk, D(expected))
                self.assertEqual(quote.intraday_base_risk, D("73.165430"))
                self.assertEqual(quote.swing_risk_per_position, D("225"))

    def test_swing_pool_is_shared_equally_and_does_not_change_intraday(self):
        for allocation, slots, expected in (("615", 3, "205"), ("900", 5, "180")):
            with self.subTest(allocation=allocation, slots=slots):
                quote = calculate_risk(
                    monthly_settings(swing_allocation=D(allocation), swing_slots=slots),
                    current_accum=D("3700"),
                    intraday_r_multiplier=D("0.4"),
                )
                self.assertEqual(quote.swing_risk_per_position, D(expected))
                self.assertEqual(quote.intraday_risk, D("29.6"))

    def test_new_month_uses_its_explicit_settings_without_prior_state(self):
        october = monthly_settings()
        november = monthly_settings(month="2026-11", swing_allocation=D("450"))
        previous = calculate_risk(
            october, current_accum=D("3658.2715"), intraday_r_multiplier=D("1")
        )
        opening = estimate_opening_accum(november.intraday_allocation)
        current = calculate_risk(november, current_accum=opening, intraday_r_multiplier=D("1"))

        self.assertEqual(current.combined_monthly_allocation, D("672"))
        self.assertEqual(current.current_accum, D("3700"))
        self.assertEqual(current.intraday_risk, D("74"))
        self.assertEqual(current.swing_risk_per_position, D("112.50"))
        self.assertEqual(previous.current_accum, D("3658.2715"))

    def test_signed_accum_and_zero_allocations_are_not_clamped(self):
        settings = monthly_settings(intraday_allocation=D("0"), swing_allocation=D("0"))
        self.assertEqual(estimate_opening_accum(D("0")), D("0"))
        for accum, expected in (("0", "0"), ("-100", "-2"), ("100", "2")):
            with self.subTest(accum=accum):
                quote = calculate_risk(settings, current_accum=D(accum), intraday_r_multiplier=D("1"))
                self.assertEqual(quote.intraday_risk, D(expected))
                self.assertEqual(quote.swing_risk_per_position, D("0"))
                self.assertEqual(quote.combined_monthly_allocation, D("0"))

    def test_repeated_calls_preserve_inputs_and_records_are_immutable(self):
        settings = monthly_settings()
        quote = calculate_risk(settings, current_accum=D("3700"), intraday_r_multiplier=D("1"))
        repeated = calculate_risk(settings, current_accum=D("3700"), intraday_r_multiplier=D("1"))
        self.assertEqual(quote, repeated)
        self.assertEqual(settings, monthly_settings())
        with self.assertRaises(FrozenInstanceError):
            settings.swing_slots = 5
        with self.assertRaises(FrozenInstanceError):
            quote.current_accum = D("0")

    def test_provider_uses_local_28_digit_precision_without_changing_context(self):
        with localcontext() as context:
            context.prec = 6
            context.rounding = ROUND_DOWN
            context.clear_flags()
            context.traps[Inexact] = True
            context.flags[Rounded] = True
            before = str(getcontext())
            opening = estimate_opening_accum(D("1"))
            quote = calculate_risk(
                monthly_settings(swing_allocation=D("1"), swing_slots=3),
                current_accum=D("3658.2715"),
                intraday_r_multiplier=D("0.4"),
            )
            self.assertEqual(str(getcontext()), before)
            self.assertEqual(opening, D("16.66666666666666666666666667"))
            self.assertEqual(quote.swing_risk_per_position, D("0.3333333333333333333333333333"))
            self.assertEqual(quote.intraday_risk, D("29.2661720"))

    def test_finite_amounts_outside_default_exponent_bounds_keep_their_scale(self):
        self.assertEqual(estimate_opening_accum(D("1E+1000000")),
                         D("1.666666666666666666666666667E+1000001"))
        for accum, multiplier, expected in (
            ("1E-1000100", "1", "2E-1000102"),
            ("1E+1000000", "1E+1000000", "2E+1999998"),
        ):
            with self.subTest(accum=accum):
                quote = calculate_risk(monthly_settings(), current_accum=D(accum),
                                       intraday_r_multiplier=D(multiplier))
                self.assertEqual(quote.intraday_risk, D(expected))
        self.assertEqual(accum_from_outcomes(D("1E-1000100"), [D("1E-1000100")]),
                         D("2E-1000100"))

    def test_unrepresentable_results_fail_explicitly_without_context_leaks(self):
        with localcontext() as context:
            context.clear_flags()
            before = str(getcontext())
            for accum, multiplier in (
                (D(f"1E{MIN_EMIN - 100}"), D("1")),
                (D(f"1E{MAX_EMAX}"), D(f"1E{MAX_EMAX}")),
            ):
                with self.subTest(accum=accum), self.assertRaisesRegex(ValueError, "Decimal arithmetic range"):
                    calculate_risk(monthly_settings(), current_accum=accum,
                                   intraday_r_multiplier=multiplier)
            self.assertEqual(str(getcontext()), before)


class AccumRollForwardTests(unittest.TestCase):
    def test_signed_prior_outcomes_update_each_next_quote_within_the_month(self):
        settings = monthly_settings()
        opening = estimate_opening_accum(settings.intraday_allocation)
        first = calculate_risk(settings, current_accum=opening, intraday_r_multiplier=D("1"))
        after_loss = accum_from_outcomes(opening, [D("-10")])
        after_gain = accum_from_outcomes(opening, [D("-10"), D("20")])

        self.assertEqual(after_loss, D("3690"))
        self.assertEqual(after_gain, D("3710"))
        self.assertEqual(calculate_risk(settings, current_accum=after_loss,
                                      intraday_r_multiplier=D("1")).intraday_risk, D("73.80"))
        self.assertEqual(calculate_risk(settings, current_accum=after_gain,
                                      intraday_r_multiplier=D("1")).intraday_risk, D("74.20"))
        self.assertEqual(first.current_accum, D("3700"))
        self.assertEqual(first.intraday_risk, D("74"))

    def test_caller_replaces_a_planned_outcome_instead_of_adding_each_edit(self):
        outcomes = [D("-10")]
        original = accum_from_outcomes(D("3700"), outcomes)
        outcomes[0] = D("-5")
        revised = accum_from_outcomes(D("3700"), outcomes)
        outcomes[0] = D("-7")
        completed = accum_from_outcomes(D("3700"), outcomes)

        self.assertEqual((original, revised, completed), (D("3690"), D("3695"), D("3693")))
        for accum, expected in ((revised, "73.90"), (completed, "73.86")):
            with self.subTest(accum=accum):
                quote = calculate_risk(monthly_settings(), current_accum=accum,
                                       intraday_r_multiplier=D("1"))
                self.assertEqual(quote.intraday_risk, D(expected))
                self.assertEqual(quote.swing_risk_per_position, D("225"))
        self.assertEqual(outcomes, [D("-7")])
        self.assertEqual(accum_from_outcomes(D("3700"), outcomes), completed)

    def test_empty_and_one_pass_outcomes_preserve_zero_and_signed_amounts(self):
        self.assertEqual(accum_from_outcomes(D("3700"), []), D("3700"))
        self.assertEqual(accum_from_outcomes(D("0"), (D(value) for value in ("-100", "0"))), D("-100"))
        self.assertEqual(accum_from_outcomes(D("-100"), [D("20")]), D("-80"))

    def test_roll_forward_does_not_change_the_callers_decimal_context(self):
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_DOWN
            context.clear_flags()
            before = str(getcontext())
            result = accum_from_outcomes(D("3700"), [D("0.123456789"), D("-0.023456789")])
            self.assertEqual(result, D("3700.100000000"))
            self.assertEqual(str(getcontext()), before)

    def test_roll_forward_rejects_invalid_opening_and_identifies_invalid_outcome(self):
        for value in (None, 1, "3700", D("NaN"), D("sNaN"), D("Infinity")):
            with self.subTest(opening=value), self.assertRaisesRegex(ValueError, "opening_accum"):
                accum_from_outcomes(value, [])
        for value in (None, True, 1, 1.0, "1", D("NaN"), D("sNaN"), D("Infinity")):
            with self.subTest(outcome=value), self.assertRaisesRegex(ValueError, r"intraday_net_outcomes\[1\]"):
                accum_from_outcomes(D("3700"), [D("1"), value])

    def test_roll_forward_rejects_non_iterable_outcomes_at_the_public_boundary(self):
        for value in (None, 1, D("1")):
            with self.subTest(outcomes=value), self.assertRaisesRegex(ValueError, "intraday_net_outcomes"):
                accum_from_outcomes(D("3700"), value)


class MoneyDisplayTests(unittest.TestCase):
    def test_approved_quote_amounts_display_to_paise_without_rounding_raw_values(self):
        settings = monthly_settings()
        for multiplier, raw, expected in (("1", "73.165430", "73.17"),
                                          ("0.4", "29.2661720", "29.27")):
            with self.subTest(multiplier=multiplier):
                quote = calculate_risk(settings, current_accum=D("3658.2715"),
                                       intraday_r_multiplier=D(multiplier))
                self.assertEqual(format_money(quote.intraday_risk), expected)
                self.assertEqual(quote.intraday_risk, D(raw))
        self.assertEqual(format_money(quote.swing_risk_per_position), "225.00")

    def test_display_rounds_half_up_for_both_signs_and_handles_zero_and_large_amounts(self):
        examples = (("1.005", "1.01"), ("-1.005", "-1.01"), ("0", "0.00"),
                    ("-2", "-2.00"), ("1E+30", "1000000000000000000000000000000.00"))
        for raw, expected in examples:
            with self.subTest(raw=raw):
                self.assertEqual(format_money(D(raw)), expected)

    def test_display_does_not_inherit_or_change_callers_decimal_context(self):
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            context.flags[Rounded] = True
            before = str(getcontext())
            self.assertEqual(format_money(D("73.165430")), "73.17")
            self.assertEqual(str(getcontext()), before)

    def test_display_rejects_missing_nondecimal_and_nonfinite_amounts(self):
        for value in (None, True, 1, 1.0, "1", D("NaN"), D("sNaN"), D("Infinity")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "amount"):
                format_money(value)


class RiskValidationTests(unittest.TestCase):
    def test_month_requires_exact_ascii_yyyy_mm_with_valid_year_and_month(self):
        invalid = (None, 202610, "", "2026-1", "26-10", "2026-00", "2026-13",
                   "0000-10", "10000-10", "2026-10-01", " 2026-10", "2026-10\n", "２０２６-１０")
        for value in invalid:
            with self.subTest(month=value), self.assertRaisesRegex(ValueError, "month"):
                calculate_risk(monthly_settings(month=value), current_accum=D("1"), intraday_r_multiplier=D("1"))
        for value in ("0001-01", "9999-12"):
            with self.subTest(month=value):
                settings = monthly_settings(month=value)
                self.assertEqual(settings.month, value)

    def test_monthly_allocations_must_be_nonnegative_finite_decimals(self):
        invalid = (None, True, 222, 222.0, "222", D("-1"), D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"))
        for field in ("intraday_allocation", "swing_allocation"):
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    calculate_risk(monthly_settings(**{field: value}), current_accum=D("1"), intraday_r_multiplier=D("1"))

    def test_swing_slots_are_positive_integers_excluding_boolean_and_fractional(self):
        for value in (None, True, False, 0, -1, 4.0, 2.5, "4", D("4")):
            with self.subTest(slots=value), self.assertRaisesRegex(ValueError, "swing_slots"):
                calculate_risk(monthly_settings(swing_slots=value), current_accum=D("1"), intraday_r_multiplier=D("1"))

    def test_opening_estimate_validates_its_public_allocation_input(self):
        for value in (None, True, 222, 222.0, "222", D("-1"), D("NaN"), D("sNaN"), D("Infinity")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "intraday_allocation"):
                estimate_opening_accum(value)

    def test_quote_requires_finite_decimal_accum_and_nonnegative_decimal_r(self):
        invalid = (None, True, 1, 1.0, "1", D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"))
        for field in ("current_accum", "intraday_r_multiplier"):
            values = invalid + ((D("-1"),) if field == "intraday_r_multiplier" else ())
            for value in values:
                inputs = {"current_accum": D("3700"), "intraday_r_multiplier": D("1"), field: value}
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    calculate_risk(monthly_settings(), **inputs)

    def test_quote_rejects_objects_that_are_not_monthly_settings(self):
        for value in (None, {}, "2026-10", D("222")):
            with self.subTest(settings=value), self.assertRaisesRegex(ValueError, "settings"):
                calculate_risk(value, current_accum=D("3700"), intraday_r_multiplier=D("1"))


if __name__ == "__main__":
    unittest.main()
