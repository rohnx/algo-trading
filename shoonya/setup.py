import os
import hashlib
from dotenv import load_dotenv
load_dotenv()
from NorenRestApiPy.NorenApi import NorenApi

CLIENT_ID   = os.environ["SHOONYA_CLIENT_ID"]
USER_ID     = os.environ["SHOONYA_USER_ID"]
SECRET_CODE = os.environ["SHOONYA_SECRET_CODE"]

# 1. Send the user to the OAuth authorize URL and capture the
#    "code" query param from the redirect back to your app.
#    https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=Your_Client_id
auth_code = os.environ["SHOONYA_AUTH_CODE"]  # obtained from the redirect

# 2. Exchange for a session token.
# Note: getAccessToken internally hashes (CLIENT_ID + SECRET_CODE + auth_code) for you.
api = NorenApi(
    host="https://api.shoonya.com/NorenWClientAPI/",
    websocket="wss://api.shoonya.com/NorenWSAPI/",
)

res = api.getAccessToken(
    authcode=auth_code,
    Secret_Code=SECRET_CODE,
    client_id=CLIENT_ID,
    UID=USER_ID,
)

if res:
    access_token, user_id, refresh_token, account_id = res
    print("Logged in successfully!")
    print("User ID:", user_id)
    print("Access Token:", access_token)
else:
    print("Login failed. Check your auth_code, credentials, or IP whitelisting.")