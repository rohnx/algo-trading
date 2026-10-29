# PCR and expiry feasibility — 23 October 2026

## Finding

The raw inputs for a NIFTY options PCR graph are available through Shoonya.
Yahoo returned no NIFTY or SENSEX option chains in the successful requests tested.
Shoonya also publishes exact NIFTY and SENSEX expiry dates without authentication.

This investigation proves access to ingredients. It does **not** establish a
complete-chain PCR, a historical PCR series, or the effectiveness of the proposed
confirmation/risk policy. No monitor behavior was changed.

## Measured evidence

The authenticated Shoonya probe ran at **09:34:31–09:34:38 IST**. The final Yahoo
probe began at **09:34:25 IST**. These were actual HTTP/SDK calls during the
session, rather than mocks or conclusions drawn only from documentation.

| Probe | Observed result | What it establishes |
| --- | --- | --- |
| Yahoo `^NSEI` and `^BSESN` options | HTTP 200, no API error, zero expiries/calls/puts; `option_chain()` returned empty members | No usable Indian index option-chain data for the tested symbols at query time |
| Yahoo SPY control | HTTP 200, 28 expiries, 164 calls and 164 puts for the nearest expiry; OI present on all 328 contracts | The options request path worked; the Indian result was not a general authentication/network failure |
| Yahoo NIFTY five-minute history | 304 rows, latest timestamp 09:30 IST | The existing NIFTY price-data path worked |
| Shoonya NIFTY index quote | Successful authenticated response | Account access and the existing NIFTY token worked |
| Shoonya option chain, `count=2` | Eight contracts around the selected strike; contract fields but no `oi` or `lp` | The endpoint resolves contracts; OI must be obtained separately for this account/API path |
| Shoonya nearest-expiry call and put quotes | Positive `oi` and `v` for both contracts | Current option open interest and traded volume are accessible |
| Shoonya five-minute history for those two contracts | Four rows each with `oi`, `intoi`, OHLC and volume | Same-day per-contract OI history exists; complete-universe history remains untested |
| Shoonya WebSocket | Two token subscriptions, ten packets; initial `tk` packets included `oi`, `poi`, and `ft` | Streaming can supply the OI baseline; the short `tf` sample did not include an OI update |
| Public NFO master | 4,067 NIFTY option contracts over 18 expiries; nearest expiry has 261 calls and 261 puts | Exact dates and a potential full-expiry universe can be enumerated |
| Public BFO master | 3,196 SENSEX options over 19 expiries | SENSEX expiry metadata is available; its master symbol is `BSXOPT` |

The single sampled strike was 22,700, expiry 27 October. Its call OI was
8,167,380 and put OI 13,866,125. Those two observations are **not a full NIFTY
PCR** and were not used to issue a confirmation or risk recommendation.

### Scope of the expiry result

The next listed expiries were **NIFTY 27 October** and **SENSEX 29 October**.
23 October did not appear as an expiry for either underlying in these masters.
The corresponding dates also appeared in each index's near-month futures list.

The files contain exact dates, not an explicit weekly/monthly flag. Current
futures dates and exchange rules can support classification of the near months;
holiday adjustments and historical sessions still require an explicit policy.
The NIFTY master already listed Monday expiries on 9 and 23 November, so a
Tuesday-only test would be incorrect.

The initial authenticated probe filtered BFO by `Symbol=SENSEX`, yielding no
matches. A subsequent public-only probe corrected the filter to `BSXOPT`.
The saved Shoonya result retains both observations; use its
`public_master_followup` or the dedicated expiry result for corrected metadata.
SENSEX authenticated quotes/history/streaming were not tested.

## Important data distinctions

1. **OI PCR and volume PCR are different measures.** For an OI-based definition,
   PCR is the sum of put OI divided by the sum of call OI over the agreed expiry
   and strike universe. Missing inputs must not become zero OI; a zero denominator
   does not produce a usable confirmation.
2. **A chain listing is not a complete PCR observation.** The nearest NIFTY expiry
   has 522 contracts; only two were quoted/subscribed. Full-universe acquisition,
   missing strikes, rate limits, and stream capacity were not stress-tested.
3. **Use total `oi`, not interval `intoi`, for an OI-level PCR.** The latter is a
   change field in the observed candle schema.
4. **The newest historical bucket may still be forming.** The 09:30 bucket was
   returned at 09:34, before its 09:35 end. It cannot be used as a completed
   five-minute observation at that moment.
5. **Price freshness does not establish OI freshness.** Initial stream packets
   included OI, but subsequent price deltas omitted it. Track explicit OI
   observations separately from price/packet times; no independent exchange OI
   timestamp or cadence guarantee was established by this short probe.
6. **Yahoo's `option_chain(date)` selects an expiry.** It does not retrieve a
   historical observation date. `lastTradeDate` is a trade timestamp, not an OI
   freshness timestamp.
7. **History coverage remains limited.** Same-day OI for two current contracts was
   demonstrated. Prior-day, expired-contract, full-chain, and 60-day PCR coverage
   were not demonstrated. A graph/replay must not backfill past points with
   today's OI or with future observations.
8. **Provider policy needs a decision.** A Yahoo price chart could use Shoonya PCR
   only with an explicit separate source/lifecycle policy. The existing monitor
   otherwise has one selected provider. Public expiry metadata can be used
   independently of authenticated PCR.

## Proposed risk table, transcribed from the user's screenshot

These are user-supplied ranges/caps, not measured results from this investigation.
The meaning of R, its base unit, and selection within each range are unresolved.

| Day category | Before 10:15 | On 10:15 | After 10:15 |
| --- | --- | --- | --- |
| Non-expiry | 0.8–1.0 | 0.7–0.9 | 0.1–0.3 |
| SENSEX weekly | 0.4–0.6 | 0.3–0.5 | 0.1–0.3 |
| NIFTY weekly | 0.1–0.5 | 0.1–0.3 | ≤0.2 |
| SENSEX monthly | 0.1–0.5 | 0.1–0.3 | ≤0.1 |
| NIFTY monthly | 0.1–0.3 | 0.1–0.2 | ≤0.1 |

Do not choose a midpoint or turn a cap into a fixed value without a user decision.

## Decisions for the specification conversation

- Is PCR based on OI or volume? Which expiry and strikes belong to its universe?
  If ATM-based, does the band move or remain fixed for the session?
- Is the graph a separate pane, what is its cadence, and what coverage/freshness
  makes a point eligible for confirmation?
- Does ORhB/ORlB mean a particular setup's close or the existing dual-ORB main
  signal? Is PCR sampled as of that close, and does equality at 1 remain neutral?
- Is PCR informational, an additional qualified signal, or a gate on existing
  signals/Telegram alerts? What happens when data is missing or stale?
- Does PCR operate only with Shoonya selected, or also alongside a Yahoo price
  chart? What connections and provenance should that permit?
- Does the expiry badge describe the viewed session date or today? How are
  historical dates, holidays, monthly-versus-weekly overlap, and conflicting
  categories resolved?
- Does R mean a risk-budget/position multiplier, a reward target, or another
  quantity? Should the UI show the table's range or select one number by an
  agreed rule? Which confirmed timestamp defines before/on/after 10:15 IST?

## Reproduce the read-only tests

From the workspace root, with the installed environment:

```sh
uv run python orb/research/pcr-expiry-2026-10-23/yahoo_probe.py --output /tmp/yahoo-pcr-new.json
uv run python orb/research/pcr-expiry-2026-10-23/expiry_probe.py --output /tmp/expiry-new.json
uv run python orb/research/pcr-expiry-2026-10-23/shoonya_probe.py --output /tmp/shoonya-pcr-new.json
```

The Shoonya probe reads configured credentials privately and makes at most one
authentication attempt. Its authenticated market tests are limited to NIFTY;
both public masters are inspected. It opens and closes a brief market-data
socket. The scripts have bounded waits and make no order or Telegram calls.
Do not run them repeatedly merely to refresh this dated evidence.

The preserved scripts were adjusted for an explicit output path and installed
package imports. The public BFO filter was corrected; authenticated scope stays
NIFTY-only. Recorded result snapshots retain the actual original observations.
The expiry snapshot omits the full 2.5 MB contract listing and retains the summary.

## Sources and saved evidence

- [Yahoo probe](yahoo_probe.py) and [sanitized result](yahoo-result.json).
- [Shoonya probe](shoonya_probe.py) and [sanitized result](shoonya-result.json).
- [Public expiry probe](expiry_probe.py) and [summary](expiry-result.json).
- [Official yfinance option-chain implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/ticker.py).
- [Official Shoonya SDK documentation](https://github.com/Shoonya-Dev/ShoonyaApi-py#readme): distinguishes contract discovery, quote OI, candle `oi`/`intoi`, and stream fields.
- [Shoonya option-chain page](https://www.shoonya.com/api-documentation/option-chain): its OI/price example conflicts with this actual response; preserve that distinction.
- [Shoonya instrument-master documentation](https://www.shoonya.com/api-documentation/instrument-token-list).
- [NSE contract specifications](https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications): expiry holidays move to the previous trading day.
- [BSE notice 20250623-59](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20250623-59) was inaccessible to this browser (403); no BSE schedule conclusion here depends solely on that failed fetch.

See [skill selection](SKILLS.md) and [fresh-conversation prompts](FRESH-CHAT.md) for
the next stage. These files describe the proposed workflow; they do not approve
an implementation or settle the open strategy choices.
