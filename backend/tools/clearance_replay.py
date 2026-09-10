"""
clearance_replay.py — recorrido end-to-end del gate `target_clearance`.

POR QUE HACE FALTA, teniendo ya un unit test: `paper_replay.py` NO ejercita este
gate. Sus velas suben en linea recta, no hay resistencia por encima, y el
clearance sale infinito. Un gate que solo se ha visto pasar cuando no habia nada
que bloquear no esta probado end-to-end.

Y un unit test tampoco basta: comprueba la funcion aislada, no que el motor
completo la CONSULTE y actue en consecuencia. Entre `target_clearance.check()`
y el rechazo de la señal hay ocho pasos donde el resultado se puede perder.

DISEÑO DEL EXPERIMENTO: dos escenarios IDENTICOS salvo en una cosa — la altura
de un swing high de 1H. Todo lo demas (velas de 5m, FVG, retest, confirmacion,
regimen, volumen) es byte a byte el mismo. Si uno pasa y el otro no, la unica
explicacion posible es el gate. Es la unica forma de atribuir el efecto.

  Escenario A - resistencia a ~1.2R  -> el objetivo de 1.6R no cabe -> RECHAZO
  Escenario B - resistencia a ~2.4R  -> hay sitio de sobra          -> SEÑAL

Uso:  python -m tools.clearance_replay
"""

from __future__ import annotations

import datetime as dt
import sys

from app.strategies.hype import engine, target_clearance
from app.strategies.hype.common import SIDE_LONG, Config
from app.strategies.indicators import BAR_1H_MS, BAR_4H_MS, BAR_5M_MS, Candle

UTC = dt.timezone.utc
SESSION = int(dt.datetime(2026, 9, 9, 0, 0, tzinfo=UTC).timestamp() * 1000)
PREV = SESSION - 24 * 3_600_000


def rising(n, start, step, tf_ms, ts0, vol=100.0):
    out, prev = [], start
    for i in range(n):
        o = prev
        c = o + step
        out.append(Candle(ts0 + i * tf_ms, o, c + 0.05, o - 0.05, c, vol))
        prev = c
    return out


def build_5m():
    """Identico al de paper_replay: sesion previa completa + setup que confirma."""
    bars = []
    prev_c = 46.0
    for i in range(288):                      # sesion previa, cobertura completa
        o = prev_c
        c = o + 0.008
        bars.append(Candle(PREV + i * BAR_5M_MS, o, c + 0.03, o - 0.03, c, 60.0))
        prev_c = c
    i = 0
    for _ in range(20):                       # warmup de la sesion actual
        o = prev_c
        c = o + 0.012
        bars.append(Candle(SESSION + i * BAR_5M_MS, o, c + 0.02, o - 0.02, c, 50.0))
        prev_c = c
        i += 1
    b = prev_c
    A = Candle(SESSION + i * BAR_5M_MS, b, b + 0.05, b - 0.03, b + 0.03, 120.0); i += 1
    B = Candle(SESSION + i * BAR_5M_MS, b + 0.03, b + 0.55, b + 0.01, b + 0.50, 400.0); i += 1
    C = Candle(SESSION + i * BAR_5M_MS, b + 0.50, b + 0.75, b + 0.25, b + 0.68, 350.0); i += 1
    D = Candle(SESSION + i * BAR_5M_MS, b + 0.68, b + 0.70, b + 0.20, b + 0.32, 150.0); i += 1
    E = Candle(SESSION + i * BAR_5M_MS, b + 0.32, b + 0.80, b + 0.28, b + 0.76, 300.0); i += 1
    bars += [A, B, C, D, E]
    return bars


def h1_with_resistance(entry, r, mult, n=120):
    """Serie 1H alcista con UN swing high aislado a `mult` R por encima de `entry`.

    El pivote se coloca lejos del final: necesita `pivot_n` barras posteriores
    ya cerradas para estar confirmado. Si estuviera al borde no seria un pivote
    todavia y el escenario probaria otra cosa.

    Las barras vecinas se mantienen ESTRICTAMENTE por debajo: la deteccion de
    pivotes usa comparacion estricta (igual que `ta.pivothigh` de TradingView),
    asi que un empate no crearia nivel.
    """
    level = entry + mult * r
    bars = rising(n, 44.0, 0.025, BAR_1H_MS, SESSION - n * BAR_1H_MS)
    idx = n - 20                                # confirmado: quedan 20 barras
    out = []
    for k, cd in enumerate(bars):
        if k == idx:
            out.append(Candle(cd.ts, cd.o, level, cd.l, cd.c, cd.v))
        else:
            # techo por debajo del nivel para que el unico pivote sea el nuestro
            out.append(Candle(cd.ts, cd.o, min(cd.h, level - 0.5), cd.l, cd.c, cd.v))
    return out, level


def run(label, mult, cfg, c4h, c5, entry_guess, r_guess):
    c1h, level = h1_with_resistance(entry_guess, r_guess, mult)
    res = engine.scan(c4h, c1h, c5, cfg)
    cr = res.checks.get("clearance_r")
    gate = res.checks.get("target_clearance")
    print(f"\n--- {label}")
    print(f"   resistencia 1H en {level:.4f}  (~{mult}R sobre la entrada)")
    print(f"   clearance calculado : {cr}")
    print(f"   gate target_clearance: {gate}")
    print(f"   motivo del motor     : {res.reason}")
    print(f"   señal                : {'SI' if res.signal else 'NO'}")
    return res, level


def main() -> int:
    cfg = Config(allow_short=False, rr=1.6, risk_mode="fixed_usd", risk_usd=1.00,
                 clearance_lookback_1h=48, clearance_use_prev_day=False)
    c4h = rising(120, 30.0, 0.22, BAR_4H_MS, SESSION - 120 * BAR_4H_MS)
    c5 = build_5m()

    print("=" * 74)
    print("REPLAY DE TARGET_CLEARANCE — DATOS SINTETICOS")
    print("Dos escenarios identicos salvo la altura de un swing high de 1H.")
    print("=" * 74)
    print(f"objetivo del brazo activo: {cfg.rr}R   ({cfg.arm_label()}, "
          f"huella {cfg.fingerprint()})")

    # Primera pasada sin el gate para conocer entry y R reales del setup.
    probe = engine.scan(c4h, rising(120, 44.0, 0.025, BAR_1H_MS,
                                    SESSION - 120 * BAR_1H_MS), c5,
                        Config(**{**cfg.__dict__, "require_target_clearance": False}))
    if probe.signal is None:
        print(f"\nel setup base no confirma ({probe.reason}); el replay no aplica.")
        return 1
    entry, r = probe.signal.entry_ref, probe.signal.risk_per_unit
    print(f"setup base: entry={entry:.4f}  stop={probe.signal.stop:.4f}  R={r:.4f}")

    a, lvl_a = run("ESCENARIO A - resistencia ANTES del objetivo", 1.2, cfg,
                   c4h, c5, entry, r)
    b, lvl_b = run("ESCENARIO B - resistencia DESPUES del objetivo", 2.4, cfg,
                   c4h, c5, entry, r)

    print("\n" + "=" * 74)
    ok_a = a.signal is None and a.reason == "target_clearance"
    ok_b = b.signal is not None
    print(f"A rechazado por clearance : {'SI' if ok_a else 'NO  <-- FALLO'}")
    print(f"B emite señal             : {'SI' if ok_b else 'NO  <-- FALLO'}")

    if ok_a and ok_b:
        print("\nEl gate ATRIBUYE correctamente: la unica diferencia entre los dos")
        print(f"escenarios es la resistencia ({lvl_a:.4f} vs {lvl_b:.4f}), y produce")
        print("rechazo o señal en consecuencia. El motor lo consulta de verdad.")
        print("=" * 74)
        return 0

    print("\nEl gate NO se comporta como dice la especificacion.")
    print("=" * 74)
    return 1


if __name__ == "__main__":
    sys.exit(main())
