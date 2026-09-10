"""
strategies/hype/short.py — rama SHORT ESTRICTA (PRD 6).

Existe por POLICY_CHANGE_001_SHORTS.md: la politica "HYPE = LONG ONLY" fue
revocada por decision explicita de Mark. No hereda ni una unidad de evidencia
de la rama long: se mide con sus propios controles, su propio N y sus propios
gates (RESEARCH_PLAN 1, hipotesis H-R2).

Este modulo NO es un espejo de long.py. Es deliberadamente mas dificil de
disparar, en cuatro puntos:

  1. El regimen 4H exige ademas RSI(14) <= 45: no basta con EMAs bajistas,
     el HTF tiene que estar debilitado.
  2. La alineacion 1H exige MACD hist < 0 Y descendiendo: el MTF tiene que
     estar acelerando a la baja, no simplemente estar abajo.
  3. El momentum NO es opcional. En long.py es el brazo F2 y se puede apagar;
     aqui forma parte de la definicion del setup y no hay flag que lo quite.
  4. El volumen exige 1.20x la media (vs 1.00x en long) y el VWAP debe estar
     descendiendo.

Una sola condicion fallida => NO TRADE. No existe el short "casi bueno".
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..indicators import (Candle, avg_volume, ema_series, macd_hist_series,
                          rsi_series, vwap_at)
from .common import SIDE_SHORT, Config, FVG

SIDE = SIDE_SHORT


def regime_ok(c4h: List[Candle], cfg: Config) -> bool:
    """Regimen 4H bajista (PRD 6.1). TODAS obligatorias, incluido RSI <= 45."""
    if len(c4h) < cfg.ema_slow + cfg.slope_lookback + 2:
        return False
    closes = [x.c for x in c4h]
    ef = ema_series(closes, cfg.ema_fast)
    es = ema_series(closes, cfg.ema_slow)
    if not (closes[-1] < ef[-1] < es[-1]
            and es[-1] < es[-1 - cfg.slope_lookback]):
        return False
    rsi = rsi_series(closes, cfg.rsi_len)
    if rsi[-1] is None:                       # fail-closed: sin RSI no hay short
        return False
    return rsi[-1] <= cfg.short_htf_rsi_max


def align_ok(c1h: List[Candle], cfg: Config) -> bool:
    """Alineacion 1H bajista (PRD 6.2). TODAS obligatorias."""
    if len(c1h) < cfg.ema_slow + 2:
        return False
    closes = [x.c for x in c1h]
    ef = ema_series(closes, cfg.ema_fast)
    es = ema_series(closes, cfg.ema_slow)
    if not (closes[-1] < ef[-1] < es[-1]):
        return False
    hist = macd_hist_series(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    if len(hist) < 2:
        return False
    return hist[-1] < 0 and hist[-1] < hist[-2]


def confirm(c5: List[Candle], k: int, g: FVG, vwap_k: float) -> bool:
    """Vela de confirmacion bajista (PRD 6.4, parte estructural)."""
    if k < 1:
        return False
    bar = c5[k]
    return (bar.c < min(g.lo, vwap_k)
            and bar.c < c5[k - 1].l
            and bar.c < bar.o)


def momentum(c5: List[Candle], t: int, cfg: Config
             ) -> Tuple[Dict[str, object], Optional[str]]:
    """Momentum SHORT (PRD 6.4). OBLIGATORIO: el motor no puede saltarselo.

    A diferencia de long.momentum, aqui no hay un flag `require_*` que consultar.
    Si esta funcion devuelve un motivo de fallo, no hay señal.
    """
    checks: Dict[str, object] = {"rsi": False, "macd": False, "volume": False,
                                 "vwap_slope": False}
    closes = [x.c for x in c5]
    fail: Optional[str] = None

    rsi = rsi_series(closes, cfg.rsi_len)
    if t >= 1 and rsi[t] is not None and rsi[t - 1] is not None:
        checks["rsi_value"] = round(rsi[t], 3)
        checks["rsi_slope"] = round(rsi[t] - rsi[t - 1], 4)
        checks["rsi"] = (cfg.rsi_short_min <= rsi[t] <= cfg.rsi_short_max
                         and rsi[t] < rsi[t - 1])
    if not checks["rsi"]:
        fail = fail or "rsi"

    hist = macd_hist_series(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    if t >= 2:
        checks["macd_hist"] = round(hist[t], 8)
        checks["macd_slope"] = round(hist[t] - hist[t - 1], 8)
        checks["macd"] = hist[t] < hist[t - 1] < hist[t - 2]
    if not checks["macd"]:
        fail = fail or "macd"

    avg_v = avg_volume(c5, t, cfg.vol_avg_len)
    if avg_v is not None and avg_v > 0:
        checks["volume_ratio"] = round(c5[t].v / avg_v, 4)
        checks["volume"] = c5[t].v >= cfg.vol_mult_short * avg_v
    if not checks["volume"]:
        fail = fail or "volume"

    # VWAP descendente: obligatorio en short, opcional (brazo F2) en long.
    ok = False
    if t >= 1:
        vw_t, _ = vwap_at(c5, t, cfg.session_utc_hour)
        vw_p, _ = vwap_at(c5, t - 1, cfg.session_utc_hour)
        ok = vw_t is not None and vw_p is not None and vw_t < vw_p
    checks["vwap_slope"] = ok
    if not ok:
        fail = fail or "vwap_slope"

    return checks, fail


def structural_stop(c5: List[Candle], g: FVG, touch_j: int, t: int,
                    atr: float, cfg: Config) -> Tuple[float, float]:
    """Stop estructural SHORT (PRD 8). Devuelve (stop, nivel_estructural).

    max(lado superior del FVG, high del tramo retest->confirmacion) + buffer*ATR.
    No depende del objetivo.
    """
    struct = max(max(c5[k].h for k in range(touch_j, t + 1)), g.hi)
    return struct + cfg.stop_buffer_atr * atr, struct


def target(entry: float, stop: float, rr: float) -> float:
    return entry - rr * (stop - entry)
