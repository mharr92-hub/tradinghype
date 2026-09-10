"""
adapters/hyperliquid_data.py — datos de mercado de Hyperliquid. SOLO LECTURA.

Este modulo no conoce claves, no firma nada y no puede enviar una orden. Solo
habla con el endpoint publico `/info`. La escritura vive en execution/, detras
de LIVE_EXECUTION=false.

Es el cuello de botella de todo el sistema: el escaner, la revalidacion de las
alertas de TradingView y el journal dependen de que estas velas sean correctas
y esten frescas.

DECISION CENTRAL — solo velas CERRADAS:
`candleSnapshot` devuelve tambien la vela EN CURSO, cuyos valores cambian cada
segundo. Todo el motor asume velas cerradas (PRD 4). Si esa vela se colara,
las señales repintarian en vivo exactamente igual que un indicador mal escrito
de TradingView, y el bug seria invisible en backtest. Por eso `closed_candles()`
la descarta SIEMPRE y no hay opcion para incluirla.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx

from ..strategies.indicators import BAR_1H_MS, BAR_4H_MS, BAR_5M_MS, Candle

DEFAULT_API = "https://api.hyperliquid.xyz"
COIN = "HYPE"

INTERVAL_MS = {"5m": BAR_5M_MS, "1h": BAR_1H_MS, "4h": BAR_4H_MS}

# `candleSnapshot` devuelve como maximo ~5000 velas por peticion. En 5m son
# ~17 dias, que es justamente la limitacion documentada en el research plan 5.
MAX_CANDLES_PER_REQUEST = 5000


class MarketDataError(RuntimeError):
    """Fallo al obtener datos. Es un kill switch (PRD 17: api_errors)."""


class StaleDataError(MarketDataError):
    """Los datos existen pero son demasiado viejos para operar con ellos."""


@dataclass(frozen=True)
class VenueMeta:
    """Metadatos del activo, leidos del venue. Nunca se asumen."""
    sz_decimals: int
    max_leverage: float
    price_decimals: int

    @property
    def size_step(self) -> float:
        return 10.0 ** (-self.sz_decimals)


@dataclass(frozen=True)
class FundingPoint:
    ts: int
    rate_hourly: float
    premium: Optional[float] = None


class HyperliquidData:
    """Cliente de lectura. Sincrono a proposito: el escaner corre una vez por
    vela de 5m, no necesita concurrencia, y el codigo sincrono es mas facil de
    razonar cuando el fallo significa "no operar"."""

    def __init__(self, api_url: str = DEFAULT_API, timeout: float = 10.0,
                 client: Optional[httpx.Client] = None):
        self.api_url = api_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- transporte ---------------------------------------------------------

    def _post_info(self, payload: dict) -> object:
        try:
            r = self._client.post(f"{self.api_url}/info", json=payload)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            # Se envuelve en una excepcion propia para que el kill switch de
            # api_errors no tenga que conocer httpx.
            raise MarketDataError(f"fallo /info {payload.get('type')!r}: {e}") from e

    # -- velas --------------------------------------------------------------

    def _raw_candles(self, interval: str, start_ms: int,
                     end_ms: int) -> List[dict]:
        if interval not in INTERVAL_MS:
            raise ValueError(f"intervalo no soportado: {interval!r}")
        data = self._post_info({
            "type": "candleSnapshot",
            "req": {"coin": COIN, "interval": interval,
                    "startTime": int(start_ms), "endTime": int(end_ms)},
        })
        if not isinstance(data, list):
            raise MarketDataError(f"candleSnapshot devolvio {type(data).__name__}")
        return data

    @staticmethod
    def _num(raw: dict, key: str) -> float:
        """float() acepta 'NaN' e 'Infinity' sin protestar, y a partir de ahi
        toda comparacion con ese valor devuelve False en silencio: un stop no
        se dispara, un gate no rechaza, un dato viejo parece fresco. Se corta
        aqui, en la frontera, que es el unico sitio donde se puede."""
        v = float(raw[key])
        if not math.isfinite(v):
            raise MarketDataError(f"valor no finito en {key!r}: {raw[key]!r}")
        return v

    @classmethod
    def _to_candle(cls, raw: dict) -> Candle:
        # Hyperliquid devuelve los numeros como STRING.
        return Candle(ts=int(raw["t"]), o=cls._num(raw, "o"), h=cls._num(raw, "h"),
                      l=cls._num(raw, "l"), c=cls._num(raw, "c"), v=cls._num(raw, "v"))

    def closed_candles(self, interval: str, lookback_bars: int,
                       now_ms: Optional[int] = None) -> List[Candle]:
        """Ultimas `lookback_bars` velas CERRADAS, ordenadas por tiempo.

        La vela en curso se descarta siempre. Sin excepciones, sin flag.
        """
        bar = INTERVAL_MS[interval]
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        # Se pide un margen extra: el venue puede tener huecos y la vela en
        # curso se va a descartar de todas formas.
        want = min(lookback_bars + 5, MAX_CANDLES_PER_REQUEST)
        start = now - want * bar
        raw = self._raw_candles(interval, start, now)

        candles = [self._to_candle(x) for x in raw]
        candles.sort(key=lambda c: c.ts)

        # Deduplicar por timestamp de apertura, quedandose con la ultima
        # version recibida (el venue puede reenviar una vela corregida).
        dedup: List[Candle] = []
        for c in candles:
            if dedup and dedup[-1].ts == c.ts:
                dedup[-1] = c
            else:
                dedup.append(c)

        closed = [c for c in dedup if c.ts + bar <= now]
        return closed[-lookback_bars:]

    def multi_timeframe(self, bars_5m: int = 600, bars_1h: int = 200,
                        bars_4h: int = 200, now_ms: Optional[int] = None
                        ) -> Tuple[List[Candle], List[Candle], List[Candle]]:
        """Los tres marcos que necesita `engine.scan()`, ya filtrados a cerradas.

        Se piden NATIVOS al venue en lugar de resamplear los 5m. Motivo: el
        resampleo desde 5m solo reproduce el bucket HTF si no falta ninguna
        vela, y en cuanto hay un hueco el 4H sale mal sin avisar. El venue ya
        sabe agregar sus propias velas.

        UN SOLO RELOJ para los tres marcos. Si cada consulta capturara su
        propio `time.time()`, una peticion a las 03:59:59.9 y otra a las
        04:00:00.1 mezclarian un cierre de 5m de las 03:55 con un cierre de 1H
        de las 04:00, y el motor decidiria sobre una vela de 5m ANTERIOR usando
        contexto HTF POSTERIOR. Es lookahead, y del sutil: solo aparece en el
        cambio de hora y desaparece al reintentar.

        Devuelve (c4h, c1h, c5) en el orden que espera scan().
        """
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        c5 = self.closed_candles("5m", bars_5m, now)
        c1h = self.closed_candles("1h", bars_1h, now)
        c4h = self.closed_candles("4h", bars_4h, now)
        return c4h, c1h, c5

    # -- frescura -----------------------------------------------------------

    @staticmethod
    def data_age_seconds(c5: List[Candle], now_ms: Optional[int] = None) -> float:
        """Segundos transcurridos desde el CIERRE de la ultima vela de 5m.

        En operacion normal oscila entre 0 y 300 s (una vela). Un valor muy por
        encima de 300 significa que faltan velas: el venue esta caido, la red
        falla, o el proceso lleva dormido un rato.
        """
        if not c5:
            return float("inf")
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        return (now - (c5[-1].ts + BAR_5M_MS)) / 1000.0

    def assert_fresh(self, c5: List[Candle], max_age_seconds: float = 420.0,
                     now_ms: Optional[int] = None) -> float:
        """Falla si los datos estan viejos. Devuelve la edad si estan bien.

        El default de 420 s (7 min) deja pasar una vela de 5m mas un margen de
        2 min para latencia y jitter del scheduler, pero no dos velas: si falta
        una vela entera, el motor estaria decidiendo con el mercado de hace 10
        minutos y eso ya no es "un poco tarde", es otro mercado.
        """
        age = self.data_age_seconds(c5, now_ms)
        if age > max_age_seconds:
            raise StaleDataError(
                f"ultima vela de 5m cerro hace {age:.0f}s "
                f"(maximo {max_age_seconds:.0f}s). No se opera con datos viejos."
            )
        return age

    @staticmethod
    def find_gaps(candles: List[Candle], interval: str) -> List[Tuple[int, int]]:
        """Huecos en la serie, como (ts_esperado, ts_encontrado).

        Importan mas de lo que parece: la edad del gap se cuenta en VELAS
        ("<= 12 velas de 5m"), no en minutos. Con velas faltantes ese conteo
        deja de significar lo que la especificacion dice que significa.
        """
        bar = INTERVAL_MS[interval]
        gaps: List[Tuple[int, int]] = []
        for prev, cur in zip(candles, candles[1:]):
            expected = prev.ts + bar
            if cur.ts != expected:
                gaps.append((expected, cur.ts))
        return gaps

    # -- funding ------------------------------------------------------------

    def funding_history(self, start_ms: int,
                        end_ms: Optional[int] = None) -> List[FundingPoint]:
        """Historico de funding. Hyperliquid lo liquida por HORA.

        Obligatorio desde que las posiciones pueden vivir 24 h (AUDIT C-04):
        un trade largo puede cruzar hasta 24 liquidaciones de funding.
        """
        payload = {"type": "fundingHistory", "coin": COIN,
                   "startTime": int(start_ms)}
        if end_ms is not None:
            payload["endTime"] = int(end_ms)
        data = self._post_info(payload)
        if not isinstance(data, list):
            raise MarketDataError("fundingHistory no devolvio una lista")
        out = []
        for x in data:
            try:
                out.append(FundingPoint(
                    ts=int(x["time"]),
                    rate_hourly=float(x["fundingRate"]),
                    premium=float(x["premium"]) if x.get("premium") is not None else None,
                ))
            except (KeyError, TypeError, ValueError) as e:
                raise MarketDataError(f"punto de funding malformado: {x!r}") from e
        out.sort(key=lambda p: p.ts)
        return out

    def current_funding_rate(self) -> float:
        """Tasa horaria vigente, con el signo del venue: positiva = los largos
        pagan. La usa el cost gate para estimar el funding esperado."""
        data = self._post_info({"type": "metaAndAssetCtxs"})
        try:
            universe = data[0]["universe"]
            ctxs = data[1]
            idx = next(i for i, a in enumerate(universe) if a["name"] == COIN)
            return float(ctxs[idx]["funding"])
        except (KeyError, IndexError, TypeError, ValueError, StopIteration) as e:
            raise MarketDataError(f"no se pudo leer el funding de {COIN}: {e}") from e

    # -- metadatos y precio -------------------------------------------------

    def venue_meta(self) -> VenueMeta:
        """Precision de tamaño y apalancamiento maximo, leidos del venue.

        `szDecimals` decide si una cantidad calculada es representable. En TINY,
        con $1 de riesgo, es la diferencia entre poder operar y tener que hacer
        SKIP — por eso se lee y no se asume.
        """
        data = self._post_info({"type": "meta"})
        try:
            asset = next(a for a in data["universe"] if a["name"] == COIN)
            sz = int(asset["szDecimals"])
            return VenueMeta(
                sz_decimals=sz,
                max_leverage=float(asset.get("maxLeverage", 3)),
                # En perps de HL los precios admiten hasta 6 cifras
                # significativas menos szDecimals.
                price_decimals=max(0, 6 - sz),
            )
        except (KeyError, TypeError, ValueError, StopIteration) as e:
            raise MarketDataError(f"no se pudo leer meta de {COIN}: {e}") from e

    def mid_price(self) -> float:
        """Precio medio actual. Se usa para medir el drift contra la referencia
        de la señal (PRD 13), nunca como precio de entrada."""
        data = self._post_info({"type": "allMids"})
        try:
            return float(data[COIN])
        except (KeyError, TypeError, ValueError) as e:
            raise MarketDataError(f"no hay mid para {COIN}: {e}") from e
