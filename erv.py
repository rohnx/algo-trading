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
charges = {} # {charge_name: {delivery: charge_rate, intraday: charge_rate}}

csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charges.csv")
with open(csv_path, newline="") as f:
    for row in csv.DictReader(f):
        charges[row["Charge"].strip()] = {
            "delivery": float(row["Delivery (%)"].strip("%")) / 100,
            "intraday": float(row["Intraday (%)"].strip("%")) / 100,
        }
# endregion

# region: get arguments from command line
if len(sys.argv) > 1:
    entry_price = float(sys.argv[1]) or ValueError("Entry price is required")
    risk = float(sys.argv[2] or 0) or ValueError("Risk is required")
    atr = float(sys.argv[3] or 6/100*entry_price) or ValueError("ATR is required") # daily atr for delivery, 15-minute atr for intraday
    style = str(sys.argv[4]) if len(sys.argv) > 4 else "intraday"
    style = "intraday" if style == "i" or style == "intraday" else "delivery"
    direction = str(sys.argv[5]) if len(sys.argv) > 5 else "long"
    direction = "long" if direction == "l" or direction == "long" else "short"
    exchange = str(sys.argv[6]) if len(sys.argv) > 6 else "nse"
    exit_price = float(sys.argv[7]) if len(sys.argv) > 7 else None
else:
    entry_price = float(input("Entry price: "))
    risk = float(input("Risk [default: 0]: ") or 0)
    atr = float(input("ATR [default: 6%]: ") or 6/100*entry_price) # daily atr for delivery, 15-minute atr for intraday
    if input("Default settings (intraday, nse, long)? [y/n]: ") == "n":
        style = input("Style [delivery (d), intraday (i)]: ") or "intraday"
        exchange = input("Exchange [nse, bse]: ") or "nse"
        direction = input("Direction [long, short]: ") or "long"
    else:
        style = "intraday"
        exchange = "nse"
        direction = "long"
# endregion

# region: load variables for intraday and delivery 
brokerageD = charges["Brokerage"]["delivery"]
brokerageI = charges["Brokerage"]["intraday"]

exc = charges[exchange.upper()]["delivery"] # same for intraday and delivery
sebi = charges["SEBI"]["delivery"] # same for intraday and delivery
ipft = charges["IPFT Contribution"]["delivery"] # same for intraday and delivery

stamp_buyD = charges["Stamp on Buy"]["delivery"]
stamp_buyI = charges["Stamp on Buy"]["intraday"]

sttD = charges["STT on Buy/Sell"]["delivery"] # 0.1% on buy & sell
sttI = charges["STT on Buy/Sell"]["intraday"] # 0.025% on sell only

gst_rate = charges["GST (on Brokerage + Exchange + SEBI + IPFT)"]["delivery"] # same for intraday and delivery

dp_rate = charges["DP"]["delivery"]*100 # only on delivery sells, 14.74/scrip
# endregion

print(f"-----{style.capitalize()} {direction.capitalize()}-----")
print("Entry:", entry_price)
print("Risk: ", risk)

def position_size(style,risk,atr):
    volatility_multiplier=3 if style == "delivery" or style == "d" else 1.7
    space = volatility_multiplier*atr
    print("Sizing: ", round(risk/space, 3), "🠚", round(risk/space, 0))
    return round(risk/space, 0)
    if round(risk/space) == 0:
        print("Not enough juice; see ya!")
        exit()


if style == "delivery" or style == "d":
    if direction == "short" or direction == "s":
        print("Shorting is not supported for delivery")
        exit()
    else:
        buy_charge=brokerageD+exc+stamp_buyD+sttD+sebi+ipft+gst_rate*(brokerageD+exc+sebi+ipft) # stt on buy & sell
        sell_charge=brokerageD+exc+sttD+sebi+ipft+gst_rate*(brokerageD+exc+sebi+ipft)

        size=position_size(style,risk,atr)

        # set-loss is initial stoploss + buy charges + self-sell charges
        stoploss_initial = (entry_price*(1+buy_charge))*size-risk+dp_rate
        stoploss = stoploss_initial/(size*(1-sell_charge))
        print("\nStoploss:",round_up(stoploss,2))
        # print("pnl",(stoploss-entry_price)*size-dp_rate-(buy_charge*entry_price-sell_charge*stoploss)*size) // verified.

        # total = price*(1+buy_charge) -> price=total/(1+buy_charge)
        breakeven_initial=entry_price*(1+buy_charge)+dp_rate/size
        breakeven=(breakeven_initial)/(1-sell_charge)
        print("Breakeven:",round_up(breakeven,2))

        # set-profit is initial takeprofit + buy charges + self-sell charges
        takeprofit_initial=(entry_price*(1+buy_charge))*size+risk+dp_rate
        takeprofit=(takeprofit_initial)/(size*(1-sell_charge))
        print("Take-Profit:",round_up(takeprofit,2), "\n")

        exit_price = float(input("Exit price: ")) if input("Exit price? [y/n]: ") == "y" else None

        if exit_price:
            pnl = (exit_price-entry_price)*size-dp_rate-(buy_charge*entry_price+sell_charge*exit_price)*size
            print("\nPNL: ",pnl)
            print("Buy charge: ", buy_charge*entry_price*size)
            print("Sell charge (+DP): ", sell_charge*exit_price*size+dp_rate)
            print("Total charges: ", buy_charge*entry_price*size+sell_charge*exit_price*size+dp_rate)
        else:
            rewardS=float(input("Reward Scale (integer): ")) if input("Use Reward Scale? [y/n]: ") == "y" else None
            for num in range(1, int(rewardS)+1):
                takeprofit_initial=(entry_price*(1+buy_charge))*size+(risk*num)+dp_rate
                takeprofit=(takeprofit_initial)/(size*(1-sell_charge))
                print(f"Profit {num}R", ":",round_up(takeprofit,2))

elif style == "intraday" or style == "i":
    if direction == "short" or direction == "s":
        sell_charge=brokerageI+exc+sttI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft)
        buy_charge=brokerageI+exc+stamp_buyI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft)

        size=position_size(style,risk,atr)
        
        # set-loss is initial stoploss - buy charges - self-sell charges # stoploss=stoploss_initial*(1-sell_charge)/(1+buy_charge)
        stoploss_initial=(entry_price*(1-sell_charge))*size+risk
        stoploss=(stoploss_initial)/(size*(1+buy_charge))
        print("\nStoploss:",round_down(stoploss,2))
        # print("pnl: ",(entry_price-stoploss)*size-(sell_charge*entry_price+buy_charge*stoploss)*size) // verified.

        # total = price*(1+sell_charge) -> price=total/(1+sell_charge)
        breakeven_initial=entry_price*(1-sell_charge)
        breakeven=(breakeven_initial)/(1+buy_charge)
        print("Breakeven:",round_down(breakeven,2))

        # set-profit is initial takeprofit - buy charges - self-sell charges
        takeprofit_initial=(entry_price*(1-sell_charge))*size-risk
        takeprofit=(takeprofit_initial)/(size*(1+buy_charge))
        print("Take-Profit:",round_down(takeprofit,2), "\n")

        exit_price = float(input("Exit price: ")) if input("Exit price? [y/n]: ") == "y" else None

        if exit_price:
            pnl = (entry_price-exit_price)*size-(sell_charge*entry_price+buy_charge*exit_price)*size
            print("\nPNL: ",pnl)
            print("Total charges: ", sell_charge*entry_price*size+buy_charge*exit_price*size)
        else:
            rewardS=float(input("Reward Scale (integer): ")) if input("Use Reward Scale? [y/n]: ") == "y" else None
            for num in range(1, int(rewardS)+1):
                takeprofit_initial=(entry_price*(1-sell_charge))*size-(risk*num)
                takeprofit=(takeprofit_initial)/(size*(1+buy_charge))
                print(f"Profit {num}R", ":",round_down(takeprofit,2))            
            
    else:
        buy_charge=brokerageI+exc+stamp_buyI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft) # no stt on buy
        sell_charge=brokerageI+exc+sttI+sebi+ipft+gst_rate*(brokerageI+exc+sebi+ipft)

        size=position_size(style,risk,atr)

        stoploss_initial=(entry_price*(1+buy_charge))*size-risk
        stoploss=(stoploss_initial)/(size*(1-sell_charge))
        print("\nStoploss:",round_up(stoploss,2))

        # total = price*(1+buy_charge) -> price=total/(1+buy_charge)
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
            print("Total charges: ", buy_charge*entry_price*size+sell_charge*exit_price*size)
        else:
            rewardS=float(input("Reward Scale (integer): ")) if input("Use Reward Scale? [y/n]: ") == "y" else None
            for num in range(1, int(rewardS)+1):
                takeprofit_initial=(entry_price*(1+buy_charge))*size+(risk*num)
                takeprofit=(takeprofit_initial)/(size*(1-sell_charge))
                print(f"Profit {num}R", ":",round_up(takeprofit,2))

