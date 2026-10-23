"""Recording hub and controlled provider sockets; no external I/O."""

from datetime import datetime
import threading

from orb.feeds import TOKEN_SYMBOLS
from orb.market import INSTRUMENTS, PRIMARY_SYMBOL
from orb.signals import IST

NOW = datetime(2026, 10, 8, 10, 5, tzinfo=IST)


SHOONYA_SYMBOLS = set(TOKEN_SYMBOLS.values())


SUBSCRIPTIONS = [f"{exchange}|{token}" for exchange, token in TOKEN_SYMBOLS]


CREDENTIALS = {"SHOONYA_CLIENT_ID": "client", "SHOONYA_USER_ID": "user",
               "SHOONYA_SECRET_CODE": "secret", "SHOONYA_AUTH_CODE": "auth"}


def frame(**fields):
    return {"t": "tf", "e": "NSE", "tk": "26000", **fields}


def row(minute=15, **fields):
    return {"time": f"08-10-2026 09:{minute}:00", "into": "100", "inth": "110",
            "intl": "90", "intc": "105", "intv": "12", "v": "9999", **fields}


class Hub:
    def __init__(self):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.events = []
        self.ticks = []
        self.tick_symbols = []
        self.tick_openings = []
        self.histories = []
        self.live_histories = []
        self.fallback_histories = []
        self.feed_status = None

    def activate_source(self, source):
        self.events.append(("activate", source))

    def set_feed_status(self, status, error=None):
        with self.condition:
            self.feed_status = status
            self.events.append(("status", status, error))
            self.condition.notify_all()

    def process_tick(self, *values, symbol=PRIMARY_SYMBOL, session_open=None):
        with self.condition:
            self.ticks.append(values)
            self.tick_symbols.append(symbol)
            self.tick_openings.append(session_open)
            self.condition.notify_all()

    def seed_historical_candles(self, candles, interval_minutes, *, symbol=PRIMARY_SYMBOL,
                                live=False, publish=False):
        with self.condition:
            self.histories.append((candles, interval_minutes, symbol))
            self.live_histories.append((symbol, live))
            self.condition.notify_all()
        if publish:
            if symbol == PRIMARY_SYMBOL:
                self.broadcast({"type": "init", **self.snapshot()})
            else:
                self.broadcast({"type": "state", **self._instrument_event(symbol)})

    def seed_fallback_history(self, candles, *, symbol, history_source_id="yahoo", publish=False):
        with self.condition:
            self.fallback_histories.append((candles, symbol, history_source_id))
            self.condition.notify_all()
        if publish and candles:
            self.broadcast({"type": "state", **self._instrument_event(symbol)})
        return bool(candles)

    def snapshot(self):
        return {"source": "test"}

    def _instrument_event(self, symbol):
        return {"symbol": symbol, "instruments": {symbol: {}}}

    def broadcast(self, event):
        self.events.append(("broadcast", event))

    def wait(self, predicate):
        with self.condition:
            return self.condition.wait_for(predicate, timeout=3)


class DummyFeed:
    def __init__(self, manager, generation):
        self.manager, self.generation = manager, generation
        self.started = self.closed = False

    def start(self):
        self.started = True
        self.manager.hub.events.append(("start", self.generation))

    def stop(self):
        self.closed = True
        self.manager.hub.events.append(("stop", self.generation))


class FakeApi:
    def __init__(self, result=("token", "user", "refresh", "account"), rows=None):
        self.result = result
        self.rows = rows
        self.callbacks = {}
        self.closed = threading.Event()
        self.started = threading.Event()
        self.history_done = threading.Event()
        self.auth_release = None
        self.history_release = None
        self.history_releases = {}
        self.history_entries = {token: threading.Event() for _, token in TOKEN_SYMBOLS}
        self.history_entered = threading.Event()
        self.auth_entered = threading.Event()
        self.history_requests = []
        self.history_condition = threading.Condition()
        self.subscriptions = []
        self.auth_calls = 0
        self.start_calls = 0
        self.close_calls = 0

    def getAccessToken(self, **kwargs):
        self.auth_calls += 1
        self.auth_kwargs = kwargs
        self.auth_entered.set()
        if self.auth_release:
            self.auth_release.wait(timeout=5)
        return self.result

    def start_websocket(self, **kwargs):
        self.start_calls += 1
        self.callbacks = kwargs
        self.started.set()
        kwargs["socket_open_callback"]()

    def subscribe(self, instruments):
        self.subscriptions.append(instruments)

    def get_time_price_series(self, **kwargs):
        with self.history_condition:
            self.history_requests.append(kwargs)
            self.history_condition.notify_all()
        self.history_entered.set()
        self.history_entries[kwargs["token"]].set()
        if release := self.history_releases.get(kwargs["token"]):
            release.wait(timeout=5)
        if self.history_release:
            self.history_release.wait(timeout=5)
        self.history_done.set()
        result = self.rows.get(kwargs["token"]) if isinstance(self.rows, dict) else self.rows
        if isinstance(result, Exception):
            raise result
        return result

    def wait_for_history(self, count):
        with self.history_condition:
            return self.history_condition.wait_for(lambda: len(self.history_requests) >= count, timeout=1)

    def close_websocket(self):
        self.close_calls += 1
        self.closed.set()


def missing_focit_api():
    return FakeApi(rows={instrument.shoonya_token: None if symbol == "FOCIT.BO" else [row()]
                        for symbol, instrument in INSTRUMENTS.items()})


class FakeYahooSocket:
    def __init__(self):
        self.listening, self.closed = threading.Event(), threading.Event()

    def subscribe(self, symbols):
        self.symbols = symbols

    def listen(self, callback):
        self.callback = callback
        self.listening.set()
        self.closed.wait(5)

    def close(self):
        self.closed.set()
