# Trading risk rules

This folder contains the risk calculation provider for the personal trading tool. It calculates monthly allocations, evolving intraday risk, and equal-share swing risk. The daily trading interface, saved trades, `erv.py` integration, and Excel export are later capabilities in the [capability map](CAPABILITY-MAP-trading-risk.md).

## Run through uv

From the repository root, this complete example uses the existing workspace environment:

```sh
uv run --locked --directory trading-risk python -c '
from decimal import Decimal as D
from risk_tool.risk import (
    MonthlyRiskSettings, accum_from_outcomes, calculate_risk,
    estimate_opening_accum, format_money,
)

settings = MonthlyRiskSettings("2026-10", D("222"), D("900"), 4)
opening = estimate_opening_accum(settings.intraday_allocation)
accum = accum_from_outcomes(opening, [D("-10"), D("-31.7285")])
quote = calculate_risk(settings, current_accum=accum, intraday_r_multiplier=D("1"))
print("Opening Accum estimate: ₹" + format_money(opening))
print("Current Accum: ₹" + format_money(quote.current_accum))
print("Intraday risk (R=1): ₹" + format_money(quote.intraday_risk))
print("Swing risk per position: ₹" + format_money(quote.swing_risk_per_position))
print("Combined monthly allocation: ₹" + format_money(quote.combined_monthly_allocation))
'
```

The illustrative prior outcomes above give:

```text
Opening Accum estimate: ₹3700.00
Current Accum: ₹3658.27
Intraday risk (R=1): ₹73.17
Swing risk per position: ₹225.00
Combined monthly allocation: ₹1122.00
```

Use `uv run --directory trading-risk ...` for imports from `risk_tool`; the root package configuration deliberately remains unchanged. `--locked` prevents a required dependency-lock change from being applied silently. No manual environment activation or new dependency is needed.

## Provider contract

All numeric money/R inputs are finite `Decimal` values constructed from strings. Integers are accepted only for the positive `swing_slots` count. Month settings use exact `YYYY-MM` notation.

| API | Result |
|---|---|
| `MonthlyRiskSettings(month, intraday_allocation, swing_allocation, swing_slots)` | Immutable monthly settings; allocations must be nonnegative. |
| `estimate_opening_accum(intraday_allocation)` | Intraday allocation / **0.06**, an estimate of starting funds to keep available. |
| `accum_from_outcomes(opening_accum, intraday_net_outcomes)` | Opening Accum plus the supplied signed outcomes, without storing state. |
| `calculate_risk(settings, *, current_accum, intraday_r_multiplier)` | Immutable `RiskQuote` containing source settings/inputs, combined allocation, base intraday risk, scaled intraday risk, and per-position swing risk. |
| `format_money(amount)` | Two-decimal text, rounded half up; it does not change the raw amount. |

The fixed rules are:

```text
Opening Accum = intraday allocation / 6%
Current Accum = opening Accum + latest preceding intraday net P&L
Intraday base risk = current Accum × 2%
Intraday risk = base risk × manual R
Swing risk per position = starting swing allocation / selected count
Combined monthly allocation = intraday allocation + starting swing allocation
```

Losses reduce Accum and gains increase it. A new quote uses the updated amount, including within one day. R = 0 remains zero; nonnegative R has no implicit ceiling. Zero/negative Accum is calculated directly without a clamp. Swing risk has no R adjustment or P&L compounding: four shares are 25% each, five are 20% each.

The caller supplies one latest outcome per preceding intraday trade, in order, excluding swings. An open trade contributes its current planned-limit net P&L; editing that limit replaces the contribution, and completing the trade replaces it with the completed result. Do not pass both the old and replacement values, or the current trade's prospective outcome when sizing that same trade. Selection, provenance, and saved quantities belong to the future workspace; this provider only calculates the supplied values.

Public validation errors are `ValueError` messages identifying the field, including an outcome's index when applicable. The provider does not import `erv.py`, read files, contact feeds, or mutate settings/quotes. Arithmetic uses an isolated 28-digit Decimal context with the platform's full exponent bounds; recurring division can round at that precision, while display rounding is kept separate. An unrepresentable arithmetic result raises `ValueError` with a Decimal arithmetic range message, so underflow cannot silently set nonzero risk to zero. The caller's precision, rounding, traps, and flags remain unchanged. See Python's [Decimal documentation](https://docs.python.org/3.14/library/decimal.html) for context and rounding behavior.

## Verify

From the repository root:

```sh
uv run --locked python -m unittest discover -s trading-risk/tests -t trading-risk -p 'test_risk.py'
uv run --locked python -m compileall -q trading-risk/risk_tool/risk.py
uv run --locked python -m unittest discover
```

Also verify imports from this directory:

```sh
uv run --locked --directory trading-risk python -m unittest discover -s tests -t . -p 'test_risk.py'
```

Run the focused suite explicitly; root discovery alone does not include this nested package. Tests use self-contained amounts and require no spreadsheet, live market session, or broker credentials.
