"""
strategies/hype/common.py — maquinaria compartida por las dos direcciones.

Aqui vive todo lo que LONG y SHORT hacen igual: la configuracion, el ciclo de
vida de un FVG (primer toque, ventana de confirmacion, invalidacion), la banda
de VWAP y los estados del setup.

Lo que NO vive aqui: las reglas que hacen al SHORT mas estricto que al LONG.
Eso esta en long.py y short.py, deliberadamente separado, para que la asimetria
sea visible y no se "corrija" por simetria (ver AUDIT_001_CONFLICTOS.md C-11).

Principios (PRD 4):
  - Solo velas COMPLETADAS. Nada usa la vela en curso.
  - Funcion pura de la historia: mismas entradas -> mismo resultado, siempre.
  - Fail-closed: condicion no evaluable => no hay señal.
  - Solo stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ..indicators import Candle, atr_at, session_start_ms, vwap_at

STRATEGY_ID = "hype_vwap_fvg_retest_v2"

SIDE_LONG = "LONG"
SIDE_SHORT = "SHORT"

# Estados del setup (PRD 10). El estado del DIA lo lleva risk/limits.py;
# esto es el estado de la oportunidad que el motor esta observando.
STATE_NO_SETUP = "NO_SETUP"
STATE_WAITING_RETEST = "WAITING_FOR_RETEST"
STATE_WAITING_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
STATE_LONG_CANDIDATE = "LONG_CANDIDATE"
STATE_SHORT_CANDIDATE = "SHORT_CANDIDATE"
# A_PLUS_READY NO lo emite el motor de estrategia: exige gates operativos
# (sizing, limite diario, kill switches) que solo conoce la capa de riesgo.
STATE_A_PLUS_READY = "A_PLUS_READY"


def STATE_CANDIDATE(side: str) -> str:
    return STATE_LONG_CANDIDATE if side == SIDE_LONG else STATE_SHORT_CANDIDATE

# Checklist en el orden en que la pinta la Signal Card (PRD 16).
CHECKLIST_KEYS = ("regime_4h", "align_1h", "fvg", "first_retest", "vwap_band",
                  "confirmation", "rsi", "macd", "volume", "vwap_slope",
                  "target_clearance", "cost_gate")


class PolicyViolation(Exception):
    """Un Plan intento salir con una direccion no autorizada por configuracion."""


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Configuracion congelada de la estrategia v2.

    Los campos marcados [OPT] son los UNICOS que entran a la rejilla de
    sensibilidad (HYPE_TRADING_RESEARCH_PLAN 2.4). Los marcados [PROD] son
    decisiones de producto de Mark: se declaran y no se buscan. El resto es
    convencion fija y no se toca.
    """

    # --- politica de direccion (PRD 2) ---
    allow_long: bool = True
    allow_short: bool = False       # hay que pedirlo explicitamente

    # --- regimen 4H / alineacion 1H ---
    ema_fast: int = 20
    ema_slow: int = 50
    slope_lookback: int = 3
    short_htf_rsi_max: float = 45.0     # solo SHORT (PRD 6.1)

    # --- sesion / VWAP ---
    session_utc_hour: int = 0           # reset 00:00 UTC = 19:00 Panama
    warmup_bars: int = 12

    # --- setup ---
    vwap_band_atr: float = 0.30         # [OPT]
    fvg_max_age_bars: int = 12          # [OPT]
    confirm_window: int = 3             # vela del toque + 2 siguientes
    require_fvg: bool = True            # F0 lo apaga (retest de VWAP sin FVG)

    # --- stop / objetivo (PRD 3, 8) ---
    atr_len: int = 20
    stop_buffer_atr: float = 0.10       # [OPT]
    rr: float = 1.6                     # E2, candidato principal. NO se optimiza:
                                        # se prueba como brazos discretos 1.0/1.6/2.0

    # --- target clearance (PRD 7) ---
    require_target_clearance: bool = True
    clearance_pivot_n: int = 2          # pivote 1H de n barras a cada lado
    clearance_lookback_1h: int = 48     # [OPT] cuantas velas 1H se miran hacia atras
    clearance_use_prev_day: bool = True

    # --- costos (PRD 12) ---
    fee_taker: float = 0.00045          # 4.5 bps/lado — CONFIRMAR el tier real
    spread_rt: float = 0.0002
    slippage_rt: float = 0.0002
    max_cost_r_long: float = 0.15       # [PROD] parametro de investigacion, no dogma
    max_cost_r_short: float = 0.12      # [PROD]
    # Funding: la tasa la reporta el venue con su signo (positiva = los largos
    # pagan). expected_hours en 0 lo deja fuera del gate; el backtester completo
    # lo contabiliza por hora de tenencia REAL (AUDIT C-04).
    funding_rate_hourly: float = 0.0
    funding_expected_hours: float = 0.0

    # --- riesgo (PRD 9) ---
    risk_mode: str = "pct_equity"       # "pct_equity" | "fixed_usd"
    risk_pct: float = 0.0025            # research
    risk_usd: float = 125.0             # [PROD] produccion; TINY usa 1.00
    qty_decimals: int = 2

    # --- ejecucion (PRD 13) ---
    signal_ttl_seconds: int = 90
    max_drift_r: float = 0.10
    max_hold_hours: int = 24            # [PROD]
    max_trades_per_day: int = 1         # [PROD]

    # --- momentum ---
    # En LONG es el brazo F2 y por defecto esta APAGADO: debe ganarse su lugar
    # contra F1 (RESEARCH_PLAN 2.2). En SHORT es parte de la definicion del
    # setup y short.py lo exige siempre, ignorando este flag.
    require_momentum_long: bool = False
    rsi_len: int = 14
    rsi_long_min: float = 50.0
    rsi_long_max: float = 68.0
    rsi_short_min: float = 32.0
    rsi_short_max: float = 48.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    vol_avg_len: int = 20
    vol_mult_long: float = 1.00
    vol_mult_short: float = 1.20        # PRD 6.4
    require_vwap_slope_long: bool = False    # parte de F2
    require_vwap_slope_short: bool = True    # obligatorio en SHORT

    def max_cost_r(self, side: str) -> float:
        return self.max_cost_r_long if side == SIDE_LONG else self.max_cost_r_short

    @property
    def max_hold_bars_5m(self) -> int:
        return self.max_hold_hours * 12


# --- perfiles ---------------------------------------------------------------

def tiny_config(allow_short: bool = False) -> Config:
    """TINY (PRD 9.2): tope duro de $1 de perdida planificada por trade."""
    return Config(allow_short=allow_short, risk_mode="fixed_usd", risk_usd=1.00)


def production_config(allow_short: bool = False, risk_usd: float = 125.0) -> Config:
    """Produccion (PRD 9.3): $100-150, default $125. Hoy inalcanzable:
    LIVE_EXECUTION=false."""
    if not (100.0 <= risk_usd <= 150.0):
        raise ValueError(
            f"risk_usd={risk_usd} fuera del rango de produccion $100-$150 (PRD 9.3)."
        )
    return Config(allow_short=allow_short, risk_mode="fixed_usd", risk_usd=risk_usd)


def research_config(filter_arm: str = "F1", exit_arm: str = "E2",
                    allow_short: bool = False, **kw) -> Config:
    """Brazo de la matriz predeclarada (RESEARCH_PLAN 3).

    FILTER: F0 (retest de VWAP sin FVG) | F1 (+FVG, base) | F2 (+momentum)
    EXIT:   E1 (1.0R, control) | E2 (1.6R, principal) | E3 (2.0R)
    """
    rr_by_arm = {"E1": 1.0, "E2": 1.6, "E3": 2.0}
    if filter_arm not in ("F0", "F1", "F2"):
        raise ValueError(f"brazo FILTER desconocido: {filter_arm!r}")
    if exit_arm not in rr_by_arm:
        raise ValueError(f"brazo EXIT desconocido: {exit_arm!r} (E4 no implementado)")
    return Config(
        allow_short=allow_short,
        require_fvg=(filter_arm != "F0"),
        require_momentum_long=(filter_arm == "F2"),
        require_vwap_slope_long=(filter_arm == "F2"),
        rr=rr_by_arm[exit_arm],
        risk_mode="pct_equity",
        **kw,
    )


# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FVG:
    """Fair value gap de 3 velas. `lo` < `hi` SIEMPRE, sea cual sea el lado.

    BULL: low de la 3a > high de la 1a -> zona (high_1a, low_3a), precio ENCIMA
    BEAR: high de la 3a < low de la 1a -> zona (high_3a, low_1a), precio DEBAJO
    """
    i3: int
    lo: float
    hi: float
    side: str

    @property
    def width(self) -> float:
        return self.hi - self.lo


@dataclass(frozen=True)
class Setup:
    """Un setup confirmado, antes de aplicarle riesgo y costos."""
    side: str
    gap: Optional[FVG]
    touch_idx: int
    confirm_idx: int
    entry_ref: float        # cierre de la vela de confirmacion: REFERENCIA, no fill
    stop: float
    atr: float
    vwap: float


# ---------------------------------------------------------------------------
# Ciclo de vida del gap
# ---------------------------------------------------------------------------

def touched(bar: Candle, g: FVG) -> bool:
    """Contacto con la zona del gap."""
    return bar.l <= g.hi if g.side == SIDE_LONG else bar.h >= g.lo


def invalidated(bar: Candle, g: FVG) -> bool:
    """Cierre a traves del lado LEJANO: el gap muere para siempre."""
    return bar.c < g.lo if g.side == SIDE_LONG else bar.c > g.hi


def band_overlaps(g: FVG, vwap: float, band: float) -> bool:
    """La zona del gap solapa VWAP +- band (PRD 5.3 / 6.3)."""
    return g.lo <= vwap + band and g.hi >= vwap - band


ConfirmFn = Callable[[List[Candle], int, FVG, float], bool]


def gap_resolution(c5: List[Candle], g: FVG, t: int, cfg: Config,
                   confirm_fn: ConfirmFn):
    """Recorre las velas posteriores a la formacion del gap hasta t inclusive.

    Devuelve (estado, k, touch_j):
      ("confirmed", k, touch_j)  confirmacion en k, primer retest en touch_j
      ("dead", motivo, None)     gap consumido; no vuelve a operarse NUNCA
      ("waiting", None, touch_j) sin resolucion aun (touch_j puede ser None)

    El estado "dead" es terminal por diseño: implementa "primer retest
    unicamente" y "gap invalidado no revive".
    """
    touch_j: Optional[int] = None
    for j in range(g.i3 + 1, t + 1):
        bar = c5[j]
        if invalidated(bar, g):
            return ("dead", "closed_through_gap", None)
        if touch_j is None:
            if touched(bar, g):
                if j - g.i3 > cfg.fvg_max_age_bars:
                    return ("dead", "stale_gap", None)
                vw, _ = vwap_at(c5, j, cfg.session_utc_hour)
                a = atr_at(c5, j, cfg.atr_len)
                if vw is None or a is None:
                    return ("dead", "no_vwap_or_atr", None)
                if not band_overlaps(g, vw, cfg.vwap_band_atr * a):
                    return ("dead", "not_near_vwap", None)
                touch_j = j
                if confirm_fn(c5, j, g, vw):
                    return ("confirmed", j, touch_j)
        else:
            if j - touch_j > cfg.confirm_window - 1:
                return ("dead", "no_confirmation", None)
            vw, _ = vwap_at(c5, j, cfg.session_utc_hour)
            if vw is not None and confirm_fn(c5, j, g, vw):
                return ("confirmed", j, touch_j)
    if touch_j is not None and t - touch_j >= cfg.confirm_window - 1:
        return ("dead", "no_confirmation", None)
    return ("waiting", None, touch_j)


def find_session_gaps(c5: List[Candle], t: int, hour: int, side: str) -> List[FVG]:
    """FVGs del lado pedido cuya vela de formacion pertenece a la sesion vigente."""
    start = session_start_ms(c5[t].ts, hour)
    gaps: List[FVG] = []
    for i in range(2, t + 1):
        if c5[i].ts < start:
            continue
        if side == SIDE_LONG:
            if c5[i].l > c5[i - 2].h:
                gaps.append(FVG(i3=i, lo=c5[i - 2].h, hi=c5[i].l, side=SIDE_LONG))
        else:
            if c5[i].h < c5[i - 2].l:
                gaps.append(FVG(i3=i, lo=c5[i].h, hi=c5[i - 2].l, side=SIDE_SHORT))
    return gaps


def resolve_side(c5: List[Candle], t: int, side: str, cfg: Config,
                 confirm_fn: ConfirmFn,
                 gap_cache: Optional[dict] = None
                 ) -> Tuple[Optional[Tuple[FVG, int]], str]:
    """Busca un gap del lado dado que confirme EXACTAMENTE en t.

    Devuelve ((gap, touch_idx) | None, estado_del_setup). El estado sirve al
    dashboard aunque no haya señal: distingue "no hay nada" de "hay un gap
    esperando retest" y de "hubo retest, falta confirmacion".
    """
    best: Optional[Tuple[FVG, int]] = None
    state = STATE_NO_SETUP
    for g in find_session_gaps(c5, t, cfg.session_utc_hour, side):
        key = (side, c5[g.i3].ts, g.lo, g.hi)   # invariante al slicing
        if gap_cache is not None and gap_cache.get(key) == "dead":
            continue
        res = gap_resolution(c5, g, t, cfg, confirm_fn)
        if gap_cache is not None:
            if res[0] == "dead":
                gap_cache[key] = "dead"
            elif res[0] == "confirmed" and res[1] < t:
                gap_cache[key] = "dead"          # ya se resolvio en el pasado
        if res[0] == "waiting":
            if res[2] is not None:
                state = STATE_WAITING_CONFIRMATION
            elif state == STATE_NO_SETUP:
                state = STATE_WAITING_RETEST
        if res[0] == "confirmed" and res[1] == t:
            best = (g, res[2])                   # si varios confirman en t, el mas reciente
    return best, state


# ---------------------------------------------------------------------------
# Costos (PRD 12)
# ---------------------------------------------------------------------------

def cost_fraction(cfg: Config, side: str) -> float:
    """Costo de ida y vuelta como fraccion del nocional.

    Fees por lado x2 + spread + slippage + funding esperado por la tenencia.
    El funding lleva signo: con tasa positiva el LARGO paga y el CORTO cobra,
    asi que para el corto es un credito. Con max_hold de 24 h esto ya no es
    ruido despreciable (AUDIT C-04).
    """
    base = 2.0 * cfg.fee_taker + cfg.spread_rt + cfg.slippage_rt
    fund = cfg.funding_rate_hourly * cfg.funding_expected_hours
    fund = fund if side == SIDE_LONG else -fund
    return max(0.0, base + fund)
