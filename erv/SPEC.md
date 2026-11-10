# ERV ↔ journal

## Objective

Keep the existing terminal calculation flow while removing the need to copy
journal risk or retype an entered trade. The confirmed scope is risk lookup and
optional OPEN entries; closing stays in the journal. The calculator and its
charge configuration move together into `erv/`.
The `uv run erv` console shortcut launches that calculator, matching the existing
`uv run orb` and `uv run journal` entry points.

## Behavior and acceptance criteria

- `uv run erv 57.7 r 2.1 d` uses the current IST month's journal delivery
  risk (swing allocation / slots). `i`, `intraday`, or the existing default style
  selects intraday risk, including today's daily R and latest saved outcomes.
- Resolve `r` after normalizing style. Existing numeric risk arguments remain
  explicit amounts. Interactive risk accepts `r`, with Enter selecting it too.
- Use the current month's settings only. Missing journal/month, unavailable
  intraday daily R, or a nonpositive/nonfinite risk gives a clear error; do not
  silently use an old month or invent a risk. Delivery lookup needs no daily R.
- Keep existing sizing, rounding, charges, price levels, and P&L formulas.
- `uv run erv` without arguments opens interactive mode. The shortcut forwards
  positional arguments to the same calculator as `uv run erv/erv.py`.
- Each supported exit-price calculation (delivery long, intraday long/short)
  offers `Add to journal? [y/n]`, default no. No exit means no entry offer.
- On opt-in, ask for ticker and entry date (today in IST by default). The date
  selects an already-configured journal month; do not create a month implicitly.
- Save OPEN with that exit price as the planned limit, carrying across the
  calculated entry, risk, ATR, quantity, style, side, exchange, charges, and P&L.
  Do not recalculate or resize on save. An `r` run retains its sizing R snapshot.
  Numeric intraday runs ask for missing entry R, defaulting to that date's daily
  policy or the month's saved manual-R fallback; the explicit cash risk stays.
- Declining or reaching EOF at the offer writes nothing. EOF during entry
  details cancels without a partial trade. Invalid details report a useful error.
- Journal calculator workers must neither request journal defaults nor offer
  journal saves. Journal previews and later exit edits still use the moved ERV.

## Commands

```sh
uv run erv 57.7 r 2.1 d
uv run erv 57.7 r 2.1 i
uv run erv 57.7 112.50 2.1 d
uv run erv
uv run journal
.venv/bin/python -m unittest discover -s erv/tests
.venv/bin/python -m unittest discover -s journal/tests -t journal
.venv/bin/python -m unittest discover
```

Direct `uv run erv/erv.py …` from the root and `uv run erv.py …` from inside
`erv/` remain supported. The console shortcut does not move the calculator or
require a root-level `erv.py` compatibility file.

## Structure and implementation

- `erv/erv.py`: existing formulas, risk token resolution, and the three entry
  offers. Load the bridge only for user-facing journal actions.
- `erv/charges.csv`: existing adjacent charge configuration, unchanged.
- `erv/journal_bridge.py`: call the existing `RiskPolicy`, `Journal.quote`, and
  `Journal.add_trade`; use the checkout's `journal/data/journal.sqlite3` and shared
  policy configuration/state. No duplicated risk formulas or schema changes.
- `erv/tests/`: standard-library unittest CLI journeys using temporary copies
  of the calculator/journal source and temporary SQLite files.
- `journal/risk_tool/calculator.py`: update the source path, preserving worker
  isolation and fixed-quantity support.
- `pyproject.toml` and `orb/risk.py`: register and launch the `erv` console
  shortcut using the checkout's calculator, following the journal launcher.

Use ordinary snake_case functions and existing journal payload names:

```python
with Journal(path) as journal:
    quote = journal.quote(month, r_multiplier)
    journal.add_trade(month, record)
```

## Testing strategy

Run the existing journal suite before relocation. Verify moved calculations
against its existing calculator parity tests. Exercise `r`, numeric standalone
use, all three OPEN-save branches, decline/EOF, current-month/daily-R failures,
and subsequent journal exit edits against real temporary journal storage. Check
the `uv run erv` shortcut with arguments and in interactive mode. Tests
must never modify the user's saved journal. No new dependencies or coverage bar.

## Boundaries

- Always: preserve numeric sizing and snapshots, keep worker calls noninteractive,
  validate through existing journal APIs, and verify the relocated commands.
- Ask first: new dependencies, database schema changes, or scope beyond entries
  and style-specific risk lookup.
- Never: execute broker orders, implement closing in ERV, save every calculation
  automatically, alter charge rates, or discard existing staged workspace work.

## Work and verification

1. Move calculator/rates and repair journal/test paths; run calculator tests.
2. Add `r` lookup and optional OPEN-save flows; pass focused CLI journeys.
3. Update usage docs, review the patch, and run journal and repository suites.

The user approved the statement of intent and authorized this work, including
the spec and directory move. No open product questions remain.

Verified: 13 ERV integration tests, 68 journal tests, and 288 repository tests
pass. Numeric `uv run erv/erv.py` from the root and `uv run erv.py` from inside
`erv/` both pass smoke checks. The moved charge CSV is byte-for-byte unchanged.
