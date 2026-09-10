"""
research/funding.py — funding por hora de tenencia REAL, con su signo.

Obligatorio desde que una posicion puede vivir 24 h (AUDIT C-04). El motor v0
lo documentaba como ausente, y con tenencias de minutos era tolerable. Con 24 h
un trade cruza hasta 24 liquidaciones horarias de Hyperliquid, y eso ya no es
ruido: es una linea del P&L.

EL SIGNO NO ES UN DETALLE. La tasa que publica el venue es positiva cuando los
LARGOS pagan a los cortos. Un modelo que use el valor absoluto, o que asuma que
el funding siempre resta, se equivoca en la mitad de los casos — y se equivoca
en direcciones opuestas para long y para short, que es la peor forma de
equivocarse porque sesga la comparacion entre las dos ramas.

Medicion del 2026-09-09: -0.00000573/h en HYPE, es decir los LARGOS COBRAN.
El research plan v0.1 asumia lo contrario. Una lectura no es una serie: por eso
este modulo interpola sobre el historico real y no acepta una constante como
sustituto salvo cuando se lo piden explicitamente.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..adapters.hyperliquid_data import FundingPoint
from ..strategies.hype.common import SIDE_LONG

HOUR_MS = 3_600_000


@dataclass(frozen=True)
class FundingCost:
    """Coste (positivo) o credito (negativo) de funding para una posicion."""
    frac_of_notional: float
    usd: float
    hours: float
    intervals: int
    used_fallback: bool     # True si falto historico y se uso una tasa constante

    @property
    def is_credit(self) -> bool:
        return self.frac_of_notional < 0


class FundingCurve:
    """Historico de funding consultable por instante.

    Guarda los puntos ordenados y responde "que tasa estaba vigente en T".
    Hyperliquid liquida cada hora, asi que la tasa vigente es la del ultimo
    punto publicado en o antes de T.
    """

    def __init__(self, points: Sequence[FundingPoint]):
        self.points = sorted(points, key=lambda p: p.ts)
        self._ts = [p.ts for p in self.points]

    def __len__(self) -> int:
        return len(self.points)

    @property
    def covers(self) -> Optional[tuple]:
        if not self.points:
            return None
        return (self.points[0].ts, self.points[-1].ts)

    def rate_at(self, ts_ms: int) -> Optional[float]:
        """Tasa horaria vigente en ts_ms, o None si el historico no llega.

        None y no 0.0 a proposito: 0.0 seria una afirmacion ("no hubo funding")
        y None es la verdad ("no lo se"). El llamador decide que hacer, y queda
        marcado en `used_fallback`.
        """
        if not self.points:
            return None
        i = bisect_right(self._ts, ts_ms) - 1
        if i < 0:
            return None
        return self.points[i].rate_hourly

    def accrue(self, entry_ts_ms: int, exit_ts_ms: int, side: str,
               notional_usd: float,
               fallback_rate: Optional[float] = None) -> FundingCost:
        """Funding acumulado entre entrada y salida.

        Se cobra en cada FRONTERA horaria cruzada, no de forma proporcional al
        tiempo: una posicion abierta 59 minutos dentro de la misma hora no paga
        funding, y una abierta 3 minutos a caballo de un cambio de hora paga una
        vez entera. Prorratear suavizaria un coste que en la realidad es
        escalonado, y con tenencias cortas la diferencia es grande.
        """
        if exit_ts_ms <= entry_ts_ms or notional_usd <= 0:
            return FundingCost(0.0, 0.0, 0.0, 0, False)

        hours = (exit_ts_ms - entry_ts_ms) / HOUR_MS
        first = ((entry_ts_ms // HOUR_MS) + 1) * HOUR_MS

        total = 0.0
        n = 0
        fallback_used = False
        t = first
        while t <= exit_ts_ms:
            rate = self.rate_at(t)
            if rate is None:
                if fallback_rate is None:
                    # Fail-closed: sin dato y sin fallback no se inventa un cero.
                    raise ValueError(
                        f"sin historico de funding para {t}; el backtest no "
                        "puede reportar un neto fiable (AUDIT C-04)."
                    )
                rate = fallback_rate
                fallback_used = True
            # Positivo = los largos pagan. Para el corto es un credito.
            total += rate if side == SIDE_LONG else -rate
            n += 1
            t += HOUR_MS

        return FundingCost(frac_of_notional=total, usd=total * notional_usd,
                           hours=hours, intervals=n, used_fallback=fallback_used)


def constant_curve(rate_hourly: float, start_ms: int, end_ms: int) -> FundingCurve:
    """Curva plana. SOLO para tests y para el estres de costos.

    No usar para reportar resultados: aplanar el funding borra exactamente la
    variacion que hace que importe.
    """
    pts = []
    t = (start_ms // HOUR_MS) * HOUR_MS
    while t <= end_ms:
        pts.append(FundingPoint(ts=t, rate_hourly=rate_hourly))
        t += HOUR_MS
    return FundingCurve(pts)
