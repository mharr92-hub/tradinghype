"""
Test 27 del TEST_PLAN — PARIDAD NUMERICA con el modulo congelado.

Es el gate bloqueante de la migracion (MIGRATION_PLAN 8.2). Afirma que el
motor v2, configurado para imitar al v1, produce EXACTAMENTE los mismos
niveles que `app/strategies/hype_long/rules.py`.

Sin esta paridad no se construye nada encima: es la unica prueba de que la
reescritura no rompio silenciosamente lo que ya funcionaba. Un motor nuevo que
"parece" dar las mismas señales no vale; tienen que ser los mismos numeros.

Configuracion de equivalencia:
  - require_momentum_long=False  -> el v1 base tampoco exigia momentum
  - rr=1.0                       -> el v1 solo conocia 1:1
  - allow_short=False            -> el v1 es long-only por construccion
  - require_target_clearance=False -> el v1 no tenia ese concepto

Con cualquier otra configuracion los dos motores DEBEN diferir: para eso se
hizo la v2.
"""

import unittest

import hype_long_vwap_retest as V1        # alias registrado en tests/__init__.py
from app.strategies.hype import engine as V2
from app.strategies.hype.common import SIDE_LONG, Config
from app.strategies.indicators import Candle

from .test_hype_long_vwap_retest import htf_context, scenario_5m


def to_v2(candles):
    """Las dos Candle son dataclasses distintas con los mismos campos."""
    return [Candle(ts=c.ts, o=c.o, h=c.h, l=c.l, c=c.c, v=c.v) for c in candles]


def equivalence_config(**kw) -> Config:
    """Config del v2 que imita al v1. `kw` puede sobrescribir cualquier default
    (por ejemplo `rr`) para los tests que verifican divergencia deliberada."""
    defaults = dict(allow_short=False, require_momentum_long=False, rr=1.0,
                    require_target_clearance=False)
    defaults.update(kw)
    return Config(**defaults)


class TestParityLong(unittest.TestCase):

    def _both(self, kind="confirm", bullish=True, cfg_v1=None, cfg_v2=None):
        c4, c1 = htf_context(bullish=bullish)
        c5 = scenario_5m(kind)
        r1 = V1.scan(c4, c1, c5, cfg_v1 or V1.Config(), equity=1000.0)
        r2 = V2.scan(to_v2(c4), to_v2(c1), to_v2(c5), cfg_v2 or equivalence_config())
        return r1, r2

    def test_same_levels_on_valid_long(self):
        r1, r2 = self._both("confirm")
        self.assertIsNotNone(r1.plan, f"v1 no emitio plan: {r1.reason}")
        self.assertIsNotNone(r2.signal, f"v2 no emitio señal: {r2.reason}")
        self.assertEqual(r2.signal.side, SIDE_LONG)
        # Los tres numeros que deciden el trade.
        self.assertAlmostEqual(r1.plan.entry, r2.signal.entry_ref, places=10)
        self.assertAlmostEqual(r1.plan.stop, r2.signal.stop, places=10)
        self.assertAlmostEqual(r1.plan.tp, r2.signal.tp, places=10)

    def test_same_cost_r(self):
        r1, r2 = self._both("confirm")
        self.assertAlmostEqual(r1.plan.cost_r, r2.signal.cost_r, places=10)

    def test_both_reject_the_same_scenarios(self):
        """Los rechazos tambien tienen que coincidir: un motor que acepta lo que
        el otro rechaza no es equivalente aunque acierte en los aceptados."""
        for kind in ("invalidated", "no_touch"):
            with self.subTest(kind=kind):
                r1, r2 = self._both(kind)
                self.assertIsNone(r1.plan)
                self.assertIsNone(r2.signal)

    def test_both_reject_bearish_regime(self):
        r1, r2 = self._both("confirm", bullish=False)
        self.assertIsNone(r1.plan)
        self.assertIsNone(r2.signal)

    def test_both_consume_the_first_retest(self):
        c4, c1 = htf_context(bullish=True)
        c5 = scenario_5m("confirm")
        i = len(c5)
        ms = V1.BAR_5M_MS
        base = c5[0].ts
        extra = [V1.Candle(base + i * ms, 51.06, 51.08, 50.50, 50.60, 150.0),
                 V1.Candle(base + (i + 1) * ms, 50.60, 51.20, 50.58, 51.15, 300.0)]
        full = c5 + extra
        r1 = V1.scan(c4, c1, full, V1.Config(), equity=1000.0)
        r2 = V2.scan(to_v2(c4), to_v2(c1), to_v2(full), equivalence_config())
        self.assertIsNone(r1.plan, "v1 reopero un gap ya consumido")
        self.assertIsNone(r2.signal, "v2 reopero un gap ya consumido")

    def test_both_apply_the_cost_gate_identically(self):
        r1, r2 = self._both("confirm",
                            cfg_v1=V1.Config(fee_taker=0.005),
                            cfg_v2=equivalence_config(fee_taker=0.005))
        self.assertIsNone(r1.plan)
        self.assertEqual(r1.reason, "cost_gate")
        self.assertIsNone(r2.signal)
        self.assertEqual(r2.reason, "cost_gate")


class TestDivergesWhenItShould(unittest.TestCase):
    """La paridad es condicional. Con la configuracion v2 real, los motores
    DEBEN diferir: si no difirieran, la v2 no estaria haciendo su trabajo."""

    def test_rr_changes_target_but_never_the_stop(self):
        """Test 32 del TEST_PLAN: el stop no depende del objetivo."""
        c4, c1 = htf_context(bullish=True)
        c5 = to_v2(scenario_5m("confirm"))
        stops, tps = [], []
        for rr in (1.0, 1.6, 2.0):
            r = V2.scan(to_v2(c4), to_v2(c1), c5, equivalence_config(rr=rr))
            self.assertIsNotNone(r.signal, f"rr={rr}: {r.reason}")
            stops.append(r.signal.stop)
            tps.append(r.signal.tp)
        self.assertEqual(len(set(round(s, 12) for s in stops)), 1,
                         "el stop cambio al cambiar el objetivo: prohibido (PRD 8)")
        self.assertEqual(len(set(round(t, 12) for t in tps)), 3,
                         "el objetivo no cambio con rr")

    def test_target_is_1_6r_with_e2(self):
        """Test 11 del TEST_PLAN."""
        c4, c1 = htf_context(bullish=True)
        c5 = to_v2(scenario_5m("confirm"))
        r = V2.scan(to_v2(c4), to_v2(c1), c5, equivalence_config(rr=1.6))
        self.assertIsNotNone(r.signal, r.reason)
        s = r.signal
        self.assertAlmostEqual(s.tp - s.entry_ref, 1.6 * (s.entry_ref - s.stop),
                               places=9)

    def test_engine_emits_candidate_not_a_plus(self):
        """Test 30-bis: el motor no puede declarar A+ porque no conoce los
        gates operativos (sizing, limite diario, kill switches)."""
        c4, c1 = htf_context(bullish=True)
        c5 = to_v2(scenario_5m("confirm"))
        r = V2.scan(to_v2(c4), to_v2(c1), c5, equivalence_config())
        self.assertIsNotNone(r.signal, r.reason)
        self.assertEqual(r.state, "LONG_CANDIDATE")
        self.assertTrue(r.signal.score.rules_complete)
        self.assertFalse(r.signal.score.is_a_plus,
                         "el motor declaro A+ sin gates operativos evaluados")


if __name__ == "__main__":
    unittest.main()
