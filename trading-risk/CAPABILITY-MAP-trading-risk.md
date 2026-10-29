# Capability map: Personal trading risk tool

Date: 2026-10-29
Status: Capability boundaries and build order accepted in conversation. Module specifications require separate review.

## Confirmed intent

Replace the recurring Excel risk lookup and trade/exit calculation workflow with a personal tool. Keep intraday and swing trading together, with independent monthly allocations and total risk equal to their sum. Preserve manual R selection, saved trade details, minor corrections, and convenient planned-exit calculations. Export monthly Excel workbooks for experimentation and sharing.

A planned exit is the **limit price** of the user's stop-limit buy/sell order. Trigger prices are outside the initial scope. Planned outcomes must remain distinguishable from completed outcomes.

Keep `erv.py` unchanged and reuse its existing calculations. Broker integration, quick notes, NIFTY expiry markings, and automatic ORB-derived R selection are outside the initial version. ORB-derived R remains a later addition.

The working assumption is a local, single-user tool. Interface and storage are decisions for the `trade-workspace` specification; SQLite was discussed but has not been selected.

All files created or edited for this task belong under the repository's `trading-risk/` directory, including this map, module specifications, future source, tests, and task artifacts. Existing root files such as `erv.py`, `charges.csv`, and `pyproject.toml` are read-only inputs to this task. The user requested this location instead of the skill's default project-root document locations.

All capabilities must support `uv` in the same workspace style as `erv.py` and `orb`, without manual environment activation. Use the existing root project/environment; do not change its manifest or lockfile for the approved risk provider. Actual user-facing launch commands belong in the specifications of capabilities that provide them.

## Confirmed risk policies

- Opening intraday Accum is the month's intraday allocation divided by a fixed 6%. This estimates the balance to keep available; it is not a broker balance lookup.
- Each preceding intraday trade contributes its latest signed net P&L once: losses reduce Accum and gains increase it. Follow the sheet's planned-limit behavior for open trades; completed results replace the corresponding estimates. Swing P&L never contributes.
- Subsequent intraday base risk is 2% of updated Accum, including updates within one day. Manual intraday R scales that amount. Do not add an Accum threshold, clamp, or unavailable-result policy.
- Swing risk is the starting swing pool divided by the selected positive allocation count, with no swing R adjustment: four gives 25% each, five gives 20% each. It does not compound with trade P&L.

## Capabilities

| Module id | Responsibility | Depends on | Specification |
|---|---|---|---|
| `risk-rules` | Monthly allocations, intraday Accum/manual R, and equal fixed swing allocations | — | Implemented and verified; usage in [README.md](README.md), evidence in `tasks/todo.md` |
| `trade-calculations` | Reuse unchanged `erv.py` for sizing, charges, and planned/actual exit calculations | — | Not written |
| `trade-workspace` | Everyday interaction, saved month settings and trades, corrections, and planned exit updates | `risk-rules`, `trade-calculations` | Not written |
| `excel-export` | Generate useful monthly Excel workbooks from saved records and calculation results | `trade-workspace` | Not written |

Build order: `risk-rules` and `trade-calculations` → `trade-workspace` → `excel-export`.

These are logical boundaries in one tool, not a requirement for separate services or deployments. Provider contracts belong in their module specifications.

## Dependency constraints

- `risk-rules` accepts explicit calculation inputs; it does not read the trade store or interpret order status. `trade-workspace` supplies preceding intraday outcomes in order, using each trade's latest estimate or completed result exactly once.
- `trade-calculations` does not own saved trades or monthly budgets. It must preserve the existing calculator's arithmetic and rounding.
- `trade-workspace` coordinates calculations and saved records without duplicating either provider's formulas.
- `excel-export` consumes the workspace's read contract. Whether exported fields recalculate in Excel, and whether edits ever return to the workspace, must be specified explicitly.

## Verified calculator feasibility

A disposable, separate-process check executed unchanged `erv.py` through `importlib`, supplying its command-line arguments and answers to the two exit prompts. Intraday long, intraday short, and delivery long each succeeded at two different exit limits. Changing exit alone preserved quantity. The source hash was unchanged.

This establishes feasibility without implementing an adapter. The calculator specification will define fresh execution, supplied input, captured results, and handling of recorded quantities. An ordinary repeated import uses Python's module cache, so it does not rerun a calculation automatically.
