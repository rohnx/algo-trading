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
without overwriting exported variables. Both providers subscribe to all six
indices through one socket:

| Index | Yahoo symbol | Shoonya subscription (exchange/token) |
| --- | --- | --- |
| NIFTY 50 (candles) | `^NSEI` | `NSE\|26000` |
| BANKNIFTY | `^NSEBANK` | `NSE\|26009` |
| Midcap Select | `NIFTY_MID_SELECT.NS` | `NSE\|26074` |
| Nifty Next 50 | `^NSMIDCP` | `NSE\|26013` |
| Fin Nifty | `NIFTY_FIN_SERVICE.NS` | `NSE\|26037` |
| BSE Focused IT (FOCIT) | `FOCIT.BO` (15-minute delay) | `BSE\|75` |

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
Until a complete bar is collected, the chart shows the current quote as a dot.
Yahoo supplies FOCIT history and streaming quotes with a 15-minute delay.
Its line-end label marks the delay with **D** and shows the upstream quote time
and delay length in its title;
its bars close against that delayed clock. If updates stop, it becomes stale.
Both providers keep separate saved data; the FOCIT backfill is composed only for
display and cannot affect NIFTY's ORB signals or Telegram alerts.

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

The **Derivatives** button shows or hides the five comparison lines together:
BANKNIFTY (amber), Midcap Select (teal), Next 50 (plum), Fin Nifty (rose), and
FOCIT (green).
Each compares movement since its **09:15 session open**. Its vertical position
is `NIFTY 50 open × index price / index open`, so equal percentage moves occupy
the same height. The right axis remains equivalent NIFTY 50 prices.

The chart fits NIFTY's candles and every valid point of the visible comparison
lines, with padding above and below. Large divergences remain fully visible;
hiding the group refits the scale to NIFTY alone. Switching between 15m and 5m
keeps the scale steady unless the finer data needs more room. Comparison names
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

Qualifying comparison lines and directional labels remain visible; other
comparisons are hidden in the idle view. Hover NIFTY's candles, header, price
marker or margin label to reveal the nonqualifying comparisons softly blurred.
Focusing the NIFTY margin label offers the same reveal from the keyboard.
Hover a qualifying line or hover/focus its label to isolate it further.
Leaving returns to the idle view. Before any index qualifies, hovering NIFTY
still reveals the comparisons so their progress can be inspected.
Stale or unavailable quotes do not qualify for emphasis. Delayed quotes remain
identified, and replay evaluates only the 5-minute closes already reached.

The lines use the selected interval's native closes: 15-minute bars in 15m,
5-minute bars in 5m, plus the forming bar when a valid live quote is available.
Each point aligns with its candle's center. Missing or invalid
bars break the line.
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
