# Algo Trading & Market Toolkit

A comprehensive Python-based algorithmic trading and market monitoring toolkit designed for Indian equity markets (NSE/BSE).

The repository includes:
1. **[NIFTY ORB Monitor](#1-nifty-orb-monitor-orb)**: A real-time NIFTY 50 dashboard with 15m/30m and 5m/45m Opening Range Breakout setups, plus a main signal when both agree.
2. **[Position & Risk Calculator](#2-position--risk-calculator-ervpy)**: A volatility-adjusted position sizing and risk management tool factoring in exchange fees, brokerages, and regulatory taxes.
3. **[Trading Journal](journal/README.md)**: A saved monthly journal with editable daily-risk and expiry calendars, intraday/swing sizing, and Excel import/export.

---

## Setup & Installation with `uv`

This project uses [`uv`](https://docs.astral.sh/uv/) for fast Python package and environment management.

### 1. Install `uv` (if not already installed)
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```
*(Alternatively, via Homebrew: `brew install uv` or pip: `pip install uv`)*

### 2. Environment Setup
The project requires Python **>= 3.14** (specified in `.python-version` and `pyproject.toml`). `uv` will automatically download the correct Python version if it is not present on your system.

From the workspace root, run:
```bash
uv sync
```
This sets up the virtual environment in `.venv/`, installs the `orb` package with its `orb`, `journal`, and `erv` commands, and installs the locked dependencies.

### 3. Running Scripts
You can run any script directly using `uv run` without manually activating the virtual environment:
```bash
# Run the ORB Monitor
uv run orb

# Run the Position Sizing Calculator
uv run erv 2500 500 25

# Open the Trading Journal
uv run journal
```

Alternatively, you can activate the environment manually:
```bash
source .venv/bin/activate
orb
```

`python -m orb` runs the same monitor. The existing checkout command
`uv run python orb/orb_monitor.py` also remains supported.

To install the monitor independently of this checkout, build and install its wheel:

```bash
uv build
uv tool install dist/algo_trading-0.1.0-py3-none-any.whl
orb
```

The wheel includes the monitor's Python modules and dashboard assets. The installed
command can run from any directory.

---

## 1. NIFTY ORB Monitor (`orb`)

A real-time 15-minute and 5-minute candlestick chart with two Opening Range Breakout (ORB) setups for NIFTY 50 (`^NSEI`).

### Features & Strategy Logic
- **Two Opening Ranges**: The 15m chart uses the first 30 minutes (09:15–09:45 IST) and confirms on 15-minute closes. The 5m chart uses the first 45 minutes (09:15–10:00 IST) and confirms on 5-minute closes. Missing opening data prevents that setup from locking.
- **Setup Arrows and Main Signal**: Each chart marks its own first close strictly above or below its range. The main signal requires both setups' latest valid closes to agree in the same direction, once per direction per day. Inside-range, missing, or invalid latest closes block agreement.
- **Interactive Web Interface**: Served at `http://localhost:8765` featuring:
  - Custom Canvas-based candlestick renderer (dark/light candles, volume bars, ORB boundary lines).
  - Hover tooltip for exact OHLC and volume inspection.
  - Multi-session history navigation via arrow buttons or calendar date picker.
  - Quick **Today** button returning to the live trading session while streaming continues.
  - Source selection between Yahoo and Shoonya, interval switching, candle replay, and a remembered dark theme.
- **Session Caching**: Real 5-minute history also supplies aggregated 15-minute bars. Completed sessions are cached separately by provider, under `orb/data/` in a checkout by default. Legacy 15-minute-only history can show its own setup but cannot manufacture 5-minute prices or a main signal.
- **Automated Session Close**: Concludes the session's final candle at 15:30 IST cleanly without awaiting additional market ticks.

### Usage

#### Standard Live Monitor
Starts the HTTP/WebSocket server and streams live Yahoo Finance quotes:
```bash
uv run orb
```
Open **[http://localhost:8765](http://localhost:8765)** in your browser.

To bind to a different port:
```bash
uv run orb --port 9000
```

Use the chart's play button to replay a saved session while the live feed continues.

### Configuration and saved data

| Location | Running from this checkout | Installed wheel |
| --- | --- | --- |
| Configuration directory | Workspace root | Current working directory |
| Saved data directory | `orb/data/` in the workspace | `${XDG_DATA_HOME:-~/.local/share}/algo-trading/orb/` |

Set `ORB_CONFIG_DIR` or `ORB_DATA_DIR` to override either directory. Overrides expand
`~`; relative paths resolve from the current working directory. Checkout defaults
remain tied to the checkout even when launched from another directory.

Shoonya reads `.env`, then `shoonya/.env`, in the configuration directory; exported
environment variables take priority over both. Telegram reads that directory's
`.env`, with exported variables taking priority. Existing checkout credentials and
caches keep their locations. See the [monitor notes](orb/README.md) for the required
credential names and provider behavior.

---

## 2. Position & Risk Calculator (`erv.py`)

A precision position sizing and trade management tool for NSE/BSE equities. It computes exact net risk, stop-loss, breakeven, and reward targets after factoring in regulatory taxes, stamp duty, turnover charges, and broker commissions.

The calculator and its charge configuration live in [`erv/`](erv/README.md).

### Features
- **Volatility-Based Sizing**: Calculates position size using Average True Range (ATR) and defined capital risk:
  - `1.7 * ATR` for Intraday (typically 15-minute ATR).
  - `3.0 * ATR` for Delivery (typically daily ATR).
- **Accurate Cost Deductions**: Accounts for all transaction costs to derive true net breakeven, stop-loss, and profit targets.
- **Styles & Directions**:
  - **Intraday**: Long and Short.
  - **Delivery**: Long only.
- **Exchange Support**: Built-in charge formulas for both NSE and BSE.
- **R-Multiple Targets & PnL**: View multiple reward tiers ($1R$, $2R$, etc.) or compute net PnL against an exit price.
- **Journal Risk & Entries**: Use `r` as the risk argument to fetch the journal's current risk for your trading style, then optionally save an entered position with its planned exit limit.

### Usage

#### 1. Interactive Mode
Run the script without arguments for interactive prompts:
```bash
uv run erv
```
Prompts include entry price, risk capital (₹), ATR, trading style (`intraday`/`delivery`), exchange (`nse`/`bse`), and direction (`long`/`short`). Leave risk blank, or enter `r`, to use journal risk after selecting the style. A numeric risk remains an explicit amount.

#### 2. Command Line Arguments Mode
Supply the initial calculation inputs directly; exit-price and journal-save prompts remain interactive:
```bash
uv run erv <entry_price> <risk-or-r> <atr> [style] [direction] [exchange]
```

#### Examples
- **Intraday Long on NSE:**
  ```bash
  uv run erv 2500 500 25
  ```
- **Intraday Short on BSE:**
  ```bash
  uv run erv 1200 400 15 i s bse
  ```
- **Delivery Long on NSE:**
  ```bash
  uv run erv 850 1500 30 delivery long nse
  ```
- **Delivery with Journal Risk:**
  ```bash
  uv run erv 57.7 r 2.1 d
  ```
- **Intraday with Journal Risk:**
  ```bash
  uv run erv 57.7 r 2.1 i
  ```

`r` uses the current IST month's saved journal settings. Intraday includes today's daily R and the latest saved intraday outcomes; delivery uses the swing allocation divided by its slots. Missing setup or unavailable intraday R is reported rather than replaced with another month's risk. Numeric risk works independently of journal setup.

After calculating an exit price, choose **Add to journal?** to save the position as `OPEN`, with that price as its planned exit limit. Entry date defaults to today in IST. Closing positions stays in `uv run journal`. See the [ERV guide](erv/README.md) for the full flow. Direct `uv run erv/erv.py` from the root and `uv run erv.py` from inside `erv/` also remain supported.

#### CLI Parameters
| Position | Parameter | Type | Default | Options / Notes |
| :---: | :--- | :--- | :--- | :--- |
| 1 | `entry_price` | Float | *Required* | Entry stock price |
| 2 | `risk` | Float or `r` | *Required* | Explicit risk capital (₹), or journal risk for the selected style |
| 3 | `atr` | Float | `0.06 * entry` when blank | ATR volatility measure; pass `''` to use the default |
| 4 | `style` | String | `intraday` | `intraday` (`i`) or `delivery` (`d`) |
| 5 | `direction` | String | `long` | `long` (`l`) or `short` (`s`) |
| 6 | `exchange` | String | `nse` | `nse` or `bse` |

---

## Charges Configuration (`erv/charges.csv`)

Transaction charges and taxes are loaded from [`erv/charges.csv`](erv/charges.csv), alongside the calculator. You can tune these rates based on your broker (Zerodha, Groww, AngelOne, etc.):

| Charge | Description | Delivery | Intraday |
| :--- | :--- | :--- | :--- |
| **Brokerage** | Broker commission percentage | 0.0% | 0.03% |
| **NSE / BSE** | Exchange transaction fee | 0.0030699% / 0.00375% | 0.0030699% / 0.00375% |
| **SEBI** | SEBI turnover charges | 0.0001% | 0.0001% |
| **Stamp on Buy** | Stamp duty levied on buy orders | 0.015% | 0.003% |
| **IPFT Contribution** | Investor Protection Fund fee | 0.0000001% | 0.0000001% |
| **STT on Buy/Sell** | Securities Transaction Tax | 0.1% (Buy & Sell) | 0.025% (Sell only) |
| **GST** | 18% on (Brokerage + Exchange + SEBI + IPFT) | 18.0% | 18.0% |
| **DP** | Depository Participant charge per scrip | ₹13.75 (flat on sell) | ₹0.00 |

---

## Testing & Quality Assurance

### Python Unit Tests
Run the test suite verifying monitor logic, candle aggregation, and ORB strategy:
```bash
uv run python -m unittest discover
```

Tests are grouped by responsibility. Run an owner independently, for example
`uv run python -m unittest orb.tests.market.test_quality`. See the
[ownership guide](orb/tests/README.md) for the contracts and shared support.

Run the journal suite separately:

```bash
uv run python -m unittest discover -s journal/tests -t journal
```

Run the ERV command and journal-integration tests:

```bash
uv run python -m unittest discover -s erv/tests
```

### UI Regression Tests (Playwright)
Run the headless browser tests verifying Canvas chart rendering and session navigation:
```bash
node orb/tests/test_ui.cjs
```
Each scenario starts with a fresh browser context and private fixture server.
Use `--list` to see scenario IDs, `--case sources/retry` to run one, and
`--reverse` to verify that scenarios are independent of execution order.
*(Requires Node.js and Playwright. You can set `PLAYWRIGHT_PATH` or `CHROME_PATH` environment variables if non-standard binary locations are used).*

---

## Project Structure

```
algo-trading/
├── .python-version              # Python version pin (3.14)
├── pyproject.toml               # Project metadata & dependencies (uv build)
├── uv.lock                      # Locked dependency tree
├── erv/                         # Position sizing & risk calculator
│   ├── erv.py                   # Calculator and interactive prompts
│   ├── charges.csv              # Indian equity brokerage & tax schedule
│   ├── journal_bridge.py        # Journal risk and optional entry saving
│   ├── SPEC.md                  # Integration scope and acceptance criteria
│   └── tests/                   # ERV command and journal-integration tests
├── journal/                     # Monthly trading journal (see journal/README.md)
│   ├── trade.py                 # Terminal launcher
│   ├── daily_risk.py            # Shared daily R policy
│   ├── config/                  # Editable risk rules and trading calendar
│   ├── risk_tool/               # Journal, sizing, and workbook handling
│   ├── data/                    # Saved trades and shared daily state (ignored)
│   ├── exports/                 # Excel exports (ignored)
│   └── tests/                   # Journal test suite
├── to-do.md                     # Planned features & backlog
└── orb/                         # NIFTY ORB Monitor
    ├── README.md                # Monitor-specific notes
    ├── __init__.py              # Importable orb package
    ├── __main__.py              # python -m orb entry point
    ├── orb_monitor.py           # HTTP/SSE server & dual ORB coordination
    ├── paths.py                 # Package assets, configuration and saved-data locations
    ├── market.py                # Instrument sessions and 5m/15m candles
    ├── feeds.py                 # Yahoo and Shoonya connections
    ├── signals.py               # Closed-candle ORB strategy
    ├── static/                  # Web dashboard front-end (HTML/CSS/JS)
    │   ├── index.html
    │   ├── style.css
    │   ├── app.js
    │   └── motion.js
    ├── data/                    # Cached 5m/15m sessions by provider
    └── tests/                   # Test suite
        ├── rules/               # Opening ranges and comparison rules
        ├── market/              # Bars, quote quality and source clocks
        ├── sessions/            # Provider history, restoration and storage
        ├── application/         # Session transitions and monitor journeys
        ├── feeds/               # Provider adapters and connection lifecycle
        ├── transport/           # HTTP and SSE contracts
        ├── notifications/       # Durable alert delivery
        ├── support/             # Ordinary fixtures shared by Python tests
        ├── browser/             # Independent browser scenarios and fixtures
        ├── test_package.py
        └── test_ui.cjs
```
