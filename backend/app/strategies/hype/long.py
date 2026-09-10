"""
strategies/hype/long.py — rama LONG (PRD 5).

"Trend-aligned first VWAP/FVG retest". Es la rama con evidencia mecanica mas
solida y la que conserva la preferencia estructural del producto, pero sigue
siendo UNPROVEN en nuestros datos (RESEARCH_PLAN 1).

Momentum: aqui es el brazo F2 y esta APAGADO por defecto. RSI y MACD derivan
del precio; deben ganarse su lugar contra F1 antes de entrar en la definicion
base. En short.py es al reves, y esa asimetria es deliberada (AUDIT C-11).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..indicators import (Candle, avg_volume, ema_series, macd_hist_series,
                          rsi_series, vwap_at)
from .common import SIDE_LONG, Config, FVG

SIDE = SIDE_LONG


def regime_ok(c4h: List[Candle], cfg: Config) -> bool:
    """Regimen 4H alcista (PRD 5.1): close > EMA20 > EMA50 y EMA50 subiendo."""
    if len(c4h) < cfg.ema_slow + cfg.slope_lookback + 2:
        return False
    closes = [x.c for x in c4h]
    ef = ema_series(closes, cfg.ema_fast)
    es = ema_series(closes, cfg.ema_slow)
    return (closes[-1] > ef[-1] > es[-1]
            and es[-1] > es[-1 - cfg.slope_lookback])


def align_ok(c1h: List[Candle], cfg: Config) -> bool:
    """Alineacion 1H (PRD 5.2). Sin filtros extra: el LONG usa reglas normales."""
    if len(c1h) < cfg.ema_slow + 2:
        return False
    closes = [x.c for x in c1h]
    ef = ema_series(closes, cfg.ema_fast)
    es = ema_series(closes, cfg.ema_slow)
    return closes[-1] > ef[-1] > es[-1]


def confirm(c5: List[Candle], k: int, g: FVG, vwap_k: float) -> bool:
    """Vela de confirmacion alcista (PRD 5.4)."""
    if k < 1:
        return False
    bar = c5[k]
    return (bar.c > max(g.hi, vwap_k)
            and bar.c > c5[k - 1].h
            and bar.c > bar.o)


def momentum(c5: List[Candle], t: int, cfg: Config
             ) -> Tuple[Dict[str, object], Optional[str]]:
    """Brazo F2 (PRD 5.5). Devuelve (checks, motivo_de_fallo).

    Los checks se devuelven SIEMPRE, pasen o no, para que el journal registre
    los valores incluso cuando el gate esta apagado. Sin eso nunca se podria
    comparar F1 contra F2 a posteriori sobre las mismas señales.
    """
    checks: Dict[str, object] = {"rsi": False, "macd": False, "volume": False}
    closes = [x.c for x in c5]
    fail: Optional[str] = None

    rsi = rsi_series(closes, cfg.rsi_len)
    if t >= 1 and rsi[t] is not None and rsi[t - 1] is not None:
        checks["rsi_value"] = round(rsi[t], 3)
        checks["rsi_slope"] = round(rsi[t] - rsi[t - 1], 4)
        checks["rsi"] = (cfg.rsi_long_min <= rsi[t] <= cfg.rsi_long_max
                         and rsi[t] > rsi[t - 1])
    if not checks["rsi"]:
        fail = fail or "rsi"

    hist = macd_hist_series(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    if t >= 2:
        checks["macd_hist"] = round(hist[t], 8)
        checks["macd_slope"] = round(hist[t] - hist[t - 1], 8)
        checks["macd"] = hist[t] > hist[t - 1] > hist[t - 2]
    if not checks["macd"]:
        fail = fail or "macd"

    avg_v = avg_volume(c5, t, cfg.vol_avg_len)
    if avg_v is not None and avg_v > 0:
        checks["volume_ratio"] = round(c5[t].v / avg_v, 4)
        checks["volume"] = c5[t].v >= cfg.vol_mult_long * avg_v
    if not checks["volume"]:
        fail = fail or "volume"

    if cfg.require_vwap_slope_long:
        ok = False
        if t >= 1:
            vw_t, _ = vwap_at(c5, t, cfg.session_utc_hour)
            vw_p, _ = vwap_at(c5, t - 1, cfg.session_utc_hour)
            ok = vw_t is not None and vw_p is not None and vw_t > vw_p
        checks["vwap_slope"] = ok
        if not ok:
            fail = fail or "vwap_slope"

    return checks, fail


def structural_stop(c5: List[Candle], g: FVG, touch_j: int, t: int,
                    atr: float, cfg: Config) -> Tuple[float, float]:
    """Stop estructural LONG (PRD 8). Devuelve (stop, nivel_estructural).

    min(lado inferior del FVG, low del tramo retest->confirmacion) - buffer*ATR.

    NO depende del objetivo. Cambiar `rr` no puede mover este numero: primero
    estructura, despues R:R. El test 32 del TEST_PLAN lo blinda.
    """
    struct = min(min(c5[k].l for k in range(touch_j, t + 1)), g.lo)
    return struct - cfg.stop_buffer_atr * atr, struct


def target(entry: float, stop: float, rr: float) -> float:
    return entry + rr * (entry - stop)
