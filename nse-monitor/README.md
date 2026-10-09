# NIFTY ORB monitor

From the workspace root, run:

```sh
.venv/bin/python nse-monitor/orb_monitor.py
```

Open http://localhost:8765. Use `--port` to choose another port. Yahoo is the
default source. The source dropdown switches to Shoonya or back to Yahoo; only
the selected provider has an upstream socket. The selection is shared by browser
tabs connected to this monitor process. A Shoonya failure is shown in the feed
status; select Yahoo to fall back without mixing providers' candles.

Shoonya uses the same OAuth SDK and environment variables as `shoonya/live_feed.py`:
`SHOONYA_CLIENT_ID`, `SHOONYA_USER_ID`, `SHOONYA_SECRET_CODE`, and a fresh
`SHOONYA_AUTH_CODE`. The adapter reads the workspace `.env` and `shoonya/.env`
without overwriting exported variables. It subscribes only to NIFTY (`NSE|26000`).
An authenticated session is reused for same-day source switches; expired sessions,
changed credentials, and a new IST day require a fresh login. Editing the `.env`
auth code takes effect the next time Shoonya is selected.
You can also start directly with `.venv/bin/python nse-monitor/orb_monitor.py --source shoonya`.

The chart starts fresh each IST day, including closed-market days. Use the
navigation arrows or date picker to select sessions. **Today** returns to today's
chart. Live updates continue in the background while you inspect history.

Rising and falling candles use distinct gray tones in both light and dark mode.
The small moon button switches themes and remembers the preference. Hover to
inspect OHLC. The 15m/5m buttons animate between real candle intervals within the
same full-day width. Each interval displays its own opening range and arrows:

| Chart | Opening range | Breakout confirmation |
| --- | --- | --- |
| 15m | First 30 minutes, 09:15–09:45 IST | A 15-minute close outside that range |
| 5m | First 45 minutes, 09:15–10:00 IST | A 5-minute close outside that range |

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

Yahoo is queried for about 60 days of 5-minute history, which is also aggregated
into 15-minute bars. Shoonya's NIFTY history is loaded where available. Completed
sessions are saved separately under `data/yahoo/` and `data/shoonya/`, with native
5-minute bars in each provider's `5m/` directory. Old date-only Yahoo files remain
readable. A session saved only at 15 minutes cannot supply genuine 5-minute bars;
the chart explains when that interval is unavailable. Such a session can show
the 15m setup's arrows, but cannot confirm the 5m setup or the main signal.

`--replay` simulates the latest completed session in a separate run. For piping
timestamped Yahoo quotes, use:

```sh
.venv/bin/python nse-monitor/yf-nse-data.py | .venv/bin/python nse-monitor/orb_monitor.py --pipe
```

Regression checks:

```sh
.venv/bin/python -m unittest discover -s nse-monitor/tests
node nse-monitor/tests/test_ui.cjs
```

The browser checks require Playwright and Chrome (the bundled Playwright runtime
is discovered automatically where available). Set `PLAYWRIGHT_PATH` to a
Playwright package directory and `CHROME_PATH` to the browser executable if they
are not installed at their usual locations. They use sample data and make no
requests to Yahoo or Shoonya. Adapter tests use mock sockets and mock login.
