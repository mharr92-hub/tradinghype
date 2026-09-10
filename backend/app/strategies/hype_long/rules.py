"""
hype_long_vwap_retest.py — HYPE LONG · "Primer retest VWAP/FVG en tendencia" · v0.1 (2026-09-09)

Implementación de referencia de la estrategia perfeccionada a partir de la guía
externa "HYPE Copilot", depurada según MULTI_STRATEGY_MASTER_PLAN.md.

POLÍTICA (inmutable, ver MULTI_STRATEGY_MASTER_PLAN §0): HYPE es LONG ONLY.
Este módulo no contiene ningún camino de código que produzca otra dirección.
No es una opción de configuración: la dirección contraria no existe aquí.

Principios de diseño:
  - Determinista sobre velas COMPLETADAS. Nada se calcula con la vela en curso.
  - Función pura de la historia: scan(c4h, c1h, c5, cfg, equity) devuelve lo
    mismo en backtest, paper y live. Mismo código en los tres.
  - Fail-closed: cualquier condición no evaluable => no hay señal.
  - Solo stdlib. Sin dependencias.

Destino en el repo: strategies/hype_long/rules.py (renombrar al integrar).
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

STRATEGY_ID = "hype_long_vwap_retest_v1"
SIDE_LONG = "LONG"  # única dirección que existe en este módulo

BAR_5M_MS = 5 * 60 * 1000


class PolicyViolation(Exception):
    """Se lanza si algo intenta producir una dirección distinta de LONG."""


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candle:
    ts: int   # epoch ms, apertura de la vela (UTC)
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass(frozen=True)
class Config:
    # --- régimen (4H) y alineación (1H) ---
    ema_fast: int = 20
    ema_slow: int = 50
    slope_lookback: int = 3          # EMA50 4H > su valor 3 barras atrás

    # --- sesión / VWAP ---
    session_utc_hour: int = 0        # reset 00:00 UTC = 19:00 Panamá
    warmup_bars: int = 12            # sin señales en la 1ª hora de la sesión

    # --- setup FVG + VWAP ---
    vwap_band_atr: float = 0.30      # [SENSIBILIDAD] zona del gap debe solapar VWAP ± k·ATR
    fvg_max_age_bars: int = 12       # [SENSIBILIDAD] edad máxima del gap al primer retest (1h)
    confirm_window: int = 3          # vela del retest + 2 siguientes

    # --- stop / objetivo ---
    atr_len: int = 20
    stop_buffer_atr: float = 0.10    # [SENSIBILIDAD] colchón bajo la estructura
    rr: float = 1.0                  # E1 base; E2 usa 2.0

    # --- costos (fracciones del nocional, ida y vuelta salvo fee que es por lado) ---
    fee_taker: float = 0.00045       # 4.5 bps por lado (confirmar tier real)
    spread_rt: float = 0.0002        # estimado ida+vuelta
    slippage_rt: float = 0.0002      # estimado ida+vuelta
    max_cost_r: float = 0.15         # [SENSIBILIDAD] filtro: costo total <= 0.15R

    # --- sizing ---
    risk_pct: float = 0.0025         # 0.25 % del equity por trade
    qty_decimals: int = 2

    # --- variante F2 (momentum), apagada en la versión base ---
    require_momentum: bool = False
    rsi_len: int = 14
    rsi_min: float = 50.0
    rsi_max: float = 68.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    vol_avg_len: int = 20


@dataclass(frozen=True)
class FVG:
    """Fair value gap alcista: low de la 3ª vela > high de la 1ª."""
    i3: int      # índice de la vela que completa el patrón
    lo: float    # límite inferior del gap (high de la 1ª vela)
    hi: float    # límite superior del gap (low de la 3ª vela)


@dataclass(frozen=True)
class Plan:
    side: str
    entry: float
    stop: float
    tp: float
    qty: float
    cost_r: float
    ts: int                 # apertura de la vela de confirmación
    strategy_id: str = STRATEGY_ID
    checklist: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class ScanResult:
    plan: Optional[Plan]
    reason: str
    checks: dict


def _assert_long_only(plan: Plan) -> None:
    if plan.side != SIDE_LONG:
        raise PolicyViolation(
            f"Direccion {plan.side!r} prohibida: HYPE es LONG ONLY por politica."
        )


# ---------------------------------------------------------------------------
# Indicadores (stdlib, deterministas)
# ---------------------------------------------------------------------------

def ema_series(vals: List[float], n: int) -> List[float]:
    """EMA sembrada con el primer valor. Requiere warmup: no usar los primeros ~n valores."""
    if not vals:
        return []
    k = 2.0 / (n + 1.0)
    out: List[float] = []
    e = vals[0]
    for i, v in enumerate(vals):
        e = v if i == 0 else v * k + e * (1.0 - k)
        out.append(e)
    return out


def rsi_series(vals: List[float], n: int = 14) -> List[Optional[float]]:
    """RSI de Wilder."""
    out: List[Optional[float]] = [None] * len(vals)
    if len(vals) < n + 1:
        return out
    ag = al = 0.0
    for i in range(1, len(vals)):
        ch = vals[i] - vals[i - 1]
        g, lo = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            ag += g
            al += lo
            if i == n:
                ag /= n
                al /= n
                out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
        else:
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + lo) / n
            out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def macd_hist_series(vals: List[float], fast: int = 12, slow: int = 26,
                     sig: int = 9) -> List[float]:
    ef = ema_series(vals, fast)
    es = ema_series(vals, slow)
    line = [a - b for a, b in zip(ef, es)]
    signal = ema_series(line, sig)
    return [m - s for m, s in zip(line, signal)]


def atr_at(candles: List[Candle], idx: int, n: int) -> Optional[float]:
    """ATR (media simple de True Range) con velas hasta idx inclusive."""
    if idx < n:  # necesita n TRs, cada uno con vela previa
        return None
    trs = []
    for i in range(idx - n + 1, idx + 1):
        pc = candles[i - 1].c
        trs.append(max(candles[i].h - candles[i].l,
                       abs(candles[i].h - pc),
                       abs(candles[i].l - pc)))
    return sum(trs) / n


def session_start_ms(ts_ms: int, hour: int) -> int:
    t = _dt.datetime.fromtimestamp(ts_ms / 1000.0, _dt.timezone.utc)
    s = t.replace(hour=hour, minute=0, second=0, microsecond=0)
    if t < s:
        s -= _dt.timedelta(days=1)
    return int(s.timestamp() * 1000)


def vwap_at(c5: List[Candle], idx: int, hour: int) -> Tuple[Optional[float], int]:
    """VWAP de sesión (precio típico ponderado por volumen) hasta idx inclusive.
    Devuelve (vwap, nº de velas de la sesión)."""
    start = session_start_ms(c5[idx].ts, hour)
    i0 = idx
    while i0 > 0 and c5[i0 - 1].ts >= start:
        i0 -= 1
    num = den = 0.0
    for i in range(i0, idx + 1):
        cd = c5[i]
        tp = (cd.h + cd.l + cd.c) / 3.0
        num += tp * cd.v
        den += cd.v
    nbars = idx - i0 + 1
    return (num / den if den > 0 else None), nbars


# ---------------------------------------------------------------------------
# Régimen y alineación
# ---------------------------------------------------------------------------

def regime_4h_ok(c4h: List[Candle], cfg: Config) -> bool:
    need = cfg.ema_slow + cfg.slope_lookback + 2
    if len(c4h) < need:
        return False
    closes = [x.c for x in c4h]
    e_fast = ema_series(closes, cfg.ema_fast)
    e_slow = ema_series(closes, cfg.ema_slow)
    return (closes[-1] > e_fast[-1] > e_slow[-1]
            and e_slow[-1] > e_slow[-1 - cfg.slope_lookback])


def align_1h_ok(c1h: List[Candle], cfg: Config) -> bool:
    if len(c1h) < cfg.ema_slow + 2:
        return False
    closes = [x.c for x in c1h]
    e_fast = ema_series(closes, cfg.ema_fast)
    e_slow = ema_series(closes, cfg.ema_slow)
    return closes[-1] > e_fast[-1] > e_slow[-1]


# ---------------------------------------------------------------------------
# Ciclo de vida del gap: primer retest, confirmación, invalidación
# ---------------------------------------------------------------------------

def _is_confirmation(c5: List[Candle], k: int, g: FVG, vwap_k: float) -> bool:
    if k < 1:
        return False
    bar = c5[k]
    return (bar.c > max(g.hi, vwap_k)
            and bar.c > c5[k - 1].h
            and bar.c > bar.o)


def gap_resolution(c5: List[Candle], g: FVG, t: int, cfg: Config):
    """Recorre las velas posteriores a la formación del gap hasta t (inclusive).

    Devuelve una tupla cuyo primer elemento es el estado:
      ('confirmed', k, touch_j)  confirmación en la vela k (primer retest en touch_j)
      ('dead', motivo, None)     gap consumido: no vuelve a operarse
      ('waiting', None, touch_j) aún sin resolución
    """
    touch_j: Optional[int] = None
    for j in range(g.i3 + 1, t + 1):
        bar = c5[j]
        if bar.c < g.lo:                       # cierre a través del lado lejano
            return ("dead", "closed_below_gap", None)
        if touch_j is None:
            if bar.l <= g.hi:                  # primer toque de la zona
                if j - g.i3 > cfg.fvg_max_age_bars:
                    return ("dead", "stale_gap", None)
                vw, _ = vwap_at(c5, j, cfg.session_utc_hour)
                a = atr_at(c5, j, cfg.atr_len)
                if vw is None or a is None:
                    return ("dead", "no_vwap_or_atr", None)
                band = cfg.vwap_band_atr * a
                # la zona del gap debe solapar la banda VWAP ± band
                if not (g.lo <= vw + band and g.hi >= vw - band):
                    return ("dead", "not_near_vwap", None)
                touch_j = j
                if _is_confirmation(c5, j, g, vw):
                    return ("confirmed", j, touch_j)
        else:
            if j - touch_j > cfg.confirm_window - 1:
                return ("dead", "no_confirmation", None)
            vw, _ = vwap_at(c5, j, cfg.session_utc_hour)
            if vw is not None and _is_confirmation(c5, j, g, vw):
                return ("confirmed", j, touch_j)
    if touch_j is not None and t - touch_j >= cfg.confirm_window - 1:
        # ventana agotada sin confirmar: el primer retest falló, gap consumido
        return ("dead", "no_confirmation", None)
    return ("waiting", None, touch_j)


def find_session_gaps(c5: List[Candle], t: int, hour: int) -> List[FVG]:
    """FVGs alcistas cuya vela de formación pertenece a la sesión vigente."""
    start = session_start_ms(c5[t].ts, hour)
    gaps: List[FVG] = []
    for i in range(2, t + 1):
        if c5[i].ts < start:
            continue
        if c5[i].l > c5[i - 2].h:
            gaps.append(FVG(i3=i, lo=c5[i - 2].h, hi=c5[i].l))
    return gaps


# ---------------------------------------------------------------------------
# Momentum (variante F2, apagada por defecto)
# ---------------------------------------------------------------------------

def momentum_ok(c5: List[Candle], t: int, cfg: Config) -> Tuple[bool, str]:
    closes = [x.c for x in c5]
    rsi = rsi_series(closes, cfg.rsi_len)
    if t < 1 or rsi[t] is None or rsi[t - 1] is None:
        return False, "rsi_warmup"
    if not (cfg.rsi_min <= rsi[t] <= cfg.rsi_max and rsi[t] > rsi[t - 1]):
        return False, "rsi"
    hist = macd_hist_series(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    if t < 2 or not (hist[t] > hist[t - 1] > hist[t - 2]):
        return False, "macd"
    vw_t, _ = vwap_at(c5, t, cfg.session_utc_hour)
    vw_p, _ = vwap_at(c5, t - 1, cfg.session_utc_hour)
    if not (vw_t is not None and vw_p is not None and vw_t > vw_p):
        return False, "vwap_dir"
    if t < cfg.vol_avg_len:
        return False, "vol_warmup"
    avg_v = sum(x.v for x in c5[t - cfg.vol_avg_len:t]) / float(cfg.vol_avg_len)
    if c5[t].v < avg_v:
        return False, "volume"
    return True, ""


# ---------------------------------------------------------------------------
# Escaneo principal
# ---------------------------------------------------------------------------

def scan(c4h: List[Candle], c1h: List[Candle], c5: List[Candle],
         cfg: Config = Config(), equity: float = 1000.0,
         gap_cache: Optional[dict] = None) -> ScanResult:
    """Evalúa la última vela COMPLETADA de 5m. Devuelve un Plan long o el motivo
    del rechazo. Todas las listas deben contener solo velas cerradas.

    gap_cache (opcional, para backtests): memoiza gaps en estado TERMINAL
    (muertos o ya confirmados en el pasado). Los estados terminales no cambian
    con más historia, así que el resultado es idéntico con o sin cache
    (hay un test que lo verifica)."""
    checks: dict = {}
    t = len(c5) - 1
    if t < cfg.atr_len + 2:
        return ScanResult(None, "insufficient_5m_history", checks)

    # 1) warmup de sesión
    vw_t, nbars = vwap_at(c5, t, cfg.session_utc_hour)
    checks["session_bars"] = nbars
    if nbars <= cfg.warmup_bars:
        return ScanResult(None, "session_warmup", checks)
    if vw_t is None:
        return ScanResult(None, "no_vwap", checks)

    # 2) régimen 4H
    checks["regime_4h"] = regime_4h_ok(c4h, cfg)
    if not checks["regime_4h"]:
        return ScanResult(None, "regime_4h_fail", checks)

    # 3) alineación 1H
    checks["align_1h"] = align_1h_ok(c1h, cfg)
    if not checks["align_1h"]:
        return ScanResult(None, "align_1h_fail", checks)

    a_t = atr_at(c5, t, cfg.atr_len)
    if a_t is None or a_t <= 0:
        return ScanResult(None, "no_atr", checks)

    # 4) gaps de la sesión: buscamos uno que confirme EXACTAMENTE en t
    best: Optional[Tuple[FVG, int]] = None
    for g in find_session_gaps(c5, t, cfg.session_utc_hour):
        key = (c5[g.i3].ts, g.lo, g.hi)  # clave invariante a slicing
        if gap_cache is not None and gap_cache.get(key) == "dead":
            continue
        state = gap_resolution(c5, g, t, cfg)
        if gap_cache is not None:
            if state[0] == "dead":
                gap_cache[key] = "dead"
            elif state[0] == "confirmed" and state[1] < t:
                gap_cache[key] = "dead"  # ya se operó (o se dejó pasar) antes
        if state[0] == "confirmed" and state[1] == t:
            best = (g, state[2])  # si varios confirman en t, manda el más reciente
    if best is None:
        return ScanResult(None, "no_setup", checks)
    g, touch_j = best
    checks["fvg"] = (round(g.lo, 6), round(g.hi, 6))
    checks["first_retest_bar"] = touch_j

    # 5) momentum (solo variante F2)
    if cfg.require_momentum:
        ok, why = momentum_ok(c5, t, cfg)
        checks["momentum"] = ok
        if not ok:
            return ScanResult(None, "momentum_fail:" + why, checks)

    # 6) niveles
    entry = c5[t].c
    low_seg = min(min(c5[k].l for k in range(touch_j, t + 1)), g.lo)
    stop = low_seg - cfg.stop_buffer_atr * a_t
    risk = entry - stop
    if risk <= 0:
        return ScanResult(None, "bad_risk", checks)
    tp = entry + cfg.rr * risk

    # 7) filtro de costos en R (fees ida+vuelta + spread + slippage; SIN funding,
    #    el funding se contabiliza en el backtest completo por hora de tenencia)
    cost_frac = 2.0 * cfg.fee_taker + cfg.spread_rt + cfg.slippage_rt
    stop_pct = risk / entry
    cost_r = cost_frac / stop_pct
    checks["cost_r"] = round(cost_r, 4)
    if cost_r > cfg.max_cost_r:
        return ScanResult(None, "cost_gate", checks)

    # 8) sizing por riesgo (incluye el costo de un trade perdedor)
    lose_cost_unit = cost_frac * entry
    qty = (equity * cfg.risk_pct) / (risk + lose_cost_unit)
    qty = math.floor(qty * (10 ** cfg.qty_decimals)) / (10 ** cfg.qty_decimals)
    if qty <= 0:
        return ScanResult(None, "qty_zero", checks)

    plan = Plan(side=SIDE_LONG, entry=entry, stop=stop, tp=tp, qty=qty,
                cost_r=cost_r, ts=c5[t].ts,
                checklist=tuple(sorted(k for k, val in checks.items()
                                       if val is True)))
    _assert_long_only(plan)
    return ScanResult(plan, "ok", checks)
