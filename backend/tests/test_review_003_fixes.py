"""
Regresiones de los 9 hallazgos de REVIEW_003 (reviewer independiente).

Cada test reproduce el fallo REPORTADO, no una version comoda de el. Si un test
de aqui se pone en verde por un cambio no relacionado, es que el arreglo se
deshizo: son la unica garantia de que estos bugs no vuelven.

Los dos mas graves, por si alguien lee solo el principio:
  R03-01  el motor podia mezclar un cierre 5m de las 03:55 con uno 1H de las
          04:00 — lookahead real, visible solo en el cambio de hora.
  R03-06  se aprobaba una entrada cuyo precio ya habia atravesado el stop.
"""

import math
import unittest

from app.adapters.hyperliquid_data import HyperliquidData, MarketDataError
from app.core.config import Settings
from app.execution.order_guard import OpenOrder, revalidate, verify_protection
from app.risk.limits import DayState, can_open_new_trade
from app.risk.sizing import Sizing, VenueSpec, size_position
from app.strategies.hype import target_clearance
from app.strategies.hype.common import SIDE_LONG, SIDE_SHORT, Config
from app.strategies.hype.engine import Signal
from app.strategies.indicators import BAR_1H_MS, BAR_5M_MS, Candle

BASE = 1789000000000


def c(ts, o=100.0, h=101.0, l=99.0, cl=100.0, v=100.0):
    return Candle(ts=ts, o=o, h=h, l=l, c=cl, v=v)


def sig(side=SIDE_LONG, entry=100.0, stop=99.0, tp=101.6, ts=BASE):
    return Signal(side=side, entry_ref=entry, stop=stop, tp=tp, rr=1.6,
                  risk_per_unit=abs(entry - stop), cost_r=0.10,
                  cost_frac=0.0013, clearance_r=2.0, ts=ts)


def settings():
    return Settings(mode="PAPER", live_execution=False, max_data_age_seconds=420.0)


class FakeTransport(HyperliquidData):
    """Registra con que `now` se pidio cada intervalo. Sin red."""

    def __init__(self):
        self.calls = []

    def closed_candles(self, interval, lookback_bars, now_ms=None):
        self.calls.append((interval, now_ms))
        bar = {"5m": BAR_5M_MS, "1h": BAR_1H_MS, "4h": 4 * BAR_1H_MS}[interval]
        return [c(now_ms - (n + 1) * bar) for n in reversed(range(3))]


class TestR0301SingleClock(unittest.TestCase):
    """Los tres marcos deben pedirse con el MISMO instante."""

    def test_all_timeframes_share_one_clock(self):
        t = FakeTransport()
        t.multi_timeframe(now_ms=None)
        stamps = {now for _, now in t.calls}
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(len(stamps), 1,
                         f"cada marco uso su propio reloj: {t.calls}")

    def test_explicit_clock_is_propagated(self):
        t = FakeTransport()
        t.multi_timeframe(now_ms=BASE)
        self.assertTrue(all(now == BASE for _, now in t.calls))


class TestR0302ClearanceCoverage(unittest.TestCase):
    """Sin historial no se aprueba: "no he mirado" != "no hay obstaculo"."""

    def test_empty_history_is_rejected(self):
        ok, cr, lvl = target_clearance.check([], [c(BASE)], 0, SIDE_LONG,
                                             100.0, 99.0, Config())
        self.assertFalse(ok)
        self.assertEqual(cr, 0.0)

    def test_partial_1h_history_below_declared_lookback_is_rejected(self):
        cfg = Config(clearance_lookback_1h=48)
        c1h = [c(BASE + i * BAR_1H_MS) for i in range(10)]   # 10 < 48
        ok, reason = target_clearance.coverage_ok(c1h, [c(BASE)], 0, cfg)
        self.assertFalse(ok)
        self.assertIn("insufficient_1h_history", reason)


class TestR0303Sizing(unittest.TestCase):

    def test_long_with_stop_above_entry_is_rejected(self):
        z = size_position(100.0, 101.0, SIDE_LONG, Config(risk_mode="fixed_usd"),
                          1000.0, VenueSpec())
        self.assertFalse(z.ok)
        self.assertEqual(z.reason, "stop_above_entry_on_long")

    def test_short_with_stop_below_entry_is_rejected(self):
        z = size_position(100.0, 99.0, SIDE_SHORT, Config(risk_mode="fixed_usd"),
                          1000.0, VenueSpec())
        self.assertFalse(z.ok)
        self.assertEqual(z.reason, "stop_below_entry_on_short")

    def test_zero_collateral_blocks_when_check_required(self):
        v = VenueSpec(requires_collateral_check=True, available_collateral_usd=0.0,
                      tick_size=0.0)
        z = size_position(100.0, 99.0, SIDE_LONG,
                          Config(risk_mode="fixed_usd", risk_usd=1.0), 1000.0, v)
        self.assertFalse(z.ok)
        self.assertEqual(z.reason, "collateral_unknown_or_zero")

    def test_non_finite_input_is_rejected(self):
        z = size_position(float("nan"), 99.0, SIDE_LONG, Config(), 1000.0,
                          VenueSpec())
        self.assertFalse(z.ok)
        self.assertEqual(z.reason, "non_finite_input")


class TestR0304ManualKillSurvivesMidnight(unittest.TestCase):

    def test_manual_kill_is_not_cleared_by_rollover(self):
        cfg = Config()
        day = DayState(session_start_ms=BASE)
        day.manual_kill = True
        day.trades_opened = 1
        day.rollover_if_needed(BASE + 30 * 60 * 60 * 1000, cfg)   # +30h
        self.assertTrue(day.manual_kill,
                        "el kill switch manual se apago solo a medianoche")
        self.assertEqual(day.trades_opened, 0, "el cupo diario si debe reiniciarse")

    def test_critical_also_survives(self):
        day = DayState(session_start_ms=BASE)
        day.critical = True
        day.critical_reason = "protective_sl_missing"
        day.rollover_if_needed(BASE + 30 * 60 * 60 * 1000, Config())
        self.assertTrue(day.critical)
        self.assertEqual(day.critical_reason, "protective_sl_missing")


class TestR0305UnknownProtectionIsRejected(unittest.TestCase):

    def _day(self):
        return DayState(session_start_ms=BASE)

    def test_order_without_coin_is_not_accepted(self):
        d = self._day()
        orders = [OpenOrder("1", "stop", 99.0, 1.0, "", "SHORT", True),
                  OpenOrder("2", "take_profit", 101.6, 1.0, "HYPE", "SHORT", True)]
        with self.assertRaises(Exception):
            verify_protection(orders, 99.0, 101.6, d, coin="HYPE",
                              position_side=SIDE_LONG, qty=1.0)
        self.assertTrue(d.critical)

    def test_nan_trigger_is_not_accepted(self):
        d = self._day()
        orders = [OpenOrder("1", "stop", float("nan"), 1.0, "HYPE", "SHORT", True),
                  OpenOrder("2", "take_profit", 101.6, 1.0, "HYPE", "SHORT", True)]
        with self.assertRaises(Exception):
            verify_protection(orders, 99.0, 101.6, d, coin="HYPE",
                              position_side=SIDE_LONG, qty=1.0)
        self.assertTrue(d.critical)


class TestR0306PriceThroughStop(unittest.TestCase):
    """El caso exacto del reporte: LONG 100/99, cotizacion 98."""

    def _gate(self, price, side=SIDE_LONG, entry=100.0, stop=99.0):
        s = sig(side=side, entry=entry, stop=stop)
        return revalidate(
            s, Sizing(True, 1.0, 100.0, 1.0, 1.0, "ok"),
            DayState(session_start_ms=BASE), Config(), settings(),
            now_ms=BASE + BAR_5M_MS + 10_000, current_price=price,
            equity=1000.0, has_open_position=False, data_age_seconds=10.0)

    def test_long_price_below_stop_is_rejected(self):
        g = self._gate(98.0)
        self.assertFalse(g.allowed)
        self.assertTrue(g.reason.startswith("price_through_stop"), g.reason)

    def test_short_price_above_stop_is_rejected(self):
        g = self._gate(102.0, side=SIDE_SHORT, entry=100.0, stop=101.0)
        self.assertFalse(g.allowed)
        self.assertTrue(g.reason.startswith("price_through_stop"), g.reason)

    def test_valid_price_still_passes(self):
        self.assertTrue(self._gate(100.02).allowed)


class TestR0307NonFinite(unittest.TestCase):

    def test_nan_data_age_does_not_pass_as_fresh(self):
        g = can_open_new_trade(DayState(session_start_ms=BASE), Config(), 1000.0,
                               has_open_position=False,
                               data_age_seconds=float("nan"))
        self.assertFalse(g.allowed, "NaN se colo como dato fresco")

    def test_adapter_rejects_nan_candle(self):
        with self.assertRaises(MarketDataError):
            HyperliquidData._to_candle(
                {"t": BASE, "o": "NaN", "h": "1", "l": "1", "c": "1", "v": "1"})

    def test_nan_current_price_is_rejected(self):
        g = revalidate(sig(), Sizing(True, 1.0, 100.0, 1.0, 1.0, "ok"),
                       DayState(session_start_ms=BASE), Config(), settings(),
                       now_ms=BASE + BAR_5M_MS + 10_000,
                       current_price=float("nan"), equity=1000.0,
                       has_open_position=False, data_age_seconds=10.0)
        self.assertFalse(g.allowed)
        self.assertEqual(g.reason, "current_price_invalid")


class TestR0308SignalTTLvsDataFreshness(unittest.TestCase):
    """Feed fresco no significa señal viva."""

    def test_expired_signal_rejected_even_with_fresh_feed(self):
        s = sig(ts=BASE)
        now = BASE + BAR_5M_MS + 91_000       # 91 s tras el cierre de la vela
        g = revalidate(s, Sizing(True, 1.0, 100.0, 1.0, 1.0, "ok"),
                       DayState(session_start_ms=BASE), Config(), settings(),
                       now_ms=now, current_price=100.0, equity=1000.0,
                       has_open_position=False, data_age_seconds=5.0)
        self.assertFalse(g.allowed)
        self.assertIn("signal_expired", g.reason)


class TestR0309FailuresReachTheJournal(unittest.TestCase):

    def test_data_failure_is_written(self):
        import tempfile
        from app.services.scanner import ForwardLogger, Scanner

        with tempfile.TemporaryDirectory() as tmp:
            sc = Scanner(Config(), mode="PAPER", data=object(),
                         logger=ForwardLogger(tmp), journal_dir=tmp)
            sc._log_failure(BASE, "market_data_error", "timeout de prueba")
            import glob
            import json
            files = glob.glob(f"{tmp}/*.jsonl")
            self.assertEqual(len(files), 1, "el fallo no llego al journal")
            rec = json.loads(open(files[0], encoding="utf-8").read().strip())
            self.assertIn("market_data_error", rec["reason"])
            self.assertFalse(rec["executable"])


if __name__ == "__main__":
    unittest.main()
