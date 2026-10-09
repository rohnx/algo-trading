import json
import time
import websocket

WS_URL = "wss://api.shoonya.com/NorenWSAPI/"

def on_message(ws, message):
    print("Received:", message)

def on_error(ws, error):
    print("Error:", error)

def on_close(ws, close_status_code, close_msg):
    print(f"Closed: code={close_status_code}, msg={close_msg}")

def on_open(ws):
    print("WebSocket connected to server. Sending auth frame...")
    # Attempt auth frame without valid access token
    auth_data = {
        "t": "a",
        "uid": "FN249893",
        "actid": "FN249893",
        "source": "API",
        "accesstoken": "dummy_token"
    }
    ws.send(json.dumps(auth_data))
    print("Sent auth frame:", auth_data)

if __name__ == "__main__":
    print(f"Connecting to {WS_URL}...")
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()
