"""Registered instruments and their public snapshot metadata."""

from dataclasses import replace
import unittest

from orb.market import INSTRUMENTS, PRIMARY_SYMBOL
from orb.orb_monitor import MonitorHub

from orb.tests.support.clock import Clock
from orb.tests.support.market import at, bar


FOCIT = "FOCIT.BO"


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(at(10, 0))
        self.hub = MonitorHub(now=self.clock)

    def test_registry_and_full_snapshot_contract(self):
        self.assertEqual(PRIMARY_SYMBOL, "^NSEI")
        derivatives = [item for item in INSTRUMENTS.values() if item.comparison_group != "sectors"]
        self.assertEqual([(item.label, item.shoonya_exchange, item.shoonya_token) for item in derivatives],
                         [("NIFTY 50", "NSE", "26000"), ("BANKNIFTY", "NSE", "26009"),
                          ("MIDCAP SELECT", "NSE", "26074"), ("NIFTY NEXT 50", "NSE", "26013"),
                          ("FIN NIFTY", "NSE", "26037"), ("FOCIT", "BSE", "75")])
        self.assertEqual([item.yahoo_symbol for item in derivatives],
                         ["^NSEI", "^NSEBANK", "NIFTY_MID_SELECT.NS", "^NSMIDCP", "NIFTY_FIN_SERVICE.NS", "FOCIT.BO"])
        self.assertEqual(len(INSTRUMENTS), 21)
        state = self.hub.snapshot()
        self.assertEqual(state["primary_symbol"], PRIMARY_SYMBOL)
        self.assertEqual(set(state["instruments"]), set(INSTRUMENTS))
        for symbol, instrument in state["instruments"].items():
            self.assertEqual(instrument["symbol"], symbol)
            self.assertEqual(instrument["comparison_group"], INSTRUMENTS[symbol].comparison_group)
            self.assertEqual(instrument["quote_delay_seconds"], 900 if symbol == FOCIT else 0)
            self.assertEqual(instrument["comparison_status"], "unavailable")
            self.assertIsNone(instrument["session_open"])
            self.assertIsNone(instrument["change_pct"])

    def test_sector_registry_matches_verified_tokens_and_short_names_in_requested_order(self):
        sectors = [item for item in INSTRUMENTS.values() if item.comparison_group == "sectors"]
        self.assertEqual([(item.symbol, item.label, item.shoonya_token) for item in sectors], [
            ("^CNXPHARMA", "PHARMA", "26023"), ("^CNXAUTO", "AUTO", "26029"),
            ("^CNXFMCG", "FMCG", "26021"), ("^CNXMETAL", "METAL", "26030"),
            ("^CNXPSUBANK", "PSU BANK", "26025"), ("^CNXREALTY", "REALTY", "26018"),
            ("NIFTY_CONSR_DURBL.NS", "CONSUMER DURABLES", "26070"),
            ("NIFTY_OIL_AND_GAS.NS", "OIL & GAS", "26071"),
            ("NIFTY_PVT_BANK.NS", "PRIVATE BANK", "26047"),
            ("^CNXENERGY", "ENERGY", "26020"), ("NIFTY_IND_DEFENCE.NS", "INDIA DEFENCE", "26092"),
            ("^CNXINFRA", "INFRASTRUCTURE", "26019"), ("^CNXPSE", "PSE", "26024"),
            ("^CNXCMDT", "COMMODITIES", "26035"), ("NIFTY_CPSE.NS", "CPSE", "26041")])
        for item in sectors:
            self.assertEqual(item.yahoo_symbol, item.symbol)
            self.assertEqual(item.shoonya_exchange, "NSE")
            self.assertEqual(item.shoonya_history_fallback, "yahoo")
            self.assertEqual(item.yahoo_delay_minutes, 0)
        self.assertEqual(INSTRUMENTS[PRIMARY_SYMBOL].comparison_group, "primary")
        self.assertEqual(sum(item.comparison_group == "derivatives" for item in INSTRUMENTS.values()), 5)

    def test_sector_group_survives_quote_deltas_and_historical_snapshots(self):
        symbol = "^CNXPHARMA"
        history = [replace(bar(slot, 5, day="2026-10-02"), symbol=symbol) for slot in range(9)]
        self.hub.seed_historical_candles(history, 5, symbol=symbol)
        self.hub.process_tick(500, symbol=symbol, session_open=490)
        self.assertEqual(self.hub._instrument_snapshot(symbol, include_history=False)["comparison_group"], "sectors")
        self.assertEqual(self.hub.snapshot("2026-10-02")["instruments"][symbol]["comparison_group"], "sectors")
