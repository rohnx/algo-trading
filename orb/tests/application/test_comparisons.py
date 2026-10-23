"""Comparison updates remain independent of primary strategies and alerts."""

from dataclasses import replace
import queue
import unittest
from unittest.mock import patch

from orb.comparison import close_only_breakout
from orb.market import INSTRUMENTS, PRIMARY_SYMBOL
from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bar
from orb.tests.support.market import close_only_bars as bars


BANK = "^NSEBANK"


FOCIT = "FOCIT.BO"


MID = "NIFTY_MID_SELECT.NS"


class ComparisonHubTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(11, 0))
        self.hub = MonitorHub(now=self.clock)

    def comparison(self, symbol=BANK, day=None):
        return self.hub.snapshot(day)["instruments"][symbol]

    def seed(self, closes=None, *, symbol=BANK, day="2026-10-05"):
        self.hub.seed_historical_candles(bars(closes, symbol=symbol, day=day), 5, symbol=symbol)

    def test_comparisons_are_independent_and_never_change_primary_or_notify(self):
        before = self.hub.snapshot()["instruments"][PRIMARY_SYMBOL]
        self.seed([*range(100, 109), 110])
        self.seed([*range(100, 109), 99], symbol=MID)
        snapshot = self.hub.snapshot()
        self.assertEqual(snapshot["instruments"][BANK]["breakout"]["direction"], "BULLISH")
        self.assertEqual(snapshot["instruments"][MID]["breakout"]["direction"], "BEARISH")
        self.assertEqual(snapshot["instruments"][PRIMARY_SYMBOL], before)
        self.assertNotIn("breakout", before)
        self.assertEqual(snapshot["signals"], [])
        self.assertFalse(snapshot["setups"]["5"]["range_locked"])

    def test_only_fifteen_minute_history_and_session_open_cannot_lock_comparison(self):
        self.hub.seed_historical_candles([replace(bar(slot), symbol=BANK) for slot in range(4)],
                                         symbol=BANK)
        self.hub.process_tick(110, symbol=BANK, session_open=100)
        self.assertFalse(self.comparison()["breakout"]["range_locked"])

    def test_tick_deltas_reuse_confirmation_without_serializing_closed_history(self):
        self.clock.value = at(10, 5)
        self.seed([*range(100, 109), 110])
        with patch("orb.orb_monitor.close_only_breakout", wraps=close_only_breakout) as evaluator:
            first = self.hub._instrument_snapshot(BANK, include_history=False)
            self.assertEqual(evaluator.call_count, 1)
            self.assertEqual(first["breakout"]["direction"], "BULLISH")
            self.assertNotIn("candles_5m", first)
            self.hub.process_tick(103, symbol=BANK)
            self.clock.value = at(10, 5, 1)
            self.hub.process_tick(104, symbol=BANK)
            delta = self.hub._instrument_snapshot(BANK, include_history=False)
            self.assertEqual(delta["breakout"], first["breakout"])
            self.assertEqual(evaluator.call_count, 1)
            self.assertNotIn("candles", delta)
            self.assertNotIn("candles_5m", delta)

    def test_valid_timer_close_clears_and_next_close_can_requalify(self):
        self.clock.value = at(10, 5)
        self.seed([*range(100, 109), 110])
        self.hub.process_tick(103, symbol=BANK)
        for minute in range(6, 10):
            self.clock.value = at(10, minute)
            self.hub.process_tick(103, symbol=BANK)
        self.clock.value = at(10, 9, 59)
        self.hub.process_tick(104, symbol=BANK)
        client = queue.Queue()
        self.hub.register_client(client)
        client.get_nowait()
        self.clock.value = at(10, 10)
        self.hub.advance_time()
        event = client.get_nowait()["instruments"][BANK]
        self.assertIsNone(event["breakout"]["direction"])
        self.assertEqual(event["breakout"]["confirmed_at"], at(10, 10).isoformat())
        self.hub.process_tick(98, symbol=BANK)
        for minute in range(11, 15):
            self.clock.value = at(10, minute)
            self.hub.process_tick(98, symbol=BANK)
        self.clock.value = at(10, 14, 59)
        self.hub.process_tick(99, symbol=BANK)
        self.clock.value = at(10, 15)
        self.hub.advance_time()
        self.assertEqual(self.comparison()["breakout"]["direction"], "BEARISH")

    def test_invalid_live_close_clears_cached_qualification(self):
        self.clock.value = at(10, 7)
        self.seed([*range(100, 109), 110])
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.hub.process_tick(120, symbol=BANK)  # Started too late for a complete5m bar.
        self.clock.value = at(10, 9, 59)
        self.hub.process_tick(121, symbol=BANK)
        self.clock.value = at(10, 10)
        self.hub.advance_time()
        state = self.hub._instrument_snapshot(BANK, include_history=False)["breakout"]
        self.assertIsNone(state["direction"])
        self.assertIsNone(state["confirmed_at"])

    def test_backfill_replacement_invalidates_light_delta_cache(self):
        self.seed([*range(100, 109), 110])
        before = self.hub._instrument_snapshot(BANK, include_history=False)["breakout"]
        self.assertEqual(before["direction"], "BULLISH")
        self.seed([*range(100, 109), 105])
        self.assertIsNone(self.hub._instrument_snapshot(BANK, include_history=False)["breakout"]["direction"])

    def test_yahoo_backfill_is_composed_before_close_only_evaluation(self):
        self.clock.value = at(10, 30)
        self.hub.activate_source("shoonya")
        self.hub.seed_fallback_history(bars([*range(100, 109), 110], symbol=FOCIT), symbol=FOCIT)
        fallback = self.comparison(FOCIT)
        self.assertTrue(fallback["history_fallback"])
        self.assertEqual(fallback["breakout"]["direction"], "BULLISH")
        self.assertEqual(self.hub.instruments[FOCIT].candles_5m, [])
        # A later invalid native close blocks a valid but older fallback confirmation.
        native = replace(bars([*range(100, 109), 110, 120], symbol=FOCIT)[-1],
                         is_complete=False, close_is_valid=False)
        self.hub.seed_historical_candles([native], 5, symbol=FOCIT)
        self.assertIsNone(self.comparison(FOCIT)["breakout"]["direction"])
        # Native valid replacements win over fallback values in the range too.
        self.hub.seed_historical_candles(bars([100] * 9 + [99, 98], symbol=FOCIT), 5, symbol=FOCIT)
        native = self.comparison(FOCIT)["breakout"]
        self.assertEqual((native["range_high"], native["range_low"]), (100, 100))
        self.assertEqual(native["direction"], "BEARISH")

    def test_delayed_fallback_does_not_promote_a_future_five_minute_close(self):
        self.clock.value = at(10, 17)  # Yahoo history is complete only through10:00.
        self.hub.activate_source("shoonya")
        self.hub.seed_fallback_history(bars([*range(100, 109), 110], symbol=FOCIT), symbol=FOCIT)
        state = self.comparison(FOCIT)["breakout"]
        self.assertTrue(state["range_locked"])
        self.assertIsNone(state["direction"])
        self.assertIsNone(state["confirmed_at"])

    def test_history_source_switch_and_new_day_do_not_leak_comparison_state(self):
        self.seed([*range(100, 109), 110])
        self.seed([*range(100, 109), 99], day="2026-10-02")
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.assertEqual(self.comparison(day="2026-10-02")["breakout"]["direction"], "BEARISH")
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.hub.activate_source("shoonya")
        self.assertFalse(self.comparison()["breakout"]["range_locked"])
        self.seed([100] * 9 + [99])
        self.assertEqual(self.comparison()["breakout"]["direction"], "BEARISH")
        self.hub.activate_source("yahoo")
        self.assertEqual(self.comparison()["breakout"]["direction"], "BULLISH")
        self.clock.value = at(10, 10, day="2026-10-06")
        self.assertFalse(self.comparison()["breakout"]["range_locked"])
        self.assertEqual(self.comparison(day="2026-10-05")["breakout"]["direction"], "BULLISH")


class ComparatorWiringTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 0))
        self.hub = MonitorHub(now=self.clock)

    def test_comparator_history_does_not_rebuild_primary_strategies(self):
        self.clock.value = at(10, 15)
        self.hub.seed_historical_candles([bar(slot, 5) for slot in range(9)], 5)
        before = self.hub.snapshot()
        for symbol in INSTRUMENTS:
            if symbol == PRIMARY_SYMBOL:
                continue
            candles = [replace(bar(slot, 5, price=500 + slot * 10), symbol=symbol) for slot in range(9)]
            self.hub.seed_historical_candles(candles, 5, symbol=symbol)
            after = self.hub.snapshot()
            for key in ("candles", "candles_5m", "setups", "signals", "orb_high", "orb_low"):
                self.assertEqual(before[key], after[key])
            self.assertEqual(len(after["instruments"][symbol]["candles"]), 3)
            self.assertEqual(len(after["instruments"][symbol]["candles_5m"]), 9)
            self.assertNotIn("setups", after["instruments"][symbol])

    def test_incremental_events_cannot_replace_primary_state_with_comparator(self):
        client = queue.Queue()
        self.hub.register_client(client)
        self.assertEqual(set(client.get_nowait()["instruments"]), set(INSTRUMENTS))
        self.hub.process_tick(100)
        primary = client.get_nowait()
        self.assertEqual(primary["type"], "tick")
        self.assertEqual(set(primary["instruments"]), {PRIMARY_SYMBOL})
        self.hub.process_tick(500, symbol=BANK)
        comparator = client.get_nowait()
        self.assertEqual(set(comparator) - {"date", "today", "source_id", "source_generation", "source_epoch"},
                         {"type", "symbol", "instruments"})
        self.assertEqual((comparator["type"], comparator["symbol"]), ("state", BANK))
        self.assertEqual(set(comparator["instruments"]), {BANK})
        self.clock.value = at(10, 5)
        self.hub.advance_time()
        events = list(client.queue)
        close = next(event for event in events if event["type"] == "candle_close")
        self.assertEqual(set(close["instruments"]), {PRIMARY_SYMBOL})
        self.assertTrue(any(event["type"] == "state" and event["symbol"] == BANK for event in events))
        self.hub.set_feed_status("disconnected")
        self.assertEqual(set(client.queue[-1]["instruments"]), set(INSTRUMENTS))
