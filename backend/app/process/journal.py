"""Append-only process ledger in the application's existing SQLAlchemy database.

Signals, decisions, fills and results are separate facts. Entry reservation and
the simulated fill commit together; retries and restarts cannot grant a second
entry. This module has no exchange-order capability.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
import time
import uuid

from sqlalchemy import BigInteger, JSON, String, UniqueConstraint, insert, select
from sqlalchemy.orm import Mapped, mapped_column

from ..db.models import Base
from .policy import (DAY_MS, HARD_CHECKS, Plan, ProcessViolation, SKIP_REASONS,
                     assess_process, canonical, entry_checks, fingerprint,
                     outcome_fields)


class ProcessEvent(Base):
    __tablename__ = "process_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(180), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    signal_id: Mapped[str | None] = mapped_column(String(128), index=True)
    block_id: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("event_key", name="uq_process_event_key"),)


class ProcessJournal:
    def __init__(self, engine):
        self.engine = engine
        ProcessEvent.__table__.create(engine, checkfirst=True)
        # Guard even raw SQL updates/deletes, not only ORM attribute changes.
        with engine.begin() as conn:
            if engine.dialect.name == "sqlite":
                for operation in ("UPDATE", "DELETE"):
                    conn.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS process_no_{operation.lower()} "
                        f"BEFORE {operation} ON process_events BEGIN "
                        "SELECT RAISE(ABORT, 'PROCESS_JOURNAL_IMMUTABLE'); END")
            elif engine.dialect.name == "postgresql":
                conn.exec_driver_sql("""CREATE OR REPLACE FUNCTION process_immutable()
                    RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                    RAISE EXCEPTION 'PROCESS_JOURNAL_IMMUTABLE'; END; $$""")
                conn.exec_driver_sql("""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'process_immutable_guard') THEN
                    CREATE TRIGGER process_immutable_guard BEFORE UPDATE OR DELETE ON process_events
                    FOR EACH ROW EXECUTE FUNCTION process_immutable(); END IF; END; $$""")
            else:
                raise RuntimeError("Process journal requires SQLite or PostgreSQL")

    @contextmanager
    def transaction(self):
        with self.engine.connect() as conn:
            try:
                if self.engine.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    conn.begin()
                    conn.exec_driver_sql("SELECT pg_advisory_xact_lock(718201609)")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def events(self, conn=None):
        if conn is None:
            with self.engine.connect() as connection:
                return self.events(connection)
        return [dict(r) for r in conn.execute(select(ProcessEvent.__table__).order_by(
            ProcessEvent.created_at_ms, ProcessEvent.id)).mappings()]

    def _append(self, conn, kind, payload, *, key=None, signal_id=None,
                block_id=None, now_ms=None):
        # JSON round-trip prevents caller mutation; nonfinite numbers are refused.
        payload = json.loads(canonical(payload))
        row = dict(id=uuid.uuid4().hex, event_key=key or uuid.uuid4().hex,
                   kind=kind, payload=payload, signal_id=signal_id, block_id=block_id,
                   created_at_ms=int(time.time() * 1000) if now_ms is None else now_ms)
        conn.execute(insert(ProcessEvent).values(**row))
        return row

    def active_block(self, mode, config, now_ms):
        with self.transaction() as conn:
            events = self.events(conn)
            ended = {e["block_id"] for e in events if e["kind"] in ("BLOCK_COMPLETED", "BLOCK_ABORTED")}
            active = [e for e in events if e["kind"] == "BLOCK_STARTED"
                      and e["payload"]["mode"] == mode and e["block_id"] not in ended]
            if active:
                block = active[-1]
                if block["payload"]["config_hash"] != fingerprint(config):
                    raise ProcessViolation("EXECUTION_BLOCK_CONFIG_FROZEN")
                return block["block_id"]
            block_id = uuid.uuid4().hex
            self._append(conn, "BLOCK_STARTED", {"mode": mode, "config": config,
                "config_hash": fingerprint(config), "target_trades": 20},
                key="block:" + block_id, block_id=block_id, now_ms=now_ms)
            return block_id

    def abort_block(self, block_id, reason, category, now_ms):
        if category not in ("bug", "safety") or not reason.strip():
            raise ProcessViolation("BLOCK_CHANGE_REQUIRES_BUG_OR_SAFETY_REASON")
        with self.transaction() as conn:
            events = self.events(conn)
            if not any(e["kind"] == "BLOCK_STARTED" and e["block_id"] == block_id for e in events):
                raise ProcessViolation("UNKNOWN_BLOCK")
            if any(e["block_id"] == block_id and e["kind"] in ("BLOCK_ABORTED", "BLOCK_COMPLETED")
                   for e in events):
                raise ProcessViolation("BLOCK_ALREADY_ENDED")
            return self._append(conn, "BLOCK_ABORTED", {"reason": reason, "category": category},
                                key="block-end:" + block_id, block_id=block_id, now_ms=now_ms)

    def observe(self, plan, now_ms):
        plan.validate()
        with self.transaction() as conn:
            prior = [e for e in self.events(conn) if e["kind"] == "SIGNAL"
                     and e["signal_id"] == plan.signal_id]
            if prior:
                # The first observed plan is authoritative; later scans cannot rewrite it.
                return Plan(**prior[0]["payload"]["plan"])
            self._append(conn, "SIGNAL", {"plan": asdict(plan), "plan_hash": plan.hash},
                         key="signal:" + plan.signal_id, signal_id=plan.signal_id,
                         block_id=plan.block_id, now_ms=now_ms)
            return plan

    @staticmethod
    def _plan(events, signal_id):
        match = next((e for e in events if e["kind"] == "SIGNAL" and e["signal_id"] == signal_id), None)
        if match is None:
            raise ProcessViolation("UNKNOWN_SIGNAL")
        return Plan(**match["payload"]["plan"])

    @staticmethod
    def open_fills(events, mode=None):
        closed = {e["signal_id"] for e in events if e["kind"] == "RESULT"}
        return [e for e in events if e["kind"] == "FILL" and e["signal_id"] not in closed
                and (mode is None or e["payload"]["mode"] == mode)]

    def decide(self, signal_id, *, decision, request_id, now_ms, plan_hash,
               risk_accepted=False, executable_price=None, reason=None,
               gate=None, fill=None):
        if decision not in ("ENTER", "SKIP") or not request_id or len(request_id) > 64:
            raise ProcessViolation("INVALID_DECISION")
        if decision == "SKIP" and reason not in SKIP_REASONS:
            raise ProcessViolation("SKIP_REASON_REQUIRED")
        with self.transaction() as conn:
            events = self.events(conn)
            retry = next((e for e in events if e["event_key"] == "request:" + request_id), None)
            if retry:
                p = retry["payload"]
                if (retry["signal_id"] != signal_id or p["decision"] != decision
                        or p["plan_hash"] != plan_hash or p.get("reason") != reason
                        or p["risk_accepted"] != risk_accepted):
                    raise ProcessViolation("IDEMPOTENCY_CONFLICT")
                return p["response"]
            plan = self._plan(events, signal_id)
            if any(e["kind"] == "DECISION" and e["signal_id"] == signal_id for e in events):
                raise ProcessViolation("DECISION_IMMUTABLE")
            response = {"entered": False, "skipped": decision == "SKIP", "reason": "ok"}
            checks = {}
            filled = None
            try:
                if plan_hash != plan.hash:
                    raise ProcessViolation("PLAN_CHANGED_REVIEW_AGAIN")
                if now_ms < plan.signal_close_ms:
                    raise ProcessViolation("SIGNAL_FROM_FUTURE")
                if decision == "ENTER":
                    if any(e["block_id"] == plan.block_id and e["kind"] in
                           ("BLOCK_COMPLETED", "BLOCK_ABORTED") for e in events):
                        raise ProcessViolation("EXECUTION_BLOCK_ENDED")
                    if any(e["kind"] == "MISSED" and e["signal_id"] == signal_id for e in events):
                        raise ProcessViolation("PRICE_DRIFT")
                    if self.open_fills(events):
                        raise ProcessViolation("POSITION_ALREADY_OPEN")
                    day = now_ms // DAY_MS
                    if any(e["kind"] == "FILL" and e["payload"]["mode"] == plan.mode
                           and e["payload"]["entry_ts_ms"] // DAY_MS == day for e in events):
                        raise ProcessViolation("DAILY_TRADE_LIMIT")
                    checks = entry_checks(plan, now_ms=now_ms, executable_price=executable_price,
                                          risk_accepted=risk_accepted, plan_hash=plan_hash)
                    if gate is None or fill is None:
                        raise ProcessViolation("EXECUTION_VALIDATION_UNAVAILABLE")
                    gate(plan, events)  # current technical/risk/data gates, supplied by backend
                    filled = fill(plan)
                    if filled.get("protection_verified") is not True:
                        raise ProcessViolation("SL_NOT_VERIFIED")
                    response = {"entered": True, "skipped": False, "reason": "ok",
                                "position": filled, "process_state": "RISK_ACCEPTED"}
            except ProcessViolation as exc:
                response = {"entered": False, "skipped": False, "reason": exc.code}
                if exc.code == "PRICE_DRIFT" and not any(
                        e["kind"] == "MISSED" and e["signal_id"] == signal_id for e in events):
                    self._append(conn, "MISSED", {"miss_reason": "PRICE_DRIFT"},
                                 key="missed:" + signal_id, signal_id=signal_id,
                                 block_id=plan.block_id, now_ms=now_ms)
                self._append(conn, "ATTEMPT", {"code": exc.code, "executed": False},
                             signal_id=signal_id, block_id=plan.block_id, now_ms=now_ms)
            self._append(conn, "DECISION", {"decision": decision, "reason": reason,
                "risk_accepted": risk_accepted, "plan_hash": plan_hash,
                "checks": checks, "response": response}, key="request:" + request_id,
                signal_id=signal_id, block_id=plan.block_id, now_ms=now_ms)
            if response["entered"]:
                self._append(conn, "FILL", dict(filled, mode=plan.mode),
                             key="fill:" + signal_id, signal_id=signal_id,
                             block_id=plan.block_id, now_ms=now_ms)
            return response

    def record_result(self, signal_id, result, checks, now_ms, *, manual_reason=None):
        with self.transaction() as conn:
            events = self.events(conn)
            prior = next((e for e in events if e["kind"] == "RESULT" and e["signal_id"] == signal_id), None)
            if prior:
                return prior["payload"]
            plan = self._plan(events, signal_id)
            opened = next((e for e in self.open_fills(events) if e["signal_id"] == signal_id), None)
            if opened is None:
                raise ProcessViolation("NO_OPEN_POSITION")
            if now_ms < opened["payload"]["entry_ts_ms"]:
                raise ProcessViolation("EXIT_BEFORE_ENTRY")
            if result["exit_reason"] == "MANUAL_EXIT" and not (manual_reason or "").strip():
                raise ProcessViolation("MANUAL_EXIT_REASON_REQUIRED")
            assessment = assess_process(checks)
            payload = dict(result, **assessment,
                           **outcome_fields(result["net_pnl_usd"], result["exit_reason"],
                                            assessment["process_quality"]))
            payload.update(checks=checks, manual_reason=manual_reason,
                           mode=plan.mode, planned_risk_usd=plan.planned_loss_usd)
            self._append(conn, "RESULT", payload, key="result:" + signal_id,
                         signal_id=signal_id, block_id=plan.block_id, now_ms=now_ms)
            results = [e for e in events if e["kind"] == "RESULT" and e["block_id"] == plan.block_id]
            ended = any(e["block_id"] == plan.block_id and e["kind"] in
                        ("BLOCK_COMPLETED", "BLOCK_ABORTED") for e in events)
            if len(results) + 1 == 20 and not ended:
                self._append(conn, "BLOCK_COMPLETED", {"closed_trades": 20},
                             key="block-end:" + plan.block_id, block_id=plan.block_id, now_ms=now_ms)
            return payload

    def mark_missed(self, signal_id, now_ms):
        with self.transaction() as conn:
            events = self.events(conn)
            plan = self._plan(events, signal_id)
            if not any(e["signal_id"] == signal_id and e["kind"] in ("MISSED", "DECISION")
                       for e in events):
                self._append(conn, "MISSED", {"miss_reason": "PRICE_DRIFT"},
                             key="missed:" + signal_id, signal_id=signal_id,
                             block_id=plan.block_id, now_ms=now_ms)

    def record_counterfactual(self, signal_id, result, model_version, now_ms):
        """Trusted research job only. Never called with browser-supplied P&L."""
        if not model_version or not isinstance(result.get("net_r"), (float, int)):
            raise ProcessViolation("COUNTERFACTUAL_MODEL_REQUIRED")
        with self.transaction() as conn:
            events = self.events(conn)
            plan = self._plan(events, signal_id)
            if now_ms <= plan.signal_close_ms:
                raise ProcessViolation("OUTCOME_BEFORE_SIGNAL")
            key = f"counterfactual:{signal_id}:{model_version}"
            if not any(e["event_key"] == key for e in events):
                self._append(conn, "COUNTERFACTUAL", dict(result, model_version=model_version,
                             hypothetical=True), key=key, signal_id=signal_id,
                             block_id=plan.block_id, now_ms=now_ms)

    def append_note(self, signal_id, text, now_ms):
        if not text.strip() or len(text) > 2000:
            raise ProcessViolation("NOTE_REQUIRED_MAX_2000")
        with self.transaction() as conn:
            plan = self._plan(self.events(conn), signal_id)
            return self._append(conn, "NOTE", {"text": text}, signal_id=signal_id,
                                block_id=plan.block_id, now_ms=now_ms)

    def record_attempt(self, signal_id, code, now_ms):
        with self.transaction() as conn:
            plan = self._plan(self.events(conn), signal_id)
            return self._append(conn, "ATTEMPT", {"code": code, "executed": False},
                                signal_id=signal_id, block_id=plan.block_id, now_ms=now_ms)

    def record_day(self, session_start_ms, mode, coverage_complete, now_ms):
        if session_start_ms % DAY_MS or now_ms < session_start_ms + DAY_MS:
            raise ProcessViolation("DAY_NOT_COMPLETE")
        with self.transaction() as conn:
            events = self.events(conn)
            key = f"day:{mode}:{session_start_ms}"
            prior = next((e for e in events if e["event_key"] == key), None)
            if prior:
                return prior
            traded = any(e["kind"] == "FILL" and e["payload"]["mode"] == mode
                         and session_start_ms <= e["payload"]["entry_ts_ms"] < session_start_ms + DAY_MS
                         for e in events)
            return self._append(conn, "DAY_CLOSED", {"mode": mode,
                "session_start_ms": session_start_ms, "coverage_complete": coverage_complete,
                "no_trade": not traded, "status": "NO_TRADE" if not traded else "TRADED"},
                key=key, now_ms=now_ms)
