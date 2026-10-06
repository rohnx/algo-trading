"""Pipe timestamped Yahoo quotes into orb_monitor.py --pipe."""

import json
import yfinance as yf


def on_message(message):
    if isinstance(message, dict) and "price" in message and "time" in message:
        print(json.dumps(message), flush=True)


if __name__ == "__main__":
    with yf.WebSocket(verbose=False) as websocket:
        websocket.subscribe(["^NSEI"])
        websocket.listen(on_message)
