"""
Test 12 del TEST_PLAN — target_clearance bloquea de verdad.

Necesario porque el recorrido `tools/paper_replay.py` NO ejercita este gate: sus
velas sintéticas suben en linea recta, no hay resistencia por encima, y el
clearance sale infinito. Un gate que solo se ha visto pasar cuando no habia
nada que bloquear no esta probado.

Lo que se verifica aqui es la asimetria que importa: un obstaculo ANTES del
objetivo rechaza, uno DESPUES deja pasar, y el umbral es exactamente `rr`.
"""

import unittest

from app.strategies.hype import target_clearance as tc
from app.strategies.hype.common import SIDE_LONG, SIDE_SHORT, Config
from app.strategies.indicators import BAR_1H_MS, BAR_5M_MS, Candle

BASE = 1789000000000
DAY_MS = 24 * 60 * 60 * 1000


def full_prev_session(price=100.0):
    """288 velas de la sesion previa: sin esto el gate rechaza por cobertura,
    y estariamos midiendo el chequeo equivocado."""
    start = tc.session_start_ms(BASE, 0)
    prev = start - DAY_MS
    return [Candle(prev + i * BAR_5M_MS, price, price + 0.01, price - 0.01,
                   price, 10.0) for i in range(288)]


def h1_with_swing_at(level, n=60, base=100.0):
    """Serie 1H plana con UN swing high aislado en el centro.

    El pivote necesita n barras a cada lado ya cerradas, asi que se coloca lejos
    de los extremos: cerca del final no estaria confirmado todavia y el test
    pasaria por el motivo equivocado.
    """
    bars = []
    for i in range(n):
        hi = level if i == n // 2 else base + 0.05
        lo = base - 0.05
        bars.append(Candle(BASE - (n - i) * BAR_1H_MS, base, hi, lo, base, 10.0))
    return bars


def h1_with_swing_low_at(level, n=60, base=100.0):
    bars = []
    for i in range(n):
        lo = level if i == n // 2 else base - 0.05
        hi = base + 0.05
        bars.append(Candle(BASE - (n - i) * BAR_1H_MS, base, hi, lo, base, 10.0))
    return bars


class TestClearanceBlocks(unittest.TestCase):

    def setUp(self):
        self.c5 = full_prev_session()
        self.t = len(self.c5) - 1
        # rr=1.6 y R=1.0 -> el objetivo esta a 1.6 por encima de la entrada.
        self.cfg = Config(rr=1.6, clearance_lookback_1h=48,
                          clearance_use_prev_day=False)

    def test_resistance_before_target_rejects(self):
        """Resistencia a 1.2R con objetivo a 1.6R: el trade no cabe."""
        c1h = h1_with_swing_at(101.2)
        ok, cr, lvl = tc.check(c1h, self.c5, self.t, SIDE_LONG, 100.0, 99.0,
                               self.cfg)
        self.assertFalse(ok, f"aprobo con obstaculo a {cr:.2f}R < 1.6R")
        self.assertAlmostEqual(cr, 1.2, places=6)
        self.assertEqual(lvl.kind, "swing_1h")

    def test_resistance_beyond_target_passes(self):
        """Resistencia a 2.0R: hay sitio de sobra."""
        c1h = h1_with_swing_at(102.0)
        ok, cr, lvl = tc.check(c1h, self.c5, self.t, SIDE_LONG, 100.0, 99.0,
                               self.cfg)
        self.assertTrue(ok, f"rechazo con {cr:.2f}R disponibles")
        self.assertAlmostEqual(cr, 2.0, places=6)

    def test_threshold_is_exactly_rr(self):
        """Justo en 1.6R pasa; justo por debajo, no. El umbral no se redondea
        a favor del trade."""
        ok_at, cr_at, _ = tc.check(h1_with_swing_at(101.6), self.c5, self.t,
                                   SIDE_LONG, 100.0, 99.0, self.cfg)
        ok_below, _, _ = tc.check(h1_with_swing_at(101.59), self.c5, self.t,
                                  SIDE_LONG, 100.0, 99.0, self.cfg)
        self.assertTrue(ok_at, f"1.6R exactos deberian pasar (cr={cr_at})")
        self.assertFalse(ok_below)

    def test_level_behind_entry_does_not_block(self):
        """Un nivel que ya quedo atras no estorba al objetivo."""
        c1h = h1_with_swing_at(99.5)          # por DEBAJO de la entrada
        ok, cr, lvl = tc.check(c1h, self.c5, self.t, SIDE_LONG, 100.0, 99.0,
                               self.cfg)
        self.assertTrue(ok)
        self.assertIsNone(lvl)

    def test_short_support_before_target_rejects(self):
        """Simetrico: para el SHORT el obstaculo es un soporte por debajo."""
        c1h = h1_with_swing_low_at(98.8)      # 1.2R por debajo de 100
        ok, cr, lvl = tc.check(c1h, self.c5, self.t, SIDE_SHORT, 100.0, 101.0,
                               self.cfg)
        self.assertFalse(ok)
        self.assertAlmostEqual(cr, 1.2, places=6)

    def test_pivot_needs_confirmation_bars(self):
        """Un pivote sin sus n barras posteriores cerradas NO existe todavia:
        contarlo seria mirar al futuro."""
        n = 60
        bars = h1_with_swing_at(101.2, n=n)
        # el swing esta en n//2; truncar justo despues lo deja sin confirmar
        truncated = bars[: n // 2 + 1]
        levels = tc.swing_levels_1h(truncated, SIDE_LONG, self.cfg)
        self.assertFalse(any(abs(l.price - 101.2) < 1e-9 for l in levels),
                         "se uso un pivote no confirmado")


if __name__ == "__main__":
    unittest.main()
