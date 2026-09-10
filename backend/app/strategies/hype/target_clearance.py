"""
strategies/hype/target_clearance.py — ¿cabe el objetivo? (PRD 7)

Concepto nuevo en v2. Subir el objetivo de 1R a 1.6R sin comprobar que hay
espacio limpio no produce mas beneficio: produce mas trades que mueren a mitad
de camino. Este modulo responde una sola pregunta:

    Entre la entrada y el objetivo, ¿hay un nivel estructural que estorbe?

REGLA DURA: solo cuentan niveles DETERMINISTAS. Nada de "zona de oferta", nada
de niveles psicologicos, nada dibujado a mano. Si dos personas no obtienen el
mismo numero a partir de las mismas velas, no es un nivel.

Niveles admitidos (PRD 7.1):
  - swing high/low de 1H confirmado (pivote de n barras a cada lado)
  - high/low de la sesion anterior (mismo reset que el VWAP)

Anti-lookahead: un pivote solo existe cuando sus n barras posteriores ya
cerraron. En el instante t, un pivote en la barra i requiere i + n <= t. Es
justo lo que hace que un swing sea "confirmado" y no una adivinanza.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..indicators import Candle, session_start_ms
from .common import SIDE_LONG, Config

DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class Level:
    price: float
    kind: str        # "swing_1h" | "prev_day"
    ts: int


def swing_levels_1h(c1h: Sequence[Candle], side: str, cfg: Config) -> List[Level]:
    """Pivotes 1H CONFIRMADOS dentro del lookback.

    Para LONG buscamos swing highs (resistencia); para SHORT, swing lows
    (soporte). Un pivote en i necesita n barras a cada lado ya cerradas, asi
    que el rango util termina en len-1-n: las ultimas n barras aun no pueden
    confirmar nada. Eso es correcto, no una limitacion.
    """
    n = cfg.clearance_pivot_n
    out: List[Level] = []
    if len(c1h) < 2 * n + 1:
        return out
    start = max(n, len(c1h) - cfg.clearance_lookback_1h)
    for i in range(start, len(c1h) - n):
        if side == SIDE_LONG:
            piv = c1h[i].h
            if all(c1h[j].h <= piv for j in range(i - n, i + n + 1) if j != i):
                out.append(Level(piv, "swing_1h", c1h[i].ts))
        else:
            piv = c1h[i].l
            if all(c1h[j].l >= piv for j in range(i - n, i + n + 1) if j != i):
                out.append(Level(piv, "swing_1h", c1h[i].ts))
    return out


def prev_day_level(c5: Sequence[Candle], t: int, side: str,
                   cfg: Config) -> Optional[Level]:
    """High (LONG) o low (SHORT) de la sesion ANTERIOR.

    La sesion anterior es [inicio_sesion_actual - 24h, inicio_sesion_actual).
    Devuelve None si no hay ninguna vela en esa ventana.

    OJO: un None aqui NO significa "no hay resistencia". Puede significar
    "faltan datos". Quien decide si los datos alcanzan es `coverage_ok()`, y
    `check()` lo consulta ANTES de mirar niveles. Esta funcion no vale por si
    sola como gate.
    """
    cur_start = session_start_ms(c5[t].ts, cfg.session_utc_hour)
    prev_start = cur_start - DAY_MS
    hi = lo = None
    ts = prev_start
    for cd in c5:
        if cd.ts >= cur_start:
            break
        if cd.ts < prev_start:
            continue
        hi = cd.h if hi is None else max(hi, cd.h)
        lo = cd.l if lo is None else min(lo, cd.l)
    if hi is None or lo is None:
        return None
    return Level(hi if side == SIDE_LONG else lo, "prev_day", ts)


def collect_levels(c1h: Sequence[Candle], c5: Sequence[Candle], t: int,
                   side: str, cfg: Config) -> List[Level]:
    levels = swing_levels_1h(c1h, side, cfg)
    if cfg.clearance_use_prev_day:
        pd = prev_day_level(c5, t, side, cfg)
        if pd is not None:
            levels.append(pd)
    return levels


def first_blocking_level(levels: Sequence[Level], entry: float,
                         side: str) -> Optional[Level]:
    """El obstaculo mas cercano en la direccion del trade.

    LONG: el nivel mas BAJO por encima del entry. SHORT: el mas ALTO por debajo.
    Los niveles que ya quedaron atras no estorban y se ignoran.
    """
    if side == SIDE_LONG:
        ahead = [x for x in levels if x.price > entry]
        return min(ahead, key=lambda x: x.price) if ahead else None
    ahead = [x for x in levels if x.price < entry]
    return max(ahead, key=lambda x: x.price) if ahead else None


def clearance_r(entry: float, stop: float, level: Optional[Level],
                side: str) -> float:
    """Espacio disponible expresado en R.

    Sin obstaculo devuelve infinito: no hay nada que limite el objetivo.
    """
    if level is None:
        return float("inf")
    r = abs(entry - stop)
    if r <= 0:
        return 0.0
    dist = (level.price - entry) if side == SIDE_LONG else (entry - level.price)
    return max(0.0, dist / r)


def coverage_ok(c1h: Sequence[Candle], c5: Sequence[Candle], t: int,
                cfg: Config) -> Tuple[bool, str]:
    """¿Hay datos suficientes para que "no hay obstaculo" signifique algo?

    Distincion que el codigo tiene que hacer explicita: "he mirado y no hay
    resistencia" y "no he podido mirar" NO son lo mismo. Sin esta comprobacion,
    una serie 1H vacia produciria clearance infinito y aprobaria cualquier
    trade, que es el fallo mas silencioso posible: el gate parece funcionar.

    Fail-closed: sin cobertura, el trade se rechaza.
    """
    min_1h = max(2 * cfg.clearance_pivot_n + 1, cfg.clearance_pivot_n + 1)
    if len(c1h) < min_1h:
        return False, f"insufficient_1h_history:{len(c1h)}<{min_1h}"

    if cfg.clearance_use_prev_day:
        cur_start = session_start_ms(c5[t].ts, cfg.session_utc_hour)
        prev_start = cur_start - DAY_MS
        n_prev = sum(1 for cd in c5 if prev_start <= cd.ts < cur_start)
        # Una sesion completa son 288 velas de 5m. Se exige el 80 %: por debajo
        # de eso el high/low de la sesion anterior puede estar simplemente
        # ausente de los datos, no ausente del mercado.
        need = int(0.8 * (DAY_MS // (5 * 60 * 1000)))
        if n_prev < need:
            return False, f"insufficient_prev_session:{n_prev}<{need}"

    return True, "ok"


def check(c1h: Sequence[Candle], c5: Sequence[Candle], t: int, side: str,
          entry: float, stop: float, cfg: Config):
    """Evalua el gate. Devuelve (pasa, clearance_R, nivel_bloqueante).

    Pasa si (a) hay datos suficientes para pronunciarse y (b) el espacio hasta
    el primer obstaculo alcanza al menos el objetivo del brazo activo
    (`cfg.rr`). Si no, el trade se rechaza: NO se reduce el objetivo para que
    quepa, porque eso seria mover el R:R despues de haber visto el obstaculo
    (PRD 3, PRD 8).
    """
    ok_cov, _ = coverage_ok(c1h, c5, t, cfg)
    if not ok_cov:
        return False, 0.0, None

    levels = collect_levels(c1h, c5, t, side, cfg)
    blocking = first_blocking_level(levels, entry, side)
    cr = clearance_r(entry, stop, blocking, side)
    return cr >= cfg.rr, cr, blocking
