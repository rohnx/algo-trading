"""Real SDK adapters retain authentication, frame, and bounded I/O contracts."""

import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orb.feeds import (
    _shoonya_api,
    _yahoo_websocket,
    REST_TIMEOUT,
    shoonya_login_url,
    SOCKET_CLOSE_TIMEOUT,
    SOCKET_OPEN_TIMEOUT,
)

from orb.tests.support.providers import CREDENTIALS, row


class TransportContractTests(unittest.TestCase):
    def test_login_link_reads_fresh_client_id_and_encodes_no_other_credentials(self):
        credentials = {**CREDENTIALS, "SHOONYA_CLIENT_ID": "client & id/one"}
        with patch("orb.feeds.read_shoonya_credentials", side_effect=[credentials, {"SHOONYA_CLIENT_ID": ""}]):
            self.assertEqual(shoonya_login_url(),
                             "https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=client+%26+id%2Fone")
            self.assertIsNone(shoonya_login_url())

    def test_yahoo_transport_bounds_dial_and_close_waits(self):
        with patch("websockets.sync.client.connect") as connect:
            socket = _yahoo_websocket()
            socket.subscribe(["^NSEI"])
            connect.assert_called_once_with(socket.url, open_timeout=SOCKET_OPEN_TIMEOUT,
                                            close_timeout=SOCKET_CLOSE_TIMEOUT)
            socket.close()
            connect.return_value.close.assert_called_once_with()

    def test_shoonya_transport_preserves_auth_and_history_contract_with_timeouts(self):
        api = _shoonya_api()
        result = {"access_token": "token", "USERID": "user", "refresh_token": "refresh",
                  "actid": "account", "susertoken": "session"}
        with patch("requests.post", side_effect=[SimpleNamespace(text=json.dumps(result)),
                                                SimpleNamespace(text=json.dumps([row()]))]) as post:
            self.assertEqual(api.getAccessToken("auth", "secret", "client", "user"),
                             ("token", "user", "refresh", "account"))
            self.assertEqual(api.get_time_price_series("NSE", "26000", 100, 200, 5), [row()])
        auth, history = post.call_args_list
        self.assertEqual(auth.args, ("https://api.shoonya.com/NorenWClientAPI//GenAcsTok",))
        self.assertEqual(auth.kwargs, {"data": "jData=" + json.dumps({
            "code": "auth", "checksum": hashlib.sha256(b"clientsecretauth").hexdigest(), "uid": "user"}),
            "timeout": REST_TIMEOUT})
        self.assertEqual(history.args, ("https://api.shoonya.com/NorenWClientAPI//TPSeries",))
        self.assertEqual(history.kwargs, {"data": "jData=" + json.dumps({
            "ordersource": "API", "uid": "user", "exch": "NSE", "token": "26000",
            "st": "100", "et": "200", "intrv": "5"}), "timeout": REST_TIMEOUT,
            "headers": {"Authorization": "Bearer token", "Content-Type": "application/json; charset=utf-8"}})
        self.assertEqual(api._NorenApi__access_token, "token")

    def test_shoonya_transport_keeps_unsuccessful_response_contract(self):
        api = _shoonya_api()
        api.injectOAuthHeader("token", "user", "account")
        with patch("requests.post", return_value=SimpleNamespace(text='{"stat":"Not_Ok"}')) as post:
            self.assertIsNone(api.getAccessToken("auth", "secret", "client", "user"))
            self.assertIsNone(api.get_time_price_series("NSE", "26000", 100, 200, 5))
            self.assertTrue(all(call.kwargs["timeout"] == REST_TIMEOUT for call in post.call_args_list))
