# ERV position and risk calculator

From the workspace root:

```sh
uv run erv 57.7 r 2.1 d
```

Run `uv run erv` without arguments for interactive mode. Direct `uv run erv/erv.py` from the workspace root and `uv run erv.py` from inside `erv/` also accept the same arguments. The calculator reads the adjacent [cash_charges.csv](cash_charges.csv) and [fno_charges.csv](fno_charges.csv) regardless of the current working directory.

## Risk input

The positional inputs are entry price, risk, ATR, style, direction, and exchange. Style defaults to intraday, direction to long, and exchange to NSE.

```sh
# Delivery: use the journal's swing risk per position.
uv run erv 57.7 r 2.1 d

# Intraday: use the journal's current intraday cash risk.
uv run erv 57.7 r 2.1 i

# Keep an explicit risk of ₹112.50.
uv run erv 57.7 112.50 2.1 d

# Intraday short on BSE.
uv run erv 57.7 r 2.1 i s bse
```

`r` selects the current month and date in IST. The journal supplies the amount:

- Intraday uses current Accum × 2% × today's daily R, including daily overrides, ORB timing, and the latest saved intraday outcomes.
- Delivery uses the month's starting swing allocation divided by its swing slots, independently of intraday outcomes and daily R.

The current journal month must already be set up through `uv run journal`. Intraday `r` requires a known daily R; set a manual daily R in the journal when its calendar or automatic selection is unavailable. ERV reports missing setup or unavailable risk instead of falling back to an older month. Numeric risk remains usable without journal setup.

Run `uv run erv` without arguments for interactive inputs. At the risk prompt, Enter or `r` requests journal risk; the selected style determines which amount is fetched.

## Save an entered position

For delivery long, intraday long, and intraday short, supplying an exit price calculates net P&L and charges, then offers **Add to journal?**. Enter or `n` declines. A calculation without an exit price does not offer to save an entry.

Choose `y` after you have entered the position. Supply its ticker and entry date; the date defaults to today in IST. The selected date's month must already exist in the journal.

ERV saves the position as `OPEN` and uses the supplied exit price as its **planned exit limit**. It carries over the calculated entry, ATR, style, direction, exchange, cash risk, quantity, charges, and net P&L without resizing the position. An `r` run also preserves its sizing R snapshot. For numeric intraday risk, the missing R at entry is prompted with the day's R as its default, or the month's saved R if daily R is unavailable.

Saving an intraday position includes its planned-limit outcome in later risk quotes, as with an entry made directly in the journal. Record its eventual actual exit through the journal's **x — Exit/close** action.

## Files and checks

- [erv.py](erv.py): calculator and interactive flow.
- [charges.csv](charges.csv): charge configuration shared with journal calculations.
- [journal_bridge.py](journal_bridge.py): journal risk lookup and entry saving.
- [SPEC.md](SPEC.md): scope and acceptance criteria.
- `tests/`: command and journal-integration tests using temporary data.

From the workspace root:

```sh
uv run python -m unittest discover -s erv/tests
uv run python -m unittest discover -s journal/tests -t journal
```

No additional dependencies or broker connection are needed. Journal calculations run ERV in an isolated worker without the optional entry-saving prompt.
