"""
indicators.py — Indicadores deterministas, solo stdlib.

Extraidos de hype_long_vwap_retest.py sin cambiar su comportamiento numerico:
strategies/hype/rules.py y strategies/hype_long/rules.py deben producir
exactamente los mismos numeros. El test tests/test_parity_long.py lo verifica
comparando ambos modulos sobre las mismas velas.

Reglas de la casa:
  - Todo se calcula sobre velas COMPLETADAS. Nada usa la vela en curso.
  - Fail-closed: si un indicador no se puede calcular, devuelve None y el
    llamador debe tratarlo como "no hay señal", nunca como cero.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

BAR_5M_MS = 5 * 60 * 1000
BAR_1H_MS = 60 * 60 * 1000
BAR_4H_MS = 4 * BAR_1H_MS


@dataclass(frozen=True)
class Candle:
    """Vela OHLCV. `ts` es el epoch en ms de la APERTURA, en UTC."""
    ts: int
    o: float
    h: float
    l: float
    c: float
    v: float


def ema_series(vals: Sequence[float], n: int) -> List[float]:
    """EMA sembrada con el primer valor. Requiere warmup: los primeros ~n
    valores no son fiables y el llamador no debe usarlos."""
    if not vals:
        return []
    k = 2.0 / (n + 1.0)
    out: List[float] = []
    e = vals[0]
    for i, v in enumerate(vals):
        e = v if i == 0 else v * k + e * (1.0 - k)
        out.append(e)
    return out


def rsi_series(vals: Sequence[float], n: int = 14) -> List[Optional[float]]:
    """RSI de Wilder. None mientras no haya warmup suficiente."""
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


def macd_hist_series(vals: Sequence[float], fast: int = 12, slow: int = 26,
                     sig: int = 9) -> List[float]:
    """Histograma MACD (linea - señal)."""
    ef = ema_series(vals, fast)
    es = ema_series(vals, slow)
    line = [a - b for a, b in zip(ef, es)]
    signal = ema_series(line, sig)
    return [m - s for m, s in zip(line, signal)]


def atr_at(candles: Sequence[Candle], idx: int, n: int) -> Optional[float]:
    """ATR (media simple de True Range) con velas hasta idx inclusive."""
    if idx < n:  # necesita n TRs, cada uno con su vela previa
        return None
    trs = []
    for i in range(idx - n + 1, idx + 1):
        pc = candles[i - 1].c
        trs.append(max(candles[i].h - candles[i].l,
                       abs(candles[i].h - pc),
                       abs(candles[i].l - pc)))
    return sum(trs) / n


def session_start_ms(ts_ms: int, hour: int) -> int:
    """Inicio de la sesion de VWAP que contiene ts_ms. Reset diario a `hour` UTC."""
    t = _dt.datetime.fromtimestamp(ts_ms / 1000.0, _dt.timezone.utc)
    s = t.replace(hour=hour, minute=0, second=0, microsecond=0)
    if t < s:
        s -= _dt.timedelta(days=1)
    return int(s.timestamp() * 1000)


def vwap_at(c5: Sequence[Candle], idx: int, hour: int) -> Tuple[Optional[float], int]:
    """VWAP de sesion (precio tipico ponderado por volumen) hasta idx inclusive.

    Devuelve (vwap, numero de velas de la sesion). vwap es None si el volumen
    acumulado es cero, en cuyo caso el llamador debe abortar la evaluacion.
    """
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


def avg_volume(c5: Sequence[Candle], idx: int, n: int) -> Optional[float]:
    """Volumen medio de las n velas ANTERIORES a idx (excluye la vela actual,
    para que 'volumen >= media' no se compare contra si mismo)."""
    if idx < n:
        return None
    return sum(x.v for x in c5[idx - n:idx]) / float(n)


def resample(c5: Sequence[Candle], tf_ms: int) -> List[Candle]:
    """Agrega velas de 5m a un timeframe mayor. Cada bucket queda indexado por
    su apertura (floor). El ultimo bucket puede estar INCOMPLETO: el llamador
    es responsable de descartarlo (ver completed_upto)."""
    buckets: List[Candle] = []
    cur_key = None
    o = h = l = c = v = None
    for cd in c5:
        key = (cd.ts // tf_ms) * tf_ms
        if key != cur_key:
            if cur_key is not None:
                buckets.append(Candle(cur_key, o, h, l, c, v))
            cur_key, o, h, l, c, v = key, cd.o, cd.h, cd.l, cd.c, cd.v
        else:
            h = max(h, cd.h)
            l = min(l, cd.l)
            c = cd.c
            v += cd.v
    if cur_key is not None:
        buckets.append(Candle(cur_key, o, h, l, c, v))
    return buckets


def completed_upto(buckets: Sequence[Candle], tf_ms: int, now_close_ms: int,
                   ptr: int) -> int:
    """Avanza ptr hasta el ultimo bucket cuyo CIERRE <= now_close_ms.
    Devuelve el nuevo ptr (indice EXCLUSIVO). Anti-lookahead."""
    while ptr < len(buckets) and buckets[ptr].ts + tf_ms <= now_close_ms:
        ptr += 1
    return ptr
