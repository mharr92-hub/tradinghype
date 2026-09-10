"""
api/tradingview.py — receptor del webhook de TradingView (PRD 15, 17, 24).

ESTE MODULO NO PUEDE EJECUTAR NADA, Y ESO ES ESTRUCTURAL, NO UNA PROMESA:
no importa `app.execution` ni `app.services.signal_manager` (que si lo importa).
La revalidacion se entrega a un SINK inyectado, un objeto con un unico metodo
`submit()`. Si mañana alguien quisiera enviar una posicion desde aqui, tendria
que añadir primero un import que un test de arquitectura puede vigilar.

Nota para quien escriba ese test: debe comprobar el GRAFO DE IMPORTS, no
substrings del fichero. El contrato del payload contiene literalmente el campo
`execution_authorized`, asi que un `assert "execution" not in source` es
imposible de satisfacer y daria una falsa sensacion de proteccion.

POR QUE EL WEBHOOK ES SECUNDARIO Y NO EL DISPARADOR PRINCIPAL:
el escaner nativo ve lo mismo que TradingView, antes que TradingView, sobre el
feed que de verdad se va a operar. Meter a TV en el camino critico de un
presupuesto de 90 s gasta entre el 3 % y el 33 % de ese presupuesto en latencia
que no controlamos, a cambio de cero informacion nueva. Lo que si aporta el
puente, y no puede darse el sistema a si mismo, es un ORACULO DIFERENCIAL: dos
implementaciones de las mismas reglas sobre dos feeds distintos. Su desacuerdo
es el detector mas barato de bugs, de drift de configuracion y de datos malos.

SOBRE EL "SECRETO": no es una firma. Pine Script no tiene criptografia ni
hashing y las alertas no admiten cabeceras HTTP propias, asi que el secreto
viaja EN CLARO dentro del cuerpo, identico en cada mensaje. Es un identificador
de portador: distingue "nuestro TradingView" de "un TradingView" y nada mas. No
aporta integridad, ni frescura, ni resistencia a repeticion. La seguridad del
puente no descansa en autenticarlo: descansa en que el puente NO TIENE
AUTORIDAD. El peor resultado de un webhook comprometido es ruido y una fila en
el log de discrepancias.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Tuple, Union

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import (BaseModel, ConfigDict, Field, StrictBool, StrictFloat,
                      StrictInt, StrictStr, field_validator, model_validator)
from sqlalchemy import select

from ..core.config import Settings
from ..db import models as m
from ..strategies.hype.scoring import assert_no_probability
from ..strategies.indicators import BAR_5M_MS, session_start_ms

router = APIRouter(tags=["tradingview"])

SCHEMA_ID = "hype.tv.signal.v1"
EVENT_CANDIDATE = "candidate"
EVENT_INVALIDATED = "invalidated"
EVENT_HEARTBEAT = "heartbeat"

MAX_BODY_BYTES = 8192
# 2 s de tolerancia de deriva de reloj. Mas alla, la alerta afirma conocer el
# futuro y eso solo puede ser un reloj roto o un payload forjado.
FUTURE_TOLERANCE_MS = 2000
# Se ACEPTA Y REGISTRA hasta 180 s aunque el TTL de decision sean 90: una
# alerta que llego a los 120 s es justamente el dato de latencia que hace falta
# para saber si el presupuesto de 90 s es realista. Descartarla en el borde
# destruiria la medicion que se quiere hacer.
ACCEPT_LATE_MS = 180_000

# Nombres que un payload de TradingView no puede contener. Ninguno de ellos es
# calculable desde un grafico: si aparecen, o el script esta roto o alguien
# esta inyectando.
FORBIDDEN_KEYS = ("qty", "size", "notional", "leverage", "equity", "balance",
                  "order_type", "api_key", "account", "wallet", "execute",
                  "secret_key", "private_key")
# `auth.secret` es legitimo y se redacta antes de persistir; el resto no.
_ALLOWED_SECRET_PATH = ("auth", "secret")


# ---------------------------------------------------------------------------
# Cableado
# ---------------------------------------------------------------------------

class RevalidationSink(Protocol):
    """Destino de la revalidacion. Deliberadamente minusculo.

    El handler solo sabe empujar; quien revalida (y por tanto quien conoce el
    motor, el riesgo y la base de datos) vive al otro lado de esta interfaz.
    """

    def submit(self, alert_id: str, canonical: Dict[str, Any]) -> None: ...


@dataclass
class WebhookConfig:
    settings: Settings
    session_factory: Any
    sink: Optional[RevalidationSink] = None
    path_token: str = ""
    # Mapa EXPLICITO tickerid de TV -> mercado interno. Un ticker desconocido
    # se rechaza: `HYPEUSDT.P` de otro exchange no es el feed de Hyperliquid,
    # y con otro libro cambian las mechas, y con las mechas cambian los FVG.
    tickerid_map: Dict[str, str] = field(default_factory=dict)
    ip_allowlist: Tuple[str, ...] = ()
    signal_ttl_seconds: int = 90
    strategy_id: str = "hype_vwap_fvg_retest_v2"


_CONFIG: Optional[WebhookConfig] = None

# Contadores del borde. Un rechazo por IP tiene que ser un evento VISIBLE: si
# TradingView cambia su lista de IPs sin avisar, todas las alertas se caen en
# silencio y el silencio se lee como "hoy no hubo setups".
_COUNTERS: Dict[str, int] = {}


def configure(config: WebhookConfig) -> None:
    """Cablea el receptor. Fail-closed: sin llamar a esto, el endpoint
    responde 503 en vez de aceptar cualquier cosa con un secreto vacio."""
    global _CONFIG
    if config.settings.tradingview_webhook_secret and \
            len(config.settings.tradingview_webhook_secret) < 32:
        raise ValueError(
            "TRADINGVIEW_WEBHOOK_SECRET < 32 caracteres. Con el webhook activo "
            "eso es un secreto adivinable, no un secreto."
        )
    if not config.tickerid_map:
        raise ValueError(
            "Sin allowlist de tickerids el webhook aceptaria cualquier "
            "mercado, y un proxy de otro exchange no es el feed que se opera."
        )
    _CONFIG = config


def counters() -> Dict[str, int]:
    return dict(_COUNTERS)


def _bump(name: str) -> None:
    _COUNTERS[name] = _COUNTERS.get(name, 0) + 1


def _cfg() -> WebhookConfig:
    if _CONFIG is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"error": "webhook_not_configured"})
    return _CONFIG


# ---------------------------------------------------------------------------
# Contrato del payload — hype.tv.signal.v1
# ---------------------------------------------------------------------------
#
# `strict=True` y `extra="forbid"`: un campo que sobra o un numero que llega
# como string son sintomas de que el Pine y este contrato ya no son el mismo
# contrato. Coaccionar "41.2" a 41.2 esconderia exactamente eso.

Number = Union[StrictFloat, StrictInt]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True,
                              populate_by_name=True)


class Auth(_Strict):
    # `repr=False` para que el secreto no aparezca en un traceback.
    secret: StrictStr = Field(repr=False)
    token_id: StrictStr = Field(max_length=16)


class Source(_Strict):
    producer: StrictStr
    script_version: StrictStr
    script_build_utc: Optional[StrictStr] = None


class Market(_Strict):
    tv_tickerid: StrictStr
    tv_exchange: Optional[StrictStr] = None
    tv_ticker: Optional[StrictStr] = None
    timeframe: StrictStr
    bar_ms: StrictInt
    mintick: Optional[Number] = None

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        if v != "5":
            raise ValueError("el motor es de 5m; cualquier otro TF no es la "
                             "misma estrategia")
        return v

    @field_validator("bar_ms")
    @classmethod
    def _bar(cls, v: int) -> int:
        if v != BAR_5M_MS:
            raise ValueError(f"bar_ms debe ser {BAR_5M_MS}")
        return v


class TimeBlock(_Strict):
    bar_open_ms: StrictInt
    bar_close_ms: StrictInt
    fired_at_ms: StrictInt
    # Advisory: el backend lo IGNORA para decidir y recalcula desde
    # bar_close_ms. Solo se compara, porque si difiere significa que el Pine
    # corre con otro TTL configurado.
    advisory_expires_at_ms: Optional[StrictInt] = None
    session_start_ms: Optional[StrictInt] = None

    @model_validator(mode="after")
    def _grid(self) -> "TimeBlock":
        if self.bar_open_ms % BAR_5M_MS != 0:
            raise ValueError("bar_open_ms fuera de la rejilla de 5m")
        if self.bar_close_ms != self.bar_open_ms + BAR_5M_MS:
            raise ValueError("bar_close_ms != bar_open_ms + 300000")
        if self.session_start_ms is not None:
            # Detecta un reset de VWAP mal configurado, que corromperia en
            # silencio todo lo que viene despues (banda, prev_day, warmup).
            if self.session_start_ms != session_start_ms(self.bar_open_ms, 0):
                raise ValueError("session_start_ms no ancla en 00:00 UTC")
        return self


class SignalBlock(_Strict):
    tv_signal_id: StrictStr = Field(max_length=128)
    side: StrictStr
    entry_ref: Number
    structural_stop: Number
    structural_level: Optional[Number] = None
    risk_per_unit: Number
    rr_arm: Optional[StrictStr] = None
    rr: Number
    target_ref: Number

    @field_validator("side")
    @classmethod
    def _side(cls, v: str) -> str:
        if v not in ("LONG", "SHORT"):
            raise ValueError("side debe ser LONG o SHORT en mayusculas")
        return v

    @model_validator(mode="after")
    def _coherent(self) -> "SignalBlock":
        # Redundante a proposito: si los tres numeros no cuadran entre si, el
        # payload esta corrupto y no hace falta mirar nada mas.
        expected = abs(float(self.entry_ref) - float(self.structural_stop))
        if abs(expected - float(self.risk_per_unit)) > max(1e-6, expected * 0.001):
            raise ValueError("risk_per_unit no cuadra con entry_ref y stop")
        return self


class Bar(_Strict):
    o: Number
    h: Number
    l: Number
    c: Number
    v: Number
    # La confirmacion exige close > high[1] (long) / close < low[1] (short).
    # Sin estos dos, el check no se puede reproducir con los datos de TV.
    prev_high: Optional[Number] = None
    prev_low: Optional[Number] = None


class Context(_Strict):
    vwap: Optional[Number] = None
    vwap_prev: Optional[Number] = None
    atr20: Optional[Number] = None
    vwap_band_k: Optional[Number] = None
    vwap_distance_atr: Optional[Number] = None
    session_bars: Optional[StrictInt] = None


class Fvg(_Strict):
    lo: Number
    hi: Number
    formed_bar_open_ms: Optional[StrictInt] = None
    first_touch_bar_open_ms: Optional[StrictInt] = None
    age_bars_at_touch: Optional[StrictInt] = None
    width_atr: Optional[Number] = None

    @model_validator(mode="after")
    def _order(self) -> "Fvg":
        # Invariante de `common.FVG`: lo < hi SIEMPRE, en los dos lados.
        if float(self.lo) >= float(self.hi):
            raise ValueError("fvg.lo debe ser < fvg.hi en ambas direcciones")
        return self


class Momentum(_Strict):
    rsi: Optional[Number] = None
    rsi_prev: Optional[Number] = None
    macd_hist: Optional[Number] = None
    macd_hist_prev1: Optional[Number] = None
    macd_hist_prev2: Optional[Number] = None
    volume: Optional[Number] = None
    avg_volume_20: Optional[Number] = None
    volume_ratio: Optional[Number] = None


class Htf(_Strict):
    bar_open_ms: Optional[StrictInt] = None
    close: Optional[Number] = None
    ema20: Optional[Number] = None
    ema50: Optional[Number] = None
    ema50_prev3: Optional[Number] = None
    rsi14: Optional[Number] = None
    macd_hist: Optional[Number] = None
    macd_hist_prev: Optional[Number] = None


class HtfBlock(_Strict):
    h4: Optional[Htf] = None
    h1: Optional[Htf] = None


class Clearance(_Strict):
    # null (no `0`, no `999`) cuando no hay nivel: "no hay obstaculo" y "no
    # pude mirar" tienen que poder distinguirse o el gate se vuelve fail-open.
    level_price: Optional[Number] = None
    level_kind: Optional[StrictStr] = None
    level_ts: Optional[StrictInt] = None
    clearance_r: Optional[Number] = None
    coverage_1h_bars: Optional[StrictInt] = None
    prev_session_5m_bars: Optional[StrictInt] = None


class Checks(_Strict):
    regime_4h: Optional[StrictBool] = None
    align_1h: Optional[StrictBool] = None
    fvg: Optional[StrictBool] = None
    first_retest: Optional[StrictBool] = None
    vwap_band: Optional[StrictBool] = None
    confirmation: Optional[StrictBool] = None
    rsi: Optional[StrictBool] = None
    macd: Optional[StrictBool] = None
    volume: Optional[StrictBool] = None
    vwap_slope: Optional[StrictBool] = None
    target_clearance: Optional[StrictBool] = None
    # SIEMPRE null: TradingView no conoce el tier de fees, el spread real, el
    # funding ni el tiempo de tenencia esperado. Un true aqui seria inventado.
    cost_gate: Optional[StrictBool] = None

    @field_validator("cost_gate")
    @classmethod
    def _cost(cls, v: Optional[bool]) -> Optional[bool]:
        if v is not None:
            raise ValueError("checks.cost_gate debe ser null: TradingView no "
                             "puede evaluar costos")
        return v


class TvSignalPayload(_Strict):
    schema_id: StrictStr = Field(alias="schema")
    event: StrictStr
    execution_authorized: StrictBool
    auth: Auth
    source: Source
    market: Market
    time: TimeBlock
    signal: Optional[SignalBlock] = None
    bar: Optional[Bar] = None
    context: Optional[Context] = None
    fvg: Optional[Fvg] = None
    momentum_5m: Optional[Momentum] = None
    htf: Optional[HtfBlock] = None
    clearance: Optional[Clearance] = None
    checks: Optional[Checks] = None
    # Se acepta como diccionario libre a proposito: el detector de drift
    # compara claves contra la Config activa, y un campo nuevo en el Pine debe
    # aparecer como diferencia, no reventar el parseo.
    config_echo: Dict[str, Union[StrictStr, StrictBool, StrictFloat,
                                 StrictInt]] = Field(default_factory=dict)

    @field_validator("schema_id")
    @classmethod
    def _schema(cls, v: str) -> str:
        if v != SCHEMA_ID:
            raise ValueError(f"schema debe ser {SCHEMA_ID}")
        return v

    @field_validator("event")
    @classmethod
    def _event(cls, v: str) -> str:
        if v not in (EVENT_CANDIDATE, EVENT_INVALIDATED, EVENT_HEARTBEAT):
            raise ValueError("event desconocido")
        return v

    @field_validator("execution_authorized")
    @classmethod
    def _exec(cls, v: bool) -> bool:
        # Identidad, no truthiness. Un payload que se AUTOATRIBUYE autoridad de
        # ejecucion es hostil o el script esta roto; en ningun caso se lee para
        # decidir nada.
        if v is not False:
            raise ValueError("execution_authorized debe ser exactamente false")
        return v

    @model_validator(mode="after")
    def _candidate_needs_levels(self) -> "TvSignalPayload":
        if self.event == EVENT_CANDIDATE and self.signal is None:
            raise ValueError("un candidato sin bloque `signal` no es un "
                             "candidato")
        return self


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _reject_constant(name: str):
    raise ValueError(f"JSON invalido: {name}")


def _no_duplicate_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"clave duplicada en el JSON: {k!r}")
        seen[k] = v
    return seen


def _strict_loads(raw: str) -> Dict[str, Any]:
    """Parseo estricto: sin NaN, sin Infinity, sin claves duplicadas.

    `str.tostring(na)` en Pine produce la cadena `NaN`, que no es JSON valido.
    Se rechaza el mensaje entero en vez de coaccionar `NaN` a 0: un cero
    silencioso en un precio es peor que un rechazo ruidoso.
    """
    return json.loads(raw, parse_constant=_reject_constant,
                      object_pairs_hook=_no_duplicate_keys)


def _scan_forbidden(node: Any, path: Tuple[str, ...] = ()) -> Optional[str]:
    """Busca campos que un payload de grafico no puede tener."""
    if isinstance(node, dict):
        for k, v in node.items():
            low = str(k).lower()
            here = path + (str(k),)
            if low == "secret" and here != _ALLOWED_SECRET_PATH:
                return ".".join(here)
            for bad in FORBIDDEN_KEYS:
                if bad in low:
                    return ".".join(here)
            found = _scan_forbidden(v, here)
            if found:
                return found
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found = _scan_forbidden(v, path + (str(i),))
            if found:
                return found
    return None


def _redact(raw: Dict[str, Any]) -> str:
    """Cuerpo listo para el journal, con el secreto fuera.

    El journal es exactamente el sitio del que se sacan copias; guardar ahi un
    secreto que viaja en claro y no caduca es regalarlo.
    """
    clone = json.loads(json.dumps(raw))
    if isinstance(clone.get("auth"), dict):
        clone["auth"]["secret"] = "[REDACTED]"
    return json.dumps(clone, ensure_ascii=False, sort_keys=True)


def _dedup_key(strategy_id: str, market: str, side: str, bar_open_ms: int) -> str:
    """Clave canonica, calculada por el BACKEND.

    Se usa `bar_open_ms` y no `bar_close_ms` porque `Signal.ts` es la apertura
    y toda la alineacion con el motor se hace sobre ella. En rejilla son
    equivalentes, pero mezclarlos produce claves que no cruzan.

    El mercado es el INTERNO, no el ticker de TV: asi el mismo setup visto
    desde dos proveedores de grafico colapsa en una sola señal.
    """
    raw = f"{strategy_id}|{market}|{side}|{bar_open_ms}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> str:
    """IP del peer inmediato. NO se lee `X-Forwarded-For`: lo escribe quien
    llama, asi que confiar en el convertiria la allowlist en decoracion. La
    allowlist de verdad va en el borde (nginx/CF); esta es la segunda capa."""
    return request.client.host if request.client else ""


def _canonical(payload: TvSignalPayload, market: str,
               alert_id: str) -> Dict[str, Any]:
    """Vista plana del payload para el comparador. Diccionario puro: el gestor
    de señales no debe depender de los modelos de esta capa."""
    return {
        "tv_alert_id": alert_id,
        "market": market,
        "side": payload.signal.side if payload.signal else None,
        "bar_open_ms": payload.time.bar_open_ms,
        "bar_close_ms": payload.time.bar_close_ms,
        "fired_at_ms": payload.time.fired_at_ms,
        "script_version": payload.source.script_version,
        "signal": payload.signal.model_dump() if payload.signal else {},
        "bar": payload.bar.model_dump() if payload.bar else {},
        "context": payload.context.model_dump() if payload.context else {},
        "fvg": payload.fvg.model_dump() if payload.fvg else {},
        "momentum_5m": (payload.momentum_5m.model_dump()
                        if payload.momentum_5m else {}),
        "htf": payload.htf.model_dump() if payload.htf else {},
        "clearance": payload.clearance.model_dump() if payload.clearance else {},
        "checks": payload.checks.model_dump() if payload.checks else {},
        "config_echo": dict(payload.config_echo),
    }


def _persist(cfg: WebhookConfig, **kwargs) -> str:
    """Escribe la fila cruda y devuelve su id. Se hace ANTES de revalidar: no
    se puede analizar despues lo que se descarto en el borde."""
    with cfg.session_factory() as session:
        alert = m.TvAlert(**kwargs)
        session.add(alert)
        session.commit()
        return alert.id


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/api/webhooks/tradingview/{path_token}")
async def tradingview_webhook(path_token: str, request: Request,
                              background: BackgroundTasks) -> Dict[str, Any]:
    """Recibe una alerta, la registra y encola su revalidacion.

    Devuelve rapido a proposito. TradingView ignora el cuerpo de la respuesta y
    no reintenta de forma fiable, asi que el codigo HTTP es para NUESTROS logs;
    lo que no puede pasar es que el handler se quede haciendo trabajo pesado
    dentro de un presupuesto de 90 segundos. La revalidacion —leer velas
    nativas, correr `scan()`, comparar— ocurre despues de responder.

    Ningun camino de esta funcion abre, modifica ni cancela nada en el venue.
    """
    cfg = _cfg()
    now = int(time.time() * 1000)

    # -- 0. borde: token de ruta, IP, tamaño --------------------------------
    # El token se compara antes de leer el cuerpo: permite rechazar sin gastar
    # CPU en parsear ni tocar la base de datos, y es rotable por separado del
    # secreto. No previene nada una vez la URL se filtra, y las URLs se filtran
    # mas facilmente que los cuerpos (logs de acceso, capturas de la alerta).
    if not cfg.path_token or not secrets.compare_digest(path_token,
                                                        cfg.path_token):
        _bump("rejected_by_path_token")
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail={"error": "forbidden"})

    ip = _client_ip(request)
    if cfg.ip_allowlist and ip not in cfg.ip_allowlist:
        # Contador con alarma, no una linea de log: si TradingView cambia sus
        # IPs, TODAS las alertas se caen y el silencio parece un dia sin setups.
        _bump("rejected_by_ip")
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail={"error": "ip_not_allowed"})

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        _bump("rejected_by_size")
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail={"error": "body_too_large"})

    body_sha = hashlib.sha256(body).hexdigest()
    text = body.decode("utf-8", errors="replace")

    # -- 1. parseo estricto -------------------------------------------------
    try:
        raw = _strict_loads(text)
        if not isinstance(raw, dict):
            raise ValueError("el cuerpo no es un objeto JSON")
    except Exception as e:
        _bump("rejected_malformed")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=text[:MAX_BODY_BYTES],
                 body_sha256=body_sha, content_length=len(body), parsed_ok=False,
                 auth_result="not_reached", verdict=m.VERDICT_REJECTED,
                 reject_reason=f"malformed:{e}"[:128])
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"error": "malformed_json", "message": str(e)})

    # -- 2. secreto compartido, en tiempo constante -------------------------
    expected = cfg.settings.tradingview_webhook_secret
    got = ""
    auth_block = raw.get("auth")
    if isinstance(auth_block, dict) and isinstance(auth_block.get("secret"), str):
        got = auth_block["secret"]
    # Secreto vacio en la configuracion => se rechaza TODO. Sin esta guarda,
    # `compare_digest("", "")` daria True y el endpoint aceptaria cualquier
    # cuerpo del mundo.
    if not expected or not secrets.compare_digest(got, expected):
        _bump("rejected_bad_secret")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=_redact(raw),
                 body_sha256=body_sha, content_length=len(body), parsed_ok=True,
                 auth_result="bad_secret", verdict=m.VERDICT_REJECTED,
                 reject_reason="bad_secret")
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail={"error": "forbidden"})

    # -- 3. campos hostiles y guarda de producto ----------------------------
    bad = _scan_forbidden(raw)
    if bad:
        _bump("rejected_forbidden_field")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=_redact(raw),
                 body_sha256=body_sha, content_length=len(body), parsed_ok=True,
                 auth_result="ok", verdict=m.VERDICT_REJECTED,
                 reject_reason=f"forbidden_field:{bad}"[:128])
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"error": "forbidden_field", "field": bad})
    try:
        # La misma guarda que protege la salida hacia la UI se aplica a la
        # ENTRADA: un campo de probabilidad que entra por aqui acabaria en la
        # tarjeta por la puerta de atras.
        assert_no_probability(raw)
    except AssertionError as e:
        _bump("rejected_probability_field")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=_redact(raw),
                 body_sha256=body_sha, content_length=len(body), parsed_ok=True,
                 auth_result="ok", verdict=m.VERDICT_REJECTED,
                 reject_reason="probability_field")
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"error": "probability_field",
                                    "message": str(e)})

    # -- 4. validacion de esquema -------------------------------------------
    try:
        payload = TvSignalPayload.model_validate(raw)
    except Exception as e:
        _bump("rejected_schema")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=_redact(raw),
                 body_sha256=body_sha, content_length=len(body), parsed_ok=True,
                 auth_result="ok", schema_name=str(raw.get("schema"))[:64],
                 verdict=m.VERDICT_REJECTED, reject_reason="schema_invalid")
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"error": "schema_invalid",
                                    "message": str(e)[:2000]})

    # -- 5. mercado: allowlist explicita ------------------------------------
    market = cfg.tickerid_map.get(payload.market.tv_tickerid)
    if market is None:
        _bump("rejected_unknown_market")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=_redact(raw),
                 body_sha256=body_sha, content_length=len(body), parsed_ok=True,
                 auth_result="ok", schema_name=payload.schema_id,
                 event=payload.event, verdict=m.VERDICT_REJECTED,
                 reject_reason=f"unknown_market:{payload.market.tv_tickerid}"[:128])
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"error": "unknown_market",
                                    "tv_tickerid": payload.market.tv_tickerid})

    # -- 6. politica de direccion -------------------------------------------
    side = payload.signal.side if payload.signal else None
    if side == "SHORT" and not cfg.settings.allow_short:
        # El Pine tiene otra politica que el backend. No es "una alerta que no
        # aplica": es una divergencia de configuracion que hay que ver.
        _bump("rejected_short_policy")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=_redact(raw),
                 body_sha256=body_sha, content_length=len(body), parsed_ok=True,
                 auth_result="ok", schema_name=payload.schema_id,
                 event=payload.event, market=market, side=side,
                 bar_open_ms=payload.time.bar_open_ms,
                 bar_close_ms=payload.time.bar_close_ms,
                 script_version=payload.source.script_version,
                 verdict=m.VERDICT_REJECTED, reject_reason="short_not_allowed")
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "short_not_allowed"})

    # -- 7. ventana temporal (replay) ---------------------------------------
    # Todo contra el reloj DEL SERVIDOR. `advisory_expires_at_ms` se ignora
    # para decidir: si el emisor pudiera fijar su propia caducidad, el TTL
    # dejaria de ser nuestro.
    bar_close = payload.time.bar_close_ms
    age_ms = now - bar_close
    if age_ms < -FUTURE_TOLERANCE_MS:
        _bump("rejected_future")
        _persist(cfg, received_at_ms=now, remote_ip=ip, raw_body=_redact(raw),
                 body_sha256=body_sha, content_length=len(body), parsed_ok=True,
                 auth_result="ok", schema_name=payload.schema_id,
                 event=payload.event, market=market, side=side,
                 bar_open_ms=payload.time.bar_open_ms, bar_close_ms=bar_close,
                 fired_at_ms=payload.time.fired_at_ms,
                 script_version=payload.source.script_version,
                 verdict=m.VERDICT_REJECTED, reject_reason="signal_from_future")
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "signal_from_the_future",
                                    "age_ms": age_ms})

    common = dict(
        received_at_ms=now, remote_ip=ip, token_id=payload.auth.token_id,
        raw_body=_redact(raw), body_sha256=body_sha, content_length=len(body),
        parsed_ok=True, auth_result="ok", schema_name=payload.schema_id,
        event=payload.event, market=market, side=side,
        bar_open_ms=payload.time.bar_open_ms, bar_close_ms=bar_close,
        fired_at_ms=payload.time.fired_at_ms,
        script_version=payload.source.script_version,
        tv_signal_id=payload.signal.tv_signal_id if payload.signal else None,
    )

    if age_ms > ACCEPT_LATE_MS:
        # Mas alla de 180 s ni siquiera sirve como dato de latencia: es una
        # repeticion. Se registra igual y se rechaza.
        _bump("rejected_stale")
        _persist(cfg, verdict=m.VERDICT_REJECTED,
                 reject_reason=f"stale:{age_ms // 1000}s", **common)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "stale_alert",
                                    "age_seconds": age_ms // 1000})

    # Entre el TTL y los 180 s se ACEPTA Y SE REGISTRA, pero no genera tarjeta:
    # llegar tarde es un dato de latencia, no una señal operable. Descartarla
    # en el borde destruiria justo la medicion que dira si 90 s son realistas.
    verdict = (m.VERDICT_ACCEPTED_LATE
               if age_ms > cfg.signal_ttl_seconds * 1000
               else m.VERDICT_ACCEPTED)
    reject_reason = None

    # El heartbeat no dedup ni revalida: existe para que el SILENCIO de
    # TradingView sea distinguible de un dia sin setups.
    if payload.event != EVENT_CANDIDATE or side is None:
        alert_id = _persist(cfg, verdict=verdict, reject_reason=reject_reason,
                            **common)
        _bump(f"event_{payload.event}")
        return {"ok": True, "signal_id": None, "verdict": verdict,
                "alert_id": alert_id}

    dedup = _dedup_key(cfg.strategy_id, market, side, payload.time.bar_open_ms)

    with cfg.session_factory() as session:
        # -- 8. marca de agua: repeticiones de cola larga --------------------
        hwm = session.execute(
            select(m.TvAlert.bar_close_ms).where(
                m.TvAlert.market == market, m.TvAlert.side == side,
                m.TvAlert.verdict.in_((m.VERDICT_ACCEPTED,
                                       m.VERDICT_ACCEPTED_LATE)))
            .order_by(m.TvAlert.bar_close_ms.desc())
        ).scalars().first()

        # -- 9. lado opuesto en la misma vela --------------------------------
        # `engine._direction()` garantiza que LONG y SHORT son mutuamente
        # excluyentes. Recibir ambos para la misma vela significa que el Pine y
        # Python discrepan en algo fundamental.
        opposite = session.execute(
            select(m.TvAlert).where(
                m.TvAlert.market == market,
                m.TvAlert.bar_open_ms == payload.time.bar_open_ms,
                m.TvAlert.side.isnot(None), m.TvAlert.side != side,
                m.TvAlert.verdict.in_((m.VERDICT_ACCEPTED,
                                       m.VERDICT_ACCEPTED_LATE)))
        ).scalars().first()

        # -- 10. deduplicacion ----------------------------------------------
        twin = session.execute(
            select(m.TvAlert).where(m.TvAlert.dedup_key == dedup)
        ).scalar_one_or_none()

    if opposite is not None:
        _bump("rejected_side_conflict")
        _persist(cfg, verdict=m.VERDICT_REJECTED,
                 reject_reason="side_conflict_same_bar", **common)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "side_conflict_same_bar",
                                    "other_side": opposite.side})

    if twin is not None:
        # Misma clave: no se re-procesa nunca. Que el cuerpo coincida o no
        # cambia el diagnostico, no la accion.
        same_body = secrets.compare_digest(twin.body_sha256, body_sha)
        _bump("duplicate" if same_body else "duplicate_body_mismatch")
        _persist(cfg, verdict=m.VERDICT_DUPLICATE, duplicate_of=twin.id,
                 dedup_key=None,
                 reject_reason=None if same_body else "duplicate_body_mismatch",
                 **common)
        return {"ok": True, "signal_id": None,
                "verdict": m.VERDICT_DUPLICATE,
                "alert_id": twin.id,
                "body_matches": same_body}

    if hwm is not None and bar_close < hwm:
        _bump("rejected_stale_bar")
        _persist(cfg, verdict=m.VERDICT_REJECTED, reject_reason="stale_bar",
                 **common)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "stale_bar",
                                    "high_water_mark_ms": hwm})

    # -- 11. persistir crudo ANTES de revalidar -----------------------------
    try:
        alert_id = _persist(cfg, dedup_key=dedup, verdict=verdict,
                            reject_reason=reject_reason, **common)
    except Exception:
        # El indice unico de `dedup_key` es quien gana la carrera entre dos
        # POST simultaneos; ninguna comprobacion en memoria puede hacerlo.
        _bump("duplicate_race")
        return {"ok": True, "signal_id": None, "verdict": m.VERDICT_DUPLICATE,
                "alert_id": None, "body_matches": None}

    # -- 12. revalidacion ASINCRONA -----------------------------------------
    # Nunca se ejecuta nada aqui: se compara contra el motor nativo y se
    # registra la discrepancia. Una alerta jamas crea una señal.
    if verdict == m.VERDICT_ACCEPTED and cfg.sink is not None:
        background.add_task(cfg.sink.submit, alert_id,
                            _canonical(payload, market, alert_id))

    _bump("accepted")
    return {"ok": True, "signal_id": None, "verdict": verdict,
            "alert_id": alert_id}
