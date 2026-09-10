"""
execution/order_guard.py — las guardas van ANTES que la capacidad de operar.

Se implementa y se testea antes que el adaptador real (IMPLEMENTATION_ORDER
P1.2 antes que P1.3). Un sistema que puede enviar ordenes antes de saber
rechazarlas esta al reves.

Dos responsabilidades:

  1. `revalidate()` — las 9 comprobaciones del PRD 17 cuando Mark pulsa ENTER.
     Nada de lo que la UI tenia en pantalla se da por bueno: la pantalla puede
     llevar 80 segundos ahi.
  2. `verify_protection()` — despues del fill, confirmar contra el exchange que
     SL y TP existen de verdad. Si el SL no esta: estado CRITICAL.

`LiveExecutionDisabled` es la ultima linea: mientras LIVE_EXECUTION=false,
cualquier intento de enviar una orden real lanza excepcion en vez de operar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ..core.config import Settings
from ..risk.limits import (KILL_SIGNAL_EXPIRED, DayState, Gate, can_open_new_trade,
                           mark_critical)
from ..risk.sizing import Sizing
from ..strategies.hype.common import SIDE_LONG, SIDE_SHORT, Config
from ..strategies.hype.engine import Signal


class LiveExecutionDisabled(RuntimeError):
    """Se intento enviar una orden real con LIVE_EXECUTION=false."""


class CriticalExecutionError(RuntimeError):
    """El stop protector no pudo colocarse o verificarse (PRD 17)."""


@dataclass(frozen=True)
class OpenOrder:
    """Una orden tal y como la reporta el EXCHANGE, no como creemos que la
    enviamos. La diferencia es todo el propósito de este modulo."""
    order_id: str
    kind: str            # "stop" | "take_profit" | "entry" | "unknown"
    trigger_price: float
    size: float
    coin: str = ""       # instrumento; un SL sobre otro activo no protege nada
    side: str = ""       # lado de la ORDEN, opuesto al de la posicion
    reduce_only: bool = False


def _closing_side(position_side: str) -> str:
    """Lado que debe tener una orden que CIERRA la posicion."""
    return SIDE_SHORT if position_side == SIDE_LONG else SIDE_LONG


def assert_can_send_orders(settings: Settings) -> None:
    """Unica puerta hacia el exchange. Todo camino de escritura pasa por aqui.

    Se llama tambien desde el adaptador, no solo desde aqui: dos comprobaciones
    en dos capas, porque una sola se puede olvidar en una refactorizacion.
    """
    if not settings.can_send_real_orders:
        raise LiveExecutionDisabled(
            f"LIVE_EXECUTION={settings.live_execution}, HYPE_MODE={settings.mode}. "
            "No se puede enviar una orden real. Cambiarlo requiere aprobacion "
            "explicita de Mark y haber superado SHADOW "
            "(HYPE_TRADING_RESEARCH_PLAN 6)."
        )


def signal_age_seconds(signal: Signal, now_ms: int, bar_ms: int = 5 * 60 * 1000) -> float:
    """Edad de la señal desde el CIERRE de su vela de confirmacion.

    `signal.ts` es la APERTURA de esa vela, asi que la señal no existe hasta
    ts + bar_ms. Medir desde la apertura regalaria 5 minutos de vida a una
    señal que solo debe durar 90 segundos.
    """
    return (now_ms - (signal.ts + bar_ms)) / 1000.0


def drift_r(signal: Signal, current_price: float) -> float:
    """Desplazamiento del precio desde la referencia, en unidades de R.

    Solo cuenta el drift ADVERSO al fill (mas caro para un largo, mas barato
    para un corto). Un precio que mejora no invalida la señal.
    """
    if signal.risk_per_unit <= 0:
        return float("inf")
    delta = ((current_price - signal.entry_ref) if signal.side == SIDE_LONG
             else (signal.entry_ref - current_price))
    return max(0.0, delta) / signal.risk_per_unit


def revalidate(signal: Signal, sizing: Sizing, day: DayState, cfg: Config,
               settings: Settings, *, now_ms: int, current_price: float,
               equity: float, has_open_position: bool,
               data_age_seconds: float, api_error_count: int = 0,
               position_mismatch: bool = False,
               clearance_still_valid: bool = True) -> Gate:
    """Las 9 comprobaciones del PRD 17. Fail-closed y en orden de gravedad."""
    gate = can_open_new_trade(day, cfg, equity, has_open_position,
                              data_age_seconds, settings.max_data_age_seconds,
                              api_error_count, position_mismatch)
    if not gate:
        return gate

    age = signal_age_seconds(signal, now_ms)
    if age > cfg.signal_ttl_seconds:
        return Gate(False, f"{KILL_SIGNAL_EXPIRED}:{age:.0f}s")
    if age < 0:
        return Gate(False, "signal_from_the_future")

    d = drift_r(signal, current_price)
    if d > cfg.max_drift_r:
        # No se persigue la entrada (PRD 13).
        return Gate(False, f"drift_exceeded:{d:.3f}R")

    if not sizing.ok:
        return Gate(False, f"sizing:{sizing.reason}")

    if signal.cost_r > cfg.max_cost_r(signal.side):
        return Gate(False, "cost_r_exceeded")

    if not clearance_still_valid:
        return Gate(False, "target_clearance_invalidated")

    return Gate(True)


def _matches(o: OpenOrder, *, price: float, coin: str, position_side: str,
             qty: float, tol: float, qty_tol: float) -> Optional[str]:
    """¿Esta orden protege REALMENTE esta posicion? Devuelve el motivo del
    fallo, o None si todo cuadra.

    Comprobar solo el precio no basta, y la diferencia importa: una orden con
    el precio correcto pero la mitad del tamaño deja media posicion desnuda; con
    el lado equivocado DUPLICA la posicion en vez de cerrarla; sobre otro
    instrumento no protege nada; y sin reduce_only puede abrir una posicion
    contraria si la original ya se cerro por otra via.
    """
    if abs(o.trigger_price - price) > tol:
        return f"precio {o.trigger_price} != esperado {price}"
    if coin and o.coin and o.coin != coin:
        return f"instrumento {o.coin!r} != esperado {coin!r}"
    expected_side = _closing_side(position_side)
    if o.side and o.side != expected_side:
        return f"lado {o.side!r} no cierra una posicion {position_side!r}"
    if qty > 0 and abs(o.size - qty) > qty_tol:
        return f"tamaño {o.size} != posicion {qty}"
    if not o.reduce_only:
        return "no es reduce_only: podria abrir posicion en vez de cerrarla"
    return None


def verify_protection(orders: Sequence[OpenOrder], expected_stop: float,
                      expected_tp: float, day: DayState,
                      *, coin: str = "", position_side: str = SIDE_LONG,
                      qty: float = 0.0, tolerance: float = 1e-6,
                      qty_tolerance: float = 1e-9) -> None:
    """Confirma contra el exchange que la posicion esta REALMENTE protegida.

    No se comprueba que "haya un stop": se comprueba que haya un stop al precio
    estructural, sobre este instrumento, en el lado que cierra, por el tamaño
    completo de la posicion y con reduce_only. Cualquier desviacion deja la
    posicion parcial o totalmente desnuda, que a efectos practicos es igual de
    grave que no tener stop.

    Un SL que no cuadra no es un aviso: es CRITICAL y bloquea nuevas
    operaciones hasta que un humano mire la cuenta.
    """
    stops = [o for o in orders if o.kind == "stop"]
    tps = [o for o in orders if o.kind == "take_profit"]

    if not stops:
        mark_critical(day, "protective_sl_missing")
        raise CriticalExecutionError(
            "CRITICAL EXECUTION ERROR: no hay stop protector en el exchange "
            "despues del fill. Posicion desprotegida."
        )

    stop_reasons = [_matches(o, price=expected_stop, coin=coin,
                             position_side=position_side, qty=qty,
                             tol=tolerance, qty_tol=qty_tolerance)
                    for o in stops]
    if all(r is not None for r in stop_reasons):
        mark_critical(day, "protective_sl_mismatch")
        raise CriticalExecutionError(
            "CRITICAL EXECUTION ERROR: ningun stop del exchange protege esta "
            f"posicion. Motivos: {'; '.join(r for r in stop_reasons if r)}"
        )

    # El TP ausente o incorrecto NO es critico: la posicion sigue protegida a
    # la baja y el time stop de 24 h sigue vigente. Se reporta para reintentar,
    # porque un TP mal colocado tampoco se puede dejar ahi.
    if not tps:
        raise RuntimeError("take_profit_missing: reintentar colocacion del TP.")
    tp_reasons = [_matches(o, price=expected_tp, coin=coin,
                           position_side=position_side, qty=qty,
                           tol=tolerance, qty_tol=qty_tolerance)
                  for o in tps]
    if all(r is not None for r in tp_reasons):
        raise RuntimeError(
            "take_profit_mismatch: ningun TP cuadra con la posicion. "
            f"Motivos: {'; '.join(r for r in tp_reasons if r)}"
        )


def unknown_orders(orders: Sequence[OpenOrder]) -> list:
    """Ordenes abiertas que el sistema no reconoce (kill switch del PRD 17)."""
    return [o for o in orders if o.kind == "unknown"]
