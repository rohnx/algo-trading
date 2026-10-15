"""Background Telegram delivery for fresh, live NIFTY dual-ORB signals.

The worker writes an outbox before posting. Telegram has no sendMessage
idempotency key: a lost response or a crash after delivery can still duplicate a
message. Ordinary repeats, provider switches and clean restarts are deduplicated.
"""

from datetime import datetime, time as dtime, timedelta
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import queue
import threading

import requests

from signals import IST

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)
REQUEST_TIMEOUT = (3, 10)
RETRY_DELAYS = (1, 2, 4, 8)
SIGNAL_MAX_AGE = timedelta(seconds=120)
DELIVERY_MAX_AGE = timedelta(minutes=10)
MAX_ATTEMPTS = 5


def read_telegram_credentials():
    """Read workspace .env without changing or overriding exported variables."""
    from dotenv import dotenv_values
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    return {key: values.get(key, "") for key in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")}


def _ist(value):
    timestamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    return timestamp.replace(tzinfo=IST) if timestamp.tzinfo is None else timestamp.astimezone(IST)


def _number(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Invalid signal price")
    return number


def format_signal(signal, source_id):
    """Return a provider-independent deduplication key and plain-text message."""
    data = signal.to_dict() if hasattr(signal, "to_dict") else signal
    direction = data["direction"]
    if (data["symbol"] != "^NSEI" or data.get("setup_id") != "dual_orb"
            or direction not in ("BULLISH", "BEARISH")
            or data.get("signal_type") != f"DUAL_ORB_{direction}"
            or source_id not in ("yahoo", "shoonya")):
        raise ValueError("Expected NIFTY dual-ORB signal")
    timestamp = _ist(data["timestamp"])
    if timestamp.weekday() >= 5 or not dtime(10, 5) <= timestamp.time() <= dtime(15, 30):
        raise ValueError("Expected a market-session confirmation")
    price = _number(data["price"])
    lines = [f"NIFTY 50 | Both ORBs {direction}",
             f"Confirmed: {timestamp:%d %b %Y %H:%M:%S} IST",
             f"NIFTY: {price:,.2f}",
             f"Provider: {'Yahoo Finance' if source_id == 'yahoo' else 'Shoonya'}"]
    latest_confirmation = None
    for interval, range_minutes in ((15, 30), (5, 45)):
        confirmation = data["confirmations"][str(interval)]
        if (confirmation["confirmation_minutes"] != interval
                or confirmation["range_minutes"] != range_minutes):
            raise ValueError("Unexpected ORB setup")
        confirmed = _ist(confirmation["confirmed_at"])
        if not timedelta(0) <= timestamp - confirmed < timedelta(minutes=interval):
            raise ValueError("Expired ORB confirmation")
        latest_confirmation = max(latest_confirmation or confirmed, confirmed)
        close = _number(confirmation["confirmation_price"])
        high, low = _number(confirmation["orb_high"]), _number(confirmation["orb_low"])
        if high < low:
            raise ValueError("Invalid ORB range")
        level, side = (high, "above") if direction == "BULLISH" else (low, "below")
        lines.extend([f"{interval}m close / {range_minutes}m ORB: {close:,.2f} {side} {level:,.2f}",
                      f"  Confirmed {confirmed:%H:%M:%S} IST | H {high:,.2f} / L {low:,.2f}"])
    if latest_confirmation != timestamp:
        raise ValueError("Signal time must match the second confirmation")
    key = f"{timestamp.date().isoformat()}:NIFTY:{direction}"
    return key, timestamp, "\n".join(lines)


class TelegramNotifier:
    """One worker, an atomic disk outbox and at most one alert per day/direction.

    Call start before enqueue. enqueue performs no disk or network operations
    and requires explicit live=True; history and replay must use the default.
    stop interrupts retry waits and leaves pending messages for a clean restart.
    """

    def __init__(self, *, cache_dir=None, now=None, token=None, chat_id=None):
        credentials = read_telegram_credentials() if token is None or chat_id is None else {}
        self._token = str((credentials.get("TELEGRAM_TOKEN", "") if token is None else token) or "").strip()
        self._chat_id = str((credentials.get("TELEGRAM_CHAT_ID", "") if chat_id is None else chat_id) or "").strip()
        self.now = now or (lambda: datetime.now(IST))
        self.directory = Path(cache_dir if cache_dir is not None else ROOT / "orb" / "data") / "notifications"
        self.outbox_path = self.directory / "telegram-outbox.json"
        self._enabled = bool(self._token and self._chat_id)
        missing = [name for name, value in (("TELEGRAM_TOKEN", self._token), ("TELEGRAM_CHAT_ID", self._chat_id))
                   if not value]
        self._status = "Telegram ready" if self._enabled else f"Telegram disabled: add {' and '.join(missing)} to .env"
        self._queue = queue.Queue()
        self._known = set()
        self._records = {}
        self._lock = threading.Lock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread = None
        self._file_lock = None

    @property
    def enabled(self):
        return self._enabled

    @property
    def status(self):
        return self._status

    def start(self):
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._file_lock = (self.directory / "telegram.lock").open("a")
            fcntl.flock(self._file_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._load()
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            self._disable("Telegram disabled: outbox unavailable, invalid, or already used by another monitor")
            self._release_file_lock()
            return
        self._known = set(self._records)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="orb-telegram", daemon=True)
        self._thread.start()

    def stop(self, timeout=2):
        with self._lock:
            self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def enqueue(self, signal, source_id, *, live=False):
        if (not live or not self.enabled or self._stop.is_set()
                or self._thread is None or not self._thread.is_alive()):
            return False
        try:
            key, timestamp, text = format_signal(signal, source_id)
            now = _ist(self.now())
            if (timestamp.date() != now.date()
                    or not -timedelta(seconds=60) <= now - timestamp <= SIGNAL_MAX_AGE):
                return False
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return False
        with self._lock:
            if self._stop.is_set() or key in self._known:
                return False
            self._known.add(key)
            self._queue.put_nowait({"key": key, "timestamp": timestamp.isoformat(), "text": text,
                                   "state": "pending", "attempts": 0, "next_attempt": now.timestamp()})
            self._status = "Telegram enabled: alert queued"
        self._wake.set()
        return True

    def _load(self):
        if not self.outbox_path.exists():
            self._records = {}
            return
        data = json.loads(self.outbox_path.read_text())
        if data["version"] != 1 or not isinstance(data["records"], dict):
            raise ValueError("Unsupported outbox")
        for key, record in data["records"].items():
            timestamp = _ist(record["timestamp"])
            if (record["key"] != key or key not in (
                    f"{timestamp.date().isoformat()}:NIFTY:BULLISH",
                    f"{timestamp.date().isoformat()}:NIFTY:BEARISH")
                    or record["state"] not in ("pending", "sent", "failed", "expired")
                    or not isinstance(record["text"], str) or len(record["text"]) > 4096
                    or not isinstance(record["attempts"], int) or not 0 <= record["attempts"] <= MAX_ATTEMPTS
                    or not math.isfinite(float(record["next_attempt"]))):
                raise ValueError("Invalid outbox record")
            record["next_attempt"] = float(record["next_attempt"])
        self._records = data["records"]

    def _persist(self):
        temporary = self.outbox_path.with_suffix(".tmp")
        with temporary.open("w") as output:
            os.chmod(temporary, 0o600)
            json.dump({"version": 1, "records": self._records}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.outbox_path)
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _drain(self):
        changed = False
        while True:
            try:
                record = self._queue.get_nowait()
            except queue.Empty:
                return changed
            self._records[record["key"]] = record
            changed = True

    def _run(self):
        try:
            while not self._stop.is_set():
                changed = self._drain()
                now = _ist(self.now())
                for record in self._records.values():
                    if record["state"] == "pending":
                        timestamp = _ist(record["timestamp"])
                        if timestamp.date() != now.date() or now - timestamp > DELIVERY_MAX_AGE:
                            record["state"], changed = "expired", True
                            LOG.warning("Telegram skipped an expired NIFTY alert")
                        elif record["attempts"] >= MAX_ATTEMPTS:
                            record["state"], changed = "failed", True
                if changed:
                    self._persist()
                pending = [record for record in self._records.values() if record["state"] == "pending"]
                due = [record for record in pending if record["next_attempt"] <= now.timestamp()]
                if due:
                    self._deliver(min(due, key=lambda record: record["next_attempt"]))
                    continue
                delay = min((record["next_attempt"] - now.timestamp() for record in pending), default=1)
                self._wake.wait(max(0.01, min(delay, 1)))
                self._wake.clear()
            # A graceful stop retains queued work without starting another request.
            if self._drain():
                self._persist()
        except Exception:
            # requests exceptions can contain a token-bearing URL; never log them.
            self._disable("Telegram disabled: delivery worker or outbox failed; check local storage and restart")
        finally:
            self._release_file_lock()

    def _deliver(self, record):
        record["attempts"] += 1
        self._persist()  # Never post an alert that has not reached the durable outbox.
        if self._stop.is_set():
            return
        code, retry_after = 0, None
        try:
            response = requests.post(f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": record["text"], "disable_web_page_preview": True},
                timeout=REQUEST_TIMEOUT, allow_redirects=False)
            code = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            if 200 <= code < 300 and body.get("ok") is True:
                record["state"] = "sent"
                self._persist()
                self._status = "Telegram enabled: last alert delivered"
                LOG.info("Telegram delivered a NIFTY dual-ORB alert")
                return
            if isinstance(body.get("error_code"), int):
                code = body["error_code"]
            if code == 429:
                parameters = body.get("parameters")
                if isinstance(parameters, dict):
                    try:
                        retry_after = max(1, float(parameters.get("retry_after", 1)))
                        if not math.isfinite(retry_after):
                            retry_after = None
                    except (ValueError, TypeError, OverflowError):
                        pass
        except requests.RequestException:
            pass
        transient = code == 0 or code == 429 or code >= 500 or 200 <= code < 300
        if transient and record["attempts"] < MAX_ATTEMPTS:
            delay = retry_after if retry_after is not None else RETRY_DELAYS[record["attempts"] - 1]
            record["next_attempt"] = _ist(self.now()).timestamp() + delay
            status = "Telegram enabled: retrying an undelivered alert"
            LOG.warning("Telegram delivery delayed; retry scheduled (attempt %s/%s)", record["attempts"], MAX_ATTEMPTS)
        else:
            record["state"] = "failed"
            guidance = {400: "check TELEGRAM_CHAT_ID and start the bot in that chat",
                        401: "check TELEGRAM_TOKEN", 403: "start or unblock the bot and check chat permissions",
                        404: "check TELEGRAM_TOKEN"}.get(code, "retry limit reached or Telegram rejected the request")
            status = f"Telegram alert failed: {guidance}"
            LOG.warning("%s", status)
        self._persist()
        self._status = status

    def _disable(self, message):
        self._enabled = False
        self._status = message
        with self._lock:
            self._stop.set()
        LOG.warning("%s", message)

    def _release_file_lock(self):
        if self._file_lock is not None:
            self._file_lock.close()
            self._file_lock = None
