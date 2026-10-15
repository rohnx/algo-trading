"""The comparator shares bar mechanics without participating in NIFTY strategies."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market import INSTRUMENTS, PRIMARY_SYMBOL, InstrumentState, history_cutoff, prune_session_files
from orb_monitor import MonitorHub
from test_features import Clock, at, bar

BANK = "^NSEBANK"
FOCIT = "FOCIT.BO"


def bank_bar(slot=0, interval=5, **kwargs):
    return replace(bar(slot, interval, **kwargs), symbol=BANK)


class InstrumentTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 0))
        self.hub = MonitorHub(now=self.clock)

    def test_registry_and_full_snapshot_contract(self):
        self.assertEqual(PRIMARY_SYMBOL, "^NSEI")
        self.assertEqual([(item.label, item.shoonya_exchange, item.shoonya_token) for item in INSTRUMENTS.values()],
                         [("NIFTY 50", "NSE", "26000"), ("BANKNIFTY", "NSE", "26009"),
                          ("MIDCAP SELECT", "NSE", "26074"), ("NIFTY NEXT 50", "NSE", "26013"),
                          ("FIN NIFTY", "NSE", "26037"), ("FOCIT", "BSE", "75")])
        self.assertEqual([item.yahoo_symbol for item in INSTRUMENTS.values()],
                         ["^NSEI", "^NSEBANK", "NIFTY_MID_SELECT.NS", "^NSMIDCP", "NIFTY_FIN_SERVICE.NS", "FOCIT.BO"])
        state = self.hub.snapshot()
        self.assertEqual(state["primary_symbol"], PRIMARY_SYMBOL)
        self.assertEqual(set(state["instruments"]), set(INSTRUMENTS))
        for symbol, instrument in state["instruments"].items():
            self.assertEqual(instrument["symbol"], symbol)
            self.assertEqual(instrument["quote_delay_seconds"], 900 if symbol == FOCIT else 0)
            self.assertEqual(instrument["comparison_status"], "unavailable")
            self.assertIsNone(instrument["session_open"])
            self.assertIsNone(instrument["change_pct"])

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

    def test_provider_and_instrument_caches_restore_without_cross_contamination(self):
        self.clock.value = at(10, 15)
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            for source, offset in (("yahoo", 0), ("shoonya", 1000)):
                if source != "yahoo":
                    hub.activate_source(source)
                for index, symbol in enumerate(INSTRUMENTS):
                    price = 100 + index * 400 + offset
                    candles = [replace(bar(slot, 5, price=price), symbol=symbol) for slot in range(9)]
                    hub.seed_historical_candles(candles, 5, symbol=symbol)
            for source, offset in (("yahoo", 0), ("shoonya", 1000)):
                restored = MonitorHub(now=self.clock, cache_dir=directory)
                restored.activate_source(source)
                state = restored.snapshot()["instruments"]
                for index, symbol in enumerate(INSTRUMENTS):
                    self.assertEqual(state[symbol]["session_open"], 100 + index * 400 + offset)
                    self.assertTrue(all(candle["symbol"] == symbol for candle in state[symbol]["candles"]))
                    self.assertEqual(len(state[symbol]["candles_5m"]), 9)
            self.assertTrue((Path(directory) / "yahoo" / "2026-10-05.json").exists())
            self.assertTrue((Path(directory) / "yahoo" / "NSEBANK" / "5m" / "2026-10-05.json").exists())

    def test_revisiting_sources_reuses_history_without_rewriting_past_sessions(self):
        self.clock.value = at(10, 15)
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorHub(now=self.clock, cache_dir=directory)
            expected = {}
            for source, offset in (("yahoo", 0), ("shoonya", 1000)):
                if source != "yahoo":
                    hub.activate_source(source)
                for index, symbol in enumerate(INSTRUMENTS):
                    candles = [replace(bar(slot, 5, day=day, price=100 + index * 400 + offset + slot),
                                       symbol=symbol)
                               for day in ("2026-10-02", "2026-10-05") for slot in range(9)]
                    hub.seed_historical_candles(candles, 5, symbol=symbol)
                expected[source] = hub.snapshot()
            with patch.object(InstrumentState, "_load_sessions", side_effect=AssertionError("history reread")), \
                    patch.object(InstrumentState, "_save_candles", autospec=True) as save:
                for source in ("yahoo", "shoonya", "yahoo"):
                    hub.activate_source(source)
                    state = hub.snapshot()
                    for key in ("instruments", "candles", "candles_5m", "setups", "signals", "orb_high", "orb_low"):
                        self.assertEqual(state[key], expected[source][key])
                    self.assertEqual(hub.snapshot("2026-10-02")["session_open"],
                                     expected[source]["session_open"])
                    for symbol, instrument in hub.instruments.items():
                        path = Path(directory) / source
                        if symbol != PRIMARY_SYMBOL:
                            path /= symbol.lstrip("^")
                        self.assertEqual(instrument.cache_dir, path)
                self.assertTrue(save.called)
                self.assertEqual({call.args[1] for call in save.call_args_list}, {"2026-10-05"})

    def test_identical_backfill_does_not_rewrite_cache_but_quality_changes_do(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InstrumentState(INSTRUMENTS[BANK], now=self.clock, cache_root=directory)
            candles = [bank_bar(slot, day=day) for day in ("2026-10-02", "2026-10-05")
                       for slot in range(9)]
            state.seed_historical_candles(candles, 5)
            with patch.object(Path, "write_text", side_effect=AssertionError("unchanged cache rewrite")):
                state.seed_historical_candles(candles, 5)
                restored = InstrumentState(INSTRUMENTS[BANK], now=self.clock, cache_root=directory)
                restored.activate_source("yahoo")
                restored.seed_historical_candles(candles, 5)
            revised = replace(candles[0], high=candles[0].high + 10, close_is_valid=False)
            state.seed_historical_candles([revised], 5)
            native = json.loads((state.cache_dir / "5m" / "2026-10-02.json").read_text())
            parent = json.loads((state.cache_dir / "2026-10-02.json").read_text())
            self.assertEqual(native[0]["high"], revised.high)
            self.assertFalse(native[0]["close_is_valid"])
            self.assertEqual(parent[0]["high"], revised.high)
            self.assertFalse(parent[0]["close_is_valid"])

    def test_failed_cache_replace_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InstrumentState(INSTRUMENTS[BANK], now=self.clock, cache_root=directory)
            candles = [bank_bar()]
            destination = state.cache_dir / "5m" / "2026-10-05.json"
            with patch.object(Path, "replace", side_effect=OSError("temporary write failure")), \
                    patch("sys.stderr"):
                state._save_candles("2026-10-05", candles, 5)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".tmp").exists())
            state._save_candles("2026-10-05", candles, 5)
            self.assertEqual(json.loads(destination.read_text()), [candles[0].to_dict()])

    def test_revisited_source_rebuilds_partial_parent_without_old_live_tick(self):
        for count, tick_time in ((1, at(9, 22)), (10, at(10, 7))):
            with self.subTest(children=count):
                self.clock.value = tick_time
                hub = MonitorHub(now=self.clock)
                hub.seed_historical_candles([bank_bar(slot, price=500 + slot) for slot in range(count)],
                                            5, symbol=BANK)
                before = hub.snapshot()["instruments"][BANK]
                self.assertTrue(hub.process_tick(999, 50, symbol=BANK))
                hub.activate_source("shoonya")
                self.clock.value += timedelta(seconds=10)
                hub.activate_source("yahoo")
                state = hub.snapshot()["instruments"][BANK]
                self.assertEqual(state["active_candle"], before["active_candle"])
                self.assertEqual(state["candles_5m"], before["candles_5m"])
                self.assertEqual(state["current_price"], 500 + count)
                self.assertIsNone(state["active_candle_5m"])
                self.assertIsNone(state["last_tick_time"])
                self.assertEqual(state["comparison_status"], "stale")
                self.assertIsNone(state["change_pct"])

    def test_revisited_source_does_not_restore_unsaved_tick_only_bars(self):
        self.assertTrue(self.hub.process_tick(999, symbol=BANK))
        self.hub.activate_source("shoonya")
        self.hub.activate_source("yahoo")
        state = self.hub.snapshot()["instruments"][BANK]
        for key in ("current_price", "last_tick_time", "history_coverage_time", "active_candle", "active_candle_5m"):
            self.assertIsNone(state[key])
        self.assertEqual(state["comparison_status"], "unavailable")

    def test_inactive_source_revisited_on_new_date_keeps_history_and_clears_live(self):
        self.clock.value = at(10, 15)
        for source, offset in (("yahoo", 0), ("shoonya", 1000)):
            if source != "yahoo":
                self.hub.activate_source(source)
            for symbol in INSTRUMENTS:
                candles = [replace(bar(slot, 5, price=100 + offset), symbol=symbol) for slot in range(9)]
                self.hub.seed_historical_candles(candles, 5, symbol=symbol)
        self.clock.value = at(10, 0, day="2026-10-06")
        for source, opening in (("yahoo", 100), ("shoonya", 1100)):
            self.hub.activate_source(source)
            state = self.hub.snapshot()
            self.assertEqual(state["date"], "2026-10-06")
            self.assertFalse(state["range_locked"])
            self.assertEqual(state["signals"], [])
            for instrument in state["instruments"].values():
                self.assertEqual(instrument["candles"], [])
                self.assertEqual(instrument["candles_5m"], [])
                for key in ("current_price", "session_open", "last_tick_time", "history_coverage_time",
                            "active_candle", "active_candle_5m"):
                    self.assertIsNone(instrument[key])
            past = self.hub.snapshot("2026-10-05")
            self.assertEqual({item["session_open"] for item in past["instruments"].values()}, {opening})

    def test_first_source_visit_still_aggregates_native_five_minute_only_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in ("shoonya", "yahoo"):
                with self.subTest(source=source):
                    cache = Path(directory) / source / "NSEBANK" / "5m"
                    cache.mkdir(parents=True)
                    for day in ("2026-10-02", "2026-10-05"):
                        candles = [bank_bar(slot, day=day, price=500 + slot) for slot in range(9)]
                        (cache / f"{day}.json").write_text(json.dumps([c.to_dict() for c in candles]))
                    hub = MonitorHub(now=self.clock, cache_dir=directory)
                    hub.activate_source(source)
                    state = hub.snapshot()["instruments"][BANK]
                    self.assertEqual((len(state["candles"]), len(state["candles_5m"])), (3, 9))
                    self.assertEqual(state["session_open"], 500)
                    past = hub.snapshot("2026-10-02")["instruments"][BANK]
                    self.assertEqual((len(past["candles"]), len(past["candles_5m"])), (3, 9))
                    self.assertEqual(past["candles"][0]["close"], 503)

    def test_inactive_source_rollover_rebuilds_its_partial_historical_parent(self):
        self.clock.value = at(10, 7)
        history = [bank_bar(slot, price=500 + slot) for slot in range(10)]
        self.hub.seed_historical_candles(history, 5, symbol=BANK)
        self.assertEqual(self.hub.snapshot()["instruments"][BANK]["active_candle"]["start_time"],
                         at(10, 0).isoformat())
        self.hub.activate_source("shoonya")
        self.clock.value = at(10, 0, day="2026-10-06")
        self.hub.activate_source("yahoo")
        state = self.hub.snapshot("2026-10-05")["instruments"][BANK]
        self.assertEqual(len(state["candles_5m"]), 10)
        self.assertEqual(len(state["candles"]), 4)
        parent = state["candles"][-1]
        self.assertEqual((parent["start_time"], parent["end_time"]),
                         (at(10, 0).isoformat(), at(10, 15).isoformat()))
        self.assertEqual((parent["open"], parent["high"], parent["low"], parent["close"], parent["volume"]),
                         (history[-1].open, history[-1].high, history[-1].low,
                          history[-1].close, history[-1].volume))
        self.assertTrue(parent["is_closed"])
        self.assertFalse(parent["is_complete"])
        self.assertFalse(parent["close_is_valid"])
        self.assertIsNone(self.hub.snapshot()["instruments"][BANK]["current_price"])
        self.hub.activate_source("shoonya")
        self.hub.activate_source("yahoo")
        self.assertEqual(self.hub.snapshot("2026-10-05")["instruments"][BANK], state)

    def test_history_and_rollover_preserve_comparator_date_without_changing_live(self):
        self.hub.seed_historical_candles([bank_bar(day="2026-10-02", price=500)], 5, symbol=BANK)
        before = self.hub.snapshot()
        past = self.hub.snapshot("2026-10-02")["instruments"][BANK]
        self.assertEqual(past["session_open"], 500)
        self.assertEqual(past["comparison_status"], "closed")
        self.assertIsNone(past["history_coverage_time"])
        self.assertEqual(before, self.hub.snapshot())
        self.hub.process_tick(600, symbol=BANK)
        self.clock.value = at(day="2026-10-06")
        self.hub.advance_time()
        current = self.hub.snapshot()["instruments"][BANK]
        self.assertIsNone(current["current_price"])
        self.assertIsNone(current["session_open"])

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


class DelayedInstrumentTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 12))
        self.state = InstrumentState(INSTRUMENTS[FOCIT], now=self.clock)

    def history(self, count=9):
        return [replace(bar(slot, 5, price=500), symbol=FOCIT) for slot in range(count)]

    def snapshot(self, feed_status="live"):
        return self.state.snapshot(self.clock(), feed_status)

    def test_delayed_history_uses_source_clock_for_active_bars_and_coverage(self):
        self.state.seed_historical_candles(self.history(), 5)
        result = self.snapshot()
        self.assertEqual(self.state.quote_delay, timedelta(minutes=15))
        self.assertEqual(self.state.market_time(), at(9, 57))
        self.assertEqual(len(result["candles_5m"]), 8)
        self.assertEqual(len(result["candles"]), 2)
        self.assertEqual(result["active_candle_5m"]["start_time"], at(9, 55).isoformat())
        self.assertEqual(result["active_candle"]["start_time"], at(9, 45).isoformat())
        self.assertFalse(result["active_candle_5m"]["is_closed"])
        self.assertTrue(result["active_candle"]["is_complete"])
        self.assertEqual(result["history_coverage_time"], at(9, 57).isoformat())
        self.assertEqual(result["session_open"], 500)
        self.assertEqual(result["quote_delay_seconds"], 900)
        self.assertEqual(result["comparison_status"], "stale")
        self.assertIsNone(result["last_tick_time"])

    def test_delayed_quote_remains_in_its_native_bar_and_closes_at_delayed_boundary(self):
        self.state.seed_historical_candles(self.history(), 5)
        self.assertTrue(self.state.process_tick(520, tick_time=at(9, 57)))
        self.state._close_elapsed(self.state.market_time())
        self.assertEqual(self.state.active_candle_5m.start_time, at(9, 55))
        for quote_time in (at(9, 58, 15), at(9, 59, 30), at(9, 59, 59)):
            self.clock.value = quote_time + timedelta(minutes=15)
            self.assertTrue(self.state.process_tick(522, tick_time=quote_time))
        self.assertEqual(self.state.active_candle_5m.end_time, at(10, 0))
        self.clock.value = at(10, 15)
        closed = self.state._close_elapsed(self.state.market_time())
        self.assertEqual({interval for interval, _ in closed}, {5, 15})
        self.assertTrue(all(candle.close_is_valid for _, candle in closed))
        self.assertEqual(self.state.candles_5m[-1].close, 522)
        self.assertEqual(len(self.state.candles_5m), 9)

    def test_delayed_freshness_does_not_make_repeated_old_quotes_fresh(self):
        self.state.seed_historical_candles(self.history(), 5)
        self.assertTrue(self.state.process_tick(525, tick_time=at(9, 57)))
        result = self.snapshot()
        self.assertEqual(result["comparison_status"], "delayed")
        self.assertEqual(result["last_tick_time"], at(9, 57).isoformat())
        self.assertAlmostEqual(result["change_pct"], 5)
        self.assertEqual(self.snapshot("disconnected")["comparison_status"], "stale")
        self.clock.value = at(10, 13, 30)
        self.assertEqual(self.snapshot()["comparison_status"], "delayed")
        self.clock.value += timedelta(seconds=1)
        self.assertTrue(self.state.process_tick(525, tick_time=at(9, 57)))
        self.assertEqual(self.snapshot()["comparison_status"], "stale")
        self.assertIsNone(self.snapshot()["change_pct"])
        self.assertFalse(self.state.process_tick(526, tick_time=at(10, 13)))

    def test_delayed_source_is_open_until_its_last_quote_window_ends(self):
        self.clock.value = at(15, 30)
        self.assertTrue(self.state.set_session_open(500, at(15, 15)))
        self.state.seed_historical_candles(self.history(75), 5)
        self.assertTrue(self.state.process_tick(525, tick_time=at(15, 15)))
        self.assertEqual(self.snapshot()["comparison_status"], "delayed")
        self.clock.value = at(15, 44, 59)
        self.state.seed_historical_candles(self.history(75), 5)
        self.assertTrue(self.state.process_tick(530, tick_time=at(15, 29, 59)))
        self.assertEqual(self.snapshot()["comparison_status"], "delayed")
        self.clock.value = at(15, 45)
        self.state._close_elapsed(self.state.market_time())
        self.assertEqual(self.snapshot()["comparison_status"], "closed")
        self.assertFalse(self.state.process_tick(531, tick_time=at(15, 30)))

    def test_source_switch_removes_delay_without_carrying_quotes_or_baseline(self):
        self.assertTrue(self.state.set_session_open(500, at(9, 57)))
        self.assertTrue(self.state.process_tick(525, tick_time=at(9, 57)))
        self.state.activate_source("shoonya")
        self.assertEqual(self.state.quote_delay, timedelta(0))
        self.assertEqual(self.state.market_time(), at(10, 12))
        self.assertEqual(self.snapshot()["quote_delay_seconds"], 0)
        self.assertIsNone(self.state.current_price)
        self.assertIsNone(self.state.session_open)
        self.assertTrue(self.state.set_session_open(600, at(10, 12)))
        self.assertTrue(self.state.process_tick(630, tick_time=at(10, 12)))
        self.assertEqual(self.snapshot()["comparison_status"], "live")
        self.assertAlmostEqual(self.snapshot()["change_pct"], 5)
        self.state.activate_source("yahoo")
        self.assertEqual(self.state.quote_delay, timedelta(minutes=15))
        self.assertIsNone(self.state.current_price)
        self.assertEqual(self.state.session_open, 500)

    def test_upstream_open_requires_fresh_same_session_metadata_and_creates_no_bar(self):
        for opening, timestamp in ((0, at(9, 57)), (float("nan"), at(9, 57)),
                                   (500, at(9, 55)), (500, at(9, 57, 1)),
                                   (500, at(9, 57, day="2026-10-02")), (500, at(9, 14))):
            with self.subTest(opening=opening, timestamp=timestamp):
                self.assertFalse(self.state.set_session_open(opening, timestamp))
        self.assertTrue(self.state.set_session_open(500, at(9, 57)))
        self.assertFalse(self.state.set_session_open(999, at(9, 57)))
        self.assertEqual(self.state.session_open, 500)
        self.assertIsNone(self.state.active_candle)
        self.assertIsNone(self.state.active_candle_5m)
        self.assertEqual(self.state.candles, [])
        self.assertIsNone(self.state.current_price)

    def test_retention_and_session_date_use_actual_day_at_midnight(self):
        self.clock.value = at(0, 5, day="2026-10-13")
        state = InstrumentState(INSTRUMENTS[FOCIT], now=self.clock)
        self.assertEqual(state.date, "2026-10-13")
        self.assertEqual(state.market_time().date().isoformat(), "2026-10-12")
        old = replace(bar(0, 5, day="2026-08-13"), symbol=FOCIT)
        retained = replace(old, start_time=at(day="2026-08-14"), end_time=at(9, 20, day="2026-08-14"))
        state.seed_historical_candles([old, retained], 5)
        self.assertNotIn("2026-08-13", state.sessions_5m)
        self.assertIn("2026-08-14", state.sessions_5m)
        self.assertEqual(state.date, "2026-10-13")


class SessionOpenPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(13, 40))

    def state(self, cache_root=None, source="shoonya", symbol=FOCIT):
        state = InstrumentState(INSTRUMENTS[symbol], now=self.clock, cache_root=cache_root)
        state.activate_source(source)
        return state

    def capture_midday_bar(self, state, opening=500):
        self.assertTrue(state.process_tick(525, tick_time=state.market_time()))
        self.assertTrue(state.set_session_open(opening, state.market_time()))
        for minute, second in ((41, 0), (42, 0), (43, 0), (44, 0), (44, 59)):
            self.clock.value = at(13, minute, second)
            self.assertTrue(state.process_tick(526, tick_time=state.market_time()))
        self.clock.value = at(13, 45)
        state._close_elapsed(state.market_time())

    def test_midday_open_survives_restart_and_historical_view_without_fake_opening_candle(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            self.capture_midday_bar(state)
            path = Path(directory) / "shoonya" / "FOCIT.BO" / "opens" / "2026-10-05.json"
            metadata = json.loads(path.read_text())
            self.assertEqual(metadata["session_open"], 500)
            self.assertEqual(metadata["symbol"], FOCIT)
            self.assertEqual(metadata["source_id"], "shoonya")
            restored = self.state(directory)
            self.assertEqual(restored.session_open, 500)
            self.assertTrue(all(c.start_time >= at(13, 40) for c in restored.candles_5m))
            self.assertEqual(len(restored.candles_5m), 1)
            self.clock.value = at(10, 0, day="2026-10-06")
            restored = self.state(directory)
            past = restored.snapshot(self.clock(), "live", "2026-10-05")
            self.assertEqual(past["session_open"], 500)
            self.assertEqual(past["current_price"], 526)
            self.assertAlmostEqual(past["change_pct"], 5.2)
            self.assertEqual(past["comparison_status"], "closed")
            self.assertIsNone(restored.session_open)

    def test_baselines_follow_provider_and_symbol_in_memory_and_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory, source="yahoo")
            self.assertTrue(state.set_session_open(500, state.market_time()))
            state.activate_source("shoonya")
            self.assertIsNone(state.session_open)
            self.assertTrue(state.set_session_open(600, state.market_time()))
            state.activate_source("yahoo")
            self.assertEqual(state.session_open, 500)
            state.activate_source("shoonya")
            self.assertEqual(state.session_open, 600)
            bank = self.state(directory, symbol=BANK)
            self.assertIsNone(bank.session_open)
            self.assertTrue(bank.set_session_open(700, bank.market_time()))
            for source, opening in (("yahoo", 500), ("shoonya", 600)):
                self.assertEqual(self.state(directory, source=source).session_open, opening)
            self.assertEqual(self.state(directory, symbol=BANK).session_open, 700)
        state = self.state()
        self.assertTrue(state.set_session_open(800, state.market_time()))
        state.reset("2026-10-06")
        self.assertIsNone(state.session_open)
        self.assertEqual(state.snapshot(self.clock(), "live", "2026-10-05")["session_open"], 800)

    def test_native_opening_candle_overrides_reported_baseline_after_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            self.assertTrue(state.set_session_open(500, state.market_time()))
            state.seed_historical_candles([replace(bar(0, 5, price=600), symbol=FOCIT)], 5)
            self.assertEqual(state.session_open, 600)
            self.assertEqual(self.state(directory).session_open, 600)
            self.clock.value = at(10, 0, day="2026-10-06")
            restored = self.state(directory)
            self.assertEqual(restored.snapshot(self.clock(), "live", "2026-10-05")["session_open"], 600)

    def test_opening_is_saved_only_once_after_validated_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            with patch.object(Path, "write_text", autospec=True, wraps=Path.write_text) as write:
                self.assertFalse(state.set_session_open(-1, state.market_time()))
                self.assertTrue(state.set_session_open(500, state.market_time()))
                self.assertFalse(state.set_session_open(501, state.market_time()))
                self.assertTrue(state.process_tick(525, tick_time=state.market_time()))
                self.assertEqual(write.call_count, 1)

    def test_native_fifteen_minute_open_wins_when_five_minute_open_is_missing(self):
        state = self.state()
        self.assertTrue(state.set_session_open(500, state.market_time()))
        state.seed_historical_candles([replace(bar(0, 15, price=600), symbol=FOCIT)], 15)
        late = replace(bar(53, 5, price=700), symbol=FOCIT)
        state.seed_historical_candles([late], 5)
        self.assertEqual(state.session_open, 600)

    def test_reported_openings_share_actual_day_retention_across_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in ("yahoo", "shoonya"):
                for day in ("2026-08-13", "2026-08-14"):
                    self.clock.value = at(13, 40, day=day)
                    state = self.state(directory, source=source)
                    self.assertTrue(state.set_session_open(500, state.market_time()))
            self.clock.value = at(0, 5, day="2026-10-13")
            restored = self.state(directory, source="yahoo")
            restored.activate_source("shoonya")
            self.assertEqual(set(restored.session_opens), {"2026-08-14"})
            state.prune_history(history_cutoff(self.clock()))
            self.assertTrue(all("2026-08-13" not in values for values in state._source_opens.values()))
            prune_session_files(directory, history_cutoff(self.clock()))
            self.assertEqual({p.stem for p in Path(directory).rglob("opens/*.json")}, {"2026-08-14"})


if __name__ == "__main__":
    unittest.main()
