# Tasks: Risk rules

Status: Plan approved and all tasks completed on 2026-10-29. Module ready for user review.
Plan: [plan.md](plan.md)
Requirements: [SPEC-risk-rules.md](../SPEC-risk-rules.md)

All paths below are relative to the repository root. All writes stay under `trading-risk/`.

## T1 — Quote monthly intraday and swing risk through uv

- [x] Implement T1 after plan approval.

**Description:** Deliver the first complete provider slice: validated monthly settings produce the estimated opening Accum and an explainable risk quote with separate intraday, swing, and combined allocation amounts.

**Acceptance criteria:**
- [x] Immutable settings/quote records and `estimate_opening_accum` / `calculate_risk` expose the approved components. Fixed 6% and 2% arithmetic reproduces the October/November fixtures; four/five swing slots give 25%/20% shares without swing R.
- [x] Public values follow the Decimal contract. Reject malformed month settings, non-Decimal/non-finite monetary values, negative allocations/R, and non-positive, boolean, or fractional slot counts with field-specific errors. Preserve R = 0, zero allocations, and signed Accum without clamping.
- [x] The package is importable through the documented uv test command, needs no root configuration change or additional dependency, performs no file/network/calculator work, and leaves the caller's Decimal context unchanged.

**Verification:**
- [x] First observe the focused contract cases fail without the new behavior; then pass with it.
- [x] Run `uv run --locked python -m unittest discover -s trading-risk/tests -t trading-risk -p 'test_risk.py'`.
- [x] Verify imported code resolves to the new feature package, with no root manifest or lockfile changes.

Evidence: 13 T1 tests passed through uv after a missing-provider RED run. `UV_CACHE_DIR=/tmp/trading-risk-uv-cache` supplies a sandbox-writable uv cache; no project or lockfile edit was needed.

**Dependencies:** None; approved spec and plan are prerequisites.

**Files likely touched:** `trading-risk/risk_tool/__init__.py`, `trading-risk/risk_tool/risk.py`, `trading-risk/tests/__init__.py`, `trading-risk/tests/test_risk.py`, and this task list.

**Estimated scope:** Medium; four source/test files plus the checklist.

## T2 — Update the next quote from preceding intraday outcomes

- [x] Implement T2 after T1.

**Description:** Add pure Accum roll-forward and demonstrate that replacing a supplied planned outcome changes the next risk quote without compounding swing results or mutating any prior quote.

**Acceptance criteria:**
- [x] `accum_from_outcomes` adds signed supplied outcomes to explicit opening Accum. Starting at 3700, -10 yields 3690/risk 73.80; then +20 yields 3710/risk 74.20, including within a single day.
- [x] Replacement input sequences reproduce one latest contribution: -10 changed to -5 yields 3695/risk 73.90; actual -7 replacing it yields 3693/risk 73.86. Repeated calls do not accumulate state or mutate earlier quotes.
- [x] Swing outcomes, status filtering, trade IDs, ordering, and storage are not added to this pure API. Document that the caller supplies only latest preceding intraday outcomes once each; zero/negative results retain the approved arithmetic.

**Verification:**
- [x] Observe focused sequence tests fail before this slice, then pass after it.
- [x] Run `uv run --locked python -m unittest discover -s trading-risk/tests -t trading-risk -p 'test_risk.py'`.
- [x] Inspect the public inputs/imports for accidental ledger, monitor, or `erv.py` coupling.

Evidence: missing-operation RED followed by 19 passing tests through uv, including sequential quotes, replacement estimates, one-pass iterables, and validation of indexed outcomes.

**Dependencies:** T1.

**Files likely touched:** `trading-risk/risk_tool/risk.py`, `trading-risk/tests/test_risk.py`, and this task list.

**Estimated scope:** Small; two source/test files plus the checklist.

## Checkpoint — Risk arithmetic and boundaries

- [x] T1 and T2 acceptance criteria and focused tests pass.
- [x] Opening Accum, intraday R, equal swing shares, same-day sequencing, and signed results match the approved spec.
- [x] No record-selection logic, side effects, root changes, or position resizing has been introduced.

## T3 — Finish display precision and uv usage documentation

- [x] Implement T3 after the arithmetic checkpoint.

**Description:** Complete the approved two-decimal display behavior, document the implemented provider and working uv examples, and verify compatibility with the existing repository.

**Acceptance criteria:**
- [x] Two-decimal `ROUND_HALF_UP` display reproduces 73.17 and 29.27 from the approved raw examples, handles signed/zero amounts, and leaves the full-precision quote and caller context unchanged.
- [x] A feature README documents the actual provider API, uv commands, fixed 6%/2% rules, caller responsibility for latest intraday outcomes, and the distinction between this module and the later trading interface. No nonexistent launcher is advertised.
- [x] Focused, feature-directory, syntax, and existing regression commands succeed; relevant failures are explained or fixed within scope. Verify root `erv.py`, `charges.csv`, `pyproject.toml`, `uv.lock`, and existing monitor files remain unchanged before marking the module ready for review.

**Verification:**
- [x] Run `uv run --locked python -m unittest discover -s trading-risk/tests -t trading-risk -p 'test_risk.py'`.
- [x] Run `uv run --locked --directory trading-risk python -m unittest discover -s tests -t . -p 'test_risk.py'`.
- [x] Run `uv run --locked python -m compileall -q trading-risk/risk_tool/risk.py`.
- [x] Run `uv run --locked python -m unittest discover`.
- [x] Execute the documented usage example and inspect changed paths; do not treat a planned example as runtime verification.

**Dependencies:** T1, T2, and the arithmetic checkpoint.

**Files likely touched:** `trading-risk/risk_tool/risk.py`, `trading-risk/tests/test_risk.py`, `trading-risk/README.md`, `trading-risk/CAPABILITY-MAP-trading-risk.md`, and this task list.

**Estimated scope:** Medium; five files including documentation and tracking.

## Checkpoint — Module ready for review

- [x] All three tasks meet their acceptance criteria, with runtime evidence through uv.
- [x] Independent review finds no unresolved correctness or scope issue; no tests or assertions were weakened to pass.
- [x] Report results and material limitations to the user. Leave other capabilities for their separate specification/review workflow.

## Verification evidence

- Focused suite: **25 tests passed**, both from the repository root and using uv's feature-directory discovery.
- Existing repository suite: **278 tests passed**. Its normal fixture-generated monitor/Telegram messages did not indicate test failures or external delivery.
- Syntax compilation passed. The README's executable example produced opening Accum 3700.00, current Accum 3658.27, intraday risk 73.17, swing risk 225.00, and combined monthly allocation 1122.00.
- Independent review found a default Decimal exponent-range edge case. Regression tests first reproduced overflow and silent underflow; expanded exponent support and explicit arithmetic-range errors resolved it. Re-review found no remaining actionable issue.
- Mutation verification temporarily changed the nonnegative validator to reject zero. Two zero-risk/allocation contract tests failed as expected. Original source was restored byte-for-byte, and all 25 focused tests passed afterward.
- Protected root file hashes (`erv.py`, `charges.csv`, `pyproject.toml`, `uv.lock`) matched the initial snapshot; tracked changes outside the feature folder were absent. Existing unrelated untracked items were preserved.
- Commands used `UV_CACHE_DIR=/tmp/trading-risk-uv-cache` because the sandbox could not write to the default uv cache. Normal documented commands need no project configuration change.
- This completes only `risk-rules`. The journal/interface, reuse adapter for `erv.py`, and Excel export are separate capabilities. No commit, PR, merge, or deployment was created.
