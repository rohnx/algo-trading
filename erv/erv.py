import os
import sys

import signal
def _quiet_interrupt(*_):
    print("\nsee ya!")
    sys.exit(0)
signal.signal(signal.SIGINT, _quiet_interrupt)

import math
def round_up(n, decimals=0):
    multiplier = 10 ** decimals
    return math.ceil(n * multiplier) / multiplier
def round_down(n, decimals=0):
    multiplier = 10 ** decimals
    return math.floor(n * multiplier) / multiplier

import csv
# region: load charges from csv
cash_charges = {} # {charge_name: {delivery: charge_rate, intraday: charge_rate}}
fno_charges = {}

csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cash_charges.csv")
with open(csv_path, newline="") as f:
    for row in csv.DictReader(f):
        cash_charges[row["Charge (%)"].strip()] = {
            "delivery": float(row["Cash Delivery"].strip("%")) / 100,
            "intraday": float(row["Cash Intraday"].strip("%")) / 100,
        }

csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fno_charges.csv")
with open(csv_path, newline="") as f:
    for row in csv.DictReader(f):
        fno_charges[row["Charges"].strip()] = {
            "options": float(row["Options"].strip("%")) / 100,
        }
# endregion

# region: get arguments from command line
if len(sys.argv) > 1:
    entry_price = float(sys.argv[1]) or ValueError("Entry price is required")
    risk_input = sys.argv[2]
    atr = float(sys.argv[3] or 6/100*entry_price) or ValueError("ATR is required") # daily atr for delivery, 15-minute atr for intraday
    style = str(sys.argv[4]) if len(sys.argv) > 4 else "intraday"
    direction = str(sys.argv[5]) if len(sys.argv) > 5 else "long"
    exchange = str(sys.argv[6]) if len(sys.argv) > 6 else "nse"
    exit_price = float(sys.argv[7]) if len(sys.argv) > 7 else None
else:
    entry_price = float(input("Entry price: "))
    risk_input = input("Risk [default: r from journal]: ") or "r"
    atr = float(input("ATR [default: 6%]: ") or 6/100*entry_price) # daily atr for delivery, 15-minute atr for intraday
    if input("Default settings (intraday, nse, long)? [y/n]: ") == "n":
        style = input("Style [delivery (d), intraday (i)]: ") or "intraday"
        exchange = input("Exchange [nse, bse]: ") or "nse"
        direction = input("Direction [long, short]: ") or "long"
    else:
        style = "intraday"
        exchange = "nse"
        direction = "long"

style = "options" if style in ("o", "options") else "delivery" if style in ("d", "delivery") else "intraday"
direction = "long" if direction in ("l", "long") else "short"

print(f"-----{style.capitalize()} {direction.capitalize()}-----")

exchange = exchange.strip().lower()
journal_r = None
if risk_input.strip().lower() == "r":
    from journal_bridge import current_risk
    try:
        risk_input, journal_r = current_risk(style)
    except ValueError as error:
        raise SystemExit(str(error)) from None
try:
    risk = float(risk_input)
except ValueError:
    raise SystemExit("Risk must be a positive number or r for journal risk") from None
if not math.isfinite(risk) or risk <= 0:
    raise SystemExit("Risk must be a finite number greater than zero")
# endregion

# region: load variables for intraday and delivery and options
brokerageD = cash_charges["Brokerage"]["delivery"]
brokerageI = cash_charges["Brokerage"]["intraday"]
brokerageO = fno_charges["Brokerage"]["options"]

exc = cash_charges[exchange.upper()]["delivery"] # same for intraday and delivery
sebi = cash_charges["SEBI"]["delivery"] # same for intraday and delivery
ipft = cash_charges["IPFT Contribution"]["delivery"] # same for intraday and delivery

stamp_buyD = cash_charges["Stamp on Buy"]["delivery"]
stamp_buyI = cash_charges["Stamp on Buy"]["intraday"]
stamp_buyO = fno_charges["Stamp on Buy"]["options"]

sttD = cash_charges["STT on Buy/Sell"]["delivery"] # 0.1% on buy & sell
sttI = cash_charges["STT on Buy/Sell"]["intraday"] # 0.025% on sell only
sttO = fno_charges["STT-OS"]["options"] # 0.15% on both options sell side& exercise

gst_rate = cash_charges["GST (on Brokerage + Exchange + SEBI + IPFT)"]["delivery"] # same for intraday and delivery and options

dp_rate = cash_charges["DP"]["delivery"]*100 # only on delivery sells, 14.74/scrip
# endregion

print("Entry:", entry_price)
print("Risk: ", risk)


def offer_journal_entry(total_charges):
    # Journal workers calculate with this script but must never offer a save.
    if __name__ != "__main__":
        return
    try:
        if input("Add to journal? [y/n]: ").strip().lower() not in ("y", "yes"):
            return
    except EOFError:
        return
    from journal_bridge import save_entry
    save_entry({
        "entry": str(entry_price), "risk": str(risk), "atr": str(atr),
        "style": style, "direction": direction, "exchange": exchange,
        "quantity": int(size), "exit_price": str(exit_price),
        "charges": str(total_charges), "pnl": str(pnl), "r_multiplier": journal_r,
    })

# total = price*(1+buy_charge) -> price=total/(1+buy_charge)
# total = price*(1+sell_charge) -> price=total/(1+sell_charge)

def position_size(style,risk,atr):
    volatility_multiplier=3 if style == "delivery" or style == "d" else 1.7
    space = volatility_multiplier*atr
    print("Sizing: ", round(risk/space, 3), "🠚", round(risk/space, 0))
    if round(risk/space) == 0:
        print("Not enough juice; see ya!")
        exit()
    return round(risk/space, 0)

if style == "delivery" or style == "d":
    if direction == "short" or direction == "s":
        print("Shorting is not supported for delivery")
        exit()
    else:
        buy_charge=brokerageD+exc+stamp_buyD+sttD+sebi+ipft+gst_rate*(brokerageD+exc+sebi+ipft) # stt on buy & sell
        sell_charge=brokerageD+exc+sttD+sebi+ipft+gst_rate*(brokerageD+exc+sebi+ipft)

        size=position_size(style,risk,atr)

        # set-target is risk + buy charges + self-sell charges
        stoploss_initial = (entry_price*(1+buy_charge))*size-risk+dp_rate
        stoploss = stoploss_initial/(size*(1-sell_charge))
        print("\nStoploss:",round_up(stoploss,2))

        breakeven_initial=entry_price*(1+buy_charge)+dp_rate/size
        breakeven=(breakeven_initial)/(1-sell_charge)
        print("Breakeven:",round_up(breakeven,2))

        takeprofit_initial=(entry_price*(1+buy_charge))*size+risk+dp_rate
        takeprofit=(takeprofit_initial)/(size*(1-sell_charge))
        print("Take-Profit:",round_up(takeprofit,2), "\n")

        exit_price = float(input("Exit price: ")) if input("Exit price? [y/n]: ") == "y" else None

        if exit_price:
            pnl = (exit_price-entry_price)*size-dp_rate-(buy_charge*entry_price+sell_charge*exit_price)*size
            print("\nPNL: ",pnl)
            print("Buy charge: ", buy_charge*entry_price*size)
            print("Sell charge (+DP): ", sell_charge*exit_price*size+dp_rate)
            total_charges = buy_charge*entry_price*size+sell_charge*exit_price*size+dp_rate
            print("Total charges: ", total_charges)
            offer_journal_entry(total_charges)
        else:
            rewardS=float(input("Reward Scale (integer): ")) if input("Use Reward Scale? [y/n]: ") == "y" else None
            for num in range(1, int(rewardS or 0)+1):
                takeprofit_initial=(entry_price*(1+buy_charge))*size+(risk*num)+dp_rate
                takeprofit=(takeprofit_initial)/(size*(1-sell_charge))
                print(f"Profit {num}R", ":",round_up(takeprofit,2))

elif style == "intraday" or style == "i":
    if direction == "short" or direction == "s":
        sell_charge=brokerageI+exc+sttI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft)
        buy_charge=brokerageI+exc+stamp_buyI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft)

        size=position_size(style,risk,atr)
        
        # set-target is risk - buy charges - sell charges
        stoploss_initial=(entry_price*(1-sell_charge))*size+risk
        stoploss=(stoploss_initial)/(size*(1+buy_charge))
        print("\nStoploss:",round_down(stoploss,2))

        breakeven_initial=entry_price*(1-sell_charge)
        breakeven=(breakeven_initial)/(1+buy_charge)
        print("Breakeven:",round_down(breakeven,2))

        takeprofit_initial=(entry_price*(1-sell_charge))*size-risk
        takeprofit=(takeprofit_initial)/(size*(1+buy_charge))
        print("Take-Profit:",round_down(takeprofit,2), "\n")

        exit_price = float(input("Exit price: ")) if input("Exit price? [y/n]: ") == "y" else None

        if exit_price:
            pnl = (entry_price-exit_price)*size-(sell_charge*entry_price+buy_charge*exit_price)*size
            print("\nPNL: ",pnl)
            total_charges = sell_charge*entry_price*size+buy_charge*exit_price*size
            print("Total charges: ", total_charges)
            offer_journal_entry(total_charges)
        else:
            rewardS=float(input("Reward Scale (integer): ")) if input("Use Reward Scale? [y/n]: ") == "y" else None
            for num in range(1, int(rewardS or 0)+1):
                takeprofit_initial=(entry_price*(1-sell_charge))*size-(risk*num)
                takeprofit=(takeprofit_initial)/(size*(1+buy_charge))
                print(f"Profit {num}R", ":",round_down(takeprofit,2))            
            
    else:
        buy_charge=brokerageI+exc+stamp_buyI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft) # no stt on buy
        sell_charge=brokerageI+exc+sttI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft)

        size=position_size(style,risk,atr)

        # set-target is risk + buy charges + self-sell charges
        stoploss_initial=(entry_price*(1+buy_charge))*size-risk
        stoploss=(stoploss_initial)/(size*(1-sell_charge))
        print("\nStoploss:",round_up(stoploss,2))

        breakeven_initial=entry_price*(1+buy_charge)
        breakeven=(breakeven_initial)/(1-sell_charge)
        print("Breakeven:",round_up(breakeven,2))

        takeprofit_initial=(entry_price*(1+buy_charge))*size+risk
        takeprofit=(takeprofit_initial)/(size*(1-sell_charge))
        print("Take-Profit:",round_up(takeprofit,2), "\n")

        exit_price = float(input("Exit price: ")) if input("Exit price? [y/n]: ") == "y" else None

        if exit_price:
            pnl = (exit_price-entry_price)*size-(buy_charge*entry_price+sell_charge*exit_price)*size
            print("\nPNL: ",pnl)
            total_charges = buy_charge*entry_price*size+sell_charge*exit_price*size
            print("Total charges: ", total_charges)
            offer_journal_entry(total_charges)
        else:
            rewardS=float(input("Reward Scale (integer): ")) if input("Use Reward Scale? [y/n]: ") == "y" else None
            for num in range(1, int(rewardS or 0)+1):
                takeprofit_initial=(entry_price*(1+buy_charge))*size+(risk*num)
                takeprofit=(takeprofit_initial)/(size*(1-sell_charge))
                print(f"Profit {num}R", ":",round_up(takeprofit,2))

elif style == "options" or style == "o":
    print("yo, hello! this' a new one bebs;)")
