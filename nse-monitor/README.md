# NIFTY ORB monitor

From the workspace root, run:

```sh
.venv/bin/python nse-monitor/orb_monitor.py
```

Open http://localhost:8765. Use `--port` to choose another port.

The chart starts fresh each IST day, including closed-market days. Scroll up over
the chart or date controls for an earlier trading session; scroll down for a later
one. The arrows and date picker also select sessions. **Today** returns to today's
chart. Live updates continue in the background while you inspect history.

Rising candles are dark gray; falling candles are light gray. Hover to inspect
OHLC. The opening range covers 09:15–10:00; arrows mark the first confirmed
15-minute close outside each side of the range. Incomplete opening data cannot
lock the range. The monitor closes the final candle at 15:30 without needing
another quote.

Yahoo supplies about 60 days of 15-minute history. Completed sessions are saved
in `data/` and remain available beyond that window. Dates without available prices
show an empty chart with an explanation.

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

The browser checks require Playwright and Chrome. Set `PLAYWRIGHT_PATH` to a
Playwright package directory and `CHROME_PATH` to the browser executable if they
are not installed at their usual locations. They use sample data and make no
requests to Yahoo.
