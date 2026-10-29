# Implementation plan: Risk rules

Module id: `risk-rules`
Date: 2026-10-29
Status: Approved by the user and completed on 2026-10-29. See `todo.md` for verification and completion evidence.
Approved requirements: [SPEC-risk-rules.md](../SPEC-risk-rules.md)
Capability index: [CAPABILITY-MAP-trading-risk.md](../CAPABILITY-MAP-trading-risk.md)
Task list: [todo.md](todo.md)

## Overview

Implement the approved pure risk provider as a small Python package under `trading-risk/`, using the existing workspace through `uv`. It will estimate opening Accum, quote intraday and equal-share swing risk, and add supplied prior intraday outcomes. It will not introduce a daily trading interface, persistence, calculator integration, or Excel export. Those remain separate capabilities with their own specifications.

## Architecture decisions

- Put the provider in `trading-risk/risk_tool/risk.py`, with package markers and offline `unittest` cases in `trading-risk/tests/`. Keep all source, tests, documentation, and task records under this feature folder.
- Use small immutable records for monthly settings and the returned quote. Settings contain month, intraday allocation, starting swing pool, and positive integer swing slots. The quote retains source inputs and raw calculated components; no transport or database schema is needed.
- Keep the three approved provider operations: `estimate_opening_accum`, `calculate_risk`, and `accum_from_outcomes`. Use Decimal values and fixed constants 0.06 and 0.02. Validate public inputs with field-specific `ValueError` messages. Do not add a dependency or a generic validation framework.
- Use a local Decimal context with 28-digit precision and full platform exponent bounds, including recurring division; never change the caller's global Decimal context. Overflow/underflow must fail explicitly. Display formatting uses `ROUND_HALF_UP` to two decimal places without replacing raw values.
- The workspace will supply the latest preceding intraday outcomes once each. The pure provider only adds those values. It does not fetch trades, interpret open/closed status, choose record ordering, or implement replacement storage.
- Preserve signed arithmetic, including zero or negative Accum and R = 0. Swing risk is the starting pool divided by slots, with no R scaling or outcome compounding. No automatic clamp, loss threshold, or sizing action is introduced.
- Use the existing parent uv environment. Root packaging only includes `orb`, so tests must set their explicit top-level directory to `trading-risk`. Local probes confirmed parent-project discovery from the feature directory. No root manifest, lockfile, environment activation, or new package entry point is needed. This follows [uv's project execution model](https://docs.astral.sh/uv/concepts/projects/run/).

## Dependency graph and order

```text
T1: Complete validated risk quote through uv
  └─ T2: Roll forward supplied intraday outcomes into the next quote
       └─ Checkpoint: approved arithmetic and provider boundaries
            └─ T3: Display precision, usage documentation, final verification
                 └─ Checkpoint: module ready for review
```

T1 is a usable calculation slice: monthly settings produce opening Accum and both risk amounts. T2 adds the sequence needed to compute the next trade's risk. T3 completes the approved display/documentation contract and verifies compatibility with the existing repository.

The detailed acceptance criteria and commands live in `todo.md`; it is the only task checklist. There was no existing plan/task list in this folder to overwrite.

## Verification strategy

Write meaningful contract tests before each behavior change, run the focused cases to observe failure, then implement the minimum behavior and rerun them. Use the approved workbook-derived fixtures as explicit amounts; tests do not read the workbook or require market data.

Run these commands from the repository root after implementation:

```sh
uv run --locked python -m unittest discover -s trading-risk/tests -t trading-risk -p 'test_risk.py'
uv run --locked python -m compileall -q trading-risk/risk_tool/risk.py
uv run --locked python -m unittest discover
```

The focused command is required: existing root discovery alone does not verify the new nested package. Run it after each slice. Run syntax and existing repository regression checks at the final checkpoint; investigate relevant failures without changing unrelated code or weakening tests.

Also run the focused suite from the feature directory through uv to verify parent-project discovery:

```sh
uv run --locked --directory trading-risk python -m unittest discover -s tests -t . -p 'test_risk.py'
```

No user-facing module entry point exists in this phase, so do not advertise `uv run risk` or `python -m risk_tool`. The future workspace capability will specify its own launcher.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Confusing the ₹222 allocation with ₹3,700 opening Accum | Use distinct names and test `222 / 0.06 = 3700`; retain both values in explanation/documentation. |
| Reversing P&L sign or double-counting edited estimates | Test signed sums and compare the next quote from replacement input sequences. Keep record selection and replacement in the workspace. |
| Accidentally freezing risk for a day or using the current trade's own estimate | Test sequential same-day next quotes using preceding outcomes only. No calendar reset or current-trade feedback is introduced here. |
| Reintroducing standard risk rules the user did not request | Explicitly test R = 0, negative Accum, uncapped nonnegative R, and swing independence. |
| Decimal display rounding changing later calculations | Keep raw fields unchanged; verify display examples, recurring division, and an unchanged caller context. |
| Tests passing through an unrelated installed package | Run through uv with an explicit test top-level, and verify imports resolve to `trading-risk/risk_tool/`. |
| Scope leaking into other capabilities or root configuration | Review the changed-path list and imports. Preserve `erv.py`, `charges.csv`, root project/lock files, and `orb/`. |

## Parallel work

Implement T1–T3 sequentially: they share the provider and test files. A separate agent can independently review the approved fixtures, provider contract, or completed diff without writing those files. Do not build an unapproved capability in parallel.

## Completion and review

The module is ready for review when all task acceptance criteria pass, actual uv execution is verified, documentation matches the implemented API, and changes stay within the requested folder. Do not mark tasks complete from compilation or hypothetical commands alone.

This plan does not create a PR, commit, merge, or deploy. It does not complete the overall Excel replacement. After module review, continue the capability workflow for the next module.

## Open questions

None for this module's scope or risk policies. The approved plan is complete; the implemented module is ready for user review. Remaining capabilities retain their own specification/review workflow.
