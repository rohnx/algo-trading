import os
import time
from dotenv import load_dotenv
from NorenRestApiPy.NorenApi import NorenApi

load_dotenv()

# Shoonya credentials
CLIENT_ID   = os.getenv("SHOONYA_CLIENT_ID", "")
USER_ID     = os.getenv("SHOONYA_USER_ID", "")
SECRET_CODE = os.getenv("SHOONYA_SECRET_CODE", "")
AUTH_CODE   = os.getenv("SHOONYA_AUTH_CODE", "")

# Initialize API instance
api = NorenApi(
    host="https://api.shoonya.com/NorenWClientAPI/",
    websocket="wss://api.shoonya.com/NorenWSAPI/"
)

def event_handler_feed_update(tick_data):
    """Callback triggered whenever a market tick arrives"""
    # tick_data contains keys like:
    # 't': touchline 'tk'
    # 'e': exchange ('NSE')
    # 'tk': token
    # 'lp': last traded price
    # 'pc': percentage change
    # 'v': volume
    print(f"[TICK] Exchange: {tick_data.get('e')} | Token: {tick_data.get('tk')} | LTP: {tick_data.get('lp')} | Vol: {tick_data.get('v')}")

def event_handler_order_update(order_data):
    """Callback triggered on order updates"""
    print("[ORDER]", order_data)

def open_callback():
    """Callback triggered when WebSocket connection is established and authenticated"""
    print("[WS] Connected and authenticated successfully!")
    
    # Subscribe to scrips (Format: 'EXCHANGE|TOKEN')
    # Examples:
    # 'NSE|26000' -> Nifty 50 Index
    # 'NSE|26009' -> Bank Nifty Index
    # 'NSE|2885'  -> Reliance Industries
    scrips = ["NSE|26000", "NSE|2885"]
    print(f"[WS] Subscribing to: {scrips}")
    api.subscribe(scrips)

def error_callback(error):
    print(f"[WS Error]: {error}")

def close_callback():
    print("[WS] Connection closed.")

def main():
    print("Step 1: Authenticating to obtain access token...")
    res = api.getAccessToken(
        authcode=AUTH_CODE,
        Secret_Code=SECRET_CODE,
        client_id=CLIENT_ID,
        UID=USER_ID
    )

    if not res:
        print("❌ Login failed. Cannot establish WebSocket without a valid access token.")
        print("Ensure your public IP is whitelisted on Shoonya and your AUTH_CODE is fresh.")
        return

    access_token, user_id, refresh_token, account_id = res
    print(f"✅ Authenticated! User: {user_id}")

    print("Step 2: Starting WebSocket feed...")
    api.start_websocket(
        subscribe_callback=event_handler_feed_update,
        order_update_callback=event_handler_order_update,
        socket_open_callback=open_callback,
        socket_error_callback=error_callback,
        socket_close_callback=close_callback
    )

    # Keep script running to listen for ticks
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping feed...")
        api.close_websocket()

if __name__ == "__main__":
    main()
