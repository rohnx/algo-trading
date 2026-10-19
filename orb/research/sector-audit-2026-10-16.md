# Sector selection and provider audit

Research and read-only provider probes on 16 October 2026, about 20:00 IST,
after the market closed. No monitor implementation changes were made for this
research. Machine-readable evidence: `sector-provider-audit-2026-10-16.json`.

## Agreed design and implementation

The user's selected scope below supersedes the earlier core/optional/deferred
recommendation. Implementation was authorized on 17 October 2026. Short chart
labels omit the NIFTY prefix because the tab already identifies the index family.

The registry, comparison tabs and sector Yahoo backfill are now implemented.
An isolated 17 October Shoonya check authenticated, retrieved REST quotes for
all 15 sectors, subscribed all 21 instruments on one socket and received
price-bearing subscription snapshots for every sector. This verifies routing
and availability, not changing-tick latency during an open market. Sanitized
evidence: `sector-connection-check-2026-10-17.json`.

Activation requires restarting the monitor and refreshing the browser tab.

Validation of the initial sector implementation on 17 October: all 260 Python
tests and 68 browser checks passed.
Browser coverage includes both providers' state handling, mutually exclusive
groups, inactive-group scale isolation, background updates, independent
close-only confirmations, provenance, replay and all 15 endpoint labels in
both intervals on desktop and mobile. An isolated public-history preview also
loaded all 75 five-minute candles for each sector for the 16 October session.

### Two mutually exclusive comparison tabs

Place **NIFTY Sectors** beside **Derivatives**. Treat them as a single-choice
tab group: at most one comparison group is selected, and only that group's
comparison lines and labels display.
Selecting NIFTY Sectors removes Derivatives overlays; selecting Derivatives
removes sector overlays. Clicking the active tab again hides both groups,
leaving only NIFTY. These toggle-off and shared-origin refinements supersede
the initial always-selected tab behavior.

The existing NIFTY 50 candles, opening ranges, price-axis label and dual-ORB
signal remain the shared primary chart. Group selection is independent of the
5m/15m selector: either comparison group works in either interval. Switching
groups preserves the interval, date, provider and replay position.

**Derivatives** retains its existing five comparison indices: BANKNIFTY,
Midcap Select, Nifty Next 50, Fin Nifty and BSE Focused IT (FOCIT).

**NIFTY Sectors** contains exactly these 15 indices, in the requested order:

1. Pharma
2. Auto
3. FMCG
4. Metal
5. PSU Bank
6. Realty
7. Consumer Durables
8. Oil & Gas
9. Private Bank
10. Energy
11. India Defence
12. Infrastructure
13. PSE
14. Commodities
15. CPSE

Nifty Bank and Nifty IT are excluded from this sector tab. BANKNIFTY and FOCIT
remain available through Derivatives. All 15 sectors above are in the agreed
scope; CPSE and Commodities are no longer deferred or optional.

### Same comparison rules in both tabs

- Each index owns its breakout state, independently of other indices and the
  selected tab. Use the highest and lowest of its first nine complete, valid
  five-minute closes from 09:15 to 10:00. Candle wicks and the session-open
  percentage baseline do not enter that range. A subsequent complete five-minute
  close strictly outside the band confirms the direction; the first possible
  confirmation is 10:05. Equality or a later close inside clears qualification.
  Incomplete opening data, invalid confirmation bars or expired live confirmation
  must not produce crisp breakout emphasis. The rule stays the same in 5m and 15m.
- Qualifying indices display solid, crisp lines and directional endpoint labels.
  Before NIFTY's first dual-ORB signal, nonqualifying indices remain visible with
  the existing soft blur. After that first signal, they are hidden in the idle
  view and revealed softly when NIFTY is hovered or keyboard-focused. This
  visibility transition does not change an index's independent breakout state.
- Hovering a qualifying line, or hovering/focusing its label, isolates that
  index and deeply softens the other indices in the selected group. Leaving
  restores the existing idle behavior. Clear group-specific hover/focus when
  switching tabs so a hidden index cannot keep the new group muted.
- Retain the minimal line-end labels: index name, percentage change since the
  09:15 session open, and the appropriate direction/provenance/status markers.
  Keep NIFTY's label in the right margin. Avoid label collisions and masking a
  label's own line; do not restore floating inspection cards or top legends.
- Plot the selected interval's observed closes, retaining valid five-minute
  children where an incomplete 15-minute parent would hide them. Preserve real
  data gaps and isolated live quotes; never invent a connecting price history.
  Display delay, history-backfill and staleness accurately for each index.
- Rebase each comparison's first valid completed selected-interval close to the
  midpoint of NIFTY's displayed ORB, using NIFTY's session open before that range
  is available. Subsequent points show proportional movement from that reference.
  A quote-only or forming-only series without a completed bar uses its session open as a stable
  reference. Add no synthetic opening point. This alignment is visual only:
  session-open percentage labels, actual timestamps, missing gaps and each
  index's five-minute close-only breakout calculations stay unchanged.
- Fit the scale using NIFTY and the selected group's visible/revealed lines.
  The inactive group's prices and labels must not influence scale or hit testing.
  Apply the existing historical/replay rules without revealing future samples
  or confirmations.

The groups are independent comparison views over shared market-data machinery.
Switching tabs must not reset instrument history, opening ranges or confirmations,
restart the provider, or trigger Telegram notifications. Continue collecting
both groups in the background through one selected-provider socket so switching
views does not create fresh coverage gaps. The resulting registry has 21 unique
instruments: NIFTY 50, five Derivatives comparisons and 15 Sectors comparisons.
Bound history/backfill request concurrency as that registry expands.

### Acceptance checks

- At most one tab is selected; the other group's paths, labels and hover targets
  disappear. Clicking the active tab hides comparisons and fits NIFTY alone.
- Comparison paths share the displayed ORB midpoint at their first completed
  selected-interval close, without inventing an opening sample or changing
  percentage labels, gaps or breakout confirmations.
- All 15 selected sector mappings load, with no unintended Bank/IT sector entry.
- Group changes preserve source, date, interval and replay state, while clearing
  old hover/focus and excluding inactive-group extrema from the fitted scale.
- A forming price or candle cannot confirm a sector breakout. Each sector's valid
  opening closes and latest completed confirmation determine its own emphasis.
- Both tabs retain the existing overview, NIFTY reveal, qualifying-index isolation,
  readable endpoint labels, provenance and freshness behavior in 5m and 15m,
  live sessions, historical sessions and replay.

## Research context

Consumer Durables adds a different consumer basket from FMCG. Infrastructure
is much broader than property developers, and is not a Realty substitute.
Oil & Gas gives a narrower industry view than Energy. PSE offers broader
government-enterprise coverage than the selected CPSE disinvestment basket.
Defence is useful as a theme but is concentrated in a few large constituents.

## Verified mappings and availability

This audit covered 17 candidates, including Bank and IT; the agreed sector
tab above uses the other 15. Each Yahoo mapping was resolved as an NSE INDEX,
not an ETF proxy. All 17 returned 75 valid regular-session five-minute OHLC
slots for today. Shoonya's
authenticated GetIndexList resolved every token below and GetQuotes returned
a positive price for each one.

| Index | Yahoo symbol | Shoonya NSE token | Shoonya valid 5m bars today |
|---|---|---|---:|
| Bank | `^NSEBANK` | `26009` | 75 |
| IT | `^CNXIT` | `26008` | 0 |
| Pharma | `^CNXPHARMA` | `26023` | 0 |
| Auto | `^CNXAUTO` | `26029` | 0 |
| FMCG | `^CNXFMCG` | `26021` | 0 |
| Metal | `^CNXMETAL` | `26030` | 0 |
| Energy | `^CNXENERGY` | `26020` | 0 |
| Private Bank | `NIFTY_PVT_BANK.NS` | `26047` | 0 |
| PSU Bank | `^CNXPSUBANK` | `26025` | 0 |
| India Defence | `NIFTY_IND_DEFENCE.NS` | `26092` | 0 |
| Realty | `^CNXREALTY` | `26018` | 0 |
| Oil & Gas | `NIFTY_OIL_AND_GAS.NS` | `26071` | 0 |
| Consumer Durables | `NIFTY_CONSR_DURBL.NS` | `26070` | 0 |
| Commodities | `^CNXCMDT` | `26035` | 0 |
| Infrastructure | `^CNXINFRA` | `26019` | 0 |
| PSE | `^CNXPSE` | `26024` | 0 |
| CPSE | `NIFTY_CPSE.NS` | `26041` | 0 |

Shoonya's public NSE symbol master listed only the five existing derivative
index underlyings. The authenticated index catalogue was necessary for the
additional sector quote tokens. Missing from the public master did not mean
missing quote support.

Shoonya TPSeries returned no candles for the 16 new sector candidates. Wider
three-day control requests for IT, Pharma and Defence returned
`Not_Ok`, `invalid input[Token not prsent]`, despite successful quotes for the
same exchange/token. This is a history-service limitation observed today;
it does not establish absence of streaming quote support.

All 17 Yahoo NSE histories had invalid OHLC at 11:45 and 11:50 on 15 October.
The FOCIT control had invalid OHLC at 11:30 and 11:35 that day. NSE membership
therefore does not eliminate provider gaps. The 15:30 closing tick is excluded
from the expected 75 regular-session candle starts.

Yahoo chart metadata did not supply `exchangeDataDelayedBy`. After-hours
closing timestamps and successful REST requests cannot prove live arrival
latency. No new WebSocket was opened in these checks. Future implementation
must measure exchange tick age and bar completeness during a live session.

## Rough overlap check

Pearson correlation of aligned five-minute log returns, 12–16 October, using
only adjacent valid slots within each session. FOCIT historical timestamps
were aligned directly; its arrival delay does not shift those candle times.
Five sessions are descriptive context, not a stable correlation estimate or
a strategy backtest.

| Pair | Return correlation | Samples |
|---|---:|---:|
| IT / FOCIT | 0.9638 | 365 |
| Bank / Private Bank | 0.9101 | 368 |
| Bank / PSU Bank | 0.7007 | 368 |
| Energy / Oil & Gas | 0.8031 | 368 |
| Energy / Commodities | 0.8819 | 368 |
| Infrastructure / Realty | 0.6481 | 368 |
| PSE / CPSE | 0.9152 | 368 |

## Strategy and implementation implications

Keep the existing completed five-minute close outside the first nine
five-minute closes as a simple sector attention filter. It can confirm before
a wick-based opening range does. A percentage change since 09:15 is a separate
measurement and must not determine the breakout arrow.

Do not count overlapping baskets as independent confirmation votes. Verify
the liquidity and entry/invalidation of the stock, ETF or contract actually
traded. A weighted index breakout can be driven by a few large constituents.

Use labelled Yahoo history backfill where Shoonya history is unavailable,
collect native Shoonya ticks into bars, and retain source provenance.
Qualification still requires nine valid opening closes, a valid completed
confirmation bar and appropriate freshness. An isolated quote cannot fill an
unobserved interval. Retain separate provider data rather than overwriting
native history. Subscribe to all selected instruments over one Shoonya socket
and bound history-request concurrency when expanding the registry.

## Sources and reproduction

- [NSE sector catalogue](https://www.niftyindices.com/indices/equity/sectoral-indices)
- [NSE thematic catalogue](https://www.niftyindices.com/indices/equity/thematic-indices)
- [Nifty IT factsheet](https://www.niftyindices.com/Factsheet/ind_nifty_it.pdf)
- [BSE Focused IT](https://www.bseindices.com/indices-details/code/134/)
- [Consumer Durables factsheet](https://www.niftyindices.com/Factsheet/Factsheet_nifty_consumer_durables.pdf)
- [FMCG factsheet](https://www.niftyindices.com/Factsheet/ind_nifty_FMCG.pdf)
- [Infrastructure factsheet](https://www.niftyindices.com/Factsheet/ind_nifty_infra.pdf)
- [Energy factsheet](https://www.niftyindices.com/Factsheet/ind_nifty_energy.pdf)
- [Oil & Gas factsheet](https://www.niftyindices.com/Factsheet/Factsheet_nifty_oil_and_gas.pdf)
- [PSE factsheet](https://www.niftyindices.com/Factsheet/ind_nifty_pse.pdf)
- [CPSE factsheet](https://www.niftyindices.com/Factsheet/ind_Nifty_CPSE.pdf)
- [Defence factsheet](https://www.niftyindices.com/Factsheet/Factsheet_NiftyIndiaDefence.pdf)
- [Shoonya symbol master](https://shoonya.com/api-documentation/symbol-master)
- [Shoonya intraday history](https://shoonya.com/api-documentation/time-price-series)
- [Shoonya streaming](https://shoonya.com/api-documentation/websocket-overview)
- [Shoonya rate limits](https://shoonya.com/api-documentation/rate-limits)

Yahoo: public search API followed by `v8/finance/chart/{symbol}` with
`range=5d&interval=5m`; validate instrument type, exchange and every OHLC slot.
Raw results: `/tmp/orb-sector-yahoo-audit.json`.

Shoonya: one-shot read-only OAuth REST requests to GetIndexList, GetQuotes and
TPSeries, plus three wider history controls. Scripts:
`/tmp/orb_sector_shoonya_probe.py`, `/tmp/orb_sector_shoonya_followup.py`.
Sanitized results: `/tmp/orb-sector-shoonya-results.json`.
No credentials, account identifiers or session tokens are in the saved evidence.

### Shared-origin and toggle-off validation — 17 October

All 72 browser checks pass, including unequal first closes in both groups and
intervals, raw prices/returns/confirmations, genuine gaps, replay cutoffs, stable
quote-only references, scale reset when the first completed reference arrives,
and toggling either group off to NIFTY alone. JavaScript syntax and whitespace
checks pass. These refinements change only presentation and controls.
