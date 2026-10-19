# NIFTY ORB monitor

From the workspace root, run:

```sh
uv run orb/orb_monitor.py
```

Open http://localhost:8765. Use `--port` to choose another port. Yahoo is the
default source. The source dropdown switches to Shoonya or back to Yahoo; only
the selected provider has an upstream socket. The selection is shared by browser
tabs connected to this monitor process. A Shoonya failure is shown in the feed
status. FOCIT uses labelled Yahoo history backfill when Shoonya's history service
rejects it, while its live quotes continue through Shoonya.

Repeated same-day switches reuse loaded history. Target-source stream data can
paint before the switch response arrives, and an older response cannot replace
newer quotes. Yahoo sockets use 3-second open and 1-second close timeouts;
Shoonya login/history requests use 3-second connection and 10-second read
timeouts. Pending history requests are serialized across switches.
Quotes carry active bars and summary values; closed history is sent on session
loads, backfills, bar closes, and recovery. Unchanged saved bars are not rewritten.

Shoonya uses the same OAuth SDK and environment variables as `shoonya/live_feed.py`:
`SHOONYA_CLIENT_ID`, `SHOONYA_USER_ID`, `SHOONYA_SECRET_CODE`, and a fresh
`SHOONYA_AUTH_CODE`. The adapter reads the workspace `.env` and `shoonya/.env`
without overwriting exported variables. Both providers subscribe to all 21
indices through one socket. Comparison tabs choose what is displayed while
prices continue collecting for both groups.

The primary chart and **Derivatives** group use:

| Index | Yahoo symbol | Shoonya subscription (exchange/token) |
| --- | --- | --- |
| NIFTY 50 (candles) | `^NSEI` | `NSE\|26000` |
| BANKNIFTY | `^NSEBANK` | `NSE\|26009` |
| Midcap Select | `NIFTY_MID_SELECT.NS` | `NSE\|26074` |
| Nifty Next 50 | `^NSMIDCP` | `NSE\|26013` |
| Fin Nifty | `NIFTY_FIN_SERVICE.NS` | `NSE\|26037` |
| BSE Focused IT (FOCIT) | `FOCIT.BO` (15-minute delay) | `BSE\|75` |

The **NIFTY Sectors** group uses these 15 indices, with short chart labels:

| Label | Yahoo symbol | Shoonya subscription |
| --- | --- | --- |
| Pharma | `^CNXPHARMA` | `NSE\|26023` |
| Auto | `^CNXAUTO` | `NSE\|26029` |
| FMCG | `^CNXFMCG` | `NSE\|26021` |
| Metal | `^CNXMETAL` | `NSE\|26030` |
| PSU Bank | `^CNXPSUBANK` | `NSE\|26025` |
| Realty | `^CNXREALTY` | `NSE\|26018` |
| Consumer Durables | `NIFTY_CONSR_DURBL.NS` | `NSE\|26070` |
| Oil & Gas | `NIFTY_OIL_AND_GAS.NS` | `NSE\|26071` |
| Private Bank | `NIFTY_PVT_BANK.NS` | `NSE\|26047` |
| Energy | `^CNXENERGY` | `NSE\|26020` |
| India Defence | `NIFTY_IND_DEFENCE.NS` | `NSE\|26092` |
| Infrastructure | `^CNXINFRA` | `NSE\|26019` |
| PSE | `^CNXPSE` | `NSE\|26024` |
| Commodities | `^CNXCMDT` | `NSE\|26035` |
| CPSE | `NIFTY_CPSE.NS` | `NSE\|26041` |

After-hours checks on 16 October 2026 verified quotes for all 15 sector tokens
and Yahoo five-minute history for each. Shoonya returned no intraday history
for these sectors; the monitor uses labelled Yahoo backfill while collecting
native Shoonya bars. Native history fetched from Shoonya remains available for
the existing five NSE chart/Derivatives instruments. The sector checks verified
REST quote availability, not live WebSocket arrival timing; live sector latency
still requires a market-hours check. See the
[provider audit](research/sector-audit-2026-10-16.md) for the recorded results.
An isolated check on 17 October authenticated successfully, subscribed all 21
instruments over one Shoonya socket, and received price-bearing subscription
snapshots for all 15 sectors. This confirms connection and routing, without
measuring changing live ticks during an open market. The sanitized
[connection check](research/sector-connection-check-2026-10-17.json) records it.

After updating this code, restart `uv run orb/orb_monitor.py` and refresh the
browser tab so both the expanded registry and new tab controls load.

Midcap Select is distinct from Midcap 50. Yahoo's `^CNXFIN` is the Financial
Services 25/50 index, so it is not used for Fin Nifty. Tokens are listed in the
[official Shoonya symbol master](https://api.shoonya.com/NSE_symbols.txt.zip).
FOCIT is the [BSE Focused IT index](https://www.bseindices.com/indices-details/code/134/),
distinct from the broader BSE IT index. Its Shoonya token comes from the
authenticated `GetIndexList` endpoint listed in the
[official API collection](https://shoonya.com/api-documentation/postman-collection).
Index tokens can differ between brokers. Shoonya routes quotes by exchange and
token together, so BSE and NSE tokens cannot collide.

Live checks on 13 October 2026 confirmed Shoonya FOCIT quotes and changing
WebSocket ticks on `BSE|75`. Shoonya's history service currently rejects this
index, so completed Yahoo candles backfill its chart, with **H** beside its
line-end label and Yahoo history/provider details in the label's title. Native Shoonya bars and
newer quotes take priority. Delayed, forming Yahoo candles cannot become native
Shoonya history, and missing intervals remain gaps. The touchline's reported session
open supplies the percentage baseline; it does not fabricate opening candles.
Reported openings are saved by provider and session, so collected bars keep
their percentage baseline after a restart. Native opening candles take priority.
If a 15-minute bar is incomplete, its valid 5-minute samples remain visible.
If neither interval has a usable forming bar, the chart shows the current quote
as a separate dot; it never joins that quote across unobserved trading.
While Shoonya is selected, small same-day Yahoo history requests run every five
minutes for indices that need backfill, including FOCIT and the 15 sectors.
They fill gaps before the live connection as completed candles become available,
without pausing the Shoonya socket or replacing its valid bars. History and
backfill requests have bounded concurrency so expanding the registry does not
start one active HTTP request per index at once.
Yahoo supplies FOCIT history and streaming quotes with a 15-minute delay.
Its line-end label marks the delay with **D** and shows the upstream quote time
and delay length in its title;
its bars close against that delayed clock. If updates stop, it becomes stale.
Both providers keep separate saved data; backfill is composed for comparison
display and each comparison's close-only breakout, and cannot affect NIFTY's
ORB signals or Telegram alerts. **H** identifies Yahoo history contribution;
**D** identifies a delayed current quote. The Shoonya dropdown choice does not
mean every historical candle came from Shoonya.

An authenticated session is reused for same-day source switches; expired sessions,
changed credentials, and a new IST day require a fresh login. Editing the `.env`
auth code takes effect the next time Shoonya is selected.
You can also start directly with `.venv/bin/python orb/orb_monitor.py --source shoonya`.

When Shoonya reports a login error, **Login help** beside the feed status opens a
compact recovery panel. It displays your configured client ID and OAuth link; update
`SHOONYA_AUTH_CODE` after login and press **Retry Shoonya**. **Check public IP**
queries `https://ifconfig.me/ip` from the monitor's machine only when pressed.
Confirm that address matches Shoonya's API IP setting, or run
`curl -s ifconfig.me` on the same machine. The panel cannot read your account's
whitelist or confirm that it matches automatically. See Shoonya's
[OAuth login](https://shoonya.com/api-documentation/manual-login-oauth) and
[IP setup](https://www.shoonya.com/api-documentation/ip-whitelisting) guidance.

The chart starts fresh each IST day, including closed-market days. Use the
navigation arrows or date picker to select sessions. **Today** returns to today's
chart. Live updates continue in the background while you inspect history.
Historical views and finished replays redraw only when their visible state changes.
Idle browser connections receive a lightweight keep-alive; market-hour changes
publish status once, and the saved-date list refreshes on session/source events
or navigation rather than polling every minute.
Healthy connection status appears in the header; the footer shows connection
problems and recovery guidance when needed.

Rising and falling candles use distinct gray tones in both light and dark mode.
The small moon button switches themes and remembers the preference. The
15m/5m buttons animate between real candle intervals within the
same full-day width. Each interval displays its own opening range and arrows:

| Chart | Opening range | Breakout confirmation |
| --- | --- | --- |
| 15m | First 30 minutes, 09:15–09:45 IST | A 15-minute close outside that range |
| 5m | First 45 minutes, 09:15–10:00 IST | A 5-minute close outside that range |

The **Derivatives** and **NIFTY Sectors** tabs select at most one comparison
group. Derivatives contains BANKNIFTY (amber), Midcap Select (teal), Next 50
(plum), Fin Nifty (rose), and FOCIT (green). NIFTY Sectors contains the 15
sector indices listed above. Selecting a tab removes the other group's lines
and labels; clicking the active tab again hides comparisons, leaving NIFTY alone.
Tab changes preserve
the date, provider, 5m/15m interval and replay position, and do not reconnect
the feed or reset opening ranges. Only the selected group's visible lines
participate in scale fitting and hover interactions.
Comparison paths and labels both measure returns from each index's **09:15
session open**. Their shared starting point is NIFTY's session open at the left
margin: a +1% index plots at 1.01 times NIFTY's open. The reference stays the
same in both intervals, live views, history and replay. The opening anchor is
display-only; it creates no candle or breakout confirmation, and missing opening
history stays disconnected. The right axis remains equivalent NIFTY prices.

The chart fits NIFTY's candles and every valid point of the visible comparison
lines, with padding above and below. Large divergences remain fully visible;
hiding the group refits the scale to NIFTY alone. Switching between 15m and 5m
changes the sampled closes while preserving their opening-price baseline. Comparison names
and percentage changes sit beside their line ends, with collision spacing,
subtle connectors and an opaque text background that masks underlying strokes.
NIFTY's name and return sit in the right price-axis margin beside its price.
There is no floating hover card or comparison legend above
the plot. Labels retain quote times, availability and provider details in their
accessible text and titles.

Each comparison has an independent **close-only 45-minute opening range**.
It uses the highest and lowest of the nine complete, valid 5-minute closes from
09:15 through 10:00; candle highs and lows do not enter this range. A later
complete 5-minute close strictly above or below that band qualifies the index,
with the first possible confirmation at 10:05. Equality or a close back inside
the band clears qualification. Incomplete opening data leaves the range unlocked,
and an invalid latest closed bar cannot confirm a breakout. The same rule applies
in both chart intervals, separately from NIFTY's existing dual-ORB strategy.
During live trading and replay, emphasis expires when the next five-minute close
is missing. Fresh quotes alone cannot revive the older confirmation; delayed
feeds use their own source clock. Label titles include the closing range and
last confirmation price for verification.

Before NIFTY's first dual-ORB signal of the session, nonqualifying comparisons
remain visible, softly blurred, without hovering. Qualifying comparison lines
and directional labels stay crisp. After NIFTY confirms, nonqualifying
comparisons are hidden in the idle view. Hover NIFTY's candles, header, price
marker or margin label to reveal the nonqualifying comparisons softly blurred.
Focusing the NIFTY margin label offers the same reveal from the keyboard.
Hover a qualifying line or hover/focus its label to isolate it further.
Leaving returns to the idle view. A return inside NIFTY's range does not restore
the default overview after that session's first signal. Replay uses the same
transition when it reaches the signal.
The price scale fits all comparisons during the initial overview, then NIFTY
and qualifying indices in the idle view after confirmation. Revealing
comparisons temporarily fits all their lines; leaving restores the idle scale,
so an invisible index cannot compress NIFTY's candles.
Stale or unavailable quotes do not qualify for emphasis. Delayed quotes remain
identified, and replay evaluates only the 5-minute closes already reached.

The lines use the selected interval's native closes: 15-minute bars in 15m,
5-minute bars in 5m, plus the forming bar when a valid live quote is available.
An incomplete 15-minute comparison bar uses its available 5-minute children,
so a partial parent cannot hide valid recent closes or its usable live child.
An explicitly valid observed close can still be plotted when its candle's OHLC
is incomplete after a late connection. This does not promote that partial candle
into a complete strategy-confirmation candle.
Completed comparison closes appear at their closing times (first at 09:30 in
15m, 09:20 in 5m); forming samples use their actual quote times. Endpoint labels
use the price and time of the plotted sample, including during animation. Missing or invalid
bars break the line. A compact **Data gap** notice in the footer identifies
missing or incomplete closed NIFTY intervals. It follows the selected session
and replay clock, without labelling the unfilled future part of today as missing.
Unavailable provider prices are never interpolated into candles or signals.
A missing opening baseline makes comparison unavailable, rather than treating
the first late quote as the open. Each index has its own freshness: after 90
seconds without a quote, its reading says **stale** and its current percentage
becomes unavailable. Revealed stale comparison history is dimmed further.
Comparison availability does not affect NIFTY's ORB signals. During browser
replay, only comparison closes already
reached by the replay clock are visible.

Each chart marks the first confirmed close in each direction for its own setup.
The main signal requires **both setups to agree in the same direction** on their
latest valid closes. It records the time the second confirmation completes, with
one main signal per direction per day. A close back inside its range clears that
setup's current agreement; an earlier arrow remains visible but does not keep
the main signal eligible. A missing or invalid latest close also blocks agreement.
Incomplete opening data cannot lock the affected range.
Disconnects and missing quotes invalidate affected bars, and stale prices cannot
confirm a close. The final candle still closes at 15:30 without another quote.

Telegram alerts use the main dual-ORB signal. Add `TELEGRAM_TOKEN` and
`TELEGRAM_CHAT_ID` to the workspace `.env`, then restart the monitor. For a private
chat, open your bot in Telegram and send `/start` before enabling delivery.
Exported environment variables take priority over `.env`. Missing configuration
disables Telegram and prints the missing variable names at startup.

Each alert includes the direction, second confirmation time in IST, NIFTY price,
provider, and both setups' confirmation prices and opening ranges. Only new live
NIFTY confluence signals enqueue alerts. When a live connection's initial history
recovers a confirmation from the last two minutes, it also queues that alert;
this covers a connection or source switch spanning the confirmation time.
Older history, browsing dates and browser replay never send alerts. Alerts are
limited to one per direction per IST day across providers and restarts.
The local `/api/session` response includes `telegram_status` for delivery
diagnostics, without credentials.

Delivery runs on a worker so Telegram cannot block quotes or bar closes. Its
durable outbox is saved under `data/notifications/`. Transient network/server
failures and rate limits retry up to five attempts; pending alerts can resume
after a restart within ten minutes of the signal. Older pending alerts expire.
Permanent or exhausted failures are logged without credentials and remain
recorded rather than repeatedly sending the same signal. Telegram has no
message idempotency key, so a lost response after successful delivery can still
produce a duplicate on retry.

The small play button replays the selected day's available candles and signals.
This is an illustrative OHLC progression, not a recording of the actual tick
path. Replay runs entirely in the browser while live prices keep collecting.
Stop replay to return to the latest view; changing date or source also stops it.

Live candle bodies and line endpoints ease into new quotes over 180 ms; displayed
quote values and signal timing still use the received data.
The scale holds steady within a view and expands when needed, rather than
contracting with each quote. Replay softens its OHLC turns and reveals comparison
segments only after their saved closes are reached. Reduced-motion preferences
disable these display transitions.

Yahoo is queried for a 60-day period of 5-minute history, which is also aggregated
into 15-minute bars. Shoonya's index history is loaded where available. Each
instrument loads independently, so missing comparison history does not hold up
NIFTY. Completed sessions are saved separately under `data/yahoo/` and
`data/shoonya/`, with native 5-minute bars in each provider's `5m/` directory.
Sessions older than 60 calendar days are removed at startup and each IST date
rollover, using the session date in the filename. The date exactly 60 days ago
is retained. Retention covers both providers, both intervals, and legacy files;
expired backfills are ignored so they cannot recreate removed sessions.
Comparison indices use the provider's symbol subdirectory (without a leading
`^`), each with its own `5m/` directory. Old date-only Yahoo files
remain readable. A session saved only at 15 minutes cannot supply genuine 5-minute bars;
the chart explains when that interval is unavailable. Such a session can show
the 15m setup's arrows, but cannot confirm the 5m setup or the main signal.

The normal provider manager is the sole live-data path. Replay runs in the chart;
the former command-line replay and quote-pipe entry points have been removed.

Regression checks:

```sh
.venv/bin/python -m unittest discover -s orb/tests
node orb/tests/test_ui.cjs
```

The browser checks require Playwright and Chrome (the bundled Playwright runtime
is discovered automatically where available). Set `PLAYWRIGHT_PATH` to a
Playwright package directory and `CHROME_PATH` to the browser executable if they
are not installed at their usual locations. They use sample data and make no
requests to Yahoo or Shoonya. Adapter tests use mock sockets and mock login.

`market.py` contains the instrument registry and shared bar, session, freshness,
and cache logic. `feeds.py` routes provider data through that registry;
`orb_monitor.py` coordinates the instruments and owns NIFTY's ORB strategies;
`comparison.py` evaluates the comparison indices' close-only breakout states.
Adding an instrument uses the same state engine, feed paths, and chart renderer;
the registry defines its canonical symbol, label, and provider identifiers.
