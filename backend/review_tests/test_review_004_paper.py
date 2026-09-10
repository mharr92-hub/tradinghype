"""Independent regressions for Claude's in-progress PAPER/fill/funding modules.

No network, orders, or production edits. Failures are unresolved review findings.
"""
import unittest

from app.adapters.hyperliquid_data import FundingPoint
from app.execution.paper import PaperBroker, PaperPosition
from app.research.fills import FillModel, resolve_exit, simulate_fill
from app.research.funding import FundingCurve, HOUR_MS
from app.risk.limits import DayState, TRADE_ACTIVE
from app.risk.sizing import VenueSpec, size_position
from app.strategies.hype.common import Config
from app.strategies.hype.engine import Signal
from app.strategies.indicators import Candle, BAR_5M_MS


def bar(i, low=99.5, high=100.5, opening=100):
    return Candle(i * BAR_5M_MS, opening, high, low, 100, 1)


class TestFillTiming(unittest.TestCase):
    def test_delay_beyond_ttl_cannot_fill(self):
        result = simulate_fill(bar(0), bar(1), "LONG", 100, 1,
                               FillModel(delay_seconds=91, ttl_seconds=90))
        self.assertFalse(result.filled)

    def test_missing_next_bar_cannot_backdate_later_observation(self):
        result = simulate_fill(bar(0), bar(3), "LONG", 100, 1)
        self.assertFalse(result.filled, "Next observation arrives after signal expiry")

    def test_drift_limit_includes_execution_degradation(self):
        result = simulate_fill(bar(0), bar(1, opening=100.09), "LONG", 100, 1)
        self.assertTrue(not result.filled or result.price <= 100.10,
                        "An accepted fill must respect the 0.10R limit")

    def test_entry_bar_stop_cannot_be_ignored(self):
        # Zero delay makes the entire entry bar eligible, avoiding intrabar ambiguity.
        result = resolve_exit([bar(0), bar(1, low=98), bar(2)], 1,
                              100, 99, 101.6, "LONG", Config(),
                              FillModel(delay_seconds=0))
        self.assertEqual(result.kind, "stop")
        self.assertEqual(result.bar_index, 1)

    def test_time_stop_does_not_hold_24h_plus_one_bar(self):
        result = resolve_exit([bar(i) for i in range(291)], 1,
                              100, 99, 101.6, "LONG", Config(),
                              FillModel(delay_seconds=0))
        self.assertEqual(result.kind, "time_stop")
        self.assertLessEqual(result.hours_held, 24)

    def test_simultaneous_stop_tp_is_conservative(self):
        result = resolve_exit([bar(0), bar(1), bar(2, low=98, high=102)], 1,
                              100, 99, 101.6, "LONG", Config())
        self.assertEqual(result.kind, "stop")


class TestPaperAccounting(unittest.TestCase):
    def position(self):
        return PaperPosition("LONG", 1, 100, 100, 99, 101.6,
                             BAR_5M_MS, 1, 0.0013)

    def test_end_of_available_data_is_not_realized_exit(self):
        pos = self.position()
        day = DayState(0, state=TRADE_ACTIVE, trades_opened=1)
        PaperBroker(Config()).resolve(pos, [bar(0), bar(1), bar(2)], day)
        self.assertFalse(pos.closed)
        self.assertEqual(day.state, TRADE_ACTIVE)
        self.assertEqual(day.realized_pnl_usd, 0)

    def test_resolving_closed_position_cannot_book_pnl_twice(self):
        pos, day = self.position(), DayState(0, trades_opened=1)
        broker = PaperBroker(Config())
        candles = [bar(0), bar(1), bar(2, low=98)]
        broker.resolve(pos, candles, day)
        first = day.realized_pnl_usd
        broker.resolve(pos, candles, day)
        self.assertEqual(day.realized_pnl_usd, first)

    def test_exit_fee_uses_exit_notional(self):
        pos = self.position()
        cfg = Config()
        PaperBroker(cfg).resolve(pos, [bar(0), bar(1), bar(2, low=98)], DayState(0))
        self.assertAlmostEqual(pos.fees_usd,
                               cfg.fee_taker * pos.qty * (pos.entry_price + pos.exit.price))

    def entry_inputs(self):
        cfg = Config(risk_mode="fixed_usd", risk_usd=1)
        signal = Signal(side="LONG", entry_ref=100, stop=99, tp=101.6,
                        rr=1.6, risk_per_unit=1, cost_r=0.13,
                        cost_frac=0.0013, clearance_r=2, ts=0)
        sizing = size_position(100, 99, "LONG", cfg, 1000,
                               VenueSpec(available_collateral_usd=1000))
        self.assertTrue(sizing.ok)
        return cfg, signal, sizing

    def test_enter_rechecks_daily_limit(self):
        cfg, signal, sizing = self.entry_inputs()
        day = DayState(0, trades_opened=1)
        result = PaperBroker(cfg).enter(signal, sizing, day, [bar(0), bar(1)], 0)
        self.assertFalse(result.entered)
        self.assertIn("daily", result.reason, "An unrelated protection error is not a daily gate")
        self.assertEqual(day.trades_opened, 1)

    def test_valid_entry_is_simulated(self):
        cfg, signal, sizing = self.entry_inputs()
        day = DayState(0)
        result = PaperBroker(cfg).enter(signal, sizing, day, [bar(0), bar(1)], 0)
        self.assertTrue(result.entered, result.reason)
        self.assertFalse(result.position.closed)
        self.assertEqual(day.trades_opened, 1)

    def test_post_fill_risk_stays_inside_budget(self):
        cfg, signal, sizing = self.entry_inputs()
        result = PaperBroker(cfg).enter(signal, sizing, DayState(0),
                                        [bar(0), bar(1, opening=100.07)], 0)
        self.assertFalse(result.reason.startswith("protection_failed"),
                         "A protection exception cannot stand in for risk validation")
        # This accepted fill is within 0.10R; budget still needs recalculation.
        if result.entered:
            pos = result.position
            loss_to_stop_with_fees = pos.qty * (
                pos.entry_price - pos.stop + cfg.fee_taker * (pos.entry_price + pos.stop))
            self.assertLessEqual(loss_to_stop_with_fees, sizing.budget_usd)


class TestFundingCoverage(unittest.TestCase):
    def test_missing_settlement_does_not_silently_reuse_old_rate(self):
        curve = FundingCurve([FundingPoint(ts=HOUR_MS, rate_hourly=0.0001)])
        with self.assertRaises(ValueError):
            curve.accrue(HOUR_MS + 1, 2 * HOUR_MS + 1, "LONG", 100)

    def test_positive_funding_debits_long_and_credits_short(self):
        curve = FundingCurve([FundingPoint(ts=HOUR_MS, rate_hourly=0.0001)])
        self.assertAlmostEqual(curve.accrue(1, HOUR_MS + 1, "LONG", 100).usd, 0.01)
        self.assertAlmostEqual(curve.accrue(1, HOUR_MS + 1, "SHORT", 100).usd, -0.01)


if __name__ == "__main__":
    unittest.main()
