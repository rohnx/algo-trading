# NIFTY ORB monitor

From the workspace root, run:

```sh
uv run orb/orb_monitor.py
```

Open http://localhost:8765. Use `--port` to choose another port. Yahoo is the
default source. The source dropdown switches to Shoonya or back to Yahoo; only
the selected provider has an upstream socket. The selection is shared by browser
tabs connected to this monitor process. A Shoonya failure is shown in the feed
status; select Yahoo to fall back without mixing providers' candles.

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
without overwriting exported variables. Both providers subscribe to all five
indices through one socket:

| Index | Yahoo symbol | Shoonya NSE token |
| --- | --- | --- |
| NIFTY 50 (candles) | `^NSEI` | `26000` |
| BANKNIFTY | `^NSEBANK` | `26009` |
| Midcap Select | `NIFTY_MID_SELECT.NS` | `26074` |
| Nifty Next 50 | `^NSMIDCP` | `26013` |
| Fin Nifty | `NIFTY_FIN_SERVICE.NS` | `26037` |

Midcap Select is distinct from Midcap 50. Yahoo's `^CNXFIN` is the Financial
Services 25/50 index, so it is not used for Fin Nifty. Tokens are listed in the
[official Shoonya symbol master](https://api.shoonya.com/NSE_symbols.txt.zip).

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
The small moon button switches themes and remembers the preference. Hover to
inspect OHLC. The 15m/5m buttons animate between real candle intervals within the
same full-day width. Each interval displays its own opening range and arrows:

| Chart | Opening range | Breakout confirmation |
| --- | --- | --- |
| 15m | First 30 minutes, 09:15–09:45 IST | A 15-minute close outside that range |
| 5m | First 45 minutes, 09:15–10:00 IST | A 5-minute close outside that range |

The **NIFTY** button shows or hides the four comparison lines together:
BANKNIFTY (amber), Midcap Select (teal), Next 50 (plum), and Fin Nifty (rose).
Each compares movement since its **09:15 session open**. Its vertical position
is `NIFTY 50 open × index price / index open`, so equal percentage moves occupy
the same height. The right axis remains equivalent NIFTY 50 prices.

The chart fits NIFTY's candles and every valid point of the visible comparison
lines, with padding above and below. Large divergences remain fully visible;
hiding the group refits the scale to NIFTY alone. Switching between 15m and 5m
keeps the scale steady unless the finer data needs more room. The compact header
leaves more height for the chart;
hover to inspect NIFTY OHLC and each comparison's actual price, percentage,
and candle time in a floating readout.

The lines use the selected interval's native closes: 15-minute bars in 15m,
5-minute bars in 5m, plus the forming bar when a valid live quote is available.
Each point aligns with its candle's center, and hover readings use the same
candle start time; their title retains the actual quote time. Missing or invalid
bars break the line.
A missing opening baseline makes comparison unavailable, rather than treating
the first late quote as the open. Each index has its own freshness: after 90
seconds without a quote, its reading says **stale** and its current percentage
becomes unavailable. Stale comparison history remains visible but dimmed.
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

The small play button replays the selected day's available candles and signals.
This is an illustrative OHLC progression, not a recording of the actual tick
path. Replay runs entirely in the browser while live prices keep collecting.
Stop replay to return to the latest view; changing date or source also stops it.

Live candle bodies and line endpoints ease into new quotes over 180 ms; displayed
quote values, hover readings, and signal timing still use the received data.
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
`orb_monitor.py` coordinates the instruments and owns NIFTY's ORB strategies.
Adding an instrument uses the same state engine, feed paths, and chart renderer;
the registry defines its canonical symbol, label, and provider identifiers.
