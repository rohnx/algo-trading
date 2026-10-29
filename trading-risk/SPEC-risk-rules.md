# Spec: Personal trading risk rules

Module id: `risk-rules`
Date: 2026-10-29
Status: Approved, implemented, and verified on 2026-10-29. The user approved the spec and `tasks/plan.md`, including uv compatibility. Verification evidence is recorded in `tasks/todo.md`. No risk-policy questions remain open.
Capability map: [CAPABILITY-MAP-trading-risk.md](CAPABILITY-MAP-trading-risk.md)

## Objective

Give the user the cash risk prescribed by their own system for intraday and swing trades, without requiring a spreadsheet lookup. This is the calculation provider for the future trading workspace. It does not choose trades, place orders, store positions, calculate fees, or choose an R multiplier automatically.

Success means that a caller can supply monthly settings, the current intraday Accum, and a manually chosen R, and receive explainable risk amounts that reproduce the confirmed workbook rules. New months use deliberately selected settings rather than silently inheriting the preceding month's closing balance.

All task documents, future source, tests, and task artifacts must live under `trading-risk/`. Existing repository files outside that directory remain unchanged.

The module and its verification commands must work through `uv run` using the existing workspace environment, without manual virtual-environment activation. Later capabilities must preserve this requirement for their user-facing commands.

## Confirmed requirements

- Intraday and swing allocations are set independently each month. Their sum is the combined monthly allocation labelled Total Risk.
- Opening intraday Accum is the monthly intraday allocation divided by 0.06. Keep this arbitrary account-balance estimation factor fixed for now; it is not an editable setting in this version.
- Intraday cash risk is exactly 2% of current Accum, scaled by the user's manual R multiplier. Apply that formula to any finite Accum without a new threshold or clamp.
- Accum rolls forward by adding each preceding intraday trade's signed net P&L. Negative P&L reduces it; positive P&L increases it. Update subsequent risk after each outcome, including multiple trades on the same day.
- Follow the sheet's style for open intraday trades: the latest planned-limit P&L contributes without requiring Position to be Out. A revised estimate or completed result replaces that trade's earlier contribution; it is not an additional outcome.
- Swing P&L never changes intraday Accum or compounds the starting swing pool.
- Swing positions have equal risk from the starting swing allocation divided by the chosen positive slot count. Four slots give 25% each, five give 20% each; there is no swing R adjustment.
- A planned exit is an order's limit price. Keep planned and completed outcome provenance distinct even though both can supply the working intraday roll-forward at different times.
- Changes to current settings or a planned exit must not silently resize a recorded position. The workspace owns saved trade inputs and calculation snapshots.
- Reuse `erv.py` unchanged in `trade-calculations`. This module must not import it or reproduce its fees, sizing, stop, or P&L formulas.

## Evidence and terminology

Read-only source: the user-supplied `Trading Spreadsheet-2026.xlsx`, particularly `Oct 2026`, `Nov 2026`, and `month template`. Conversation-confirmed requirements take precedence over incidental workbook examples. The workbook is a reference, not a runtime or test dependency.

| Term | Meaning and source |
|---|---|
| Intraday allocation | The monthly amount in D2; ₹222 in October and November. This is distinct from Accum. |
| Swing allocation | F2; ₹900 in October and ₹450 in November. |
| Combined monthly allocation | D2 + F2, as in October/November A2. It is not the evolving intraday Accum or a live account balance. |
| Intraday fraction | E2; fixed at 0.02 in this version. |
| Opening Accum | The month's estimated starting calculation balance. October/November C5 use D2 / 0.06, giving ₹3,700. The user confirmed that 6% is a fixed estimate of funds to keep available for the chosen intraday risk allocation. |
| Current Accum | The current intraday calculation base after the admitted outcomes. October C28 caches approximately ₹3,658.2715. |
| R multiplier | The user-selected exposure adjustment in column B. It is not a realized performance R calculation. |
| Swing slots | The positive equal allocation count: October/November G2 = 1/4; September uses 1/3. The count is not restricted to these examples; five means 1/5. |

### Workbook observations and confirmed interpretation

The normal October roll-forward adds the preceding row's P&L when that row is Intraday and otherwise carries Accum forward. For example, C12 uses `IF(E11="Intraday",C11+P11,C11)`. I18 is In, yet P18 contributes to C19. The user confirmed following this style: planned-limit estimates can affect the working Accum before a trade is completed. Preserve a separate label for their provenance rather than changing the admission rule to realized-only.

J6 uses `IF(E6="Swing",$F$2*$G$2,$C6*$E$2)*B6`, applying the row R multiplier to either style. The user's clarification takes precedence for this version: swing risk is the starting pool divided equally by the selected count, without a per-position or shared swing R multiplier.

The first example trade row's Accum expression differs from the later roll-forward. The code must express the approved chronological rule directly, without copying cell-position artifacts or treating the example trade as a required first trade.

## Calculation contract

All money amounts are INR. Fractions and R are dimensionless. Use decimal amounts constructed from strings, not binary-float inputs.

Provider operations:

1. `estimate_opening_accum(intraday_allocation)` returns the monthly intraday allocation divided by the fixed 0.06 estimate.
2. `calculate_risk(settings, *, current_accum, intraday_r_multiplier)` returns a risk quote without side effects.
3. `accum_from_outcomes(opening_accum, intraday_net_outcomes)` returns opening Accum plus the supplied signed outcomes. The caller supplies the latest net outcome of each preceding intraday trade exactly once, excluding swing results. For an open trade this is the planned-limit estimate; after completion it is the completed result. This operation does not read a ledger or inspect trade status itself.
4. `format_money(amount)` returns two-decimal text using `ROUND_HALF_UP`, without a currency symbol or grouping and without changing the original amount.

`MonthlyRiskSettings` is an immutable record containing `month` (`YYYY-MM`), `intraday_allocation`, `swing_allocation`, and positive integer `swing_slots`. The 0.02 intraday fraction and 0.06 opening-Accum estimate are fixed constants in this version, not editable settings. The workspace obtains opening Accum from `estimate_opening_accum`, then supplies current Accum for a risk quote. There is no separate opening-Accum override or silent prior-month carry-forward.

The immutable `RiskQuote` retains `settings`, `current_accum`, `intraday_r_multiplier`, `combined_monthly_allocation`, `intraday_base_risk`, `intraday_risk`, and `swing_risk_per_position`. Monthly allocations remain accessible through `quote.settings`. Returning the components makes the calculation explainable without a separate formula implementation in the consumer.

### Confirmed arithmetic

```text
combined_monthly_allocation = intraday_allocation + swing_allocation
opening_accum = intraday_allocation / 0.06
current_accum = opening_accum + sum(latest_prior_intraday_net_outcomes)
intraday_base_risk = current_accum * 0.02
intraday_risk = intraday_base_risk * chosen_intraday_R
swing_risk_per_position = starting_swing_allocation / swing_slots
```

The roll-forward uses signed addition, matching the sheet: an outcome of -10 subtracts ₹10 from Accum, while +20 adds ₹20. Do not subtract signed P&L a second time.

The caller preserves the trade/row sequence, including within one day. A new trade's quote uses preceding outcomes, not that same trade's prospective P&L. When a prior trade's planned limit changes, replace its previous outcome and recompute the working Accum; do not repeatedly add each edit. The updated amount sizes subsequent trades without changing the risk or quantity captured for an already recorded trade.

### Validation and precision

- Public numeric inputs must be finite decimal values. Monthly allocations and R must be nonnegative; swing slots must be a positive integer. Current Accum and net outcomes may be signed. Invalid inputs raise `ValueError` with the offending field identified.
- R = 0 is a valid zero-risk selection. Do not turn it into the default R = 1.
- Apply the same 2% arithmetic at zero or negative Accum. For R = 1, zero Accum gives zero risk and Accum -100 gives raw risk -2. This module does not clamp the amount, substitute a minimum, or return an unavailable result. Downstream use must not reinterpret a signed result as a positive allocation.
- Retain decimal precision through calculations. Display formatting is two decimal places with `ROUND_HALF_UP`; display rounding must not alter the stored/raw quote.
- Arithmetic uses an isolated 28-digit context with the platform's full Decimal exponent bounds. Unrepresentable results raise an explicit `ValueError` identifying the Decimal arithmetic range; underflow must not silently turn nonzero risk into zero. This is a numeric representation limit, not an Accum threshold or loss-management rule.
- Conversion to `erv.py`'s numeric inputs and its quantity rounding belong to `trade-calculations`; this module does not change either.
- Do not impose ORB/expiry ranges, an R ceiling, or new loss-management rules. The user chooses R manually in this version.

## Tech stack

Use the repository's Python >=3.14 environment through `uv`; the inspected interpreter is 3.14.4 and the inspected uv version is 0.12.24. Use standard-library `decimal`, ordinary Python data records/type hints, and `unittest`. No new runtime dependency is needed for this module.

The root project/environment remains authoritative. Local no-sync probes confirmed that `uv run` from the root and `uv run --directory trading-risk` discover the same environment. No separate project, root package entry point, or root manifest change is required for this pure module. See the official [uv project execution documentation](https://docs.astral.sh/uv/concepts/projects/run/) for the environment model.

Database choice, browser/terminal interaction, Excel libraries, and an application launch command belong to later module specifications. No choice of those technologies is implied here.

## Commands

Run from the repository root. The explicit test top-level directory keeps all new Python packages and tests inside `trading-risk/`. Use `--locked` so these commands reject a required lockfile change rather than rewriting the root lockfile.

```sh
# Focused rule verification
uv run --locked python -m unittest discover -s trading-risk/tests -t trading-risk -p 'test_risk.py'

# Syntax verification
uv run --locked python -m compileall -q trading-risk/risk_tool/risk.py

# Existing repository regression command
uv run --locked python -m unittest discover
```

There is no separate build or development server for this pure module. No configured Python linter was found; this spec does not add one. Run the focused command separately from existing root discovery. Root packaging currently includes only `orb`; the explicit test top-level makes `risk_tool` importable without changing that packaging. Future launch configuration must stay inside `trading-risk/`; do not change the root `pyproject.toml` or `uv.lock`.

## Project structure

The implemented layout stays under the user-requested folder:

```text
trading-risk/
  CAPABILITY-MAP-trading-risk.md  Capability index and accepted boundaries
  SPEC-risk-rules.md             This module's requirements and resolved policies
  README.md                      Executable uv example and provider API
  tasks/
    plan.md                     Approved implementation plan
    todo.md                     Completed tasks and verification evidence
  risk_tool/
    __init__.py                  Package marker
    risk.py                      Pure risk provider
  tests/
    __init__.py                  Test package marker
    test_risk.py                 Offline contract tests
```

Existing `erv.py` and `charges.csv` remain at the repository root and are used read-only by the separate calculator capability. Existing `orb/` remains unchanged. Do not create placeholder modules for the other capabilities or place task artifacts outside `trading-risk/`.

## Code style

Follow the repository's Python snake_case naming, small functions, and explicit records. The following is a style example for the arithmetic, not a completed provider implementation:

```python
from decimal import Decimal


def intraday_risk(accum: Decimal, r_multiplier: Decimal) -> Decimal:
    return accum * Decimal("0.02") * r_multiplier
```

Keep labels precise: for example, `Intraday risk: ₹73.17 · R: 1` and `Swing risk per position: ₹225.00`. Keep raw values available independently of presentation text. Do not label the monthly allocation or Accum as the actual broker account balance.

## Testing strategy and success criteria

Use offline `unittest` contract cases with self-contained numeric fixtures. Tests must not require the source workbook, Downloads directory, live markets, broker credentials, or a running monitor. No arbitrary coverage percentage is proposed; cover the approved rules, their independence, and the meaningful boundaries below.

| Criterion | Expected result |
|---|---|
| October monthly allocations: 222 and 900 | Combined monthly allocation 1122; four swing slots give 225 each. |
| Intraday allocation 222 | Fixed 6% estimate gives opening Accum 3700. |
| Explicit Accum 3658.2715, R 1 | Fixed 2% fraction gives raw intraday risk 73.165430; display 73.17. |
| Same Accum, R 0.4 | Raw intraday risk 29.2661720; display 29.27. |
| Same positive Accum, R 0 | Intraday risk is exactly zero. |
| Swing allocation 615 with three slots | Fixed baseline risk 205 per position. |
| Swing allocation 900 with five slots | Equal 20% shares give 180 per position; no swing R scaling. |
| November allocations: 222 and 450 | Combined allocation 672; four swing slots give 112.50 each. |
| Opening Accum 3700 and preceding intraday outcome -10 | Current Accum 3690; at R 1, subsequent intraday risk 73.80. |
| Then a preceding intraday outcome +20, on the same day | Current Accum 3710; at R 1, next intraday risk 74.20. |
| Open intraday outcome -10 revised to -5 | One latest contribution gives Accum 3695 and risk 73.90, not Accum 3685. |
| That same trade completes at net -7 | Completed result replaces the -5 estimate: Accum 3693 and risk 73.86. |
| A preceding swing outcome changes | It is excluded from intraday outcomes; neither intraday Accum nor the starting swing pool compounds with it. |
| Change only swing allocation or slot count | Intraday risk remains unchanged for the same explicit Accum and R. |
| Change only intraday outcomes or R | Fixed swing baseline remains unchanged. |
| Independent new-month settings | No implicit carry-forward of the previous month's closing Accum. |
| Accum 0 or -100, R 1 | Raw intraday risk is 0 or -2 respectively; no clamp or unavailable result. Swing amounts remain independent. |
| Invalid settings, non-finite values, or invalid slot counts | Predictable validation errors; no plausible-looking fallback risk. |
| Repeated calls with the same inputs | Identical values, no mutated inputs, and no file, network, database, or calculator side effects. |

Assert exact Decimal results for explicit fixtures. Use a ₹0.000001 absolute tolerance when comparing with the workbook's cached floating-point amounts, and compare displayed amounts to the paise. Cover the fixed 6% estimate, fixed 2% risk fraction, signed roll-forward, and equal swing allocation explicitly.

Consumer integration tests for selecting the latest preceding intraday outcomes, excluding swings, avoiding duplicates, preserving recorded quantities, and correcting history belong in the owning workspace specification. This pure module tests the resulting supplied values without reading mutable trade records.

## Boundaries

- **Always:** Keep all task edits under `trading-risk/`; support `uv run` and document verification through uv; preserve the fixed 6% opening estimate, 2% intraday fraction, signed intraday roll-forward, and equal swing proportions; expose calculation inputs/components; validate public inputs; separate raw precision from display; retain planned/completed provenance; run focused and relevant existing checks for implementation changes.
- **Ask first:** Change confirmed risk semantics, fixed fractions, or historical treatment; introduce dependencies or packaging/CI changes within the task folder; extend this module into persistence, UI, or external integrations. Record approved changes in the spec first.
- **Never:** Write task source/configuration changes outside `trading-risk/`; edit the root `pyproject.toml` or `uv.lock`; edit or move the root `erv.py` or `charges.csv`; duplicate their fee/P&L/sizing logic; import the trade store or monitor; switch to realized-only Accum; add an Accum clamp/threshold; scale swing risk with R; automatically select R; mutate saved positions; treat a planned limit as proof of execution.

## Resolved decisions

The user resolved the original questions as follows:

| Decision | Confirmed rule |
|---|---|
| Q1 — Opening Accum | Use allocation / 0.06. Keep 6% fixed for now as an estimate of funds to keep available for that intraday risk allocation. |
| Q2 — Intraday outcome admission | Follow the sheet's style: add preceding intraday signed net P&L, including the latest planned-limit estimate while open. Exclude swing P&L. Risk remains 2% of Accum before manual R. |
| Q3 — Swing proportions | Starting swing pool / selected count only; equal shares, no swing R or P&L compounding. |
| Q4 — Same-day sequencing | Update Accum after each preceding intraday outcome and use the updated base for the next risk quote. |

## Open questions

None for this module. The user approved the specification with uv compatibility explicitly included. Interface, storage, and calculator integration details belong to their later module specifications.
