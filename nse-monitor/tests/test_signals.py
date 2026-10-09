"""Closed-bar ORB behavior, including replay and trading-day boundaries."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from signals import Candle, IST, OpeningRangeBreakoutStrategy, SignalDirection, Tick


def candle(hour, minute, *, high=110, low=90, close=100, day=5, closed=True, minutes=15):
    start = datetime(2026, 10, day, hour, minute, tzinfo=IST)
    return Candle("^NSEI", 100, high, low, close, 1, start,
                  start + timedelta(minutes=minutes), closed)


def opening_bars(day=5):
    return [candle(9, 15, high=105, low=95, day=day),
            candle(9, 30, high=110, low=92, day=day),
            candle(9, 45, high=108, low=90, day=day)]


def lock_range(strategy, day=5):
    for bar in opening_bars(day):
        strategy.on_candle_close(bar)


class OpeningRangeTests(unittest.TestCase):
    def test_final_opening_close_locks_range(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        for bar in opening_bars()[:2]:
            self.assertIsNone(strategy.on_candle_close(bar))
            self.assertFalse(strategy.range_locked)
        self.assertIsNone(strategy.on_candle_close(opening_bars()[2]))
        self.assertTrue(strategy.range_locked)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (110, 90))

    def test_tick_or_wick_outside_range_does_not_signal(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy)
        bar = candle(10, 0, high=120, low=80, close=100)
        self.assertIsNone(strategy.on_tick(Tick("^NSEI", 120, bar.start_time)))
        self.assertIsNone(strategy.on_candle_close(bar))
        self.assertIsNone(strategy.last_signal)

    def test_breakout_requires_strict_close_and_keeps_one_per_direction(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy)
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, close=110)))
        bull = strategy.on_candle_close(candle(10, 15, high=115, close=115))
        self.assertEqual(bull.direction, SignalDirection.BULLISH)
        self.assertEqual(bull.timestamp, datetime(2026, 10, 5, 10, 30, tzinfo=IST))
        self.assertEqual((bull.level, bull.stop_loss, bull.target_1r, bull.target_2r),
                         (110, 90, 130, 150))
        self.assertEqual(bull.symbol, "^NSEI")
        self.assertIsNone(strategy.on_candle_close(candle(10, 30, high=120, close=120)))
        self.assertIsNone(strategy.on_candle_close(candle(10, 45, close=90)))
        bear = strategy.on_candle_close(candle(11, 0, low=85, close=85))
        self.assertEqual(bear.direction, SignalDirection.BEARISH)
        self.assertEqual((bear.level, bear.stop_loss, bear.target_1r, bear.target_2r),
                         (90, 110, 70, 50))

    def test_buffer_and_optional_repeated_signals(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45, buffer_pts=2, one_signal_per_direction=False)
        lock_range(strategy)
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, high=112, close=112)))
        for minute in (15, 30):
            self.assertIsNotNone(strategy.on_candle_close(candle(10, minute, high=113, close=113)))
        self.assertIsNone(strategy.on_candle_close(candle(10, 45, low=88, close=88)))
        self.assertIsNotNone(strategy.on_candle_close(candle(11, 0, low=87, close=87)))

    def test_missing_or_duplicate_opening_candles_cannot_lock(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        for bar in (opening_bars()[0], opening_bars()[0], opening_bars()[2]):
            strategy.feed_historical_candle(bar)
        self.assertFalse(strategy.range_locked)
        self.assertEqual(len(strategy.range_candles), 2)
        self.assertIsNone(strategy.on_tick(Tick("^NSEI", 200, candle(10, 0).start_time)))
        self.assertFalse(strategy.range_locked)
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, high=200, close=200)))

    def test_partial_history_cannot_lock_or_signal(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        for bar in opening_bars()[:2]:
            strategy.feed_historical_candle(bar)
        strategy.feed_historical_candle(candle(9, 45, high=200, low=1, closed=False))
        self.assertFalse(strategy.range_locked)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (110, 92))
        strategy.feed_historical_candle(opening_bars()[2])
        self.assertIsNone(strategy.feed_historical_candle(candle(10, 0, high=200, close=200, closed=False)))
        self.assertIsNone(strategy.last_signal)

    def test_opening_candle_missing_early_ticks_cannot_lock_range(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        bars = opening_bars()
        bars[0].is_complete = False
        for bar in bars:
            strategy.on_candle_close(bar)
        self.assertFalse(strategy.range_locked)
        self.assertEqual(len(strategy.range_candles), 2)
        self.assertFalse(bars[0].to_dict()["is_complete"])
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, high=200, close=200)))
        # A later complete history bar can fill the missing opening coverage.
        strategy.on_candle_close(opening_bars()[0])
        self.assertTrue(strategy.range_locked)

    def test_postrange_candle_can_confirm_close_despite_missing_early_ticks(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy)
        bar = candle(10, 0, high=120, close=115)
        bar.is_complete = False
        self.assertIsNotNone(strategy.on_candle_close(bar))

    def test_active_history_provisionally_updates_high_and_low(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        active = candle(9, 15, high=108, low=91, close=102, closed=False)
        strategy.on_tick(Tick("^NSEI", 102, active.start_time), active)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (108, 91))
        strategy.on_tick(Tick("^NSEI", 200, candle(10, 0).start_time))
        self.assertFalse(strategy.range_locked)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (108, 91))

    def test_premarket_and_misaligned_candles_cannot_contaminate_range(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        strategy.on_tick(Tick("^NSEI", 1000, candle(9, 0).start_time))
        for bar in (candle(9, 0, high=1000), candle(9, 16, high=1000)):
            strategy.on_candle_close(bar)
        incomplete = candle(9, 15, high=1000)
        incomplete.end_time -= timedelta(minutes=1)
        strategy.on_candle_close(incomplete)
        lock_range(strategy)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (110, 90))
        self.assertIsNone(strategy.on_candle_close(candle(15, 30, high=200, close=200)))

    def test_day_rollover_resets_levels_and_direction_latches(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy, day=2)  # Friday to Monday
        strategy.on_candle_close(candle(10, 0, high=120, close=120, day=2))
        strategy.on_tick(Tick("^NSEI", 100, candle(9, 15).start_time))
        self.assertFalse(strategy.range_locked)
        self.assertFalse(strategy.bullish_triggered)
        self.assertIsNone(strategy.last_signal)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (100, 100))
        lock_range(strategy)
        self.assertIsNotNone(strategy.on_candle_close(candle(10, 0, high=120, close=120)))

    def test_historical_day_rollover_resets_without_ticks(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy, day=2)
        strategy.feed_historical_candle(opening_bars()[0])
        self.assertFalse(strategy.range_locked)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (105, 95))

    def test_delayed_previous_day_data_does_not_reset_current_day(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy)
        before = strategy.get_state()
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, high=200, close=200, day=2)))
        strategy.on_tick(Tick("^NSEI", 200, candle(9, 15, day=2).start_time))
        self.assertEqual(strategy.get_state(), before)

    def test_invalid_future_data_does_not_erase_valid_state(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy)
        before = strategy.get_state()
        for bar in (candle(9, 0, day=6), candle(9, 16, day=6),
                    candle(9, 15, day=6, closed=False)):
            strategy.on_candle_close(bar)
            self.assertEqual(strategy.get_state(), before)
        strategy.on_tick(Tick("^NSEI", 200, candle(9, 0, day=6).start_time))
        self.assertEqual(strategy.get_state(), before)

    def test_locked_range_cannot_change_when_opening_bar_is_replaced(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy)
        strategy.on_candle_close(candle(10, 0, high=120, close=120))
        before = strategy.get_state()
        strategy.on_candle_close(candle(9, 15, high=200, low=50))
        self.assertEqual(strategy.get_state(), before)

    def test_history_and_live_generate_identical_signals(self):
        bars = opening_bars() + [candle(10, 0, high=120, close=115),
                                 candle(10, 15, high=125, close=120),
                                 candle(10, 30, low=80, close=85)]
        live, history = OpeningRangeBreakoutStrategy(range_minutes=45), OpeningRangeBreakoutStrategy(range_minutes=45)
        live_signals, history_signals = [], []
        for bar in bars:
            live.on_tick(Tick(bar.symbol, bar.high, bar.start_time), bar)
            result = live.on_candle_close(bar)
            if result:
                live_signals.append(result.to_dict())
            result = history.feed_historical_candle(bar)
            if result:
                history_signals.append(result.to_dict())
        self.assertEqual(live_signals, history_signals)
        self.assertEqual(len(live_signals), 2)
        self.assertEqual(live.get_state(), history.get_state())

    def test_utc_and_naive_timestamps_use_indian_market_session(self):
        for zone in (timezone.utc, None):
            strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
            for bar in opening_bars():
                bar.start_time = bar.start_time.astimezone(zone) if zone else bar.start_time.replace(tzinfo=None)
                bar.end_time = bar.end_time.astimezone(zone) if zone else bar.end_time.replace(tzinfo=None)
                strategy.on_candle_close(bar)
            self.assertTrue(strategy.range_locked)
            self.assertEqual((strategy.orb_high, strategy.orb_low), (110, 90))

    def test_explicit_reset_clears_all_session_state(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45)
        lock_range(strategy)
        strategy.on_candle_close(candle(10, 0, high=120, close=120))
        strategy.reset_day()
        self.assertEqual(strategy.get_state(), OpeningRangeBreakoutStrategy(range_minutes=45).get_state())
        self.assertEqual(strategy.range_candles, [])
        self.assertIsNone(strategy.session_date)


def opening_bars_5m(day=5):
    bars = []
    for slot in range(9):
        start = datetime(2026, 10, day, 9, 15, tzinfo=IST) + timedelta(minutes=slot * 5)
        bars.append(candle(start.hour, start.minute, day=day, minutes=5))
    bars[-1].high, bars[-1].low = 112, 88
    return bars


class TimeframePairTests(unittest.TestCase):
    def test_default_30m_range_locks_at_0945_and_confirms_at_1000(self):
        strategy = OpeningRangeBreakoutStrategy()
        bars = opening_bars()
        strategy.on_candle_close(bars[0])
        self.assertFalse(strategy.range_locked)
        self.assertIsNone(strategy.on_candle_close(bars[1]))
        self.assertTrue(strategy.range_locked)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (110, 92))
        self.assertIsNone(strategy.get_state()["confirmation_direction"])
        signal = strategy.on_candle_close(candle(9, 45, high=120, low=80, close=115))
        self.assertEqual(signal.timestamp, datetime(2026, 10, 5, 10, 0, tzinfo=IST))
        self.assertEqual((strategy.orb_high, strategy.orb_low), (110, 92))
        self.assertEqual(signal.direction, SignalDirection.BULLISH)

    def test_45m_range_needs_nine_5m_bars_then_confirms_at_1005(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45, confirmation_minutes=5)
        bars = opening_bars_5m()
        for bar in bars[:-1]:
            self.assertIsNone(strategy.on_candle_close(bar))
            self.assertFalse(strategy.range_locked)
        self.assertIsNone(strategy.on_candle_close(bars[-1]))
        self.assertTrue(strategy.range_locked)
        self.assertEqual((strategy.orb_high, strategy.orb_low), (112, 88))
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, minutes=5, close=112)))
        signal = strategy.on_candle_close(candle(10, 5, minutes=5, close=113, high=115))
        self.assertEqual(signal.timestamp, datetime(2026, 10, 5, 10, 10, tzinfo=IST))
        first = OpeningRangeBreakoutStrategy(range_minutes=45, confirmation_minutes=5)
        for bar in bars:
            first.feed_historical_candle(bar)
        self.assertEqual(first.on_candle_close(candle(10, 0, minutes=5, high=115, close=113)).timestamp,
                         datetime(2026, 10, 5, 10, 5, tzinfo=IST))

    def test_5m_setup_cannot_use_15m_history_or_closes(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45, confirmation_minutes=5)
        for bar in opening_bars():
            strategy.feed_historical_candle(bar)
        self.assertFalse(strategy.range_locked)
        self.assertEqual(strategy.range_candles, [])
        for bar in opening_bars_5m():
            strategy.on_candle_close(bar)
        self.assertTrue(strategy.range_locked)
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, high=200, close=200)))
        self.assertIsNone(strategy.get_state()["confirmation_direction"])

    def test_15m_setup_ignores_5m_breakout_before_15m_close(self):
        strategy = OpeningRangeBreakoutStrategy()
        for bar in opening_bars()[:2]:
            strategy.on_candle_close(bar)
        self.assertIsNone(strategy.on_candle_close(candle(9, 45, minutes=5, high=200, close=200)))
        self.assertIsNone(strategy.last_signal)
        self.assertIsNone(strategy.get_state()["confirmation_direction"])
        self.assertIsNotNone(strategy.on_candle_close(candle(9, 45, high=120, close=115)))

    def test_missing_incomplete_or_duplicate_5m_opening_coverage_cannot_lock(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45, confirmation_minutes=5)
        bars = opening_bars_5m()
        bars[4].is_complete = False
        for bar in bars + [bars[0]]:
            strategy.feed_historical_candle(bar)
        self.assertFalse(strategy.range_locked)
        self.assertEqual(len(strategy.range_candles), 8)
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, minutes=5, high=200, close=200)))
        replacement = opening_bars_5m()[4]
        strategy.feed_historical_candle(replacement)
        self.assertTrue(strategy.range_locked)
        self.assertEqual(len(strategy.range_candles), 9)

    def test_each_provisional_range_stops_at_its_own_boundary(self):
        for range_minutes, confirmation_minutes, end_hour, end_minute in ((30, 15, 9, 45), (45, 5, 10, 0)):
            strategy = OpeningRangeBreakoutStrategy(range_minutes=range_minutes,
                                                   confirmation_minutes=confirmation_minutes)
            start = datetime(2026, 10, 5, 9, 15, tzinfo=IST)
            strategy.on_tick(Tick("^NSEI", 100, start))
            end = datetime(2026, 10, 5, end_hour, end_minute, tzinfo=IST)
            strategy.on_tick(Tick("^NSEI", 200, end - timedelta(seconds=1)))
            strategy.on_tick(Tick("^NSEI", 300, end))
            self.assertEqual((strategy.orb_high, strategy.orb_low), (200, 100))
            self.assertFalse(strategy.range_locked)

    def test_signal_and_range_state_identify_their_timeframe_pair(self):
        for range_minutes, confirmation_minutes in ((30, 15), (45, 5)):
            strategy = OpeningRangeBreakoutStrategy(range_minutes=range_minutes,
                                                   confirmation_minutes=confirmation_minutes)
            bars = opening_bars()[:2] if confirmation_minutes == 15 else opening_bars_5m()
            for bar in bars:
                strategy.on_candle_close(bar)
            start = bars[-1].end_time
            result = strategy.on_candle_close(candle(start.hour, start.minute,
                                                     minutes=confirmation_minutes, high=200, close=200))
            for data in (result.to_dict(), strategy.get_state()):
                self.assertEqual(data["range_minutes"], range_minutes)
                self.assertEqual(data["confirmation_minutes"], confirmation_minutes)
                self.assertEqual(data["setup_id"], f"orb_{range_minutes}m_{confirmation_minutes}m")
            self.assertIn(f"{confirmation_minutes}-min close", result.message)

    def test_current_direction_updates_after_arrow_latch_and_clears_inside_range(self):
        strategy = OpeningRangeBreakoutStrategy()
        for bar in opening_bars()[:2]:
            strategy.on_candle_close(bar)
        arrow = strategy.on_candle_close(candle(9, 45, high=115, close=115))
        self.assertEqual(strategy.get_state()["confirmation_direction"], "BULLISH")
        self.assertIsNone(strategy.on_candle_close(candle(10, 0, close=100)))
        self.assertEqual(strategy.get_state()["confirmation_direction"], "NEUTRAL")
        self.assertEqual(strategy.get_state()["confirmation_price"], 100)
        self.assertIsNone(strategy.on_candle_close(candle(10, 15, high=116, close=116)))
        state = strategy.get_state()
        self.assertEqual(state["confirmation_direction"], "BULLISH")
        self.assertEqual(state["confirmed_at"], datetime(2026, 10, 5, 10, 30, tzinfo=IST).isoformat())
        self.assertEqual(state["confirmation_price"], 116)
        self.assertEqual(strategy.last_signal, arrow)

    def test_invalid_close_clears_current_confirmation_without_erasing_arrow(self):
        strategy = OpeningRangeBreakoutStrategy(range_minutes=45, confirmation_minutes=5)
        for bar in opening_bars_5m():
            strategy.on_candle_close(bar)
        arrow = strategy.on_candle_close(candle(10, 0, minutes=5, high=115, close=115))
        invalid = candle(10, 5, minutes=5, high=120, close=120)
        invalid.close_is_valid = False
        self.assertIsNone(strategy.on_candle_close(invalid))
        state = strategy.get_state()
        for field in ("confirmation_direction", "confirmed_at", "confirmation_price"):
            self.assertIsNone(state[field])
        self.assertEqual(strategy.last_signal, arrow)
        self.assertIsNone(strategy.on_candle_close(candle(10, 10, minutes=5, high=125, close=125)))
        self.assertEqual(strategy.get_state()["confirmation_direction"], "BULLISH")
        before = strategy.get_state()
        strategy.on_candle_close(invalid)
        strategy.on_candle_close(candle(10, 0, minutes=5, low=80, close=80))
        self.assertEqual(strategy.get_state(), before)

    def test_buffer_applies_to_current_direction_as_well_as_arrow(self):
        strategy = OpeningRangeBreakoutStrategy(buffer_pts=2)
        for bar in opening_bars()[:2]:
            strategy.on_candle_close(bar)
        self.assertIsNone(strategy.on_candle_close(candle(9, 45, high=112, close=112)))
        self.assertEqual(strategy.get_state()["confirmation_direction"], "NEUTRAL")
        self.assertIsNotNone(strategy.on_candle_close(candle(10, 0, low=89, close=89)))
        self.assertEqual(strategy.get_state()["confirmation_direction"], "BEARISH")

    def test_5m_session_alignment_and_timezone_handling(self):
        for zone in (timezone.utc, None):
            strategy = OpeningRangeBreakoutStrategy(range_minutes=45, confirmation_minutes=5)
            bad = candle(9, 16, minutes=5, high=1000)
            strategy.on_candle_close(bad)
            for bar in opening_bars_5m():
                bar.start_time = bar.start_time.astimezone(zone) if zone else bar.start_time.replace(tzinfo=None)
                bar.end_time = bar.end_time.astimezone(zone) if zone else bar.end_time.replace(tzinfo=None)
                strategy.on_candle_close(bar)
            self.assertTrue(strategy.range_locked)
            self.assertEqual((strategy.orb_high, strategy.orb_low), (112, 88))

    def test_history_and_live_agree_for_both_new_pairs(self):
        for range_minutes, confirmation_minutes in ((30, 15), (45, 5)):
            live = OpeningRangeBreakoutStrategy(range_minutes=range_minutes,
                                               confirmation_minutes=confirmation_minutes)
            history = OpeningRangeBreakoutStrategy(range_minutes=range_minutes,
                                                  confirmation_minutes=confirmation_minutes)
            bars = opening_bars()[:2] if confirmation_minutes == 15 else opening_bars_5m()
            start = bars[-1].end_time
            for close in (120, 100, 80):
                bars.append(candle(start.hour, start.minute, minutes=confirmation_minutes,
                                   high=125, low=75, close=close))
                start += timedelta(minutes=confirmation_minutes)
            live_signals, history_signals = [], []
            for bar in bars:
                live.on_tick(Tick(bar.symbol, bar.high, bar.start_time), bar)
                actual = live.on_candle_close(bar)
                expected = history.feed_historical_candle(bar)
                if actual:
                    live_signals.append(actual.to_dict())
                if expected:
                    history_signals.append(expected.to_dict())
            self.assertEqual(live_signals, history_signals)
            self.assertEqual(len(live_signals), 2)
            self.assertEqual(live.get_state(), history.get_state())

    def test_unsupported_timeframe_pairs_are_rejected(self):
        for range_minutes, confirmation_minutes in ((30, 5), (45, 10), (33, 15)):
            with self.assertRaises(ValueError):
                OpeningRangeBreakoutStrategy(range_minutes=range_minutes,
                                             confirmation_minutes=confirmation_minutes)


if __name__ == "__main__":
    unittest.main()
