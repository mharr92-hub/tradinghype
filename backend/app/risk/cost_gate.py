"""
risk/cost_gate.py — ¿se comen los costos el trade? (PRD 12)

Con 1:1, un taker de 4.5 bps/lado pone el break-even en 54.5-55.5 % de acierto.
Con 1.6R el listón baja, pero el argumento no desaparece: si el stop esta muy
cerca en terminos porcentuales, los costos fijos pesan una fraccion enorme de R
y el trade nace muerto.

LA REGLA QUE NO SE NEGOCIA: si el gate no pasa, el trade se salta. NUNCA se
ensancha el stop para que el costo relativo baje. Ensanchar el stop no reduce
el costo: reduce el numero de veces que se ve el costo, a cambio de perder mas
cuando se pierde.

Con max_hold de hasta 24 h el funding deja de ser ruido y entra en la cuenta
con su signo: tasa positiva significa que el LARGO paga y el CORTO cobra.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..strategies.hype.common import SIDE_LONG, Config, cost_fraction


@dataclass(frozen=True)
class CostBreakdown:
    """Desglose que se muestra en la Signal Card. Nada agregado sin detallar."""
    fees_frac: float
    spread_frac: float
    slippage_frac: float
    funding_frac: float          # con signo: negativo = credito
    total_frac: float
    cost_r: float
    limit_r: float
    passed: bool

    def usd(self, notional: float) -> float:
        return self.total_frac * notional


def estimate_funding_frac(cfg: Config, side: str,
                          hours: float | None = None) -> float:
    """Funding esperado como fraccion del nocional, con signo.

    Hyperliquid liquida funding por hora. CONFIRMAR contra la API antes de
    reportar resultados: si el intervalo real fuese distinto, todos los
    numeros de tenencia larga estarian mal por un factor constante.
    """
    h = cfg.funding_expected_hours if hours is None else hours
    raw = cfg.funding_rate_hourly * h
    return raw if side == SIDE_LONG else -raw


def evaluate(entry: float, stop: float, side: str, cfg: Config,
             hours: float | None = None) -> CostBreakdown:
    """Evalua el gate para unos niveles dados."""
    fees = 2.0 * cfg.fee_taker
    funding = estimate_funding_frac(cfg, side, hours)
    total = max(0.0, fees + cfg.spread_rt + cfg.slippage_rt + funding)

    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return CostBreakdown(fees, cfg.spread_rt, cfg.slippage_rt, funding,
                             total, float("inf"), cfg.max_cost_r(side), False)

    cost_r = total / (risk / entry)
    limit = cfg.max_cost_r(side)
    return CostBreakdown(fees, cfg.spread_rt, cfg.slippage_rt, funding,
                         total, cost_r, limit, cost_r <= limit)


def sanity_check_no_stop_widening(stop_before: float, stop_after: float,
                                  side: str) -> None:
    """Guarda contra la tentacion mas peligrosa del sistema.

    Se llama en cualquier punto donde el stop pudiera recalcularse despues de
    haber visto el resultado del cost gate. Si el stop se alejo de la entrada,
    alguien intento comprar la aprobacion del gate con riesgo.
    """
    if side == SIDE_LONG and stop_after < stop_before - 1e-9:
        raise ValueError(
            "El stop se ensancho tras evaluar los costos. Prohibido (PRD 8, 12)."
        )
    if side != SIDE_LONG and stop_after > stop_before + 1e-9:
        raise ValueError(
            "El stop se ensancho tras evaluar los costos. Prohibido (PRD 8, 12)."
        )
