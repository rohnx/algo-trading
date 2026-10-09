"""Single-provider NIFTY feeds, normalized to timestamped prices and 5m bars.

Provider callbacks only enqueue messages. Every write to the monitor is guarded
by a generation check, so an old HTTP request or socket cannot cross a switch.
"""

from collections import deque
from datetime import datetime, time as dtime, timedelta
import logging
import math
import os
from pathlib import Path
import queue
import threading

from signals import Candle, IST

SYMBOL = "^NSEI"
NIFTY_TOKEN = "26000"
QUEUE_SIZE = 4096
ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_KEYS = ("SHOONYA_CLIENT_ID", "SHOONYA_USER_ID", "SHOONYA_SECRET_CODE", "SHOONYA_AUTH_CODE")


def feed_timestamp(value):
    """Shoonya ft is epoch seconds; accept the millisecond convention too.

    Magnitude and date bounds reject milliseconds interpreted as seconds,
    microseconds, blank values, and clock-relative timestamps.
    """
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Invalid feed timestamp")
    if 946684800000 <= number < 4102444800000:
        number /= 1000
    elif not 946684800 <= number < 4102444800:
        raise ValueError("Invalid feed timestamp")
    return datetime.fromtimestamp(number, IST)


def _volume(value):
    number = float(value or 0)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid volume")
    return int(number)


class ShoonyaQuotes:
    """Reconstruct touchline state without inventing a price event timestamp."""

    def __init__(self, now=None):
        self.state = {}
        self.cumulative = None
        self.pending_volume = 0
        self.last_time = None
        self.now = now

    def update(self, message):
        if (not isinstance(message, dict) or message.get("t") not in ("tk", "tf")
                or str(message.get("e")) != "NSE"
                or str(message.get("tk")) != NIFTY_TOKEN):
            return None
        timestamp = None
        try:
            if "ft" in message:
                timestamp = feed_timestamp(message["ft"])
                if self.now is not None and timestamp > self.now() + timedelta(seconds=60):
                    return None
                if self.last_time and timestamp < self.last_time:
                    return None
            if timestamp and self.last_time and timestamp.date() != self.last_time.date():
                self.cumulative = None
                self.pending_volume = 0
            if message["t"] == "tk":
                self.state[("NSE", NIFTY_TOKEN)] = dict(message)
                self.cumulative = None
                self.pending_volume = 0
            else:
                self.state.setdefault(("NSE", NIFTY_TOKEN), {}).update(message)
            if "v" in message:
                cumulative = _volume(message["v"])
                if self.cumulative is not None and cumulative >= self.cumulative:
                    self.pending_volume += cumulative - self.cumulative
                else:
                    # A snapshot, day rollover, or reset establishes a baseline.
                    self.pending_volume = 0
                self.cumulative = cumulative
            # lp and ft must be present together in THIS frame. Merged ft may
            # be old, and a volume/depth-only update is not a new price tick.
            if "lp" not in message or timestamp is None:
                return None
            price = float(message["lp"])
            if not math.isfinite(price) or price <= 0:
                return None
        except (TypeError, ValueError, OverflowError, OSError):
            return None
        self.last_time = timestamp
        volume, self.pending_volume = self.pending_volume, 0
        return price, volume, timestamp


def _history_candle(start, values, volume, now):
    start = start.replace(tzinfo=IST) if start.tzinfo is None else start.astimezone(IST)
    if (start.weekday() >= 5 or not dtime(9, 15) <= start.time() < dtime(15, 30)
            or start.minute % 5 or start.second or start.microsecond or start > now):
        return None
    prices = [float(value) for value in values]
    if not all(math.isfinite(value) and value > 0 for value in prices):
        return None
    opening, high, low, close = prices
    if high < max(opening, close, low) or low > min(opening, close, high):
        return None
    session_open = start.replace(hour=9, minute=15)
    end = start + timedelta(minutes=5)
    return Candle(SYMBOL, *prices, _volume(volume), start, end,
                  is_closed=end <= now, slot=int((start - session_open).total_seconds() // 300))


def shoonya_history(rows, now=None):
    """Parse actual Noren candle fields; intv is interval, v cumulative volume."""
    now = now or datetime.now(IST)
    candles = {}
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("stat", "Ok")).lower() != "ok":
            continue
        try:
            if row.get("ssboe"):
                start = feed_timestamp(row["ssboe"])
            else:
                text = str(row["time"])
                try:
                    start = datetime.fromisoformat(text)
                except ValueError:
                    start = datetime.strptime(text.replace("/", "-"), "%d-%m-%Y %H:%M:%S")
            candle = _history_candle(start, [row[key] for key in ("into", "inth", "intl", "intc")],
                                     row.get("intv", 0), now)
            if candle:
                candles[candle.start_time] = candle
        except (TypeError, ValueError, KeyError, OverflowError, OSError):
            continue
    return sorted(candles.values(), key=lambda candle: candle.start_time)


def fetch_yahoo_5m_history():
    import yfinance as yf
    history = yf.Ticker(SYMBOL).history(period="60d", interval="5m")
    now = datetime.now(IST)
    candles = []
    for timestamp, row in history.iterrows():
        try:
            candle = _history_candle(timestamp.to_pydatetime(),
                                     [row[key] for key in ("Open", "High", "Low", "Close")],
                                     row.get("Volume", 0), now)
            if candle:
                candles.append(candle)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return sorted(candles, key=lambda candle: candle.start_time)


def _shoonya_api():
    from NorenRestApiPy.NorenApi import NorenApi

    class ManagedNorenApi(NorenApi):
        """Repair the installed SDK's disconnected-stop and 100ms retry loop.

        Auth and history retain the working SDK implementation. These private
        names match NorenRestApiOAuth 0.0.41, imported as NorenRestApiPy.
        """

        def _NorenApi__ws_run_forever(self):
            self.retry_delay = 1
            while not self._NorenApi__stop_event.is_set():
                try:
                    self._NorenApi__websocket.run_forever(
                        ping_interval=3, ping_payload='{"t":"h"}')
                except Exception:
                    pass
                if self._NorenApi__stop_event.wait(self.retry_delay):
                    break
                self.retry_delay = min(self.retry_delay * 2, 30)

        def _NorenApi__ws_send(self, *args, **kwargs):
            # The stock SDK waits forever when subscribe races a disconnect.
            if (not self._NorenApi__websocket_connected
                    or self._NorenApi__stop_event.is_set()):
                raise ConnectionError("Shoonya socket is disconnected")
            with self._NorenApi__ws_mutex:
                return self._NorenApi__websocket.send(*args, **kwargs)

        def close_websocket(self):
            stop = getattr(self, "_NorenApi__stop_event", None)
            if stop is not None:
                stop.set()
            self._NorenApi__websocket_connected = False
            socket = self._NorenApi__websocket
            if socket is not None:
                socket.close()
            thread = getattr(self, "_NorenApi__ws_thread", None)
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError("Shoonya socket did not stop; source switch was cancelled")

    # The SDK's DEBUG messages contain login payloads and tokens.
    logging.getLogger("NorenRestApiPy.NorenApi").setLevel(logging.CRITICAL)
    return ManagedNorenApi(host="https://api.shoonya.com/NorenWClientAPI/",
                           websocket="wss://api.shoonya.com/NorenWSAPI/")


class FeedManager:
    def __init__(self, hub, *, yahoo_factory=None, shoonya_factory=None):
        self.hub = hub
        self.source = None
        self._feed = None
        self._generation = 0
        self._switch_lock = threading.Lock()
        self._shoonya_session = None
        self._factories = {"yahoo": yahoo_factory or YahooFeed,
                           "shoonya": shoonya_factory or ShoonyaFeed}

    def _apply(self, generation, action):
        with self.hub.lock:
            if generation != self._generation:
                return False
            action()
            return True

    def _cached_shoonya(self, credentials, day, generation):
        key = tuple(credentials.get(name) for name in CREDENTIAL_KEYS)
        with self.hub.lock:
            if generation != self._generation:
                return None
            if self._shoonya_session is not None:
                saved_key, saved_day, api = self._shoonya_session
                if key == saved_key and day == saved_day:
                    return api
                self._shoonya_session = None
        return None

    def _remember_shoonya(self, credentials, day, api, generation):
        key = tuple(credentials.get(name) for name in CREDENTIAL_KEYS)
        self._apply(generation, lambda: setattr(self, "_shoonya_session", (key, day, api)))

    def _forget_shoonya(self, api):
        with self.hub.lock:
            if self._shoonya_session is not None and self._shoonya_session[2] is api:
                self._shoonya_session = None

    def select_source(self, source):
        if source not in self._factories:
            raise ValueError("Choose yahoo or shoonya")
        with self._switch_lock:
            if source == self.source and self._feed is not None:
                worker = getattr(self._feed, "worker", None)
                failed = getattr(self.hub, "feed_status", None) == "error"
                exited = worker is not None and not worker.is_alive()
                if not failed and not exited:
                    return source
            with self.hub.lock:
                self._generation += 1
            if self._feed is not None:
                # This must finish before creating the replacement socket.
                try:
                    self._feed.stop()
                except Exception:
                    self.hub.set_feed_status("error", error="Previous feed could not stop; source switch cancelled")
                    raise
            with self.hub.lock:
                self.source = source
                self.hub.activate_source(source)
                self._feed = self._factories[source](self, self._generation)
                self._feed.start()
            return source

    def stop(self):
        with self._switch_lock:
            with self.hub.lock:
                self._generation += 1
            if self._feed is not None:
                self._feed.stop()
                self._feed = None


class _Feed:
    def __init__(self, manager, generation):
        self.manager = manager
        self.generation = generation
        self.stopped = threading.Event()
        self.socket_lock = threading.Lock()
        self.incoming = queue.Queue(maxsize=QUEUE_SIZE)

    def apply(self, action):
        return self.manager._apply(self.generation, action)

    def status(self, status, error=None):
        self.apply(lambda: self.manager.hub.set_feed_status(status, error=error))

    def seed(self, candles):
        def action():
            self.manager.hub.seed_historical_candles(candles, interval_minutes=5)
            self.manager.hub.broadcast({"type": "init", **self.manager.hub.snapshot()})
        self.apply(action)

    def tick(self, values):
        self.apply(lambda: self.manager.hub.process_tick(*values))


class YahooFeed(_Feed):
    def __init__(self, manager, generation, *, websocket_factory=None, history_fetcher=None):
        super().__init__(manager, generation)
        self.websocket_factory = websocket_factory
        self.history_fetcher = history_fetcher or fetch_yahoo_5m_history
        self.socket = None
        self.overflow = threading.Event()
        self.connection_lock = threading.Lock()
        self.connection = 0
        self.connected = False

    def start(self):
        self.status("connecting")
        self.worker = threading.Thread(target=self._consume, daemon=True, name="yahoo-quotes")
        self.runner = threading.Thread(target=self._run, daemon=True, name="yahoo-feed")
        self.worker.start()
        self.runner.start()

    def _enqueue(self, message, connection):
        if self.stopped.is_set():
            return
        try:
            self.incoming.put_nowait((connection, dict(message)))
        except queue.Full:
            self.overflow.set()

    def _consume(self):
        while not self.stopped.is_set():
            if self.overflow.is_set():
                self.status("disconnected", "Yahoo quote queue overflowed; reconnecting")
                with self.socket_lock:
                    if self.socket is not None:
                        self.socket.close()
                self.overflow.clear()
                _clear_queue(self.incoming)
            try:
                connection, message = self.incoming.get(timeout=0.1)
            except queue.Empty:
                continue
            with self.connection_lock:
                if not self.connected or connection != self.connection:
                    continue
            try:
                if message.get("id", SYMBOL) != SYMBOL:
                    continue
                price = float(message["price"])
                timestamp = feed_timestamp(message["time"])
                if math.isfinite(price) and price > 0:
                    self.tick((price, _volume(message.get("last_size", 0)), timestamp))
            except (TypeError, ValueError, KeyError, OverflowError, OSError):
                continue

    def _run(self):
        delay = 1
        while not self.stopped.is_set():
            self.status("connecting")
            history_error = None
            try:
                candles = self.history_fetcher()
                if candles:
                    self.seed(candles)
                else:
                    history_error = "Yahoo 5-minute history unavailable"
            except Exception:
                history_error = "Yahoo 5-minute history unavailable"
            try:
                if self.websocket_factory is None:
                    import yfinance as yf
                    factory = lambda: yf.WebSocket(verbose=False)
                else:
                    factory = self.websocket_factory
                with self.socket_lock:
                    if self.stopped.is_set():
                        return
                    self.socket = factory()
                    self.socket.subscribe([SYMBOL])
                with self.connection_lock:
                    self.connection += 1
                    connection = self.connection
                    self.connected = True
                self.status("live", history_error)
                self.socket.listen(lambda message: self._enqueue(message, connection))
            except Exception:
                self.status("disconnected", "Yahoo feed disconnected; reconnecting")
            finally:
                with self.connection_lock:
                    self.connected = False
                _clear_queue(self.incoming)
                with self.socket_lock:
                    if self.socket is not None:
                        self.socket.close()
                        self.socket = None
            if not self.stopped.is_set():
                self.status("disconnected", "Yahoo feed disconnected; reconnecting")
            if self.stopped.wait(delay):
                return
            delay = min(delay * 2, 30)

    def stop(self):
        self.stopped.set()
        with self.socket_lock:
            had_socket = self.socket is not None
            if self.socket is not None:
                self.socket.close()
        for thread in (getattr(self, "worker", None), getattr(self, "runner", None)):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=5 if had_socket else 1)
        if had_socket and self.runner.is_alive():
            raise RuntimeError("Yahoo socket did not stop; source switch was cancelled")


def _clear_queue(incoming):
    while True:
        try:
            incoming.get_nowait()
        except queue.Empty:
            return


class ShoonyaFeed(_Feed):
    def __init__(self, manager, generation, *, api_factory=None, credentials=None, now=None):
        super().__init__(manager, generation)
        self.api_factory = api_factory or _shoonya_api
        self.credentials = credentials
        self.now = now or (lambda: datetime.now(IST))
        self.api = None
        self.controls = deque(maxlen=32)
        self.callback_lock = threading.Lock()
        self.wake = threading.Event()
        self.connection = 0
        self.connected = False
        self.ready = None
        self.auth_failed = False
        self.quotes = ShoonyaQuotes(now=self.now)
        self.cutoff = None
        self.history_lock = threading.Lock()

    def start(self):
        self.status("connecting")
        self.worker = threading.Thread(target=self._run, daemon=True, name="shoonya-feed")
        self.worker.start()

    def _credentials(self):
        if self.credentials is not None:
            return self.credentials
        from dotenv import dotenv_values
        # Read fresh on every selection. Persisting .env values into os.environ
        # would keep an expired auth code even after the user edits the file.
        values = {**dotenv_values(ROOT / ".env"),
                  **dotenv_values(ROOT / "shoonya" / ".env"), **os.environ}
        return {key: values.get(key, "") for key in CREDENTIAL_KEYS}

    def _transition(self, kind, detail=None):
        with self.callback_lock:
            if self.stopped.is_set() or (self.auth_failed and kind != "auth_error"):
                return
            self.connection += 1
            self.connected = kind == "open"
            self.controls.append((kind, self.connection, detail))
        self.wake.set()

    def _on_open(self):
        if self.api is not None:
            self.api.retry_delay = 1
        self._transition("open")

    def _on_close(self):
        self._transition("close")

    def _on_error(self, error):
        # Never echo SDK errors, which may include authentication material.
        terminal = isinstance(error, dict) and error.get("t") == "ak"
        if terminal:
            with self.callback_lock:
                self.auth_failed = True
        self._transition("auth_error" if terminal else "error")

    def _on_tick(self, message):
        if not isinstance(message, dict):
            return
        with self.callback_lock:
            if self.stopped.is_set() or not self.connected:
                return
            connection = self.connection
        try:
            self.incoming.put_nowait((connection, dict(message)))
        except queue.Full:
            self._transition("overflow")
        self.wake.set()

    def _is_connected(self, connection):
        with self.callback_lock:
            return not self.stopped.is_set() and self.connected and connection == self.connection

    def _refresh_history(self, connection):
        candles = None
        cutoff = self.now()
        # A hung SDK HTTP request must not spawn more requests on every retry.
        if self.history_lock.acquire(blocking=False):
            try:
                rows = self.api.get_time_price_series(
                    exchange="NSE", token=NIFTY_TOKEN,
                    starttime=(cutoff - timedelta(days=60)).replace(hour=0, minute=0, second=0,
                                                                microsecond=0).timestamp(),
                    endtime=cutoff.timestamp(), interval=5)
                candles = shoonya_history(rows, now=self.now()) if isinstance(rows, list) else None
            except Exception:
                pass
            finally:
                self.history_lock.release()
        with self.callback_lock:
            if self.stopped.is_set() or connection != self.connection:
                return
            self.controls.append(("history", connection, (candles, cutoff)))
        self.wake.set()

    def _control(self, kind, connection, detail):
        with self.callback_lock:
            if self.stopped.is_set() or connection != self.connection:
                return
        if kind == "open":
            self.ready = None
            self.quotes = ShoonyaQuotes(now=self.now)
            self.cutoff = None
            _clear_queue(self.incoming)
            self.status("connecting")
            try:
                with self.socket_lock:
                    if not self._is_connected(connection):
                        return
                    self.api.subscribe(["NSE|26000"])
                threading.Thread(target=self._refresh_history, args=(connection,), daemon=True,
                                 name="shoonya-history").start()
            except Exception:
                self._transition("error")
        elif kind == "history":
            if not self._is_connected(connection):
                return
            candles, cutoff = detail
            if candles:
                self.seed(candles)
                # REST supplied the current candle. Ignore older buffered ticks
                # to avoid re-applying prices/volume already included in it.
                self.cutoff = cutoff
            self.ready = connection
            note = None if candles else "Shoonya 5-minute history unavailable; waiting for complete live bars"
            self.status("live", note)
        else:
            self.ready = None
            _clear_queue(self.incoming)
            if kind == "auth_error":
                self.manager._forget_shoonya(self.api)
                self.status("error", "Shoonya authentication failed; refresh SHOONYA_AUTH_CODE")
                with self.socket_lock:
                    self.api.close_websocket()
            else:
                self.status("disconnected", "Shoonya feed disconnected; reconnecting")
                if kind in ("error", "overflow"):
                    with self.socket_lock:
                        socket = getattr(self.api, "_NorenApi__websocket", None)
                        if socket is not None:
                            socket.close()

    def _run(self):
        credentials = self._credentials()
        day = self.now().astimezone(IST).date()
        self.api = self.manager._cached_shoonya(credentials, day, self.generation)
        missing = [key for key in CREDENTIAL_KEYS if not credentials.get(key)]
        if missing:
            self.status("error", "Missing Shoonya credentials: " + ", ".join(missing))
            return
        try:
            if self.api is None:
                self.api = self.api_factory()
                result = self.api.getAccessToken(
                    authcode=credentials["SHOONYA_AUTH_CODE"],
                    Secret_Code=credentials["SHOONYA_SECRET_CODE"],
                    client_id=credentials["SHOONYA_CLIENT_ID"], UID=credentials["SHOONYA_USER_ID"])
                if not result or len(result) != 4 or not result[0]:
                    self.status("error", "Shoonya login failed; refresh SHOONYA_AUTH_CODE and check credentials/IP")
                    return
                # OAuth auth codes may be single-use. Keep the authenticated API
                # for same-day toggles; stop closes its socket before reuse.
                self.manager._remember_shoonya(credentials, day, self.api, self.generation)
            with self.socket_lock:
                if self.stopped.is_set():
                    return
                self.api.start_websocket(subscribe_callback=self._on_tick,
                                         socket_open_callback=self._on_open,
                                         socket_close_callback=self._on_close,
                                         socket_error_callback=self._on_error)
        except Exception:
            self.status("error", "Shoonya login or connection failed; check credentials, IP and auth code")
            return
        while not self.stopped.is_set():
            self.wake.wait(0.1)
            self.wake.clear()
            with self.callback_lock:
                controls = list(self.controls)
                self.controls.clear()
            for control in controls:
                self._control(*control)
            for _ in range(500):
                if self.ready is None or not self._is_connected(self.ready):
                    break
                with self.callback_lock:
                    if self.controls:
                        break
                try:
                    connection, message = self.incoming.get_nowait()
                except queue.Empty:
                    break
                if connection != self.ready:
                    continue
                values = self.quotes.update(message)
                if values and (self.cutoff is None or values[2] > self.cutoff):
                    if self._is_connected(connection):
                        self.tick(values)

    def stop(self):
        self.stopped.set()
        self.wake.set()
        with self.socket_lock:
            if self.api is not None:
                self.api.close_websocket()
        worker = getattr(self, "worker", None)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1)
