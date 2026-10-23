"""Provider history frames become sorted canonical five-minute candles."""

from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orb.feeds import fetch_yahoo_5m_history, shoonya_history, ShoonyaQuotes
from orb.market import INSTRUMENTS
from orb.signals import IST

from orb.tests.support.providers import frame, NOW, row


class HistoryFrameTests(unittest.TestCase):
    def test_every_registered_history_uses_yahoo_identifier_and_canonical_candles(self):
        history = SimpleNamespace(iterrows=lambda: iter([(SimpleNamespace(to_pydatetime=lambda: NOW),
            {"Open": 100, "High": 110, "Low": 90, "Close": 105, "Volume": 12})]))
        for symbol, instrument in INSTRUMENTS.items():
            with self.subTest(symbol=symbol), patch("yfinance.Ticker") as ticker, patch("orb.feeds.datetime", wraps=datetime) as clock:
                clock.now.return_value = NOW + timedelta(minutes=instrument.yahoo_delay_minutes)
                ticker.return_value.history.return_value = history
                candles = fetch_yahoo_5m_history(symbol)
                ticker.assert_called_once_with(instrument.yahoo_symbol)
                ticker.return_value.history.assert_called_once_with(period="60d", interval="5m")
                self.assertEqual([candle.symbol for candle in candles], [symbol])

    def test_history_is_sorted_5m_ohlc_and_interval_volume(self):
        bars = shoonya_history([row(25), row(15), row(20), row(16), row(30, into="nan")], now=NOW)
        self.assertEqual([bar.start_time.minute for bar in bars], [15, 20, 25])
        self.assertTrue(all(bar.volume == 12 for bar in bars))
        self.assertTrue(all(bar.end_time - bar.start_time == timedelta(minutes=5) for bar in bars))
        self.assertTrue(all(bar.start_time.utcoffset() == IST.utcoffset(None) for bar in bars))

    def test_bank_history_and_quote_state_keep_their_symbol(self):
        bars = shoonya_history([row()], now=NOW, symbol="^NSEBANK")
        self.assertEqual(bars[0].symbol, "^NSEBANK")
        nifty, bank = ShoonyaQuotes(now=lambda: NOW), ShoonyaQuotes(now=lambda: NOW, token="26009")
        nifty.update(frame(lp="100", ft=NOW.timestamp(), v="1000"))
        bank_time = NOW - timedelta(seconds=1)
        self.assertEqual(bank.update(frame(tk="26009", lp="200", ft=bank_time.timestamp(), v="20")),
                         (200, 0, bank_time))
        self.assertIsNone(bank.update(frame(lp="999", ft=NOW.timestamp(), v="9999")))
        self.assertEqual(nifty.update(frame(lp="101", ft=NOW.timestamp(), v="1007"))[1], 7)
        self.assertEqual(bank.update(frame(tk="26009", lp="201", ft=NOW.timestamp(), v="23"))[1], 3)
