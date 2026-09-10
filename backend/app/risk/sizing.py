"""
risk/sizing.py — cuantos HYPE, y cuando la respuesta correcta es NINGUNO (PRD 9).

Autoridad sobre la cantidad que se envia al exchange. El motor de estrategia
calcula niveles; este modulo decide si esos niveles caben en una posicion real.

LA REGLA QUE NO SE NEGOCIA: si no se puede construir una posicion valida sin
superar el presupuesto de riesgo, el resultado es SKIP. Nunca se sube el riesgo
para alcanzar el notional minimo del venue. Un $1.40 de riesgo "porque el
minimo del exchange no dejaba menos" es un 40 % de exceso disfrazado de
detalle tecnico.

$1 en TINY es el RISK BUDGET (perdida planificada hasta el stop, costos
incluidos), NO una posicion nocional de $1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..strategies.hype.common import SIDE_LONG, Config


@dataclass(frozen=True)
class VenueSpec:
    """Restricciones del exchange. Se leen de la API, no se asumen."""
    min_notional_usd: float = 10.0
    max_notional_usd: float = 100_000.0
    size_decimals: int = 2
    tick_size: float = 0.001
    available_collateral_usd: float = 0.0
    max_leverage: float = 3.0


@dataclass(frozen=True)
class Sizing:
    ok: bool
    qty: float
    notional: float
    risk_usd: float             # perdida planificada REAL, costos incluidos
    budget_usd: float
    reason: str = "ok"

    @property
    def skipped(self) -> bool:
        return not self.ok


def risk_budget(cfg: Config, equity: float) -> float:
    """Presupuesto de riesgo para UN trade (PRD 9)."""
    if cfg.risk_mode == "fixed_usd":
        return cfg.risk_usd
    if cfg.risk_mode == "pct_equity":
        return equity * cfg.risk_pct
    raise ValueError(f"risk_mode desconocido: {cfg.risk_mode!r}")


def size_position(entry: float, stop: float, side: str, cfg: Config,
                  equity: float, venue: VenueSpec,
                  cost_frac: Optional[float] = None) -> Sizing:
    """Cantidad a operar, o SKIP con motivo.

    La perdida esperada por unidad incluye el costo del trade PERDEDOR: si solo
    se dividiera por la distancia al stop, el riesgo real superaria el
    presupuesto justo en la cantidad que se pierde en fees y slippage.
    """
    from ..strategies.hype.common import cost_fraction
    cfrac = cost_fraction(cfg, side) if cost_frac is None else cost_frac

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0 or entry <= 0:
        return Sizing(False, 0.0, 0.0, 0.0, 0.0, "bad_levels")

    budget = risk_budget(cfg, equity)
    if budget <= 0:
        return Sizing(False, 0.0, 0.0, 0.0, budget, "no_budget")

    loss_per_unit = risk_per_unit + cfrac * entry
    raw_qty = budget / loss_per_unit

    step = 10 ** venue.size_decimals
    qty = math.floor(raw_qty * step) / step      # siempre hacia ABAJO
    if qty <= 0:
        return Sizing(False, 0.0, 0.0, 0.0, budget, "qty_below_precision")

    notional = qty * entry
    planned_risk = qty * loss_per_unit

    # El redondeo hacia abajo solo puede reducir el riesgo, nunca aumentarlo.
    # Esta guarda existe por si alguien cambia el redondeo en el futuro.
    if planned_risk > budget + 1e-9:
        return Sizing(False, qty, notional, planned_risk, budget, "risk_over_budget")

    if notional < venue.min_notional_usd:
        # Aqui es donde se cede a la tentacion. No se cede.
        return Sizing(False, qty, notional, planned_risk, budget,
                      "below_min_notional_would_require_more_risk")
    if notional > venue.max_notional_usd:
        return Sizing(False, qty, notional, planned_risk, budget, "above_max_notional")
    if venue.available_collateral_usd > 0:
        needed = notional / max(venue.max_leverage, 1e-9)
        if needed > venue.available_collateral_usd:
            return Sizing(False, qty, notional, planned_risk, budget,
                          "insufficient_collateral")

    return Sizing(True, qty, notional, planned_risk, budget, "ok")


def expected_profit_usd(entry: float, tp: float, qty: float, side: str,
                        cost_frac: float) -> float:
    """Beneficio neto estimado si toca TP. Se muestra en la Signal Card para
    que Mark vea el numero real, no el bruto."""
    gross = (tp - entry) * qty if side == SIDE_LONG else (entry - tp) * qty
    return gross - cost_frac * entry * qty
