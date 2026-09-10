"""
db/models.py — el journal relacional (PRD 16, 19, 21).

INVARIANTE CENTRAL, y es la razon de que este modulo exista antes que ninguna
pantalla: la fila `signals` se ESCRIBE ANTES DE CONOCER EL RESULTADO, con todas
las columnas de resultado en NULL, en la misma transaccion que sus
`gate_evaluations`, y antes de que pueda existir ninguna `decisions` (lo
garantiza la clave foranea). Se registran igual las señales saltadas, las
expiradas, las bloqueadas por el limite diario y las que fallaron el sizing.

Sin eso, el journal solo contendria los trades que salieron bien de recordar, y
las dos preguntas que abren el limite de 1 trade/dia — "¿el primer A+ del dia
fue peor que el mejor?" y "¿la seleccion discrecional de Mark mejora la
estrategia?" — quedarian sin respuesta posible para siempre (AUDIT C-14).

DECISIONES QUE NO SON OBVIAS:

  * El journal JSONL de `services/scanner.py` NO se sustituye. Aquel puede
    arrancar hoy, sobrevive a un esquema a medio migrar y se lee con cualquier
    herramienta. Esta base de datos se alimenta DESDE el, nunca al reves.
  * `daily_state` es una TABLA, no el dataclass en memoria de risk/limits.py.
    Un reinicio del proceso reseteaba `trades_opened` y, peor, el flag
    `critical`, que por diseño no debe tener recuperacion automatica: el estado
    que existe para forzar que un humano mire la cuenta se limpiaba solo con un
    restart.
  * `trades.sl_price` es el stop ESTRUCTURAL y no se recalcula desde el fill;
    `tp_price` si (PRD 17 pasos 4-5). Son dos columnas distintas a proposito.
  * `funding_paid_usd` es columna propia y no un sumando escondido en fees
    (AUDIT C-04): con tenencias de hasta 24 h ya no es ruido y su signo puede
    invertirse.
  * Prohibicion estructural: ninguna columna puede llamarse de forma que un
    usuario la lea como probabilidad. `assert_no_probability_columns()` lo
    comprueba sobre el metadata, no sobre un payload, para que el fallo ocurra
    en el commit que añade la columna y no meses despues en la UI.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import (JSON, BigInteger, Boolean, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, create_engine)
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column, relationship,
                            sessionmaker)

# Se importa la MISMA tupla que usa scoring, no una copia: dos listas de
# palabras prohibidas divergen en cuanto alguien añade una a un solo lado.
from ..strategies.hype.scoring import _FORBIDDEN


# --- vocabulario del journal ------------------------------------------------
# Strings y no Enum de SQL: el valor tiene que poder leerse tal cual en un
# volcado de la base dentro de tres meses sin consultar el codigo.

SOURCE_NATIVE = "native_scan"
SOURCE_TV = "tv_webhook"

STATUS_CANDIDATE = "CANDIDATE"
STATUS_A_PLUS_READY = "A_PLUS_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_EXPIRED_SEEN = "EXPIRED_SEEN"
STATUS_EXPIRED_UNSEEN = "EXPIRED_UNSEEN"
STATUS_SKIPPED = "SKIPPED"
STATUS_ENTERED = "ENTERED"

DECISION_ENTER = "ENTER"
DECISION_SKIP = "SKIP"
DECISION_EXPIRED = "EXPIRED"
OUTCOME_ACCEPTED = "ACCEPTED"
OUTCOME_REJECTED = "REJECTED"

VERDICT_ACCEPTED = "ACCEPTED"
VERDICT_ACCEPTED_LATE = "ACCEPTED_LATE"
VERDICT_DUPLICATE = "DUPLICATE"
VERDICT_REJECTED = "REJECTED"

# Clases de discrepancia TV vs backend. Ninguna se silencia.
DISC_MATCH = "MATCH"
DISC_FEED_DELTA = "FEED_DELTA"
DISC_LEVEL_DRIFT = "LEVEL_DRIFT"
DISC_CONFIG_DRIFT = "CONFIG_DRIFT"
DISC_SIDE_CONFLICT = "SIDE_CONFLICT"
DISC_TV_ONLY = "TV_ONLY"
DISC_NATIVE_ONLY = "NATIVE_ONLY"
DISC_PENDING_NATIVE_BAR = "PENDING_NATIVE_BAR"

PHASE_ON_SIGNAL = "on_signal"
PHASE_ON_ENTER = "on_enter"


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# tv_alerts — crudo e inmutable
# ---------------------------------------------------------------------------

class TvAlert(Base):
    """Lo que llego por el webhook, tal cual llego.

    Se persiste ANTES de validar el esquema y antes de revalidar nada: no se
    puede analizar lo que se descarto en el borde, y un payload rechazado suele
    ser el que mas informacion contiene.

    `raw_body` se guarda con el secreto REDACTADO. Ese secreto no aporta
    integridad ni frescura (viaja identico en cada mensaje) pero si se filtra
    permite a cualquiera emitir payloads aceptados, y un journal es exactamente
    el sitio del que se sacan copias.
    """
    __tablename__ = "tv_alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    received_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    remote_ip: Mapped[Optional[str]] = mapped_column(String(64))
    token_id: Mapped[Optional[str]] = mapped_column(String(16))
    raw_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_length: Mapped[int] = mapped_column(Integer, default=0)

    schema_name: Mapped[Optional[str]] = mapped_column(String(64))
    event: Mapped[Optional[str]] = mapped_column(String(32))
    parsed_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_result: Mapped[str] = mapped_column(String(32), default="unchecked")

    # Clave canonica calculada por el BACKEND. El `tv_signal_id` que manda el
    # Pine se guarda aparte y solo se compara: no se confia en el.
    dedup_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    tv_signal_id: Mapped[Optional[str]] = mapped_column(String(128))
    duplicate_of: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("tv_alerts.id"))

    market: Mapped[Optional[str]] = mapped_column(String(32))
    side: Mapped[Optional[str]] = mapped_column(String(8))
    bar_open_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    bar_close_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    fired_at_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    script_version: Mapped[Optional[str]] = mapped_column(String(32))

    verdict: Mapped[str] = mapped_column(String(24), default=VERDICT_REJECTED)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(128))

    __table_args__ = (
        # La unicidad la garantiza la base de datos, no un `if` en el handler:
        # dos POST concurrentes de la misma alerta pasan cualquier comprobacion
        # hecha en memoria.
        UniqueConstraint("dedup_key", name="uq_tv_alert_dedup"),
        Index("ix_tv_alert_recv", "received_at_ms"),
    )


# ---------------------------------------------------------------------------
# signals — la fila que se escribe antes de conocer el resultado
# ---------------------------------------------------------------------------

class SignalRow(Base):
    """Una señal del motor con todo su contexto operativo.

    Se llama `SignalRow` y no `Signal` a proposito: `engine.Signal` es el valor
    inmutable que produce la estrategia, y esta fila es su registro persistente
    mas lo que el motor NO puede saber (sizing, estado del dia, equity).
    Confundirlos lleva a persistir cosas que la estrategia nunca calculo.
    """
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)

    # identidad reproducible
    strategy_id: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(32), default="v2")
    # Hash de la Config congelada. Sin el, una señal de hace tres meses no se
    # puede reproducir: no se sabria con que parametros se genero.
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default=SOURCE_NATIVE)
    tv_alert_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("tv_alerts.id"))

    # tiempo
    market: Mapped[str] = mapped_column(String(32), default="HYPE")
    side: Mapped[str] = mapped_column(String(8))
    bar_open_ms: Mapped[int] = mapped_column(BigInteger)
    bar_close_ms: Mapped[int] = mapped_column(BigInteger)
    session_start_ms: Mapped[int] = mapped_column(BigInteger)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)

    # niveles (estructura primero, objetivo despues: PRD 8)
    entry_ref: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    structural_level: Mapped[Optional[float]] = mapped_column(Float)
    tp: Mapped[float] = mapped_column(Float)
    rr: Mapped[float] = mapped_column(Float)
    rr_arm: Mapped[Optional[str]] = mapped_column(String(4))
    risk_per_unit: Mapped[float] = mapped_column(Float)
    cost_r: Mapped[Optional[float]] = mapped_column(Float)
    cost_frac: Mapped[Optional[float]] = mapped_column(Float)
    # PROVISIONAL mientras sea False: el Cost_R se calculo con un fee supuesto,
    # no con el tier real de la cuenta.
    fee_taker_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    clearance_r: Mapped[Optional[float]] = mapped_column(Float)
    clearance_level: Mapped[Optional[float]] = mapped_column(Float)
    clearance_kind: Mapped[Optional[str]] = mapped_column(String(16))

    # evidencia
    checks: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    checklist: Mapped[List[str]] = mapped_column(JSON, default=list)
    features: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    score_passed: Mapped[int] = mapped_column(Integer, default=0)
    score_total: Mapped[int] = mapped_column(Integer, default=0)
    rules_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    is_a_plus: Mapped[bool] = mapped_column(Boolean, default=False)
    stage: Mapped[str] = mapped_column(String(24), default="WAITING")
    scan_reason: Mapped[str] = mapped_column(String(64), default="")

    # contexto operativo
    vwap: Mapped[Optional[float]] = mapped_column(Float)
    atr: Mapped[Optional[float]] = mapped_column(Float)
    data_age_seconds: Mapped[Optional[float]] = mapped_column(Float)
    equity_usd: Mapped[Optional[float]] = mapped_column(Float)
    risk_mode: Mapped[Optional[str]] = mapped_column(String(16))
    venue_spec: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    qty: Mapped[Optional[float]] = mapped_column(Float)
    notional: Mapped[Optional[float]] = mapped_column(Float)
    planned_risk_usd: Mapped[Optional[float]] = mapped_column(Float)
    budget_usd: Mapped[Optional[float]] = mapped_column(Float)
    sizing_ok: Mapped[Optional[bool]] = mapped_column(Boolean)
    sizing_reason: Mapped[Optional[str]] = mapped_column(String(64))

    # estado
    status: Mapped[str] = mapped_column(String(24), default=STATUS_CANDIDATE)
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(128))
    # Distinguir EXPIRED_SEEN de EXPIRED_UNSEEN decide si el problema es de
    # entrega o del reloj de 90 s. Colapsarlos borra la unica pista.
    seen_at_ms: Mapped[Optional[int]] = mapped_column(BigInteger)

    gates: Mapped[List["GateEvaluation"]] = relationship(back_populates="signal")
    decisions: Mapped[List["Decision"]] = relationship(back_populates="signal")

    __table_args__ = (
        # Una señal por (estrategia, mercado, lado, vela). El escaner nativo y
        # el webhook de TradingView colapsan en el mismo evento en vez de crear
        # dos señales para la misma vela.
        UniqueConstraint("strategy_id", "market", "side", "bar_open_ms",
                         name="uq_signal_key"),
        Index("ix_signal_status", "status", "expires_at_ms"),
    )


class GateEvaluation(Base):
    """Cada gate, con su motivo y la fase en que se evaluo.

    Aqui queda con nombre propio el A+ que el limite diario dejo pasar. Es la
    version persistente de `limits.record_blocked_a_plus()`, que hoy vive en
    una lista en memoria y se pierde al reiniciar el proceso.
    """
    __tablename__ = "gate_evaluations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    signal_id: Mapped[str] = mapped_column(String(32), ForeignKey("signals.id"))
    gate_name: Mapped[str] = mapped_column(String(32))
    passed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[Optional[str]] = mapped_column(String(128))
    phase: Mapped[str] = mapped_column(String(16), default=PHASE_ON_SIGNAL)
    evaluated_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    signal: Mapped[SignalRow] = relationship(back_populates="gates")

    __table_args__ = (Index("ix_gate_signal", "signal_id", "phase"),)


class Decision(Base):
    """ENTER / SKIP / EXPIRED. Se escribe SIEMPRE, pase o no el gate.

    Un ENTER rechazado por gate es informacion de primera: el humano dijo si y
    la maquina dijo no. Nunca es "solo un error de UI".
    """
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    signal_id: Mapped[str] = mapped_column(String(32), ForeignKey("signals.id"))
    # Una por clic del frontend. Un reintento de red devuelve la MISMA
    # respuesta en lugar de abrir una segunda posicion.
    idempotency_key: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(8))
    outcome: Mapped[str] = mapped_column(String(16))
    gate_reason: Mapped[Optional[str]] = mapped_column(String(128))
    decided_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    decision_latency_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    # Sin el precio y el drift EN EL MOMENTO de decidir no se puede
    # reconstruir despues que veia Mark cuando decidio.
    price_at_decision: Mapped[Optional[float]] = mapped_column(Float)
    drift_r_at_decision: Mapped[Optional[float]] = mapped_column(Float)
    signal_age_seconds: Mapped[Optional[float]] = mapped_column(Float)
    reason_code: Mapped[Optional[str]] = mapped_column(String(32))
    reason_text: Mapped[Optional[str]] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(32), default="mark")
    response: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    signal: Mapped[SignalRow] = relationship(back_populates="decisions")

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_decision_idem"),
    )


class TradeRow(Base):
    """Un trade real o simulado. `is_synthetic` marca los de fixture y replay:
    mezclarlos con los reales contamina toda metrica posterior."""
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    signal_id: Mapped[str] = mapped_column(String(32), ForeignKey("signals.id"))
    decision_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("decisions.id"))
    mode: Mapped[str] = mapped_column(String(8))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)

    intended_entry: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[Optional[float]] = mapped_column(Float)
    fill_ts_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    qty: Mapped[float] = mapped_column(Float)
    entry_exchange_id: Mapped[Optional[str]] = mapped_column(String(64))
    sl_exchange_id: Mapped[Optional[str]] = mapped_column(String(64))
    tp_exchange_id: Mapped[Optional[str]] = mapped_column(String(64))
    # ESTRUCTURAL: no se recalcula desde el fill (PRD 17 paso 4).
    sl_price: Mapped[float] = mapped_column(Float)
    # Este SI se recalcula: R cambio al cambiar la entrada real (paso 5).
    tp_price: Mapped[Optional[float]] = mapped_column(Float)
    r_actual: Mapped[Optional[float]] = mapped_column(Float)
    protection_verified_at_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    critical_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    critical_reason: Mapped[Optional[str]] = mapped_column(String(128))

    exit_ts_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(16))
    gross_pnl_usd: Mapped[Optional[float]] = mapped_column(Float)
    fees_paid_usd: Mapped[Optional[float]] = mapped_column(Float)
    funding_paid_usd: Mapped[Optional[float]] = mapped_column(Float)
    net_pnl_usd: Mapped[Optional[float]] = mapped_column(Float)
    mfe_r: Mapped[Optional[float]] = mapped_column(Float)
    mae_r: Mapped[Optional[float]] = mapped_column(Float)
    hold_seconds: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        # Una señal no puede producir dos trades ni con una carrera perfecta.
        UniqueConstraint("signal_id", name="uq_trade_signal"),
    )


class HypotheticalOutcome(Base):
    """Resultado que HABRIA tenido una señal sin trade: saltada, bloqueada por
    el limite diario, expirada o rechazada por gate (AUDIT C-14). Lo resuelve
    un job posterior recorriendo velas nativas hacia delante."""
    __tablename__ = "hypothetical_outcomes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    signal_id: Mapped[str] = mapped_column(String(32), ForeignKey("signals.id"))
    resolved_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    outcome: Mapped[str] = mapped_column(String(16))     # TP | SL | TIME_STOP
    r_result: Mapped[Optional[float]] = mapped_column(Float)
    mfe_r: Mapped[Optional[float]] = mapped_column(Float)
    mae_r: Mapped[Optional[float]] = mapped_column(Float)
    hold_seconds: Mapped[Optional[float]] = mapped_column(Float)
    resolver_version: Mapped[str] = mapped_column(String(16), default="v1")

    __table_args__ = (UniqueConstraint("signal_id", name="uq_hypo_signal"),)


class Discrepancy(Base):
    """TradingView vs backend, campo a campo. Se escribe TAMBIEN cuando
    coinciden (`MATCH`): sin los aciertos no se puede calcular la tasa de
    discrepancia, que es la medicion del criterio 6 del MVP.

    Tener dos implementaciones de las mismas reglas sobre dos feeds distintos
    es un oraculo diferencial gratis. Su desacuerdo es el detector mas barato
    de bugs, de drift de configuracion y de problemas de datos. Silenciarlo no
    seria limpiar ruido: seria declarar cumplido un criterio del MVP sin
    haberlo verificado nunca.
    """
    __tablename__ = "discrepancies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tv_alert_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("tv_alerts.id"))
    signal_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("signals.id"))
    kind: Mapped[str] = mapped_column(String(24))        # DISC_*
    severity: Mapped[str] = mapped_column(String(16))
    fields: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    script_version: Mapped[Optional[str]] = mapped_column(String(32))
    config_echo_diff: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    tv_latency_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    native_scan_reason: Mapped[Optional[str]] = mapped_column(String(64))
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    __table_args__ = (Index("ix_disc_kind", "kind", "created_at_ms"),)


class DailyStateRow(Base):
    """Estado del dia, persistido. Una fila por sesion UTC.

    `critical` y `manual_kill` DEBEN sobrevivir a un reinicio: un kill switch
    que se limpia solo con un restart no es un kill switch, es un temporizador.
    """
    __tablename__ = "daily_state"

    session_start_ms: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(String(24), default="WAITING")
    trades_opened: Mapped[int] = mapped_column(Integer, default=0)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    manual_kill: Mapped[bool] = mapped_column(Boolean, default=False)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    critical_reason: Mapped[str] = mapped_column(String(128), default="")
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class KillSwitchEvent(Base):
    """Cada activacion y cada limpieza de un kill switch, con quien la hizo.

    Se registra tambien la LIMPIEZA: un estado CRITICAL que desaparece sin
    dejar rastro de quien lo limpio es indistinguible de uno que nunca ocurrio.
    """
    __tablename__ = "kill_switch_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_start_ms: Mapped[Optional[int]] = mapped_column(BigInteger)
    switch: Mapped[str] = mapped_column(String(32))      # limits.KILL_*
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[Optional[str]] = mapped_column(String(256))
    actor: Mapped[str] = mapped_column(String(32), default="system")
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    __table_args__ = (Index("ix_kill_time", "created_at_ms"),)


# ---------------------------------------------------------------------------
# guardas y utilidades
# ---------------------------------------------------------------------------

def assert_no_probability_columns() -> None:
    """Falla si alguna columna se leeria como probabilidad (PRD 14).

    Hermano del test 31 del TEST_PLAN, pero a nivel de ESQUEMA: una columna
    prohibida solo llega a la UI si antes existe en la tabla. Comprobarlo aqui
    hace que el fallo ocurra en el commit que la añade, no meses despues.
    """
    for table in Base.metadata.tables.values():
        for col in table.columns:
            low = col.name.lower()
            for word in _FORBIDDEN:
                if word in low:
                    raise AssertionError(
                        f"Columna prohibida {table.name}.{col.name}: el sistema "
                        "no expone probabilidades sin un modelo calibrado OOS "
                        "(PRD 14)."
                    )


def make_engine(database_url: str, echo: bool = False):
    """Engine. SQLite solo para desarrollo local (PRD 16); produccion Postgres.

    `check_same_thread=False` porque FastAPI atiende el webhook y la UI en
    hilos distintos; la serializacion real la da el pool, no ese flag.
    """
    kwargs: Dict[str, Any] = {"echo": echo, "future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **kwargs)


def make_session_factory(engine) -> sessionmaker:
    """`expire_on_commit=False` a proposito: las filas se leen despues del
    commit para construir la respuesta HTTP, y con el default cada atributo
    dispararia un SELECT nuevo dentro del handler."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


def create_all(engine) -> None:
    """Crea el esquema. Se valida la prohibicion ANTES de crear nada: si el
    esquema es ilegal, no debe llegar a existir en disco."""
    assert_no_probability_columns()
    Base.metadata.create_all(engine)


def supports_row_locks(session) -> bool:
    """SQLite no implementa SELECT ... FOR UPDATE. Se consulta el dialecto en
    vez de asumirlo para que el mismo codigo de ENTER corra en local y en
    produccion sin duplicar la rama."""
    try:
        return session.get_bind().dialect.name not in ("sqlite",)
    except Exception:
        return False
