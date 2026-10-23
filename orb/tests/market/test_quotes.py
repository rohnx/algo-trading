"""Independent instrument prices, opening baselines, and live handoff."""

from dataclasses import replace
import unittest

from orb.market import INSTRUMENTS, PRIMARY_SYMBOL
from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bank_bar, bar


BANK = "^NSEBANK"


class InstrumentQuoteTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 0))
        self.hub = MonitorHub(now=self.clock)

    def test_all_indices_have_independent_open_return_freshness_and_bars(self):
        self.hub.activate_source("shoonya")
        self.hub.set_feed_status("live")
        for index, symbol in enumerate(INSTRUMENTS):
            opening = 100 * (index + 1)
            candles = [replace(bar(slot, 5, price=opening), symbol=symbol) for slot in range(9)]
            self.hub.seed_historical_candles(candles, 5, symbol=symbol)
            self.hub.process_tick(opening * (1 + index / 100), symbol=symbol)
        states = self.hub.snapshot()["instruments"]
        for index, symbol in enumerate(INSTRUMENTS):
            with self.subTest(symbol=symbol):
                self.assertEqual(states[symbol]["session_open"], 100 * (index + 1))
                self.assertAlmostEqual(states[symbol]["change_pct"], index)
                self.assertEqual(states[symbol]["comparison_status"], "live")
                self.assertEqual(states[symbol]["active_candle_5m"]["symbol"], symbol)
        self.clock.value = at(10, 2)
        self.hub.process_tick(101)
        states = self.hub.snapshot()["instruments"]
        self.assertEqual(states[PRIMARY_SYMBOL]["comparison_status"], "live")
        for symbol in INSTRUMENTS:
            if symbol != PRIMARY_SYMBOL:
                self.assertEqual(states[symbol]["comparison_status"], "stale")
                self.assertIsNone(states[symbol]["change_pct"])

    def test_late_first_quote_never_becomes_session_open(self):
        for index, symbol in enumerate(INSTRUMENTS):
            price = 100 + index * 100
            self.assertTrue(self.hub.process_tick(price, symbol=symbol))
            state = self.hub.snapshot()["instruments"][symbol]
            self.assertEqual(state["current_price"], price)
            self.assertIsNone(state["session_open"])
            self.assertIsNone(state["change_pct"])
            self.assertEqual(state["comparison_status"], "unavailable")
        self.assertEqual(self.hub.current_price, 100)

    def test_opening_tick_and_native_history_supply_independent_percentages(self):
        self.clock.value = at()
        self.hub.process_tick(100)
        self.hub.process_tick(500, symbol=BANK)
        self.clock.value = at(9, 16)
        self.hub.process_tick(102)
        self.hub.process_tick(495, symbol=BANK)
        instruments = self.hub.snapshot()["instruments"]
        self.assertAlmostEqual(instruments[PRIMARY_SYMBOL]["change_pct"], 2)
        self.assertAlmostEqual(instruments[BANK]["change_pct"], -1)
        self.clock.value = at(10, 0)
        self.hub.seed_historical_candles([bank_bar(slot, price=600) for slot in range(9)], 5, symbol=BANK)
        state = self.hub.snapshot()
        self.assertEqual(state["instruments"][BANK]["session_open"], 600)
        self.assertEqual(state["instruments"][PRIMARY_SYMBOL]["session_open"], 100)

    def test_missing_invalid_and_late_history_do_not_supply_open(self):
        for opening in (bank_bar(0, complete=False), replace(bank_bar(0), close_is_valid=False), bank_bar(1)):
            with self.subTest(opening=opening):
                hub = MonitorHub(now=self.clock)
                hub.seed_historical_candles([opening], 5, symbol=BANK)
                self.assertIsNone(hub.snapshot()["instruments"][BANK]["session_open"])

    def test_current_five_minute_backfill_survives_live_handoff_and_close(self):
        for symbol in INSTRUMENTS:
            with self.subTest(symbol=symbol):
                self.clock.value = at(10, 12)
                hub = MonitorHub(now=self.clock)
                hub.activate_source("shoonya")
                hub.set_feed_status("live")
                history = [replace(bar(slot, 5, price=500), symbol=symbol) for slot in range(12)]
                history[-1] = replace(history[-1], open=500, high=515, low=490, close=512, volume=17)
                hub.seed_historical_candles(history, 5, symbol=symbol)
                state = hub.snapshot()["instruments"][symbol]
                self.assertEqual(state["active_candle_5m"], replace(history[-1], is_closed=False).to_dict())
                self.assertEqual(state["current_price"], 512)
                self.assertIsNone(state["last_tick_time"])
                self.assertEqual(state["history_coverage_time"], at(10, 12).isoformat())
                self.assertEqual(state["comparison_status"], "stale")
                self.assertEqual(len(state["candles_5m"]), 11)
                historical = hub.snapshot("2026-10-02")["instruments"][symbol]
                self.assertIsNone(historical["history_coverage_time"])

                self.clock.value = at(10, 12, 1)
                self.assertTrue(hub.process_tick(518, 3, symbol=symbol))
                state = hub.snapshot()["instruments"][symbol]
                active = state["active_candle_5m"]
                self.assertEqual(state["last_tick_time"], at(10, 12, 1).isoformat())
                self.assertEqual(state["history_coverage_time"], at(10, 12).isoformat())
                self.assertEqual(state["comparison_status"], "live")
                self.assertEqual((active["open"], active["high"], active["low"], active["close"], active["volume"]),
                                 (500, 518, 490, 518, 20))
                self.assertTrue(active["is_complete"])
                self.assertTrue(active["close_is_valid"])
                for tick_time in (at(10, 13, 15), at(10, 14, 30), at(10, 14, 59)):
                    self.clock.value = tick_time
                    self.assertTrue(hub.process_tick(514, 2, symbol=symbol))
                self.clock.value = at(10, 15, 1)
                self.assertTrue(hub.process_tick(520, 4, symbol=symbol))
                state = hub.snapshot()["instruments"][symbol]
                closed = state["candles_5m"][-1]
                self.assertEqual(len(state["candles_5m"]), 12)
                self.assertEqual((closed["open"], closed["high"], closed["low"], closed["close"], closed["volume"]),
                                 (500, 518, 490, 514, 26))
                self.assertTrue(closed["is_complete"])
                self.assertTrue(closed["close_is_valid"])
                self.assertEqual(state["candles_5m"][-2]["end_time"], closed["start_time"])
                self.assertEqual(closed["end_time"], state["active_candle_5m"]["start_time"])

    def test_current_five_minute_backfill_retains_quality_flags(self):
        self.clock.value = at(10, 12)
        for complete, valid_close in ((False, True), (True, False), (False, False)):
            with self.subTest(complete=complete, valid_close=valid_close):
                hub = MonitorHub(now=self.clock)
                history = [bank_bar(slot, 5, price=500) for slot in range(12)]
                history[-1] = replace(history[-1], is_complete=complete, close_is_valid=valid_close)
                hub.seed_historical_candles(history, 5, symbol=BANK)
                active = hub.snapshot()["instruments"][BANK]["active_candle_5m"]
                self.assertIsNotNone(active)
                self.assertEqual((active["is_complete"], active["close_is_valid"]), (complete, valid_close))
                self.assertTrue(hub.process_tick(502, 1, symbol=BANK))
                active = hub.snapshot()["instruments"][BANK]["active_candle_5m"]
                self.assertEqual((active["is_complete"], active["close_is_valid"]), (complete, valid_close))

    def test_freshness_and_invalid_close_are_independent(self):
        self.hub.seed_historical_candles([bar(0, 5)], 5)
        self.hub.seed_historical_candles([bank_bar()], 5, symbol=BANK)
        self.hub.process_tick(100)
        self.hub.process_tick(500, symbol=BANK)
        for minute in range(1, 5):
            self.clock.value = at(10, minute)
            self.hub.process_tick(102)
        self.clock.value = at(10, 4, 59)
        self.hub.process_tick(102)
        state = self.hub.snapshot()
        self.assertEqual(state["market_status"], "live")
        self.assertEqual(state["instruments"][PRIMARY_SYMBOL]["comparison_status"], "live")
        self.assertEqual(state["instruments"][BANK]["comparison_status"], "stale")
        self.assertIsNone(state["instruments"][BANK]["change_pct"])
        self.clock.value = at(10, 5)
        state = self.hub.snapshot()
        self.assertTrue(state["candles_5m"][-1]["close_is_valid"])
        self.assertFalse(state["instruments"][BANK]["candles_5m"][-1]["close_is_valid"])
        self.assertFalse(self.hub.process_tick(999, tick_time=at(10, 4), symbol=BANK))

    def test_invalid_latest_saved_close_keeps_raw_price_without_return(self):
        for historical, hour in ((False, 16), (True, 16), (False, 10)):
            with self.subTest(historical=historical, hour=hour):
                self.hub = MonitorHub(now=self.clock)
                self.clock.value = at(hour, 0)
                day = "2026-10-02" if historical else "2026-10-05"
                candles = [bank_bar(slot, day=day, price=500) for slot in range(2)]
                candles[-1].close_is_valid = False
                self.hub.seed_historical_candles(candles, 5, symbol=BANK)
                state = self.hub.snapshot(day)["instruments"][BANK]
                self.assertEqual(state["date"], day)
                self.assertEqual(state["current_price"], 501)
                self.assertEqual(state["session_open"], 500)
                self.assertIsNone(state["change_pct"])
                self.assertEqual(state["comparison_status"], "unavailable")
