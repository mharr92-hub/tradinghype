"""
strategies/hype/engine.py — orquestador. UNICA `scan()` publica del sistema.

FUENTE DE VERDAD (PRD 4). Esta misma funcion la llaman el backtester, PAPER,
SHADOW, TINY y LIVE. No existe ni puede existir una segunda implementacion de
las reglas: TradingView visualiza, nunca decide.

Lo que este modulo NO hace, a proposito:
  - no calcula cantidad: eso es risk/sizing.py, que conoce los minimos del venue;
  - no decide si se puede operar hoy: eso es risk/limits.py;
  - no envia ordenes: eso es execution/, con LIVE_EXECUTION=false.

`entry_ref` es el cierre de la vela de confirmacion y es una REFERENCIA, no un
fill. El fill realista (delay, spread, slippage, drift, missed trade) lo modela
research/fills.py en backtest y el adaptador en vivo. Confundir ambas cosas fue
el sesgo optimista del motor v0 (AUDIT C-05).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..indicators import Candle, atr_at, vwap_at
from . import long as long_rules
from . import short as short_rules
from . import target_clearance
from .common import (CHECKLIST_KEYS, SIDE_LONG, SIDE_SHORT, STATE_NO_SETUP,
                     STATE_SIGNAL_READY, STATE_WAITING_CONFIRMATION, Config,
                     PolicyViolation, cost_fraction, resolve_side)
from .scoring import Score, score

STRATEGY_ID = "hype_vwap_fvg_retest_v2"


@dataclass(frozen=True)
class Signal:
    """Un setup A+ con sus niveles calculados. Sin cantidad todavia."""
    side: str
    entry_ref: float
    stop: float
    tp: float
    rr: float
    risk_per_unit: float
    cost_r: float
    cost_frac: float
    clearance_r: float
    ts: int                     # apertura de la vela de confirmacion
    strategy_id: str = STRATEGY_ID
    checklist: Tuple[str, ...] = field(default_factory=tuple)
    features: Dict[str, float] = field(default_factory=dict)
    score: Optional[Score] = None


@dataclass(frozen=True)
class ScanResult:
    signal: Optional[Signal]
    reason: str
    state: str
    side_evaluated: Optional[str]
    checks: Dict[str, object]

    @property
    def checklist_bools(self) -> Dict[str, bool]:
        return {k: bool(self.checks.get(k)) for k in CHECKLIST_KEYS}


def _rules_for(side: str):
    return long_rules if side == SIDE_LONG else short_rules


def _direction(c4h: List[Candle], c1h: List[Candle], cfg: Config
               ) -> Tuple[Optional[str], Dict[str, object]]:
    """Direccion candidata. LONG y SHORT son mutuamente excluyentes: sus
    condiciones de regimen no pueden ser ciertas a la vez."""
    checks: Dict[str, object] = {}
    long_regime = cfg.allow_long and long_rules.regime_ok(c4h, cfg)
    short_regime = cfg.allow_short and short_rules.regime_ok(c4h, cfg)
    checks["regime_4h_long"] = long_regime
    checks["regime_4h_short"] = short_regime

    if long_regime and long_rules.align_ok(c1h, cfg):
        checks["regime_4h"] = True
        checks["align_1h"] = True
        return SIDE_LONG, checks
    if short_regime and short_rules.align_ok(c1h, cfg):
        checks["regime_4h"] = True
        checks["align_1h"] = True
        return SIDE_SHORT, checks

    checks["regime_4h"] = long_regime or short_regime
    checks["align_1h"] = False
    return None, checks


def scan(c4h: List[Candle], c1h: List[Candle], c5: List[Candle],
         cfg: Config = Config(), gap_cache: Optional[dict] = None) -> ScanResult:
    """Evalua la ULTIMA vela completada de 5m.

    Todas las listas deben contener solo velas CERRADAS. El llamador es
    responsable de descartar la vela en curso; el motor asume que ya se hizo.

    gap_cache (opcional, para backtests): memoiza gaps en estado terminal. Los
    estados terminales no cambian con mas historia, asi que el resultado es
    identico con o sin cache (test 28 del TEST_PLAN).
    """
    checks: Dict[str, object] = {}

    if not cfg.require_fvg:
        # Brazo F0 (retest de VWAP sin exigir FVG). Aun no implementado: la
        # zona de retest tendria que anclarse a la banda de VWAP en lugar de a
        # un gap, y eso cambia el ciclo de vida completo. Se prefiere fallar
        # ruidosamente antes que devolver numeros que no significan lo que el
        # nombre del brazo promete.
        raise NotImplementedError(
            "Brazo F0 no implementado (RESEARCH_PLAN 3). Usar F1 o F2."
        )

    t = len(c5) - 1
    if t < cfg.atr_len + 2:
        return ScanResult(None, "insufficient_5m_history", STATE_NO_SETUP, None, checks)

    # 1) warmup de sesion y VWAP
    vw_t, nbars = vwap_at(c5, t, cfg.session_utc_hour)
    checks["session_bars"] = nbars
    if nbars <= cfg.warmup_bars:
        return ScanResult(None, "session_warmup", STATE_NO_SETUP, None, checks)
    if vw_t is None:
        return ScanResult(None, "no_vwap", STATE_NO_SETUP, None, checks)
    checks["vwap"] = round(vw_t, 6)
    checks["price"] = c5[t].c

    # 2) direccion
    side, dchecks = _direction(c4h, c1h, cfg)
    checks.update(dchecks)
    if side is None:
        return ScanResult(None, "regime_or_align_fail", STATE_NO_SETUP, None, checks)
    checks["side"] = side
    rules = _rules_for(side)

    a_t = atr_at(c5, t, cfg.atr_len)
    if a_t is None or a_t <= 0:
        return ScanResult(None, "no_atr", STATE_NO_SETUP, side, checks)
    checks["atr"] = round(a_t, 6)
    checks["vwap_distance_atr"] = round(abs(c5[t].c - vw_t) / a_t, 4)

    # 3) gap del lado correcto que confirme EXACTAMENTE en t
    best, state = resolve_side(c5, t, side, cfg, rules.confirm, gap_cache)
    if best is None:
        checks["fvg"] = False
        checks["first_retest"] = False
        checks["confirmation"] = False
        return ScanResult(None, "no_setup", state, side, checks)

    g, touch_j = best
    checks.update({
        "fvg": True, "first_retest": True, "vwap_band": True, "confirmation": True,
        "fvg_lo": round(g.lo, 6), "fvg_hi": round(g.hi, 6),
        "fvg_width_atr": round(g.width / a_t, 4),
        "first_retest_bar": touch_j, "gap_age_bars": touch_j - g.i3,
    })

    # 4) momentum. En SHORT es obligatorio y no hay flag que lo apague;
    #    en LONG es el brazo F2 y por defecto solo se REGISTRA, no filtra.
    mchecks, mfail = rules.momentum(c5, t, cfg)
    checks.update(mchecks)
    gate_momentum = (side == SIDE_SHORT) or cfg.require_momentum_long
    if gate_momentum and mfail:
        return ScanResult(None, "momentum_fail:" + mfail,
                          STATE_WAITING_CONFIRMATION, side, checks)

    # 5) niveles. Estructura primero; el objetivo se deriva DESPUES (PRD 8).
    entry = c5[t].c
    stop, struct = rules.structural_stop(c5, g, touch_j, t, a_t, cfg)
    risk = abs(entry - stop)
    if risk <= 0:
        return ScanResult(None, "bad_risk", state, side, checks)
    tp = rules.target(entry, stop, cfg.rr)
    checks["struct_level"] = round(struct, 6)
    checks["stop_pct"] = round(risk / entry, 6)

    # 6) target clearance (PRD 7)
    if cfg.require_target_clearance:
        ok, cr, blocking = target_clearance.check(c1h, c5, t, side, entry, stop, cfg)
        checks["clearance_r"] = round(cr, 4) if cr != float("inf") else 999.0
        checks["clearance_level"] = round(blocking.price, 6) if blocking else None
        checks["clearance_kind"] = blocking.kind if blocking else "none"
        checks["target_clearance"] = ok
        if not ok:
            return ScanResult(None, "target_clearance", state, side, checks)
    else:
        checks["target_clearance"] = True
        checks["clearance_r"] = 999.0

    # 7) cost gate (PRD 12). Nunca se ensancha el stop para pasarlo.
    cfrac = cost_fraction(cfg, side)
    cost_r = cfrac / (risk / entry)
    checks["cost_r"] = round(cost_r, 4)
    checks["cost_frac"] = round(cfrac, 8)
    checks["cost_gate"] = cost_r <= cfg.max_cost_r(side)
    if not checks["cost_gate"]:
        return ScanResult(None, "cost_gate", state, side, checks)

    # 8) A+
    sc = score(side, checks, require_momentum_long=cfg.require_momentum_long)
    if not sc.is_a_plus:
        return ScanResult(None, "not_a_plus:" + ",".join(sc.failed), state, side, checks)

    if side == SIDE_SHORT and not cfg.allow_short:
        raise PolicyViolation(
            "SHORT deshabilitado (ver docs/POLICY_CHANGE_001_SHORTS.md)."
        )

    features = {k: float(v) for k, v in checks.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
    sig = Signal(side=side, entry_ref=entry, stop=stop, tp=tp, rr=cfg.rr,
                 risk_per_unit=risk, cost_r=cost_r, cost_frac=cfrac,
                 clearance_r=float(checks["clearance_r"]), ts=c5[t].ts,
                 checklist=tuple(k for k in CHECKLIST_KEYS if checks.get(k) is True),
                 features=features, score=sc)
    return ScanResult(sig, "ok", STATE_SIGNAL_READY, side, checks)
