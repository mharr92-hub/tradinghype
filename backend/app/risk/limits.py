"""
risk/limits.py — limite diario, time stop y kill switches (PRD 10, 11, 17).

Tres responsabilidades, todas de la forma "¿puede el sistema abrir algo ahora?":

  1. Maquina de estados del dia, con maximo 1 trade nuevo por dia en V1.
  2. Time stop: ninguna posicion vive mas de 24 h.
  3. Kill switches: las 11 condiciones que detienen nuevas operaciones.

REGISTRO OBLIGATORIO (AUDIT C-14): cuando el limite diario bloquea un setup
A+, la señal se registra IGUAL, con su resultado hipotetico. Sin ese registro
nunca se podra responder si "el primer A+ del dia" fue peor que "el mejor",
que es exactamente la pregunta que abre el limite de 1 trade/dia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..strategies.hype.common import Config
from ..indicators import session_start_ms

HOUR_MS = 60 * 60 * 1000

# Estados del dia (PRD 10)
WAITING = "WAITING"
LONG_CANDIDATE = "LONG_CANDIDATE"
SHORT_CANDIDATE = "SHORT_CANDIDATE"
A_PLUS_READY = "A_PLUS_READY"
TRADE_ACTIVE = "TRADE_ACTIVE"
TRADE_DONE = "TRADE_DONE"
NO_TRADE = "NO_TRADE"

# Motivos de kill switch (PRD 17)
KILL_CONSECUTIVE_LOSSES = "consecutive_losses"
KILL_DAILY_LOSS = "daily_loss_limit"
KILL_STALE_DATA = "market_data_stale"
KILL_API_ERRORS = "api_errors"
KILL_POSITION_MISMATCH = "position_mismatch"
KILL_SL_MISSING = "protective_sl_missing"
KILL_UNKNOWN_ORDER = "unknown_open_order"
KILL_UNEXPECTED_POSITION = "unexpected_hype_position"
KILL_COST_EXCEEDED = "cost_r_exceeded"
KILL_SIGNAL_EXPIRED = "signal_expired"
KILL_DAILY_LIMIT = "daily_trade_limit_reached"
KILL_MANUAL = "manual_kill_switch"


@dataclass
class DayState:
    """Estado del dia de trading. El dia empieza con el mismo reset que el
    VWAP (00:00 UTC = 19:00 Panama), no con la medianoche local: si no, el
    limite diario y la sesion de VWAP se desalinearian."""
    session_start_ms: int
    state: str = WAITING
    trades_opened: int = 0
    realized_pnl_usd: float = 0.0
    consecutive_losses: int = 0
    blocked_a_plus: List[dict] = field(default_factory=list)
    manual_kill: bool = False
    critical: bool = False
    critical_reason: str = ""

    def rollover_if_needed(self, now_ms: int, cfg: Config) -> None:
        start = session_start_ms(now_ms, cfg.session_utc_hour)
        if start != self.session_start_ms:
            prev_losses = self.consecutive_losses      # sobrevive al cambio de dia
            critical = self.critical                   # CRITICAL tambien: es manual
            reason = self.critical_reason
            self.__init__(session_start_ms=start)
            self.consecutive_losses = prev_losses
            self.critical = critical
            self.critical_reason = reason


@dataclass(frozen=True)
class Gate:
    allowed: bool
    reason: str = "ok"

    def __bool__(self) -> bool:
        return self.allowed


def can_open_new_trade(day: DayState, cfg: Config, equity: float,
                       has_open_position: bool,
                       data_age_seconds: float,
                       max_data_age_seconds: float = 120.0,
                       api_error_count: int = 0,
                       position_mismatch: bool = False) -> Gate:
    """Puerta unica para abrir una posicion nueva. Fail-closed en todo.

    El orden importa: primero lo que indica que el sistema esta roto
    (CRITICAL, mismatch, datos viejos), despues lo que indica que el dia ya
    termino. Un sistema roto no debe reportar "limite diario alcanzado".
    """
    if day.critical:
        return Gate(False, f"critical:{day.critical_reason}")
    if day.manual_kill:
        return Gate(False, KILL_MANUAL)
    if position_mismatch:
        return Gate(False, KILL_POSITION_MISMATCH)
    if api_error_count > 0:
        return Gate(False, KILL_API_ERRORS)
    if data_age_seconds > max_data_age_seconds:
        return Gate(False, KILL_STALE_DATA)
    if has_open_position:
        return Gate(False, KILL_UNEXPECTED_POSITION if day.state != TRADE_ACTIVE
                    else "position_already_open")
    if day.consecutive_losses >= 3:
        return Gate(False, KILL_CONSECUTIVE_LOSSES)
    if equity > 0 and day.realized_pnl_usd <= -0.01 * equity:
        return Gate(False, KILL_DAILY_LOSS)
    if day.trades_opened >= cfg.max_trades_per_day:
        return Gate(False, KILL_DAILY_LIMIT)
    return Gate(True)


def record_blocked_a_plus(day: DayState, signal_dict: dict) -> None:
    """Registra un A+ que el limite diario dejo pasar (AUDIT C-14).

    Esto NO es telemetria opcional. Es el unico dato que permitira medir si el
    limite de 1 trade/dia cuesta o ahorra dinero.
    """
    day.blocked_a_plus.append(signal_dict)


def time_stop_due(entry_ts_ms: int, now_ms: int, cfg: Config) -> bool:
    """True cuando la posicion alcanzo max_hold_hours (PRD 11)."""
    return now_ms - entry_ts_ms >= cfg.max_hold_hours * HOUR_MS


def time_stop_bar_index(entry_bar_idx: int, cfg: Config) -> int:
    """Indice de la vela de 5m en la que se dispara el time stop en backtest.
    24 h = 288 velas."""
    return entry_bar_idx + cfg.max_hold_bars_5m


def mark_critical(day: DayState, reason: str) -> None:
    """Estado CRITICAL (PRD 17): tras un fallo del stop protector no se abre
    nada mas hasta resolucion manual. No hay recuperacion automatica: el punto
    de este estado es que un humano mire la cuenta."""
    day.critical = True
    day.critical_reason = reason


def register_trade_result(day: DayState, net_pnl_usd: float) -> None:
    day.realized_pnl_usd += net_pnl_usd
    if net_pnl_usd < 0:
        day.consecutive_losses += 1
    else:
        day.consecutive_losses = 0
    day.state = TRADE_DONE
