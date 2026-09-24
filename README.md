# Algo Trading - Position & Risk Calculator

A Python-based position sizing and risk management tool designed for Indian equity markets (NSE/BSE). It calculates true risk-adjusted position sizes, stop-loss, breakeven, and take-profit levels by accounting for regulatory fees, taxes, and broker charges.

---

#### Examples (default is intraday, long, nse)

- **Intraday Long on NSE:**
  ```bash
  python3 erv.py 2500 500 25
  ```

- **Intraday Short on BSE:**
  ```bash
  python3 erv.py 1200 400 15 i s bse
  ```

- **Delivery Long on NSE:**
  ```bash
  python3 erv.py 850 1500 30 delivery long nse
  ```

## Features

- **Volatility-Based Sizing**: Calculates position size based on Average True Range (ATR) and capital risk. The default multipliers are chosen as:
  - `1.7 * ATR` for Intraday (typically 15-minute ATR).
  - `3.0 * ATR` for Delivery (typically daily ATR).
- **Accurate Cost Adjustments**: Factors in all transaction charges to determine exact net stop-loss, breakeven, and profit targets.
- **Support for Styles & Directions**:
  - **Intraday**: Long and Short.
  - **Delivery**: Long only.
- **Exchange Support**: NSE and BSE fee rates.
- **R-Multiple Targets & PnL**: View multiple reward tiers ($1R$, $2R$, etc.) or compute exact PnL and fee breakdown against an exit price.

---

## Charges Configuration (`charges.csv`)

Transaction charges and taxes are loaded from [`charges.csv`](charges.csv). You can update these rates based on your broker (e.g., Zerodha, Groww, AngelOne) or regulatory revisions:

| Charge | Description | Delivery | Intraday |
| :--- | :--- | :--- | :--- |
| **Brokerage** | Broker commission percentage | 0.0% | 0.03% |
| **NSE / BSE** | Exchange transaction fee | 0.0030699% / 0.00375% | 0.0030699% / 0.00375% |
| **SEBI** | SEBI turnover charges | 0.0001% | 0.0001% |
| **Stamp on Buy** | Stamp duty levied on buy orders | 0.015% | 0.003% |
| **IPFT Contribution** | Investor Protection Fund fee | 0.0000001% | 0.0000001% |
| **STT on Buy/Sell** | Securities Transaction Tax | 0.1% (Buy & Sell) | 0.025% (Sell only) |
| **GST** | 18% on (Brokerage + Exchange + SEBI + IPFT) | 18.0% | 18.0% |
| **DP** | Depository Participant charge per scrip (flat ₹) | ₹13.75 (on sell) | ₹0.00 |

---

## Usage (`erv.py`)

### 1. Interactive Mode
Run the script without arguments for interactive prompts:

```bash
python3 erv.py
```

Prompts:
- **Entry price**: Price per share.
- **Risk**: Total capital amount willing to risk (₹).
- **ATR**: Average True Range (defaults to 6% of entry price if omitted).
- **Settings**: Style (`delivery`/`intraday`), Exchange (`nse`/`bse`), Direction (`long`/`short`).
- **Exit Price / Reward Scale**: Provide an exit price for PnL analysis or specify an integer reward scale (e.g., `3` for 1R, 2R, 3R targets).

### 2. Command Line Arguments Mode
You can also supply arguments directly to bypass the initial setup prompts:

```bash
python3 erv.py <entry_price> <risk> [atr] [style] [direction] [exchange]
```

#### Arguments
| Position | Parameter | Type | Default | Options / Notes |
| :---: | :--- | :--- | :--- | :--- |
| 1 | `entry_price` | Float | *Required* | Entry stock price |
| 2 | `risk` | Float | *Required* | Total risk capital (₹) |
| 3 | `atr` | Float | `0.06 * entry` | ATR volatility measure |
| 4 | `style` | String | `intraday` | `intraday` (`i`) or `delivery` (`d`) |
| 5 | `direction` | String | `long` | `long` (`l`) or `short` (`s`) |
| 6 | `exchange` | String | `nse` | `nse` or `bse` |

> [!NOTE]
> Once the position size, stop-loss, breakeven, and baseline take-profit are calculated and printed, the script prompts whether you wish to enter an **Exit price** (for PnL & fee calculation) or view **Reward Scale** multiples (e.g. $1R$, $2R$, $3R$).