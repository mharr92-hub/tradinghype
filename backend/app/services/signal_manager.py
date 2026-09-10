"""
services/signal_manager.py — ciclo de vida de la señal (PRD 10, 13, 14, 16, 17).

El motor de estrategia produce CANDIDATOS y nada mas. Este modulo es el unico
sitio donde un candidato puede ascender a A_PLUS_READY, y solo tras evaluar los
tres gates que el motor no puede ver porque no dependen de las velas:

    sizing_ok            — ¿cabe la posicion en el venue sin subir el riesgo?
    daily_limit_ok       — ¿queda cupo hoy?
    kill_switches_clear  — ¿esta el sistema sano?

`scoring.score()` recibe esos tres en `operational`. Sin ellos NUNCA devuelve
`is_a_plus`, y eso es deliberado: un setup impecable que no cabe en el tamaño
minimo del venue no es un trade A+, es un trade imposible.

TRES DECISIONES QUE NO SON OBVIAS:

1. LA DISCREPANCIA TV/BACKEND NO SE SILENCIA, Y TAMPOCO PARA LAS OPERACIONES.
   Se escribe una fila por alerta procesada, incluidas las que COINCIDEN: sin
   los aciertos no hay denominador y la tasa de discrepancia no existe. Cuando
   la discrepancia es grave el puente pasa a UNTRUSTED, que degrada una
   afirmacion de calidad y NO toca `can_open_new_trade()`. Si una discrepancia
   detuviera las operaciones, cualquiera capaz de forjar un payload —el canal
   menos confiable del sistema— podria impedir que Mark opere.

2. LA ALERTA DE TRADINGVIEW NUNCA CREA UNA SEÑAL. Como mucho MARCA una señal
   que el escaner nativo ya produjo. TradingView calcula sobre otro feed, con
   otras semillas de indicadores y con hasta una vela de 5 m de retraso en las
   fronteras HTF; es *aproximadamente* la estrategia, nunca la estrategia
   (PRD 4, 15).

3. EL TTL SE MIDE DESDE EL CIERRE DE LA VELA, no desde su apertura ni desde que
   la señal se persistio. `Signal.ts` es la apertura, asi que la señal no
   existe hasta ts + 5 min; medir desde la apertura regalaria 5 minutos de vida
   a algo que solo debe durar 90 segundos (order_guard.signal_age_seconds).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from sqlalchemy import select

from ..core.config import MODE_PAPER, MODE_RESEARCH, MODE_SHADOW, Settings
from ..db import models as m
from ..execution.order_guard import (LiveExecutionDisabled,
                                     assert_can_send_orders, drift_r,
                                     revalidate, signal_age_seconds)
from ..risk.limits import (KILL_DAILY_LIMIT, DayState, Gate, can_open_new_trade)
from ..risk.sizing import (Sizing, VenueSpec, expected_profit_usd,
                           size_position)
from ..strategies.hype import engine
from ..strategies.hype.common import Config
from ..strategies.hype.scoring import assert_no_probability, score
from ..strategies.indicators import BAR_5M_MS, Candle, session_start_ms

MARKET_HYPE = "HYPE"

# Modos en los que ENTER simula en vez de enviar nada al venue. La lista es
# explicita y no "todo lo que no sea TINY/LIVE": un modo nuevo debe decidir a
# que lado cae, no heredar el permisivo por descuido.
SIMULATED_MODES = (MODE_RESEARCH, MODE_PAPER, MODE_SHADOW)


# ---------------------------------------------------------------------------
# Tolerancias de comparacion TV vs backend (declaradas, no improvisadas)
# ---------------------------------------------------------------------------
#
# Una tolerancia estrecha en volumen genera ruido sin informacion: dos
# proveedores agregan distinto. Una tolerancia cualquiera en un booleano oculta
# el unico tipo de hallazgo que siempre importa. Por eso no hay una constante
# global, hay una por familia de campo.

PRICE_TOL_BPS = 2.0          # max(1 tick, 2 bps) — el tick lo pone el venue
REL_TOL = {
    "atr": 0.01,
    "risk_per_unit": 0.01,
    "volume": 0.05,
    "avg_volume_20": 0.05,
    "macd_hist": 0.02,
}
ABS_TOL = {
    "rsi": 0.5,
    "clearance_r": 0.05,
}

TRUST_TRUSTED = "TRUSTED"
TRUST_UNTRUSTED = "UNTRUSTED"

# Discrepancias que ensucian la afirmacion de paridad y por tanto marcan el
# puente como no fiable hasta que un humano lo limpie.
_UNTRUSTING = (m.DISC_SIDE_CONFLICT, m.DISC_CONFIG_DRIFT, m.DISC_LEVEL_DRIFT)


class MarketDataPort(Protocol):
    """Lo minimo que el gestor necesita del venue. Es un Protocol y no la clase
    concreta para que los tests inyecten velas de fixture sin red, y para que
    este modulo no dependa de httpx."""

    def multi_timeframe(self) -> Tuple[List[Candle], List[Candle], List[Candle]]: ...
    def data_age_seconds(self, c5: List[Candle],
                         now_ms: Optional[int] = None) -> float: ...
    def mid_price(self) -> float: ...


@dataclass(frozen=True)
class ActionResult:
    """Resultado de una accion de usuario. `code` es estable y legible: la API
    lo traduce a HTTP, pero el journal guarda el codigo, no el numero."""
    ok: bool
    code: str
    payload: Dict[str, Any] = field(default_factory=dict)


def config_hash(cfg: Config) -> str:
    """Huella de la Config con la que se genero una señal.

    Sin esto, reproducir una señal de hace tres meses es adivinar: los mismos
    datos con otro `band_k` o otro `rr` producen otra cosa y nada lo delataria.
    """
    payload = json.dumps({k: v for k, v in sorted(cfg.__dict__.items())},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _price_tol(price: float, tick: float) -> float:
    return max(tick, abs(price) * PRICE_TOL_BPS / 10_000.0)


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


class SignalManager:
    """Duenio del ciclo de vida: CANDIDATE -> A_PLUS_READY -> ENTERED/SKIPPED/
    EXPIRED. Ninguna otra pieza escribe en `signals`."""

    def __init__(self, settings: Settings, cfg: Config, session_factory,
                 *, data: Optional[MarketDataPort] = None,
                 venue: Optional[VenueSpec] = None,
                 equity_usd: Optional[float] = None,
                 paper_executor=None):
        self.settings = settings
        self.cfg = cfg
        self.session_factory = session_factory
        self.data = data
        # El VenueSpec se LEE de la API del venue; el default aqui es solo un
        # arranque en frio y `sizing_ok` fallara con motivo si no cuadra.
        self.venue = venue or VenueSpec()
        self.equity_usd = settings.equity_usd if equity_usd is None else equity_usd
        # Ejecutor simulado inyectable. Si no esta cableado, ENTER en PAPER
        # RECHAZA en vez de inventarse un fill: `entry = close` fue exactamente
        # el sesgo optimista que documenta AUDIT C-05.
        self.paper_executor = paper_executor
        self.bridge_trust = TRUST_TRUSTED
        self.bridge_untrusted_reason = ""

    # -- utilidades ---------------------------------------------------------

    def _now(self, now_ms: Optional[int] = None) -> int:
        return int(time.time() * 1000) if now_ms is None else int(now_ms)

    def _ttl_ms(self) -> int:
        return int(self.cfg.signal_ttl_seconds * 1000)

    def _day_row(self, session, now_ms: int, *, lock: bool = False
                 ) -> m.DailyStateRow:
        """Fila del dia, creandola si no existe. El dia arranca con el mismo
        reset que el VWAP (00:00 UTC): si el limite diario y la sesion de VWAP
        no compartieran frontera, "un trade al dia" significaria otra cosa cada
        vez que se cruzara la medianoche local."""
        start = session_start_ms(now_ms, self.cfg.session_utc_hour)
        stmt = select(m.DailyStateRow).where(
            m.DailyStateRow.session_start_ms == start)
        if lock and m.supports_row_locks(session):
            # Sin el bloqueo, dos ENTER simultaneos leen trades_opened = 0 y
            # ambos pasan el limite diario.
            stmt = stmt.with_for_update()
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            prev = session.execute(
                select(m.DailyStateRow)
                .order_by(m.DailyStateRow.session_start_ms.desc())
            ).scalars().first()
            row = m.DailyStateRow(session_start_ms=start)
            if prev is not None:
                # El cambio de dia reinicia el CUPO, no las alarmas. Un kill
                # switch que se apaga solo a las 00:00 UTC es un temporizador.
                row.manual_kill = prev.manual_kill
                row.critical = prev.critical
                row.critical_reason = prev.critical_reason
                row.consecutive_losses = prev.consecutive_losses
            session.add(row)
            session.flush()
        return row

    @staticmethod
    def _to_day_state(row: m.DailyStateRow) -> DayState:
        return DayState(session_start_ms=row.session_start_ms, state=row.state,
                        trades_opened=row.trades_opened,
                        realized_pnl_usd=row.realized_pnl_usd,
                        consecutive_losses=row.consecutive_losses,
                        manual_kill=row.manual_kill, critical=row.critical,
                        critical_reason=row.critical_reason)

    @staticmethod
    def _from_day_state(row: m.DailyStateRow, day: DayState) -> None:
        row.state = day.state
        row.trades_opened = day.trades_opened
        row.realized_pnl_usd = day.realized_pnl_usd
        row.consecutive_losses = day.consecutive_losses
        row.manual_kill = day.manual_kill
        row.critical = day.critical
        row.critical_reason = day.critical_reason
        row.updated_at_ms = m.now_ms()

    def _operational_gates(self, day: DayState, sizing: Sizing,
                           data_age_seconds: float,
                           has_open_position: bool = False,
                           api_error_count: int = 0) -> Tuple[Dict[str, bool], Gate]:
        """Los tres gates operativos, separados a proposito.

        `can_open_new_trade()` devuelve un solo motivo y el orden importa: si
        falla por algo que indica sistema roto, `daily_limit_ok` se marca False
        aunque quede cupo. Es fail-closed: no se puede afirmar que el cupo esta
        disponible cuando ni siquiera se llego a comprobar.
        """
        gate = can_open_new_trade(
            day, self.cfg, self.equity_usd, has_open_position,
            data_age_seconds, self.settings.max_data_age_seconds,
            api_error_count)
        daily_limit_ok = bool(gate)
        kill_clear = bool(gate)
        if not gate and gate.reason == KILL_DAILY_LIMIT:
            # El unico caso en el que el sistema esta sano y aun asi no se abre.
            kill_clear = True
            daily_limit_ok = False
        return ({"sizing_ok": sizing.ok,
                 "daily_limit_ok": daily_limit_ok,
                 "kill_switches_clear": kill_clear}, gate)

    # -- creacion -----------------------------------------------------------

    def record_scan(self, result: engine.ScanResult, *,
                    data_age_seconds: float,
                    source: str = m.SOURCE_NATIVE,
                    tv_alert_id: Optional[str] = None,
                    market: str = MARKET_HYPE,
                    now_ms: Optional[int] = None) -> Optional[str]:
        """Persiste un candidato del motor con sus gates operativos.

        Devuelve el id de la señal, o None si el scan no produjo candidato (ese
        caso ya queda en el journal JSONL del escaner, que registra TAMBIEN los
        rechazos con su motivo).

        La fila se escribe con TODAS las columnas de resultado en NULL y en la
        misma transaccion que sus `gate_evaluations`. Se escribe aunque el
        sizing falle y aunque el limite diario la bloquee: justamente esas son
        las que hacen falta para responder si el limite cuesta o ahorra dinero.
        """
        sig = result.signal
        if sig is None:
            return None

        now = self._now(now_ms)
        bar_close = sig.ts + BAR_5M_MS

        with self.session_factory() as session:
            existing = session.execute(
                select(m.SignalRow).where(
                    m.SignalRow.strategy_id == sig.strategy_id,
                    m.SignalRow.market == market,
                    m.SignalRow.side == sig.side,
                    m.SignalRow.bar_open_ms == sig.ts)
            ).scalar_one_or_none()
            if existing is not None:
                # Misma vela, misma señal. Reescanear no puede duplicarla.
                return existing.id

            day_row = self._day_row(session, now)
            day = self._to_day_state(day_row)

            sizing = size_position(sig.entry_ref, sig.stop, sig.side, self.cfg,
                                   self.equity_usd, self.venue, sig.cost_frac)
            operational, gate = self._operational_gates(day, sizing,
                                                        data_age_seconds)
            full = score(sig.side, result.checks,
                         require_momentum_long=self.cfg.require_momentum_long,
                         operational=operational)

            status = m.STATUS_A_PLUS_READY if full.is_a_plus else m.STATUS_CANDIDATE
            blocked_reason = None
            if not full.is_a_plus:
                if not sizing.ok:
                    status = m.STATUS_BLOCKED
                    blocked_reason = f"sizing:{sizing.reason}"
                elif not gate:
                    status = m.STATUS_BLOCKED
                    blocked_reason = gate.reason

            row = m.SignalRow(
                strategy_id=sig.strategy_id, config_hash=config_hash(self.cfg),
                source=source, tv_alert_id=tv_alert_id, market=market,
                side=sig.side, bar_open_ms=sig.ts, bar_close_ms=bar_close,
                session_start_ms=session_start_ms(sig.ts,
                                                  self.cfg.session_utc_hour),
                created_at_ms=now, expires_at_ms=bar_close + self._ttl_ms(),
                entry_ref=sig.entry_ref, stop=sig.stop,
                structural_level=_num(result.checks.get("struct_level")),
                tp=sig.tp, rr=sig.rr, rr_arm=_rr_arm(sig.rr),
                risk_per_unit=sig.risk_per_unit, cost_r=sig.cost_r,
                cost_frac=sig.cost_frac,
                fee_taker_confirmed=self.cfg.fee_taker_confirmed,
                clearance_r=sig.clearance_r,
                clearance_level=_num(result.checks.get("clearance_level")),
                clearance_kind=result.checks.get("clearance_kind"),
                checks=dict(result.checks), checklist=list(sig.checklist),
                features=dict(sig.features), score_passed=full.passed,
                score_total=full.total, rules_complete=full.rules_complete,
                is_a_plus=full.is_a_plus, stage=full.stage,
                scan_reason=result.reason,
                vwap=_num(result.checks.get("vwap")),
                atr=_num(result.checks.get("atr")),
                data_age_seconds=data_age_seconds, equity_usd=self.equity_usd,
                risk_mode=self.cfg.risk_mode, venue_spec=asdict(self.venue),
                qty=sizing.qty, notional=sizing.notional,
                planned_risk_usd=sizing.risk_usd, budget_usd=sizing.budget_usd,
                sizing_ok=sizing.ok, sizing_reason=sizing.reason,
                status=status, blocked_reason=blocked_reason,
            )
            session.add(row)
            session.flush()

            for name, passed in operational.items():
                session.add(m.GateEvaluation(
                    signal_id=row.id, gate_name=name, passed=bool(passed),
                    reason=None if passed else (
                        sizing.reason if name == "sizing_ok" else gate.reason),
                    phase=m.PHASE_ON_SIGNAL, evaluated_at_ms=now))
            for name in ("cost_gate", "target_clearance"):
                session.add(m.GateEvaluation(
                    signal_id=row.id, gate_name=name,
                    passed=result.checks.get(name) is True,
                    reason=None, phase=m.PHASE_ON_SIGNAL, evaluated_at_ms=now))

            if status == m.STATUS_BLOCKED and blocked_reason == KILL_DAILY_LIMIT:
                # AUDIT C-14: el A+ que el limite dejo pasar queda registrado
                # con su id, para que el resolutor hipotetico lo recorra luego.
                session.add(m.GateEvaluation(
                    signal_id=row.id, gate_name="blocked_a_plus",
                    passed=False, reason=KILL_DAILY_LIMIT,
                    phase=m.PHASE_ON_SIGNAL, evaluated_at_ms=now))

            session.commit()
            return row.id

    # -- lectura ------------------------------------------------------------

    def current(self, now_ms: Optional[int] = None,
                market: str = MARKET_HYPE) -> Dict[str, Any]:
        """Estado para la home (PRD 16).

        Cuando no hay señal devuelve el MOTIVO, no un null: la app tiene que
        poder explicar por que hoy no hay nada. "No hay setup" y "no pude
        mirar" son respuestas distintas y colapsarlas convierte un fallo de
        datos en un dia tranquilo.
        """
        now = self._now(now_ms)
        with self.session_factory() as session:
            self._expire(session, now)
            row = session.execute(
                select(m.SignalRow)
                .where(m.SignalRow.market == market,
                       m.SignalRow.status.in_((m.STATUS_CANDIDATE,
                                               m.STATUS_A_PLUS_READY)),
                       m.SignalRow.expires_at_ms > now)
                .order_by(m.SignalRow.bar_open_ms.desc())
            ).scalars().first()
            day_row = self._day_row(session, now)
            payload: Dict[str, Any] = {
                "server_now_ms": now,
                "mode": self.settings.mode,
                "live_execution": self.settings.live_execution,
                "day": {"session_start_ms": day_row.session_start_ms,
                        "state": day_row.state,
                        "trades_opened": day_row.trades_opened,
                        "critical": day_row.critical,
                        "critical_reason": day_row.critical_reason,
                        "manual_kill": day_row.manual_kill},
                "bridge_trust": self.bridge_trust,
                "bridge_untrusted_reason": self.bridge_untrusted_reason,
                "signal": None,
                "reason": "no_setup",
            }
            if row is not None:
                payload["signal"] = self._card(session, row, now)
                payload["reason"] = row.scan_reason
                if row.seen_at_ms is None:
                    # Marca de entrega: separa EXPIRED_SEEN de EXPIRED_UNSEEN,
                    # que es lo que distingue "problema de entrega" de
                    # "problema de reloj" (y por tanto que arreglo aplica).
                    row.seen_at_ms = now
            session.commit()

        # Guarda de producto: nada que se lea como probabilidad sale de aqui.
        assert_no_probability(payload)
        return payload

    def _card(self, session, row: m.SignalRow, now: int) -> Dict[str, Any]:
        """Los 12 campos de la Signal Card + la checklist + el reloj.

        Todos los numeros salen del calculo NATIVO. Nada de TradingView entra
        en la tarjeta: mezclar origenes destruiria justo la propiedad que hace
        util el puente. Lo de TV es una insignia al lado, nunca un dato dentro.
        """
        cost_usd = (row.cost_frac or 0.0) * row.entry_ref * (row.qty or 0.0)
        fees_usd = 2.0 * self.cfg.fee_taker * row.entry_ref * (row.qty or 0.0)
        funding_usd = (self.cfg.funding_rate_hourly
                       * self.cfg.funding_expected_hours
                       * row.entry_ref * (row.qty or 0.0))
        profit = expected_profit_usd(row.entry_ref, row.tp, row.qty or 0.0,
                                     row.side, row.cost_frac or 0.0)
        disc = session.execute(
            select(m.Discrepancy).where(m.Discrepancy.signal_id == row.id)
            .order_by(m.Discrepancy.created_at_ms.desc())
        ).scalars().first()
        return {
            "id": row.id, "side": row.side, "market": row.market,
            "stage": row.stage, "status": row.status,
            "strategy_id": row.strategy_id, "config_hash": row.config_hash,
            "source": row.source,
            # niveles
            "entry_ref": row.entry_ref, "stop": row.stop, "tp": row.tp,
            "rr": row.rr, "rr_arm": row.rr_arm,
            "risk_per_unit": row.risk_per_unit,
            "structural_level": row.structural_level,
            # tamaño y dinero
            "qty": row.qty, "notional": row.notional,
            "dollar_risk_usd": row.planned_risk_usd,
            "budget_usd": row.budget_usd,
            "estimated_fees_usd": round(fees_usd, 4),
            "estimated_funding_usd": round(funding_usd, 4),
            "estimated_total_cost_usd": round(cost_usd, 4),
            "expected_profit_at_tp_usd": round(profit, 4),
            "cost_r": row.cost_r,
            # PROVISIONAL: el Cost_R se calculo con un fee supuesto. Va como
            # dato y no como comentario para que llegue al analisis posterior.
            "fee_taker_confirmed": row.fee_taker_confirmed,
            "clearance_r": row.clearance_r,
            "clearance_level": row.clearance_level,
            "clearance_kind": row.clearance_kind,
            # reloj: el cliente cuenta contra server_now_ms, nunca contra el suyo
            "bar_open_ms": row.bar_open_ms, "bar_close_ms": row.bar_close_ms,
            "expires_at_ms": row.expires_at_ms,
            "ttl_remaining_ms": max(0, row.expires_at_ms - now),
            "signal_age_seconds": round((now - row.bar_close_ms) / 1000.0, 1),
            # evidencia
            "checklist": {k: (row.checks.get(k) is True)
                          for k in engine.CHECKLIST_KEYS},
            "score_label": f"RULE COMPLIANCE {row.score_passed}/{row.score_total}",
            "rules_complete": row.rules_complete, "is_a_plus": row.is_a_plus,
            "sizing_ok": row.sizing_ok, "sizing_reason": row.sizing_reason,
            "blocked_reason": row.blocked_reason,
            "data_age_seconds": row.data_age_seconds,
            "tv_badge": None if disc is None else {
                "class": disc.kind, "severity": disc.severity,
                "fields": len(disc.fields or []),
                "tv_latency_ms": disc.tv_latency_ms},
        }

    # -- expiracion ---------------------------------------------------------

    def _expire(self, session, now: int) -> int:
        """Barrido de TTL. Un SKIP explicito y un EXPIRED por tiempo agotado
        son datos DISTINTOS y no se colapsan."""
        rows = session.execute(
            select(m.SignalRow).where(
                m.SignalRow.status.in_((m.STATUS_CANDIDATE,
                                        m.STATUS_A_PLUS_READY)),
                m.SignalRow.expires_at_ms <= now)
        ).scalars().all()
        for row in rows:
            row.status = (m.STATUS_EXPIRED_SEEN if row.seen_at_ms
                          else m.STATUS_EXPIRED_UNSEEN)
            session.add(m.Decision(
                signal_id=row.id,
                idempotency_key=f"expire:{row.id}",
                decision=m.DECISION_EXPIRED, outcome=m.OUTCOME_REJECTED,
                gate_reason="ttl_elapsed", decided_at_ms=now,
                decision_latency_ms=now - row.bar_close_ms,
                signal_age_seconds=(now - row.bar_close_ms) / 1000.0,
                actor="system"))
        return len(rows)

    def expire_due(self, now_ms: Optional[int] = None) -> int:
        now = self._now(now_ms)
        with self.session_factory() as session:
            n = self._expire(session, now)
            session.commit()
        return n

    # -- ENTER --------------------------------------------------------------

    def enter(self, signal_id: str, idempotency_key: str, *,
              actor: str = "mark", now_ms: Optional[int] = None) -> ActionResult:
        """ENTER de Mark. Revalida con datos FRESCOS y decide.

        Nada de lo que la UI tenia en pantalla se da por bueno: puede llevar 80
        segundos ahi. Se piden precio y edad de datos otra vez, se recalcula el
        sizing y se pasa por `order_guard.revalidate()` (las 9 comprobaciones
        del PRD 17). La decision se PERSISTE pase o no el gate.

        Sobre `assert_can_send_orders()`: se invoca SIEMPRE. Que lance
        `LiveExecutionDisabled` no es un caso de error, es la comprobacion
        funcionando — y es lo que decide la rama:
          - en modos simulados, su negativa CONFIRMA que la ruta real esta
            cerrada y se procede a simular;
          - en TINY/LIVE, su negativa aborta y el motivo se muestra literal.
        Asi la unica puerta hacia el exchange se ejercita en cada ENTER, tambien
        en PAPER, en vez de estrenarse el dia que haya dinero encima.
        """
        now = self._now(now_ms)
        with self.session_factory() as session:
            prev = session.execute(
                select(m.Decision).where(
                    m.Decision.idempotency_key == idempotency_key)
            ).scalar_one_or_none()
            if prev is not None:
                # Reintento de red: misma respuesta, no una segunda posicion.
                return ActionResult(prev.outcome == m.OUTCOME_ACCEPTED,
                                    prev.response.get("code", "duplicate"),
                                    dict(prev.response))

            row = session.get(m.SignalRow, signal_id)
            if row is None:
                return ActionResult(False, "signal_not_found",
                                    {"signal_id": signal_id})

            # Bloqueo de fila del dia durante toda la secuencia revalidar ->
            # abrir -> incrementar cupo.
            day_row = self._day_row(session, now, lock=True)
            day = self._to_day_state(day_row)

            if self.data is None:
                # Fail-closed: sin datos frescos no se revalida nada. Usar los
                # de la pantalla seria exactamente lo que el PRD 17 prohibe.
                return self._record_enter(session, row, day_row, now,
                                          idempotency_key, actor,
                                          Gate(False, "market_data_unavailable"),
                                          None, None, None)

            c4h, c1h, c5 = self.data.multi_timeframe()
            data_age = self.data.data_age_seconds(c5, now)
            price = self.data.mid_price()

            sig = _engine_signal(row)
            sizing = size_position(sig.entry_ref, sig.stop, sig.side, self.cfg,
                                   self.equity_usd, self.venue, sig.cost_frac)

            # El target clearance se reevalua con las velas de AHORA: un nivel
            # nuevo entre la señal y el ENTER invalida el trade igual que el
            # drift, y no hacerlo dejaria pasar un objetivo que ya no cabe.
            clearance_ok = self._clearance_still_valid(c1h, c5, sig)

            gate = revalidate(sig, sizing, day, self.cfg, self.settings,
                              now_ms=now, current_price=price,
                              equity=self.equity_usd, has_open_position=False,
                              data_age_seconds=data_age,
                              clearance_still_valid=clearance_ok)
            return self._record_enter(session, row, day_row, now,
                                      idempotency_key, actor, gate, sizing,
                                      price, sig)

    def _clearance_still_valid(self, c1h: List[Candle], c5: List[Candle],
                               sig: engine.Signal) -> bool:
        """Reevaluacion del clearance con datos actuales. Fail-closed: si no se
        puede evaluar, se responde False. "No hay obstaculo" y "no he podido
        mirar" no son lo mismo, y confundirlos convierte el gate en fail-open
        silencioso, que es el fallo mas caro de detectar."""
        if not self.cfg.require_target_clearance:
            return True
        try:
            from ..strategies.hype import target_clearance
            ok, _cr, _lvl = target_clearance.check(
                c1h, c5, len(c5) - 1, sig.side, sig.entry_ref, sig.stop,
                self.cfg)
            return bool(ok)
        except Exception:
            return False

    def _record_enter(self, session, row: m.SignalRow, day_row: m.DailyStateRow,
                      now: int, idempotency_key: str, actor: str, gate: Gate,
                      sizing: Optional[Sizing], price: Optional[float],
                      sig: Optional[engine.Signal]) -> ActionResult:
        age = (now - row.bar_close_ms) / 1000.0
        drift = (drift_r(sig, price) if sig is not None and price else None)

        def persist(code: str, outcome: str, payload: Dict[str, Any],
                    gate_reason: Optional[str]) -> ActionResult:
            payload = dict(payload)
            payload.setdefault("code", code)
            payload.setdefault("signal_id", row.id)
            session.add(m.Decision(
                signal_id=row.id, idempotency_key=idempotency_key,
                decision=m.DECISION_ENTER, outcome=outcome,
                gate_reason=gate_reason, decided_at_ms=now,
                decision_latency_ms=now - row.bar_close_ms,
                price_at_decision=price, drift_r_at_decision=drift,
                signal_age_seconds=age, actor=actor, response=payload))
            session.add(m.GateEvaluation(
                signal_id=row.id, gate_name="revalidate", passed=bool(gate),
                reason=None if gate else gate.reason, phase=m.PHASE_ON_ENTER,
                evaluated_at_ms=now))
            session.commit()
            assert_no_probability(payload)
            return ActionResult(outcome == m.OUTCOME_ACCEPTED, code, payload)

        if not gate:
            # El motivo se muestra LITERAL a Mark. `drift_exceeded:0.137R`
            # enseña algo; "no se pudo entrar" no enseña nada y erosiona la
            # confianza en el sistema.
            return persist("gate_rejected", m.OUTCOME_REJECTED,
                           {"gate": gate.reason, "signal_age_seconds": age,
                            "price_at_decision": price,
                            "drift_r": drift},
                           gate.reason)

        try:
            assert_can_send_orders(self.settings)
            live_allowed, live_msg = True, ""
        except LiveExecutionDisabled as e:
            live_allowed, live_msg = False, str(e)

        if not live_allowed and self.settings.mode not in SIMULATED_MODES:
            # TINY/LIVE con el interruptor apagado: se aborta con el motivo
            # completo. Cambiarlo no es una decision de ingenieria.
            return persist("live_execution_disabled", m.OUTCOME_REJECTED,
                           {"gate": "live_execution_disabled",
                            "message": live_msg},
                           "live_execution_disabled")

        if live_allowed:
            # No hay adaptador de escritura todavia. Se falla ruidosamente en
            # vez de devolver un 200 que sugiera que la posicion existe.
            return persist("live_path_not_implemented", m.OUTCOME_REJECTED,
                           {"gate": "live_path_not_implemented",
                            "message": "La ruta de ejecucion real no esta "
                                       "cableada; ningun ENTER puede abrir "
                                       "posicion todavia."},
                           "live_path_not_implemented")

        if self.paper_executor is None:
            return persist("paper_executor_not_wired", m.OUTCOME_REJECTED,
                           {"gate": "paper_executor_not_wired",
                            "message": "Simulacion no cableada. No se inventa "
                                       "un fill: entry = close fue el sesgo "
                                       "optimista de AUDIT C-05."},
                           "paper_executor_not_wired")

        result = self.paper_executor(sig, sizing, self._to_day_state(day_row))
        if not getattr(result, "entered", False):
            return persist("simulated_entry_rejected", m.OUTCOME_REJECTED,
                           {"gate": getattr(result, "reason", "unknown")},
                           getattr(result, "reason", "unknown"))

        pos = result.position
        day_row.trades_opened += 1
        day_row.state = "TRADE_ACTIVE"
        day_row.updated_at_ms = now
        row.status = m.STATUS_ENTERED
        session.flush()

        session.add(m.TradeRow(
            signal_id=row.id, mode=self.settings.mode, is_synthetic=True,
            intended_entry=row.entry_ref, fill_price=pos.entry_price,
            fill_ts_ms=pos.entry_ts_ms, qty=pos.qty,
            sl_price=pos.stop, tp_price=pos.tp))
        return persist("entered", m.OUTCOME_ACCEPTED,
                       {"fill_price": pos.entry_price, "qty": pos.qty,
                        "sl_price": pos.stop, "tp_price": pos.tp,
                        "is_synthetic": True, "mode": self.settings.mode},
                       None)

    # -- SKIP ---------------------------------------------------------------

    def skip(self, signal_id: str, idempotency_key: str, *,
             reason_code: str = "otro", reason_text: str = "",
             actor: str = "mark",
             now_ms: Optional[int] = None) -> ActionResult:
        """SKIP explicito de Mark.

        Se acepta TAMBIEN despues del TTL: un SKIP y un EXPIRED son datos
        distintos, y colapsarlos borraria el otro lado del corte MARK ACCEPTED
        vs MARK SKIPPED del PRD 19. La señal no se borra: sigue en el journal
        con todos sus niveles y se resuelve hipoteticamente igual que las
        bloqueadas.
        """
        now = self._now(now_ms)
        with self.session_factory() as session:
            prev = session.execute(
                select(m.Decision).where(
                    m.Decision.idempotency_key == idempotency_key)
            ).scalar_one_or_none()
            if prev is not None:
                return ActionResult(True, "duplicate", dict(prev.response))

            row = session.get(m.SignalRow, signal_id)
            if row is None:
                return ActionResult(False, "signal_not_found",
                                    {"signal_id": signal_id})

            price = None
            drift = None
            if self.data is not None:
                try:
                    price = self.data.mid_price()
                    drift = drift_r(_engine_signal(row), price)
                except Exception:
                    # Un fallo de datos no puede impedir registrar un SKIP: la
                    # decision del humano es el dato, el precio es contexto.
                    price, drift = None, None

            age = (now - row.bar_close_ms) / 1000.0
            payload = {"code": "skipped", "signal_id": row.id,
                       "reason_code": reason_code,
                       "signal_age_seconds": age,
                       "price_at_decision": price}
            if row.status in (m.STATUS_CANDIDATE, m.STATUS_A_PLUS_READY):
                row.status = m.STATUS_SKIPPED
            session.add(m.Decision(
                signal_id=row.id, idempotency_key=idempotency_key,
                decision=m.DECISION_SKIP, outcome=m.OUTCOME_ACCEPTED,
                decided_at_ms=now, decision_latency_ms=now - row.bar_close_ms,
                price_at_decision=price, drift_r_at_decision=drift,
                signal_age_seconds=age, reason_code=reason_code,
                reason_text=reason_text or None, actor=actor,
                response=payload))
            session.commit()
        assert_no_probability(payload)
        return ActionResult(True, "skipped", payload)

    # -- puente TradingView -------------------------------------------------

    def revalidate_tv_alert(self, alert_id: str,
                            canonical: Dict[str, Any]) -> None:
        """Revalida una alerta de TradingView con datos NATIVOS y registra la
        comparacion. Es el destino de la cola asincrona del webhook.

        La alerta NO crea señal. Se corre el mismo `engine.scan()` de siempre —
        sin variantes ni "modo webhook" — y se compara. Si el scan nativo no
        produjo señal, o produjo otra direccion, o para otra vela, no hay
        tarjeta: hay una fila de discrepancia y se acabo.
        """
        now = self._now()
        if self.data is None:
            self._write_discrepancy(alert_id, None, m.DISC_PENDING_NATIVE_BAR,
                                    "high", [], canonical,
                                    "market_data_unavailable")
            return

        c4h, c1h, c5 = self.data.multi_timeframe()
        if not c5:
            self._write_discrepancy(alert_id, None, m.DISC_PENDING_NATIVE_BAR,
                                    "high", [], canonical, "no_5m_candles")
            return

        bar_open = canonical.get("bar_open_ms")
        if bar_open is not None and c5[-1].ts != bar_open:
            # TradingView va por delante (o por detras) del store nativo. Si se
            # repite, el sintoma es un WS desincronizado, no una anecdota.
            self._write_discrepancy(alert_id, None, m.DISC_PENDING_NATIVE_BAR,
                                    "low",
                                    [{"field": "bar_open_ms",
                                      "tv": bar_open, "native": c5[-1].ts,
                                      "delta": (c5[-1].ts - bar_open),
                                      "tolerance": 0, "passes": False}],
                                    canonical, "bar_not_available_yet")
            return

        result = engine.scan(c4h, c1h, c5, self.cfg)
        data_age = self.data.data_age_seconds(c5, now)
        signal_id = None
        if result.signal is not None:
            signal_id = self.record_scan(result, data_age_seconds=data_age,
                                         source=m.SOURCE_NATIVE, now_ms=now)

        kind, severity, fields = self._compare(canonical, result)
        self._write_discrepancy(alert_id, signal_id, kind, severity, fields,
                                canonical, result.reason)

        if kind in _UNTRUSTING and self.bridge_trust == TRUST_TRUSTED:
            # UNTRUSTED degrada una afirmacion de calidad. NO es un kill
            # switch: si lo fuera, un payload forjado podria impedir operar.
            self.bridge_trust = TRUST_UNTRUSTED
            self.bridge_untrusted_reason = kind

        # La alerta queda enlazada a la señal nativa, si la hubo. El enlace es
        # de auditoria; la tarjeta se pinta con los numeros nativos.
        with self.session_factory() as session:
            alert = session.get(m.TvAlert, alert_id)
            if alert is not None and signal_id is not None:
                sig_row = session.get(m.SignalRow, signal_id)
                if sig_row is not None and sig_row.tv_alert_id is None:
                    sig_row.tv_alert_id = alert.id
            session.commit()

    def _compare(self, tv: Dict[str, Any], native: engine.ScanResult
                 ) -> Tuple[str, str, List[Dict[str, Any]]]:
        """Compara payload TV vs ScanResult nativo campo a campo.

        Devuelve (clase, severidad, campos). Se devuelven TODOS los campos
        comparados, tambien los que pasan: la fila tiene que servir para
        explicar la diferencia, no solo para señalarla.
        """
        fields: List[Dict[str, Any]] = []
        tick = self.venue.tick_size

        def cmp_num(name: str, tv_val: Any, nat_val: Any,
                    tol: float) -> Optional[bool]:
            a, b = _num(tv_val), _num(nat_val)
            if a is None or b is None:
                return None
            ok = abs(a - b) <= tol
            fields.append({"field": name, "tv": a, "native": b,
                           "delta": round(a - b, 10), "tolerance": tol,
                           "passes": ok})
            return ok

        sig = native.signal
        checks = native.checks

        # 1) Configuracion. Cero tolerancia: si el Pine corre con band_k = 0.50
        # y Python con 0.30 ambos "funcionan" y divergen para siempre.
        echo = tv.get("config_echo") or {}
        cfg_diff: Dict[str, Any] = {}
        for key, tv_val in echo.items():
            nat_val = getattr(self.cfg, _ECHO_TO_CONFIG.get(key, key), None)
            if nat_val is None:
                continue
            if isinstance(nat_val, float) or isinstance(tv_val, float):
                same = abs(float(tv_val) - float(nat_val)) < 1e-9
            else:
                same = tv_val == nat_val
            if not same:
                cfg_diff[key] = {"tv": tv_val, "native": nat_val}
        if cfg_diff:
            fields.append({"field": "config_echo", "tv": echo,
                           "native": cfg_diff, "delta": None,
                           "tolerance": 0, "passes": False})

        # 2) Direccion.
        tv_side = tv.get("side")
        nat_side = sig.side if sig else native.side_evaluated
        if tv_side and nat_side and tv_side != nat_side and sig is not None:
            fields.append({"field": "side", "tv": tv_side, "native": nat_side,
                           "delta": None, "tolerance": 0, "passes": False})
            return m.DISC_SIDE_CONFLICT, "critical", fields

        # 3) OHLCV: el comparador de FEEDS. Sin esto, toda discrepancia es
        # inatribuible: no se puede distinguir un bug de implementacion de dos
        # proveedores que ven mercados distintos.
        feed_ok = True
        bar = tv.get("bar") or {}
        native_price = _num(checks.get("price"))
        if native_price is not None and "c" in bar:
            r = cmp_num("bar.c", bar.get("c"), native_price,
                        _price_tol(native_price, tick))
            feed_ok = feed_ok and (r is not False)
        vol_native = _num((native.checks or {}).get("volume_ratio"))
        r = cmp_num("volume_ratio", (tv.get("momentum_5m") or {}).get("volume_ratio"),
                    vol_native, abs(vol_native or 0.0) * REL_TOL["volume"])
        feed_ok = feed_ok and (r is not False)

        # 4) Si el nativo no vio señal, ya no hay niveles que comparar.
        if sig is None:
            return m.DISC_TV_ONLY, "medium", fields

        # 5) Niveles. Con OHLCV iguales, mismos datos deberian dar mismos
        # numeros: una diferencia aqui es un BUG en una de las dos partes.
        level_ok = True
        for name, tv_val, nat_val in (
                ("entry_ref", (tv.get("signal") or {}).get("entry_ref"), sig.entry_ref),
                ("stop", (tv.get("signal") or {}).get("structural_stop"), sig.stop),
                ("target_ref", (tv.get("signal") or {}).get("target_ref"), sig.tp),
                ("vwap", (tv.get("context") or {}).get("vwap"),
                 _num(checks.get("vwap"))),
        ):
            if nat_val is None:
                continue
            r = cmp_num(name, tv_val, nat_val, _price_tol(float(nat_val), tick))
            level_ok = level_ok and (r is not False)

        r = cmp_num("atr20", (tv.get("context") or {}).get("atr20"),
                    _num(checks.get("atr")),
                    abs(_num(checks.get("atr")) or 0.0) * REL_TOL["atr"])
        level_ok = level_ok and (r is not False)
        r = cmp_num("risk_per_unit", (tv.get("signal") or {}).get("risk_per_unit"),
                    sig.risk_per_unit,
                    sig.risk_per_unit * REL_TOL["risk_per_unit"])
        level_ok = level_ok and (r is not False)
        r = cmp_num("clearance_r", (tv.get("clearance") or {}).get("clearance_r"),
                    sig.clearance_r, ABS_TOL["clearance_r"])
        level_ok = level_ok and (r is not False)

        # 6) Booleanos: cero tolerancia. `cost_gate` de TV llega siempre null
        # (TradingView no conoce el tier de fees ni el funding) y por eso no se
        # compara: un true ahi seria un numero inventado.
        bool_ok = True
        tv_checks = tv.get("checks") or {}
        for key in engine.CHECKLIST_KEYS:
            if key == "cost_gate":
                continue
            tv_val = tv_checks.get(key)
            if tv_val is None:
                continue
            nat_val = checks.get(key) is True
            ok = bool(tv_val) == nat_val
            fields.append({"field": f"checks.{key}", "tv": bool(tv_val),
                           "native": nat_val, "delta": None, "tolerance": 0,
                           "passes": ok})
            bool_ok = bool_ok and ok

        if cfg_diff:
            return m.DISC_CONFIG_DRIFT, "high", fields
        if not level_ok or not bool_ok:
            # Con OHLCV dentro de tolerancia, esto es un bug; con OHLCV fuera,
            # la causa mas probable es el feed. La clase dice cual investigar.
            return ((m.DISC_LEVEL_DRIFT, "high", fields) if feed_ok
                    else (m.DISC_FEED_DELTA, "low", fields))
        if not feed_ok:
            return m.DISC_FEED_DELTA, "low", fields
        return m.DISC_MATCH, "info", fields

    def _write_discrepancy(self, alert_id: Optional[str],
                           signal_id: Optional[str], kind: str, severity: str,
                           fields: List[Dict[str, Any]],
                           canonical: Dict[str, Any],
                           native_reason: str) -> None:
        latency = None
        fired = canonical.get("fired_at_ms")
        bar_close = canonical.get("bar_close_ms")
        if isinstance(fired, int) and isinstance(bar_close, int):
            latency = fired - bar_close
        with self.session_factory() as session:
            session.add(m.Discrepancy(
                tv_alert_id=alert_id, signal_id=signal_id, kind=kind,
                severity=severity, fields=fields,
                script_version=canonical.get("script_version"),
                config_echo_diff={f["field"]: f["native"] for f in fields
                                  if f.get("field") == "config_echo"},
                tv_latency_ms=latency, native_scan_reason=native_reason))
            session.commit()


# Nombres del `config_echo` del Pine que no coinciden con los campos de Config.
# Se declaran aqui, en una tabla, en vez de repartir `if`s por el comparador.
_ECHO_TO_CONFIG = {
    "band_k": "vwap_band_atr",
    "buffer_atr": "stop_buffer_atr",
    "gap_max_age_bars": "fvg_max_age_bars",
}


def _rr_arm(rr: float) -> str:
    """Brazos discretos del research plan. `rr` no se optimiza: se prueban tres
    valores y se declara cual se uso."""
    if abs(rr - 1.0) < 1e-9:
        return "E1"
    if abs(rr - 1.6) < 1e-9:
        return "E2"
    if abs(rr - 2.0) < 1e-9:
        return "E3"
    return "EX"


def _engine_signal(row: m.SignalRow) -> engine.Signal:
    """Reconstruye el valor del motor desde la fila persistida.

    `order_guard.revalidate()` trabaja con `engine.Signal` a proposito: la
    revalidacion mide edad y drift contra los MISMOS campos que produjo la
    estrategia, no contra una copia paralela que podria haberse editado.
    """
    return engine.Signal(
        side=row.side, entry_ref=row.entry_ref, stop=row.stop, tp=row.tp,
        rr=row.rr, risk_per_unit=row.risk_per_unit, cost_r=row.cost_r or 0.0,
        cost_frac=row.cost_frac or 0.0, clearance_r=row.clearance_r or 0.0,
        ts=row.bar_open_ms, strategy_id=row.strategy_id,
        checklist=tuple(row.checklist or ()), features=dict(row.features or {}))
