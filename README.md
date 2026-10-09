# Algo Trading & Market Toolkit

A comprehensive Python-based algorithmic trading and market monitoring toolkit designed for Indian equity markets (NSE/BSE).

The repository includes:
1. **[NIFTY ORB Monitor](#1-nifty-orb-monitor-nse-monitor)**: A real-time NIFTY 50 dashboard with 15m/30m and 5m/45m Opening Range Breakout setups, plus a main signal when both agree.
2. **[Position & Risk Calculator](#2-position--risk-calculator-ervpy)**: A volatility-adjusted position sizing and risk management tool factoring in exchange fees, brokerages, and regulatory taxes.

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
This sets up the virtual environment in `.venv/` and installs all locked dependencies (`pandas`, `requests`, `yfinance`).

### 3. Running Scripts
You can run any script directly using `uv run` without manually activating the virtual environment:
```bash
# Run the ORB Monitor
uv run python nse-monitor/orb_monitor.py

# Run the Position Sizing Calculator
uv run python erv.py 2500 500 25
```

Alternatively, you can activate the environment manually:
```bash
source .venv/bin/activate
python nse-monitor/orb_monitor.py
```

---

## 1. NIFTY ORB Monitor (`nse-monitor`)

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
- **Session Caching**: Real 5-minute history also supplies aggregated 15-minute bars. Completed sessions are cached separately by provider under `nse-monitor/data/`. Legacy 15-minute-only history can show its own setup but cannot manufacture 5-minute prices or a main signal.
- **Automated Session Close**: Concludes the session's final candle at 15:30 IST cleanly without awaiting additional market ticks.

### Usage

#### Standard Live Monitor
Starts the HTTP/WebSocket server and streams live Yahoo Finance quotes:
```bash
uv run python nse-monitor/orb_monitor.py
```
Open **[http://localhost:8765](http://localhost:8765)** in your browser.

To bind to a different port:
```bash
uv run python nse-monitor/orb_monitor.py --port 9000
```

#### Replay Mode
Simulates price action and signals from the most recently completed session:
```bash
uv run python nse-monitor/orb_monitor.py --replay
```

#### Pipe Mode
Pipes live timestamped quote JSON lines from the WebSocket feeder into the monitor:
```bash
uv run python nse-monitor/yf-nse-data.py | uv run python nse-monitor/orb_monitor.py --pipe
```

---

## 2. Position & Risk Calculator (`erv.py`)

A precision position sizing and trade management tool for NSE/BSE equities. It computes exact net risk, stop-loss, breakeven, and reward targets after factoring in regulatory taxes, stamp duty, turnover charges, and broker commissions.

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

### Usage

#### 1. Interactive Mode
Run the script without arguments for interactive prompts:
```bash
uv run python erv.py
```
Prompts include entry price, risk capital (₹), ATR, trading style (`intraday`/`delivery`), exchange (`nse`/`bse`), and direction (`long`/`short`).

#### 2. Command Line Arguments Mode
Bypass interactive prompts by passing arguments directly:
```bash
uv run python erv.py <entry_price> <risk> [atr] [style] [direction] [exchange]
```

#### Examples
- **Intraday Long on NSE:**
  ```bash
  uv run python erv.py 2500 500 25
  ```
- **Intraday Short on BSE:**
  ```bash
  uv run python erv.py 1200 400 15 i s bse
  ```
- **Delivery Long on NSE:**
  ```bash
  uv run python erv.py 850 1500 30 delivery long nse
  ```

#### CLI Parameters
| Position | Parameter | Type | Default | Options / Notes |
| :---: | :--- | :--- | :--- | :--- |
| 1 | `entry_price` | Float | *Required* | Entry stock price |
| 2 | `risk` | Float | *Required* | Total risk capital (₹) |
| 3 | `atr` | Float | `0.06 * entry` | ATR volatility measure |
| 4 | `style` | String | `intraday` | `intraday` (`i`) or `delivery` (`d`) |
| 5 | `direction` | String | `long` | `long` (`l`) or `short` (`s`) |
| 6 | `exchange` | String | `nse` | `nse` or `bse` |

---

## Charges Configuration (`charges.csv`)

Transaction charges and taxes are loaded from [`charges.csv`](charges.csv). You can tune these rates based on your broker (Zerodha, Groww, AngelOne, etc.):

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
uv run python -m unittest discover -s nse-monitor/tests
```

### UI Regression Tests (Playwright)
Run the headless browser tests verifying Canvas chart rendering and session navigation:
```bash
node nse-monitor/tests/test_ui.cjs
```
*(Requires Node.js and Playwright. You can set `PLAYWRIGHT_PATH` or `CHROME_PATH` environment variables if non-standard binary locations are used).*

---

## Project Structure

```
algo-trading/
├── .python-version              # Python version pin (3.14)
├── pyproject.toml               # Project metadata & dependencies (uv build)
├── uv.lock                      # Locked dependency tree
├── charges.csv                  # Indian equity brokerage & tax schedule
├── erv.py                       # Position sizing & risk calculator
├── to-do.md                     # Planned features & backlog
└── nse-monitor/                 # NIFTY ORB Monitor
    ├── README.md                # Monitor-specific notes
    ├── orb_monitor.py           # HTTP/WebSocket server & stream engine
    ├── signals.py               # Candle aggregation & ORB breakout strategy
    ├── yf-nse-data.py           # Yahoo Finance live websocket stream pipe
    ├── static/                  # Web dashboard front-end (HTML/CSS/JS)
    │   ├── index.html
    │   ├── style.css
    │   └── app.js
    ├── data/                    # Cached daily 15-minute OHLC session JSONs
    └── tests/                   # Test suite
        ├── test_monitor.py
        ├── test_signals.py
        └── test_ui.cjs
```
