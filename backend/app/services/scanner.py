"""
services/scanner.py — escaner continuo sobre velas cerradas + forward logger.

Es lo mas barato que puede estar corriendo hoy y lo mas valioso a medio plazo:
el reloj de las 4 semanas de PAPER empieza cuando esto arranca, no cuando el
backtest historico termina (HYPE_TRADING_RESEARCH_PLAN 6, fase 1).

DOS RESPONSABILIDADES SEPARADAS A PROPOSITO:

  1. GENERAR señales — depende solo del mercado.
  2. DECIDIR si se pueden ejecutar — depende del limite diario, de los kill
     switches y del estado de la cuenta.

Se mantienen separadas porque el limite de 1 trade/dia descarta señales
VALIDAS. Si el limite bloqueara tambien la generacion, el `N` de la muestra
caeria sin que nadie se diera cuenta y los kill criteria del research plan
matarian brazos que si tenian frecuencia (AUDIT C-06). Y sin registrar los A+
bloqueados nunca se podra responder si "el primer A+ del dia" fue peor que "el
mejor" (AUDIT C-14).

El journal es JSONL append-only. Deliberadamente NO usa la base de datos: puede
arrancar hoy, sobrevive a un reinicio, se lee con cualquier herramienta y no
puede corromperse por un esquema a medio migrar. La persistencia relacional se
alimenta despues DESDE este archivo, nunca al reves.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from ..adapters.hyperliquid_data import (HyperliquidData, MarketDataError,
                                         StaleDataError)
from ..execution.order_guard import signal_age_seconds
from ..risk.limits import DayState, can_open_new_trade, record_blocked_a_plus
from ..risk.sizing import VenueSpec, expected_profit_usd, size_position
from ..strategies.hype import engine
from ..strategies.hype.common import SIDE_LONG, Config
from ..strategies.hype.scoring import assert_no_probability, score
from ..strategies.indicators import BAR_5M_MS

SCHEMA_VERSION = 2


@dataclass
class ScanRecord:
    """Una observacion del escaner. Se escribe SIEMPRE, haya señal o no.

    Registrar tambien los rechazos es lo que permitira despues contestar por
    que no hubo trades: sin ellos solo se sabria que no los hubo.
    """
    schema: int
    ts_scan_ms: int
    ts_bar_ms: int
    mode: str
    reason: str
    state: str
    side: Optional[str] = None
    has_signal: bool = False

    # contexto de mercado
    price: Optional[float] = None
    vwap: Optional[float] = None
    atr: Optional[float] = None
    regime_4h_long: Optional[bool] = None
    regime_4h_short: Optional[bool] = None
    session_bars: Optional[int] = None

    # niveles
    entry_ref: Optional[float] = None
    stop: Optional[float] = None
    tp: Optional[float] = None
    rr: Optional[float] = None
    clearance_r: Optional[float] = None

    # costos y riesgo
    cost_r: Optional[float] = None
    cost_frac: Optional[float] = None
    funding_rate_hourly: Optional[float] = None
    fee_taker: Optional[float] = None
    fee_taker_confirmed: bool = False       # PROVISIONAL mientras sea False

    qty: Optional[float] = None
    notional: Optional[float] = None
    risk_usd: Optional[float] = None
    expected_profit_usd: Optional[float] = None
    sizing_ok: Optional[bool] = None
    sizing_reason: Optional[str] = None

    # decision operativa — separada de la generacion
    executable: Optional[bool] = None
    execution_block_reason: Optional[str] = None
    blocked_by_daily_limit: bool = False

    # Congelamiento (RESEARCH_PLAN 2.4): identifica QUE estrategia produjo esta
    # observacion. Si alguien mueve un umbral a mitad de la muestra, las dos
    # mitades quedan separables en el analisis en vez de mezclarse en silencio.
    strategy_fingerprint: Optional[str] = None
    strategy_arm: Optional[str] = None
    signal_age_seconds: Optional[float] = None
    checks: Dict[str, object] = field(default_factory=dict)
    checklist: List[str] = field(default_factory=list)
    data_age_seconds: Optional[float] = None


class ForwardLogger:
    """Journal JSONL append-only, un archivo por dia UTC."""

    def __init__(self, directory: str = "journal"):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, ts_ms: int) -> str:
        day = time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000.0))
        return os.path.join(self.directory, f"scans-{day}.jsonl")

    def write(self, rec: ScanRecord) -> str:
        path = self._path(rec.ts_scan_ms)
        payload = asdict(rec)
        # Guarda de producto (PRD 14): nada que se lea como probabilidad puede
        # entrar al journal, porque de ahi pasaria despues a la UI.
        assert_no_probability(payload)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())   # sobrevive a un corte; el journal es la
                                   # unica evidencia de lo que el sistema vio
        return path


class Scanner:
    """Escanea al cierre de cada vela de 5m y registra lo que ve."""

    def __init__(self, cfg: Config, mode: str = "PAPER",
                 equity: float = 1000.0,
                 data: Optional[HyperliquidData] = None,
                 logger: Optional[ForwardLogger] = None,
                 journal_dir: str = "journal"):
        self.cfg = cfg
        self.mode = mode
        self.equity = equity
        self.data = data or HyperliquidData()
        self.logger = logger or ForwardLogger(journal_dir)
        self.day = DayState(session_start_ms=0)
        self.gap_cache: dict = {}
        self._last_bar_ms: Optional[int] = None
        self._venue: Optional[VenueSpec] = None
        self._api_errors = 0

    # -- venue ---------------------------------------------------------------

    def venue_spec(self) -> VenueSpec:
        """Se lee una vez y se cachea. Los minimos del venue cambian poco, y
        pedirlos en cada vela añade un punto de fallo por nada."""
        if self._venue is None:
            m = self.data.venue_meta()
            self._venue = VenueSpec(
                min_notional_usd=10.0,          # minimo de HL; confirmar por API
                max_notional_usd=100_000.0,
                size_decimals=m.sz_decimals,
                max_leverage=m.max_leverage,
                available_collateral_usd=0.0,
            )
        return self._venue

    # -- una pasada ----------------------------------------------------------

    def scan_once(self, now_ms: Optional[int] = None) -> ScanRecord:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        venue = self.venue_spec()

        # El funding real entra en el cost gate. Con tenencias de hasta 24 h
        # ya no es ruido, y su SIGNO puede invertirse (AUDIT C-04).
        try:
            funding = self.data.current_funding_rate()
            self._api_errors = 0
        except MarketDataError:
            funding = 0.0
            self._api_errors += 1

        cfg = Config(**{**self.cfg.__dict__,
                        "funding_rate_hourly": funding,
                        "qty_decimals": venue.size_decimals})

        c4h, c1h, c5 = self.data.multi_timeframe()
        age = self.data.data_age_seconds(c5)
        self.day.rollover_if_needed(now, cfg)

        r = engine.scan(c4h, c1h, c5, cfg, gap_cache=self.gap_cache)
        ck = r.checks

        rec = ScanRecord(
            schema=SCHEMA_VERSION, ts_scan_ms=now,
            ts_bar_ms=c5[-1].ts if c5 else 0,
            mode=self.mode, reason=r.reason, state=r.state,
            side=r.side_evaluated, has_signal=r.signal is not None,
            price=ck.get("price"), vwap=ck.get("vwap"), atr=ck.get("atr"),
            regime_4h_long=ck.get("regime_4h_long"),
            regime_4h_short=ck.get("regime_4h_short"),
            session_bars=ck.get("session_bars"),
            funding_rate_hourly=funding,
            fee_taker=cfg.fee_taker,
            fee_taker_confirmed=cfg.fee_taker_confirmed,
            strategy_fingerprint=cfg.fingerprint(), strategy_arm=cfg.arm_label(),
            data_age_seconds=age,
            checks={k: v for k, v in ck.items()},
        )

        if r.signal is not None:
            s = r.signal
            z = size_position(s.entry_ref, s.stop, s.side, cfg, self.equity,
                              venue, s.cost_frac)
            gate = can_open_new_trade(
                self.day, cfg, self.equity,
                has_open_position=False, data_age_seconds=age,
                max_data_age_seconds=420.0, api_error_count=self._api_errors,
            )
            rec.entry_ref, rec.stop, rec.tp, rec.rr = s.entry_ref, s.stop, s.tp, s.rr
            rec.clearance_r = s.clearance_r
            rec.cost_r, rec.cost_frac = s.cost_r, s.cost_frac
            rec.qty, rec.notional = z.qty, z.notional
            rec.risk_usd, rec.sizing_ok, rec.sizing_reason = z.risk_usd, z.ok, z.reason
            rec.expected_profit_usd = (
                expected_profit_usd(s.entry_ref, s.tp, z.qty, s.side, s.cost_frac)
                if z.ok else None
            )
            # Frescura del FEED y vigencia de la SEÑAL son cosas distintas.
            # `assert_fresh` mira si el mercado nos llega; el TTL mira si esta
            # señal concreta sigue viva. Una señal de hace 91 s sobre un feed
            # perfectamente fresco NO es ejecutable (PRD 13).
            age_sig = signal_age_seconds(s, now)
            rec.signal_age_seconds = age_sig
            ttl_ok = 0 <= age_sig <= cfg.signal_ttl_seconds

            rec.executable = bool(gate) and z.ok and ttl_ok
            if rec.executable:
                rec.execution_block_reason = None
            elif not ttl_ok:
                rec.execution_block_reason = f"signal_expired:{age_sig:.0f}s"
            elif not gate:
                rec.execution_block_reason = gate.reason
            else:
                rec.execution_block_reason = f"sizing:{z.reason}"
            rec.checklist = list(s.checklist)

            # A+ REAL: reglas completas + gates operativos verdes (PRD 14).
            full = score(s.side, ck,
                         require_momentum_long=cfg.require_momentum_long,
                         operational={"sizing_ok": z.ok,
                                      "daily_limit_ok": bool(gate),
                                      "kill_switches_clear": bool(gate)})
            rec.checks["a_plus"] = full.is_a_plus
            rec.checks["stage"] = full.stage

            if not gate and gate.reason == "daily_trade_limit_reached":
                # AUDIT C-14: se registra el A+ que el limite dejo pasar, con
                # todo lo necesario para evaluarlo despues.
                rec.blocked_by_daily_limit = True
                record_blocked_a_plus(self.day, asdict(rec))

        self.logger.write(rec)
        return rec

    # -- fallos ---------------------------------------------------------------

    def _log_failure(self, now_ms: int, kind: str, detail: str) -> None:
        """Un fallo de datos tambien es una observacion, y se escribe al journal.

        Antes solo se imprimia por consola. El efecto era que el historial
        omitia EXACTAMENTE los intervalos en que el sistema no pudo evaluar el
        mercado, y un journal con huecos invisibles se lee como un journal
        completo: al analizarlo, esos minutos parecerian "no hubo señal" en vez
        de "no se supo". Con el forward log alimentando la decision de promover
        a dinero real, esa diferencia importa.
        """
        rec = ScanRecord(
            schema=SCHEMA_VERSION, ts_scan_ms=now_ms, ts_bar_ms=0,
            mode=self.mode, reason=f"{kind}:{detail}", state="NO_SETUP",
            has_signal=False, executable=False,
            execution_block_reason=kind,
        )
        try:
            self.logger.write(rec)
        except Exception as e:                      # nunca matar el bucle
            print(f"[scanner] no se pudo escribir el fallo al journal: {e}")
        print(f"[scanner] {kind} ({self._api_errors}): {detail}")

    # -- bucle ---------------------------------------------------------------

    def next_close_ms(self, now_ms: int) -> int:
        """Cierre de la proxima vela de 5m, mas un colchon.

        El colchon existe porque el venue no publica la vela en el milisegundo
        exacto del cierre. Sin el, la primera peticion devolveria la vela
        anterior y el escaner iria una vela por detras todo el rato.
        """
        return ((now_ms // BAR_5M_MS) + 1) * BAR_5M_MS + 3000

    def run(self, iterations: Optional[int] = None,
            on_record: Optional[Callable[[ScanRecord], None]] = None,
            sleep_fn: Callable[[float], None] = time.sleep) -> None:
        """Bucle continuo. `iterations` acota para tests; None = indefinido.

        Un fallo de datos NO mata el bucle: se registra, cuenta para el kill
        switch de api_errors y se reintenta en la vela siguiente. Un escaner
        que muere al primer timeout de red no sirve para un forward log de
        cuatro semanas.
        """
        n = 0
        while iterations is None or n < iterations:
            now = int(time.time() * 1000)
            try:
                rec = self.scan_once(now)
                if on_record:
                    on_record(rec)
            except StaleDataError as e:
                self._api_errors += 1
                self._log_failure(now, "stale_data", str(e))
            except MarketDataError as e:
                self._api_errors += 1
                self._log_failure(now, "market_data_error", str(e))
            n += 1
            if iterations is not None and n >= iterations:
                break
            wait = max(1.0, (self.next_close_ms(int(time.time() * 1000))
                             - int(time.time() * 1000)) / 1000.0)
            sleep_fn(wait)
