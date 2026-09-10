"""Independent reviewer regressions. No network and no exchange orders.

From backend: python -B -m unittest discover -s review_tests -v
Tests assert the required behavior; failures are open findings for Claude.
This directory is separate from the implementer's tests to avoid edit conflicts.
"""
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx

from app.adapters.hyperliquid_data import HyperliquidData, MarketDataError
from app.core.config import Settings
from app.execution import order_guard as guard
from app.risk.limits import DayState, can_open_new_trade, time_stop_due
from app.risk.sizing import Sizing, VenueSpec, size_position
from app.strategies.hype.common import Config
from app.strategies.hype.engine import Signal
from app.strategies.hype import target_clearance
from app.strategies.indicators import Candle, BAR_5M_MS, BAR_1H_MS, BAR_4H_MS

DAY = 24 * BAR_1H_MS


class TestSizingContract(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(risk_mode="fixed_usd", risk_usd=1.0)
        self.venue = VenueSpec(available_collateral_usd=100, max_leverage=1)

    def test_zero_collateral_rejects(self):
        result = size_position(100, 99, "LONG", self.cfg, 100,
                               replace(self.venue, available_collateral_usd=0))
        self.assertFalse(result.ok, "Zero collateral must not disable its check")

    def test_long_stop_above_entry_rejects(self):
        result = size_position(100, 101, "LONG", self.cfg, 100, self.venue)
        self.assertFalse(result.ok, "abs(entry-stop) hides wrong-side stops")

    def test_valid_tiny_position_stays_within_planned_budget(self):
        result = size_position(100, 99, "LONG", self.cfg, 100, self.venue)
        self.assertTrue(result.ok)
        self.assertLessEqual(result.risk_usd, 1.0)


class TestDailyLimits(unittest.TestCase):
    def test_manual_kill_survives_midnight(self):
        day = DayState(0, manual_kill=True)
        day.rollover_if_needed(DAY, Config())
        self.assertTrue(day.manual_kill, "Midnight must not authorize resuming")

    def test_nan_data_age_rejects(self):
        result = can_open_new_trade(DayState(0), Config(), 100, False, float("nan"))
        self.assertFalse(result.allowed)

    def test_daily_limit_blocks_second_trade(self):
        result = can_open_new_trade(DayState(0, trades_opened=1), Config(), 100, False, 0)
        self.assertFalse(result.allowed)

    def test_time_stop_uses_elapsed_time(self):
        self.assertFalse(time_stop_due(0, DAY - 1, Config()))
        self.assertTrue(time_stop_due(0, DAY, Config()))


class TestClearanceCoverage(unittest.TestCase):
    def test_missing_obstacle_candle_cannot_turn_reject_into_accept(self):
        hourly = [Candle(i * BAR_1H_MS, 90, 91, 89, 90, 1) for i in range(48)]
        previous = [Candle(DAY + i * BAR_5M_MS, 90, 91, 89, 90, 1) for i in range(288)]
        previous[120] = Candle(previous[120].ts, 90, 100.5, 89, 90, 1)
        current = Candle(2 * DAY, 100, 100.1, 99.9, 100, 1)
        full = previous + [current]
        self.assertFalse(target_clearance.check(hourly, full, len(full)-1,
                                               "LONG", 100, 99, Config())[0])
        partial = previous[:120] + previous[121:] + [current]
        self.assertFalse(target_clearance.check(hourly, partial, len(partial)-1,
                                               "LONG", 100, 99, Config())[0],
                         "Missing one bar removes the blocking previous-day high")

    def test_five_hourly_bars_do_not_cover_declared_48h(self):
        hourly = [Candle(i * BAR_1H_MS, 90, 91, 89, 90, 1) for i in range(5)]
        current = [Candle(DAY, 100, 101, 99, 100, 1)]
        cfg = Config(clearance_use_prev_day=False, clearance_lookback_1h=48)
        self.assertFalse(target_clearance.check(hourly, current, 0, "LONG", 100, 99, cfg)[0])


class TestNativeDataBoundary(unittest.TestCase):
    def test_fetches_cannot_leak_later_htf_into_earlier_5m(self):
        boundary = BAR_4H_MS

        def reply(request):
            import json
            interval = json.loads(request.content)["req"]["interval"]
            tf = {"5m": BAR_5M_MS, "1h": BAR_1H_MS, "4h": BAR_4H_MS}[interval]
            times = [boundary - tf]
            if interval == "5m":
                times.insert(0, boundary - 2 * tf)
            rows = [{"t": ts, "o": "100", "h": "101", "l": "99", "c": "100", "v": "1"}
                    for ts in times]
            return httpx.Response(200, json=rows)

        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            adapter = HyperliquidData(client=client)
            clock = [(boundary-1)/1000, (boundary+1)/1000, (boundary+2)/1000]
            # Replace only the adapter's module binding, not httpx/cookiejar's clock.
            with patch("app.adapters.hyperliquid_data.time") as adapter_clock:
                adapter_clock.time.side_effect = clock
                c4, c1, c5 = adapter.multi_timeframe()
        cutoff = c5[-1].ts + BAR_5M_MS
        self.assertTrue(all(c.ts + BAR_1H_MS <= cutoff for c in c1),
                        "1H context must be available at the evaluated 5m close")
        self.assertTrue(all(c.ts + BAR_4H_MS <= cutoff for c in c4))

    def test_nonfinite_closed_candle_rejects(self):
        rows = [{"t": 0, "o": "100", "h": "101", "l": "99", "c": "NaN", "v": "1"}]
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=rows))) as client:
            with self.assertRaises((MarketDataError, ValueError)):
                HyperliquidData(client=client).closed_candles("5m", 1, now_ms=BAR_5M_MS)

    def test_open_candle_is_excluded(self):
        rows = [{"t": ts, "o": "100", "h": "101", "l": "99", "c": "100", "v": "1"}
                for ts in (0, BAR_5M_MS)]
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=rows))) as client:
            result = HyperliquidData(client=client).closed_candles("5m", 2, now_ms=BAR_5M_MS)
        self.assertEqual([c.ts for c in result], [0])


class TestProtectionAndEntry(unittest.TestCase):
    def orders(self):
        return [guard.OpenOrder("review-stop", "stop", 110, 1, coin="HYPE", side="LONG", reduce_only=True),
                guard.OpenOrder("review-tp", "take_profit", 84, 1, coin="HYPE", side="LONG", reduce_only=True)]

    def verify(self, orders, day):
        guard.verify_protection(orders, 110, 84, day, coin="HYPE", position_side="SHORT", qty=1)

    def test_valid_short_protection_is_accepted(self):
        day = DayState(0)
        self.verify(self.orders(), day)
        self.assertFalse(day.critical)

    def test_missing_exchange_instrument_or_side_is_not_verified(self):
        day = DayState(0)
        orders = self.orders()
        orders[0] = replace(orders[0], coin="", side="")
        with self.assertRaises(guard.CriticalExecutionError):
            self.verify(orders, day)
        self.assertTrue(day.critical)

    def test_nan_stop_trigger_is_not_verified(self):
        day = DayState(0)
        orders = self.orders()
        orders[0] = replace(orders[0], trigger_price=float("nan"))
        with self.assertRaises(guard.CriticalExecutionError):
            self.verify(orders, day)
        self.assertTrue(day.critical)

    def test_favorable_drift_past_structural_stop_rejects_entry(self):
        signal = Signal(side="LONG", entry_ref=100, stop=99, tp=101.6, rr=1.6,
                        risk_per_unit=1, cost_r=0.1, cost_frac=0.001, clearance_r=2, ts=0)
        sizing = Sizing(True, 0.5, 50, 0.55, 1)
        result = guard.revalidate(signal, sizing, DayState(0), Config(), Settings(),
                                  now_ms=BAR_5M_MS+1000, current_price=98, equity=100,
                                  has_open_position=False, data_age_seconds=1)
        self.assertFalse(result.allowed, "A favorable quote past the stop invalidates the trade")

    def test_live_false_blocks_order_permission(self):
        for mode in ("RESEARCH", "PAPER", "SHADOW", "TINY", "LIVE"):
            with self.subTest(mode=mode), self.assertRaises(guard.LiveExecutionDisabled):
                guard.assert_can_send_orders(Settings(mode=mode, live_execution=False))


if __name__ == "__main__":
    unittest.main()
