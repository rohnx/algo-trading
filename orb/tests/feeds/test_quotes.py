"""Shoonya quote deltas retain token, clock, volume, and opening metadata."""

from datetime import timedelta
import unittest

from orb.feeds import feed_timestamp, ShoonyaQuotes

from orb.tests.support.providers import frame, NOW


class QuoteContractTests(unittest.TestCase):
    def test_snapshot_diffs_and_volume_only_updates(self):
        quotes = ShoonyaQuotes()
        self.assertEqual(quotes.update(frame(t="tk", lp="100", ft=str(NOW.timestamp()),
                                             v="1000", bp1="99")), (100, 0, NOW))
        self.assertIsNone(quotes.update(frame(v="1020", ft=str((NOW + timedelta(seconds=1)).timestamp()))))
        self.assertIsNone(quotes.update(frame(lp="101")))
        timestamp = NOW + timedelta(seconds=2)
        self.assertEqual(quotes.update(frame(lp="102", ft=str(int(timestamp.timestamp() * 1000)))),
                         (102, 20, timestamp))
        self.assertEqual(quotes.update(frame(lp="103", ft=str((timestamp + timedelta(seconds=1)).timestamp())))[1], 0)

    def test_rejects_other_tokens_stale_time_and_invalid_prices(self):
        quotes = ShoonyaQuotes()
        quotes.update(frame(t="tk", lp="100", ft=NOW.timestamp(), v="100"))
        for message in [frame(tk="2885", lp="999", ft=NOW.timestamp()),
                        frame(e="BSE", lp="999", ft=NOW.timestamp()),
                        frame(lp="nan", ft=NOW.timestamp()), frame(lp="0", ft=NOW.timestamp()),
                        frame(lp="999", ft=123), frame(lp="999", ft=None),
                        frame(lp="999", ft=(NOW - timedelta(seconds=1)).timestamp(), v="999")]:
            self.assertIsNone(quotes.update(message))
        self.assertEqual(quotes.update(frame(lp="101", ft=(NOW + timedelta(seconds=1)).timestamp(), v="105"))[1], 5)

    def test_cumulative_reset_and_new_session_are_baselines(self):
        quotes = ShoonyaQuotes()
        quotes.update(frame(lp="100", ft=NOW.timestamp(), v="1000"))
        self.assertEqual(quotes.update(frame(lp="101", ft=(NOW + timedelta(seconds=1)).timestamp(), v="2"))[1], 0)
        self.assertEqual(quotes.update(frame(lp="101", ft=(NOW + timedelta(seconds=2)).timestamp(), v="7"))[1], 5)
        tomorrow = NOW + timedelta(days=1)
        self.assertEqual(quotes.update(frame(lp="102", ft=tomorrow.timestamp(), v="300"))[1], 0)

    def test_bse_quotes_require_their_own_exchange_and_token(self):
        quotes = ShoonyaQuotes(now=lambda: NOW, exchange="BSE", token="75")
        self.assertIsNone(quotes.update(frame(tk="75", lp="999", ft=NOW.timestamp())))
        self.assertIsNone(quotes.update(frame(e="BSE", lp="999", ft=NOW.timestamp())))
        self.assertEqual(quotes.update(frame(e="BSE", tk="75", lp="30000", ft=NOW.timestamp(), v="100")),
                         (30000, 0, NOW))
        self.assertEqual(quotes.update(frame(e="BSE", tk="75", lp="30010", ft=NOW.timestamp(), v="105")),
                         (30010, 5, NOW))

    def test_touchline_open_is_retained_across_deltas_without_inventing_tick_times(self):
        quotes = ShoonyaQuotes(now=lambda: NOW, exchange="BSE", token="75")
        quotes.update(frame(t="tk", e="BSE", tk="75", lp="30000", o="29000", ft=NOW.timestamp()))
        self.assertEqual(quotes.session_open, 29000)
        quotes.update(frame(e="BSE", tk="75", lp="30010", ft=NOW.timestamp()))
        self.assertEqual(quotes.session_open, 29000)
        self.assertIsNone(quotes.update(frame(e="BSE", tk="75", o="99999")))
        self.assertEqual(quotes.session_open, 29000)
        quotes.update(frame(t="tk", e="BSE", tk="75", lp="30010", o="nan", ft=NOW.timestamp()))
        self.assertIsNone(quotes.session_open)

    def test_feed_timestamp_accepts_epoch_units_but_no_receipt_fallback(self):
        self.assertEqual(feed_timestamp(NOW.timestamp()), NOW)
        self.assertEqual(feed_timestamp(NOW.timestamp() * 1000), NOW)
        for value in (0, None, "", "nan", NOW.timestamp() * 1000000):
            with self.assertRaises((ValueError, TypeError)):
                feed_timestamp(value)

    def test_future_feed_time_cannot_poison_subsequent_quotes(self):
        quotes = ShoonyaQuotes(now=lambda: NOW)
        self.assertIsNone(quotes.update(frame(lp="999", ft=(NOW + timedelta(days=1)).timestamp())))
        self.assertEqual(quotes.update(frame(lp="100", ft=NOW.timestamp()))[0], 100)
