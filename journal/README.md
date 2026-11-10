# Trading journal

From the workspace root, run:

```sh
uv run journal
```

This opens the saved month with your current intraday risk, swing risk, positions, and net outcomes.

## Daily use

- **a — Entry:** enter the ticker, entry, ATR, direction, and date. The tool supplies that day's R, risk, and suggested quantity. Enter an R override if needed, then choose an exit **limit price** and save.
- **x — Exit/close:** change a saved trade's limit or record its actual exit. Charges and P&L update using `erv/erv.py`; the recorded quantity stays fixed.
- **r — R:** set a manual R for a particular date, or enter `auto` to clear it. Out-of-range R gives a small warning and remains usable.
- **m — Month:** open another month or create one with separate intraday/swing allocations.
- **e — Excel:** export an editable monthly `.xlsx`, following the November sheet's layout.
- **v — All:** view the full month if the terminal shows only its latest rows.
- **? — More:** month planning (`p`), setup, workbook import, entry corrections, trade details, and removal.
- **q — Quit:** saved changes remain available next time.

`OPEN` outcomes use your planned limit; `OUT` outcomes use the exit you recorded. **Net at limits** combines these working outcomes; **Closed** includes completed outcomes only. An imported fee-only row is shown separately and contributes to intraday Accum once.

## Entries from ERV

From the workspace root, use `r` in the calculator's risk position:

```sh
uv run erv 57.7 r 2.1 d
uv run erv 57.7 r 2.1 i
```

Delivery fetches the current IST month's swing risk per position. Intraday fetches its current cash risk using today's daily R, including manual overrides and the latest saved intraday outcomes. Numeric risk arguments remain explicit amounts. The month must already be set up; ERV does not fall back to an older month or create one automatically. Intraday `r` also needs an available daily R; delivery does not depend on it.

After an exit-price calculation, the optional **Add to journal?** prompt saves an entered position as `OPEN`, with that price as its planned limit. It asks for the ticker and entry date (today in IST by default), while carrying over the calculation's entry, ATR, style, direction, exchange, risk, quantity, charges, and P&L. An `r` run retains its sizing R snapshot; a numeric intraday run asks for R at entry, defaulting to the day's R or the month's saved R when daily R is unavailable. Declining the prompt leaves the journal unchanged.

Saved intraday entries immediately contribute their planned-limit outcomes to subsequent risk quotes. Use the journal's **x — Exit/close** flow to record the eventual actual exit. See the [ERV guide](../erv/README.md).

## Risk calculations

Intraday and swing allocations are independent. Their sum is the monthly allocation, not the broker account balance.

```text
opening_accum = intraday_allocation / 0.06
current_accum = opening_accum + latest_signed_intraday_net_outcomes
intraday_risk = current_accum * 0.02 * daily_R
swing_risk_per_position = starting_swing_allocation / swing_slots
```

Each intraday trade contributes once: an open trade uses its planned-limit outcome, which is replaced when the limit changes or an actual exit is recorded. Every new quote includes updated outcomes, including earlier trades that day, and excludes its own proposed outcome. Swing outcomes affect neither intraday Accum nor the starting swing pool. New months have their own settings, without automatic Accum carry-forward.

Allocations and R must be nonnegative; zero and R above one are allowed. Swing slots must be a positive integer. Signed Accum is not clamped; a zero or negative quote cannot size a new trade, and positive risk must still cover at least one share.

For example, intraday allocation 222 gives opening Accum 3700 and risk 74 at R 1 (29.60 at R 0.4). A swing pool of 450 across four slots gives 112.50 per position. October's Accum 3658.2715 gives 73.17 at R 1 or 29.27 at R 0.4.

## Editable daily risk and calendar

Two CSV files are the independent data source:

- [config/risk-rules.csv](config/risk-rules.csv): `expiry_state,timing_band,r_min,r_max`. It contains all 15 combinations transcribed from the reference image. A `≤` limit starts at zero, consistent with nonnegative R.
- [config/trading-calendar.csv](config/trading-calendar.csv): `date,trading_day,expiry_state,source`. November 2026 is supplied as an editable starting month, derived from the official holiday calendars and expiry rules linked in the rows and below. No web fetching runs in the app.

`expiry_state` is `non_expiry`, `sensex_weekly`, `nifty_weekly`, `sensex_monthly`, or `nifty_monthly`. `timing_band` is `before_1015`, `at_1015`, or `after_1015`; the boundary is exactly 10:15:00 IST. Each rule must exist once, with finite nonnegative bounds and `r_min <= r_max`.

Calendar dates use `YYYY-MM-DD`, once per date. `trading_day=yes` requires an expiry state; `no` means no **regular ORB session**, with an empty expiry state. Leave both fields blank for an unverified date. Missing and unverified dates require manual R; they never silently become non-expiry days. Keep a source URL or a short manual note in `source`. Where expiries overlap, explicitly choose the day's controlling state rather than expecting the app to invent a priority.

Before an ORB signal, the default is the highest R across that expiry state's timing bands. The first main NIFTY dual-ORB timestamp selects the timing band, whose maximum becomes the default. Later opposite signals keep the first timing band. A manual R for that date takes priority; `auto` restores automatic selection. Without a known ORB time, the warning range spans all timing bands for that expiry state. Saved trades keep their entry R, risk, quantity, and fee snapshots after any rule/calendar edit.

Live provider history can recover an earlier signal from the current day. First-signal and override state is independent of Telegram configuration, delivery, and its outbox. Historical browsing and replay do not write daily-risk state. Invalid CSV is reported; a deliberate manual override remains usable with a warning.

Show the optional planning table without setting up a journal month:

```sh
uv run journal --month 2026-11 --plan
```

Create a CSV template for another month (printing to the terminal, without overwriting your calendar):

```sh
uv run journal --calendar-template 2026-12
```

Fill the template manually or ask an AI: “Verify every date for this month's regular NSE/BSE sessions using official sources. Use the supplied CSV columns and allowed expiry states; account for holiday shifts and monthly expiry. Leave uncertain dates unclassified and record source URLs. Flag overlapping expiry states for manual selection.” Append the completed rows to the calendar CSV, keeping one header and no duplicate dates. Changes are read at the next lookup.

The November seed excludes the special Muhurat session on November 8 from regular ORB sessions. Its expiry dates are derived from [NSE's expiry rules](https://www.nseindia.com/static/products-services/equity-derivatives-nifty50), [NSE's 2026 holiday circular](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf), [BSE's expiry notice](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20250623-59), and [BSE's 2026 holiday notice](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20251212-8).

## Files

Your journal is saved in `journal/data/journal.sqlite3`; the first ORB time and daily overrides are in `journal/data/daily-risk.sqlite3`. Exports default to `journal/exports/`. Both folders are ignored by Git. Back up `data/` when backing up your trading records.

Import another workbook through **? → i**. Import preserves the sheet's saved charges/P&L; changing an exit refreshes that trade's result using the current `erv/erv.py` and `erv/charges.csv`. Importing over a month that already has entries is refused to prevent duplicates.

The original workbook is unchanged. October's imported fee-only adjustment of -2.253467581 is retained separately and counted once. Ticker-only and numerically unchanged corrections preserve imported fee/P&L snapshots. A planned exit is the order's **limit price**, not its trigger or evidence of execution.

Exports keep historical quantities, risk, and fee snapshots, and contain only recorded trades and adjustments. Excel can recalculate net P&L when you change an exit, but fees do not recalculate inside Excel; update them in the app or edit the snapshot for an experiment. `Next R` is an editable export snapshot, not a live CSV or ORB connection.

No broker connection or additional package installation is needed. Run `uv run orb` independently to record the first main signal and include Daily R in Telegram alerts. Expiry-only R and manual overrides work without running ORB. The calculator and charge schedule live together in `erv/`.

By default both apps find the data in this checkout. `--risk-home /absolute/path/to/journal` or `TRADING_RISK_HOME` selects another rules/calendar directory. `TRADING_RISK_STATE_PATH` selects the same daily-state file for both apps. A journal launched with `--data-dir` uses a separate daily-state file in that directory unless the environment variable selects one explicitly; set it for ORB too when sharing a custom journal folder.

## Optional commands

Print the saved month and exit:

```sh
uv run journal --show
```

The tests are for development, not how to launch the journal:

```sh
uv run python -m unittest discover -s journal/tests -t journal
```

## Code

`daily_risk.py` is the standalone standard-library provider shared by the journal and optional ORB adapter. `RiskPolicy(root=None, state_path=None)` exposes `resolve(date)`, `set_override(date, value_or_none)`, `record_first_orb(datetime)`, and `calendar_month(month)`. Decisions include Decimal R/bounds, expiry/timing, selection source, first signal time, and a warning. Datetimes use IST; naive signal times are interpreted as IST. SQLite upserts retain the earliest timestamp and preserve overrides across concurrent callers.

Within `risk_tool/`, `risk.py` owns the pure Decimal formulas with an isolated 28-digit context, explicit overflow/underflow errors, and two-decimal half-up display rounding. `journal.py` owns saved months/trades and latest outcomes; `calculator.py` runs `erv/erv.py` in an isolated worker, keeping its interactive input and process-wide effects separate. The worker never prompts to save another journal entry. `terminal.py` owns the CLI and `workbook.py` owns portable XLSX import/export. Tests live in `tests/`.

Neither app requires the other's process. There are no new dependencies, broker execution, automatic calendar downloads, or mandatory color conventions.
