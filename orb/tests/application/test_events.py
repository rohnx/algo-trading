"""Application events retain dates, histories, and complete SSE recovery snapshots."""

import io
import queue
import unittest
from unittest.mock import patch

from orb.market import INSTRUMENTS
from orb.orb_monitor import MonitorHub, SYMBOL
from orb.signals import Candle

from orb.tests.support.clock import Clock
from orb.tests.support.market import five_bars, timestamp


class EventStreamTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(timestamp("2026-10-05", 10, 5))
        self.hub = MonitorHub(now=self.clock)

    def test_market_hours_publish_status_without_ticks_or_idle_snapshots(self):
        clock = Clock(timestamp("2026-10-05", 9, 14, 59))
        hub = MonitorHub(now=clock)
        hub.activate_source("shoonya")
        client = queue.Queue()
        hub.register_client(client)
        self.assertEqual(client.get_nowait()["market_status"], "closed")

        clock.value = timestamp("2026-10-05", 9, 15)
        hub.advance_time()
        opened = client.get_nowait()
        self.assertEqual((opened["type"], opened["market_status"]), ("status", "waiting"))
        hub.advance_time()
        self.assertTrue(client.empty())

        clock.value = timestamp("2026-10-05", 10, 5)
        for symbol in INSTRUMENTS:
            hub.seed_historical_candles(five_bars("2026-10-05", symbol=symbol), 5, symbol=symbol)
        self.assertTrue(client.empty())
        clock.value = timestamp("2026-10-05", 15, 30)
        hub.advance_time()
        closed = client.get_nowait()
        self.assertEqual((closed["type"], closed["market_status"]), ("status", "closed"))
        self.assertTrue(all(item["comparison_status"] == "closed" for item in closed["instruments"].values()))
        hub.advance_time()
        self.assertTrue(client.empty())

        clock.value = timestamp("2026-10-06", 9, 15)
        hub.advance_time()
        self.assertEqual(client.get_nowait()["type"], "session")
        self.assertTrue(client.empty(), "date rollover already carries the new market status")

    def test_sse_init_and_tick_include_session_dates(self):
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        self.assertEqual(initial["type"], "init")
        self.assertEqual(initial["date"], "2026-10-05")
        self.assertEqual(initial["today"], "2026-10-05")
        self.hub.process_tick(100, tick_time=self.clock.value)
        tick = client.get_nowait()
        self.assertEqual(tick["type"], "tick")
        self.assertEqual(tick["date"], initial["date"])
        self.assertEqual(tick["today"], initial["today"])

    def test_quote_events_serialize_only_active_bars_and_keep_instrument_summary(self):
        for symbol in INSTRUMENTS:
            self.hub.seed_historical_candles(five_bars("2026-10-05", symbol=symbol), 5, symbol=symbol)
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        closed = {id(candle) for instrument in self.hub.instruments.values()
                  for candle in instrument.candles + instrument.candles_5m}
        serialize = Candle.to_dict

        def active_only(candle):
            self.assertNotIn(id(candle), closed, "A quote must not serialize closed history")
            return serialize(candle)

        for symbol in INSTRUMENTS:
            quote_time = self.hub.instruments[symbol].market_time(self.clock.value)
            with self.subTest(symbol=symbol), patch.object(Candle, "to_dict", active_only):
                self.assertTrue(self.hub.process_tick(115, 2, quote_time, symbol=symbol))
                event = client.get_nowait()
            delta = event["instruments"][symbol]
            self.assertNotIn("candles", delta)
            self.assertNotIn("candles_5m", delta)
            full = self.hub.snapshot()["instruments"][symbol]
            self.assertEqual(delta, {key: value for key, value in full.items()
                                     if key not in ("candles", "candles_5m")})
            self.assertEqual(delta["active_candle_5m"]["close"], 115)
            self.assertEqual(delta["last_tick_time"], quote_time.isoformat())
            self.assertEqual(full["candles_5m"], initial["instruments"][symbol]["candles_5m"])

    def test_reported_open_supplies_returns_without_inventing_opening_bars(self):
        for symbol, hour, minute, second in ((SYMBOL, 9, 15, 20), (SYMBOL, 10, 5, 0), ("FOCIT.BO", 10, 5, 0)):
            with self.subTest(symbol=symbol, first_tick=(hour, minute, second)):
                self.clock.value = timestamp("2026-10-19", hour, minute, second)
                hub = MonitorHub(now=self.clock)
                hub.activate_source("shoonya")
                hub.set_feed_status("live")
                client = queue.Queue()
                hub.register_client(client)
                client.get_nowait()
                self.assertTrue(hub.process_tick(23100, tick_time=self.clock.value,
                                                 symbol=symbol, session_open=23000))
                event = client.get_nowait()
                instrument = event["instruments"][symbol]
                self.assertEqual(instrument["session_open"], 23000)
                self.assertEqual(instrument["comparison_status"], "live")
                self.assertAlmostEqual(instrument["change_pct"], (23100 / 23000 - 1) * 100)
                state = hub.snapshot()
                instrument = state["instruments"][symbol]
                self.assertEqual(instrument["candles"], [])
                self.assertEqual(instrument["candles_5m"], [])
                self.assertEqual(instrument["active_candle"]["open"], 23100)
                self.assertEqual(instrument["active_candle_5m"]["open"], 23100)
                self.assertFalse(state["range_locked"])
                self.assertEqual(state["signals"], [])
                self.assertTrue(all(not setup["range_locked"] for setup in state["setups"].values()))

    def test_candle_close_supplies_full_history_after_quote_delta(self):
        self.hub.seed_historical_candles(five_bars("2026-10-05"), 5)
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        self.hub.process_tick(115, 2, self.clock.value)
        self.assertNotIn("candles_5m", client.get_nowait()["instruments"][SYMBOL])
        self.clock.value = timestamp("2026-10-05", 10, 9, 59)
        self.hub.process_tick(116, 3, self.clock.value)
        client.get_nowait()
        self.clock.value = timestamp("2026-10-05", 10, 10)
        self.hub.advance_time()
        closed = client.get_nowait()
        self.assertEqual((closed["type"], closed["interval_minutes"]), ("candle_close", 5))
        instrument = closed["instruments"][SYMBOL]
        self.assertEqual(instrument["candles_5m"][:-1], initial["candles_5m"])
        self.assertEqual(instrument["candles_5m"][-1], closed["candle"])
        self.assertEqual(instrument, self.hub.snapshot()["instruments"][SYMBOL])

    def test_confirmed_signal_arrives_with_close_without_duplicate_signal_event(self):
        self.clock.value = timestamp("2026-10-05", 10, 0)
        opening = five_bars("2026-10-05")
        for candle in opening[6:]:
            candle.open = candle.close = 115
            candle.high, candle.low = 120, 110
        self.hub.seed_historical_candles(opening, 5)
        for minute in range(5):
            self.clock.value = timestamp("2026-10-05", 10, minute)
            self.hub.process_tick(125, tick_time=self.clock.value)
        client = queue.Queue()
        self.hub.register_client(client)
        initial = client.get_nowait()
        self.assertEqual(initial["last_tick_time"], self.clock.value.isoformat())
        self.assertEqual(initial["signals"], [])
        self.clock.value = timestamp("2026-10-05", 10, 5)
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.hub.advance_time()
        closed = client.get_nowait()
        self.assertEqual((closed["type"], closed["interval_minutes"]), ("candle_close", 5))
        self.assertEqual(len(closed["signals"]), 1)
        signal = closed["signals"][0]
        self.assertEqual(signal["direction"], "BULLISH")
        self.assertEqual(signal["timestamp"], self.clock.value.isoformat())
        self.assertEqual(signal["confirmations"]["15"]["confirmed_at"],
                         timestamp("2026-10-05", 10, 0).isoformat())
        self.assertEqual(signal["confirmations"]["5"]["confirmed_at"], self.clock.value.isoformat())
        self.assertIn(signal["message"], output.getvalue())
        self.assertTrue(client.empty())

    def test_backlogged_sse_client_receives_complete_snapshot(self):
        self.hub.seed_historical_candles(five_bars("2026-10-05"), 5)
        client = queue.Queue(maxsize=1)
        self.hub.register_client(client)
        self.hub.process_tick(100, tick_time=self.clock.value)
        recovered = client.get_nowait()
        self.assertEqual(recovered["type"], "init")
        self.assertEqual(recovered["active_candle"]["close"], 100)
        self.assertEqual(len(recovered["instruments"][SYMBOL]["candles_5m"]), 9)
        self.assertEqual(len(recovered["candles"]), 3)

    def test_backlogged_clients_share_one_complete_recovery_snapshot(self):
        clients = [queue.Queue(maxsize=1), queue.Queue(maxsize=1)]
        for client in clients:
            self.hub.register_client(client)
        with patch.object(self.hub, "_snapshot", wraps=self.hub._snapshot) as snapshot:
            self.hub.process_tick(100, tick_time=self.clock.value)
            self.assertEqual(snapshot.call_count, 1)
        events = [client.get_nowait() for client in clients]
        self.assertEqual(events[0], events[1])
        self.assertEqual(events[0]["type"], "init")
        self.assertEqual(events[0]["active_candle"]["close"], 100)
