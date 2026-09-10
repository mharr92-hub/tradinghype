"""
research/fills.py — modelo de fill realista. Compartido por BACKTEST y PAPER.

Existe porque el motor v0 entraba al cierre de la vela de confirmacion
(AUDIT C-05), y eso no es conservador: es optimista de forma sistematica. La
vela de confirmacion es POR DEFINICION una vela de impulso a favor. Comprar
justo en su cierre es comprar en el punto mas caro del tramo, y el backtest lo
daba por conseguido siempre, gratis y al instante.

Tres cosas que este modulo hace y el v0 no hacia:

  1. La entrada ocurre DESPUES del cierre de la vela de señal, no en el.
  2. El precio se degrada en contra: spread + slippage, siempre adverso.
  3. Si el precio se aleja mas de 0.10R, el trade NO SE HACE. En vivo eso es
     "no se persigue la entrada" (PRD 13); en backtest es un trade que la
     estrategia no habria capturado y que no puede contarse como ganado.

El punto 3 es el que mas cambia los numeros: un backtest que asume que siempre
entras te esta enseñando una estrategia distinta de la que vas a operar.

`execution/paper.py` importa exactamente estas funciones, para que PAPER y
BACKTEST no puedan divergir (PRD 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..strategies.hype.common import SIDE_LONG, Config
from ..strategies.indicators import BAR_5M_MS, Candle


@dataclass(frozen=True)
class FillModel:
    """Parametros del fill. Separados de Config porque describen la EJECUCION,
    no la estrategia, y se estresan aparte (RESEARCH_PLAN 4, estres de fill)."""
    delay_seconds: float = 3.0      # cierre de vela -> orden efectivamente enviada
    spread_frac: float = 0.0001     # medio spread aplicado en contra, por lado
    slippage_frac: float = 0.0001   # impacto adicional, siempre adverso
    max_drift_r: float = 0.10       # PRD 13
    ttl_seconds: float = 90.0       # PRD 13


@dataclass(frozen=True)
class Fill:
    filled: bool
    price: float
    ts_ms: int
    drift_r: float
    reason: str = "ok"

    @property
    def missed(self) -> bool:
        return not self.filled


def adverse_price(reference: float, side: str, model: FillModel) -> float:
    """Degrada el precio en contra del trade. Nunca a favor.

    Un fill que a veces sale mejor de lo esperado existe en la realidad, pero
    modelarlo en backtest solo sirve para inflar el resultado: el error se
    acumula hacia el lado agradable.
    """
    frac = model.spread_frac + model.slippage_frac
    return reference * (1.0 + frac) if side == SIDE_LONG else reference * (1.0 - frac)


def drift_r(reference: float, observed: float, risk_per_unit: float,
            side: str) -> float:
    """Cuanto se ha movido el precio EN CONTRA, en unidades de R.

    Solo cuenta el movimiento adverso: si el precio mejora, el trade sigue
    siendo valido y el drift es 0.
    """
    if risk_per_unit <= 0:
        return float("inf")
    delta = ((observed - reference) if side == SIDE_LONG
             else (reference - observed))
    return max(0.0, delta) / risk_per_unit


def simulate_fill(signal_bar: Candle, next_bar: Optional[Candle], side: str,
                  entry_ref: float, risk_per_unit: float,
                  model: FillModel = FillModel()) -> Fill:
    """Fill en la primera observacion de mercado POSTERIOR al cierre de la señal.

    Con datos de 5m, la primera observacion disponible es la APERTURA de la
    vela siguiente. Es una aproximacion, y su direccion importa: la apertura
    esta mas cerca del cierre anterior que cualquier precio intrabar posterior,
    asi que sigue siendo mas favorable que la realidad. Se documenta como cota
    OPTIMISTA, no como precio exacto — con velas de 5m no se puede hacer mejor
    sin datos de tick.
    """
    if next_bar is None:
        return Fill(False, 0.0, signal_bar.ts + BAR_5M_MS, float("inf"),
                    "no_next_observation")

    observed = next_bar.o
    d = drift_r(entry_ref, observed, risk_per_unit, side)
    ts = signal_bar.ts + BAR_5M_MS + int(model.delay_seconds * 1000)

    if d > model.max_drift_r:
        # No se persigue la entrada. Este trade no existe.
        return Fill(False, observed, ts, d, f"drift_exceeded:{d:.3f}R")

    return Fill(True, adverse_price(observed, side, model), ts, d, "ok")


@dataclass(frozen=True)
class Exit:
    kind: str          # "stop" | "tp" | "time_stop" | "open_at_end"
    price: float
    bar_index: int
    ts_ms: int
    hours_held: float
    mfe_r: float       # maximum favorable excursion, en R
    mae_r: float       # maximum adverse excursion, en R


def resolve_exit(c5: List[Candle], entry_index: int, entry_price: float,
                 stop: float, tp: float, side: str, cfg: Config,
                 model: FillModel = FillModel()) -> Exit:
    """Camina hacia delante desde entry_index+1 hasta que algo cierra la posicion.

    Tres salidas (PRD 11): STOP, TP y TIME STOP a 24 h. El v0 no tenia la
    tercera y dejaba posiciones abiertas hasta el final de los datos.

    Regla conservadora: si una vela toca STOP y TP, cuenta como PERDIDA. Con
    velas de 5m no se puede saber cual se toco primero, y elegir el resultado
    agradable en cada empate infla el backtest de forma invisible.

    El precio de salida se acota al rango REALMENTE negociado en la vela. Si el
    mercado abre con hueco por debajo del stop, el fill ocurre en la apertura,
    no al precio del stop: devolver el stop seria inventar liquidez que no
    existio.
    """
    risk = abs(entry_price - stop)
    last = min(len(c5), entry_index + 1 + cfg.max_hold_bars_5m)
    mfe = mae = 0.0

    for j in range(entry_index + 1, last):
        bar = c5[j]
        fav = ((bar.h - entry_price) if side == SIDE_LONG
               else (entry_price - bar.l))
        adv = ((entry_price - bar.l) if side == SIDE_LONG
               else (bar.h - entry_price))
        if risk > 0:
            mfe = max(mfe, fav / risk)
            mae = max(mae, adv / risk)

        hit_stop = bar.l <= stop if side == SIDE_LONG else bar.h >= stop
        hit_tp = bar.h >= tp if side == SIDE_LONG else bar.l <= tp
        hours = (bar.ts + BAR_5M_MS - c5[entry_index].ts) / 3_600_000.0

        if hit_stop:                       # prioridad conservadora
            px = min(stop, bar.o) if side == SIDE_LONG else max(stop, bar.o)
            return Exit("stop", px, j, bar.ts, hours, mfe, mae)
        if hit_tp:
            px = max(tp, bar.o) if side == SIDE_LONG else min(tp, bar.o)
            return Exit("tp", px, j, bar.ts, hours, mfe, mae)

    if last <= entry_index + 1:
        bar = c5[entry_index]
        return Exit("open_at_end", bar.c, entry_index, bar.ts, 0.0, mfe, mae)

    j = last - 1
    bar = c5[j]
    hours = (bar.ts + BAR_5M_MS - c5[entry_index].ts) / 3_600_000.0
    kind = "time_stop" if j - entry_index >= cfg.max_hold_bars_5m else "open_at_end"
    # El time stop sale a mercado: se degrada el precio en contra igual que una
    # entrada, porque cerrar tambien cuesta.
    px = adverse_price(bar.c, SIDE_LONG if side != SIDE_LONG else "SHORT", model)
    return Exit(kind, px, j, bar.ts, hours, mfe, mae)
