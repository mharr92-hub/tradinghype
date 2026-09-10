"""
Tests de hype_long_vwap_retest v0.1 — ejecutar: python3 -m unittest test_hype_long_vwap_retest -v

Cubren: política long-only, escenario end-to-end sintético (impulso → FVG →
retest en VWAP → confirmación), filtro de costos, primer-retest-único,
invalidación, gap viejo, warmup de sesión y régimen bajista.
"""
import datetime as dt
import unittest

import hype_long_vwap_retest as S

UTC = dt.timezone.utc
BASE = int(dt.datetime(2026, 9, 8, 0, 0, tzinfo=UTC).timestamp() * 1000)


def mk(ts, o, h, l, c, v):
    return S.Candle(ts=ts, o=o, h=h, l=l, c=c, v=v)


def rising_series(n, start, step, tf_ms, ts0):
    """Serie alcista simple: cada vela cierra step por encima de la anterior."""
    out = []
    prev_c = start
    for i in range(n):
        o = prev_c
        c = o + step
        out.append(mk(ts0 + i * tf_ms, o, c + 0.1, o - 0.1, c, 100.0))
        prev_c = c
    return out


def falling_series(n, start, step, tf_ms, ts0):
    out = []
    prev_c = start
    for i in range(n):
        o = prev_c
        c = o - step
        out.append(mk(ts0 + i * tf_ms, o, o + 0.1, c - 0.1, c, 100.0))
        prev_c = c
    return out


def htf_context(bullish=True):
    """4H y 1H sintéticos que pasan (o no) el régimen."""
    h4_ms = 4 * 60 * 60 * 1000
    h1_ms = 60 * 60 * 1000
    ts0_4h = BASE - 80 * h4_ms
    ts0_1h = BASE - 80 * h1_ms
    if bullish:
        c4 = rising_series(80, 30.0, 0.25, h4_ms, ts0_4h)
        c1 = rising_series(80, 48.0, 0.03, h1_ms, ts0_1h)
    else:
        c4 = falling_series(80, 80.0, 0.25, h4_ms, ts0_4h)
        c1 = falling_series(80, 55.0, 0.03, h1_ms, ts0_1h)
    return c4, c1


def scenario_5m(kind="confirm"):
    """Sesión 5m: 20 velas de warmup suave, impulso A-B-C que deja un FVG
    (zona 50.36–50.55), retest D y vela E. Variantes:
      confirm      -> E confirma (señal en E)
      invalidated  -> D cierra bajo el gap
      no_touch     -> D no llega a la zona
    """
    bars = []
    prev_c = 50.0
    for i in range(20):
        o = prev_c
        c = o + 0.015
        bars.append(mk(BASE + i * S.BAR_5M_MS, o, c + 0.02, o - 0.02, c, 50.0))
        prev_c = c
    i = 20
    A = mk(BASE + i * S.BAR_5M_MS, 50.31, 50.36, 50.28, 50.34, 120.0); i += 1
    B = mk(BASE + i * S.BAR_5M_MS, 50.34, 50.85, 50.32, 50.80, 400.0); i += 1
    C = mk(BASE + i * S.BAR_5M_MS, 50.80, 51.05, 50.55, 50.98, 350.0); i += 1
    bars += [A, B, C]  # FVG alcista: C.l=50.55 > A.h=50.36 -> zona (50.36, 50.55)
    if kind == "invalidated":
        D = mk(BASE + i * S.BAR_5M_MS, 50.98, 51.00, 50.28, 50.30, 200.0); i += 1
        E = mk(BASE + i * S.BAR_5M_MS, 50.30, 51.10, 50.28, 51.06, 300.0); i += 1
        bars += [D, E]
        return bars
    if kind == "no_touch":
        D = mk(BASE + i * S.BAR_5M_MS, 50.98, 51.00, 50.70, 50.90, 150.0); i += 1
        E = mk(BASE + i * S.BAR_5M_MS, 50.90, 51.10, 50.85, 51.06, 300.0); i += 1
        bars += [D, E]
        return bars
    D = mk(BASE + i * S.BAR_5M_MS, 50.98, 51.00, 50.50, 50.62, 150.0); i += 1  # 1er toque
    E = mk(BASE + i * S.BAR_5M_MS, 50.62, 51.10, 50.58, 51.06, 300.0); i += 1  # confirma
    bars += [D, E]
    return bars


class TestIndicators(unittest.TestCase):
    def test_vwap_hand_computed(self):
        c = [mk(BASE, 10, 12, 8, 10, 10.0),           # tp=10, v=10
             mk(BASE + S.BAR_5M_MS, 10, 14, 10, 12, 20.0)]  # tp=12, v=20
        vw, n = S.vwap_at(c, 1, 0)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(vw, (10 * 10 + 12 * 20) / 30.0)

    def test_fvg_detection(self):
        bars = scenario_5m("confirm")
        gaps = S.find_session_gaps(bars, len(bars) - 1, 0)
        self.assertTrue(any(abs(g.lo - 50.36) < 1e-9 and abs(g.hi - 50.55) < 1e-9
                            for g in gaps))

    def test_ema_follows_rising_series(self):
        vals = [float(x) for x in range(1, 101)]
        e = S.ema_series(vals, 20)
        self.assertGreater(e[-1], e[-10])
        self.assertLess(e[-1], vals[-1])


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.c4, self.c1 = htf_context(bullish=True)

    def test_confirmation_emits_long_plan(self):
        c5 = scenario_5m("confirm")
        res = S.scan(self.c4, self.c1, c5, S.Config(), equity=1000.0)
        self.assertEqual(res.reason, "ok", msg=str(res.checks) + " " + res.reason)
        p = res.plan
        self.assertIsNotNone(p)
        self.assertEqual(p.side, S.SIDE_LONG)
        self.assertLess(p.stop, p.entry)
        self.assertGreater(p.tp, p.entry)
        self.assertAlmostEqual(p.tp - p.entry, p.entry - p.stop, places=9)  # 1:1
        self.assertLessEqual(p.cost_r, S.Config().max_cost_r)
        self.assertGreater(p.qty, 0)
        # el stop protege la estructura: por debajo del gap
        self.assertLess(p.stop, 50.36)

    def test_no_signal_before_confirmation(self):
        c5 = scenario_5m("confirm")[:-1]  # hasta D: toque sin confirmación
        res = S.scan(self.c4, self.c1, c5, S.Config(), equity=1000.0)
        self.assertIsNone(res.plan)
        self.assertEqual(res.reason, "no_setup")

    def test_first_retest_is_consumed(self):
        c5 = scenario_5m("confirm")
        i = len(c5)
        # tras la confirmación en E, el precio vuelve a la zona y "confirma" otra vez
        F = mk(BASE + i * S.BAR_5M_MS, 51.06, 51.08, 50.50, 50.60, 150.0); i += 1
        G = mk(BASE + i * S.BAR_5M_MS, 50.60, 51.20, 50.58, 51.15, 300.0); i += 1
        res = S.scan(self.c4, self.c1, c5 + [F, G], S.Config(), equity=1000.0)
        self.assertIsNone(res.plan)   # el gap ya se consumió en E
        self.assertEqual(res.reason, "no_setup")

    def test_invalidated_gap_never_signals(self):
        c5 = scenario_5m("invalidated")
        res = S.scan(self.c4, self.c1, c5, S.Config(), equity=1000.0)
        self.assertIsNone(res.plan)

    def test_no_touch_no_signal(self):
        c5 = scenario_5m("no_touch")
        res = S.scan(self.c4, self.c1, c5, S.Config(), equity=1000.0)
        self.assertIsNone(res.plan)

    def test_cost_gate_rejects_expensive_trade(self):
        c5 = scenario_5m("confirm")
        cfg = S.Config(fee_taker=0.005)  # 50 bps por lado -> costo en R enorme
        res = S.scan(self.c4, self.c1, c5, cfg, equity=1000.0)
        self.assertIsNone(res.plan)
        self.assertEqual(res.reason, "cost_gate")

    def test_stale_gap_rejected(self):
        c5 = scenario_5m("confirm")
        cfg = S.Config(fvg_max_age_bars=0)
        res = S.scan(self.c4, self.c1, c5, cfg, equity=1000.0)
        self.assertIsNone(res.plan)

    def test_session_warmup_blocks_early_signals(self):
        c5 = scenario_5m("confirm")[:10]
        res = S.scan(self.c4, self.c1, c5, S.Config(), equity=1000.0)
        self.assertIsNone(res.plan)


class TestPolicy(unittest.TestCase):
    def test_bearish_regime_never_trades(self):
        c4, c1 = htf_context(bullish=False)
        c5 = scenario_5m("confirm")
        res = S.scan(c4, c1, c5, S.Config(), equity=1000.0)
        self.assertIsNone(res.plan)
        self.assertEqual(res.reason, "regime_4h_fail")

    def test_module_source_has_no_opposite_direction(self):
        src = open(S.__file__, encoding="utf-8").read()
        self.assertNotIn('"SHORT"', src)
        self.assertNotIn("'SHORT'", src)

    def test_policy_guard_raises_on_tampered_plan(self):
        p = S.Plan(side="X", entry=1.0, stop=0.9, tp=1.1, qty=1.0,
                   cost_r=0.05, ts=BASE)
        with self.assertRaises(S.PolicyViolation):
            S._assert_long_only(p)


class TestGapCache(unittest.TestCase):
    def test_cache_gives_identical_results(self):
        c4, c1 = htf_context(bullish=True)
        c5 = scenario_5m("confirm")
        i = len(c5)
        F = mk(BASE + i * S.BAR_5M_MS, 51.06, 51.08, 50.50, 50.60, 150.0); i += 1
        G = mk(BASE + i * S.BAR_5M_MS, 50.60, 51.20, 50.58, 51.15, 300.0); i += 1
        full = c5 + [F, G]
        cache: dict = {}
        for t in range(23, len(full) + 1):
            a = S.scan(c4, c1, full[:t], S.Config(), 1000.0)
            b = S.scan(c4, c1, full[:t], S.Config(), 1000.0, gap_cache=cache)
            self.assertEqual(a.reason, b.reason, msg=f"t={t}")
            self.assertEqual(a.plan is None, b.plan is None, msg=f"t={t}")
            if a.plan is not None:
                self.assertAlmostEqual(a.plan.entry, b.plan.entry)
                self.assertAlmostEqual(a.plan.stop, b.plan.stop)


class TestMomentumVariant(unittest.TestCase):
    def test_momentum_flag_gates_signal(self):
        c4, c1 = htf_context(bullish=True)
        c5 = scenario_5m("confirm")
        cfg = S.Config(require_momentum=True)
        res = S.scan(c4, c1, c5, cfg, equity=1000.0)
        # con la F2 activada el resultado es 'ok' o un rechazo explícito de momentum;
        # nunca un plan que se salte el gate
        if res.plan is None:
            self.assertTrue(res.reason.startswith("momentum_fail"))
        else:
            self.assertTrue(res.checks.get("momentum") is True)


if __name__ == "__main__":
    unittest.main()
