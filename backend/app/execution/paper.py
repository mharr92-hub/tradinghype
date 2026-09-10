"""
execution/paper.py — motor PAPER. Simula, no envia dinero.

Decision de Mark (2026-09-09): en PAPER, ENTER SIMULA la operacion. Ninguna
ruta de este modulo toca el exchange, y `assert_can_send_orders()` no se invoca
nunca porque no hay nada que enviar. LIVE_EXECUTION sigue en false y este
modulo no lo consulta ni lo necesita.

POR QUE IMPORTA QUE SEA ABURRIDO: el valor de PAPER no esta en simular bien las
ganancias, sino en ejercitar la MECANICA completa — revalidacion, sizing, fill
degradado, colocacion y verificacion de protecciones, time stop, journal — con
las mismas piezas que se usaran con dinero real. Si PAPER usara su propio
modelo de fill, PAPER dejaria de predecir nada sobre TINY.

Por eso importa `research/fills.py` en vez de tener su propia version: PAPER y
BACKTEST comparten codigo, no solo intencion (PRD 2).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from ..research.fills import Exit, Fill, FillModel, resolve_exit, simulate_fill
from ..research.funding import FundingCurve
from ..risk.limits import (DayState, TRADE_ACTIVE, TRADE_DONE, mark_critical,
                           register_trade_result, time_stop_due)
from ..risk.sizing import Sizing
from ..strategies.hype.common import SIDE_LONG, Config
from ..strategies.hype.engine import Signal
from ..strategies.indicators import BAR_5M_MS, Candle
from .order_guard import OpenOrder, verify_protection

COIN = "HYPE"


@dataclass
class PaperPosition:
    """Posicion simulada. Los campos replican lo que hara falta en TINY."""
    side: str
    qty: float
    entry_price: float          # fill REAL simulado, no la referencia
    entry_ref: float            # lo que la señal proponia
    stop: float                 # estructural, NO se recalcula desde el fill
    tp: float
    entry_ts_ms: int
    entry_bar_index: int
    cost_frac: float
    orders: List[OpenOrder] = field(default_factory=list)
    closed: bool = False
    exit: Optional[Exit] = None
    net_pnl_usd: Optional[float] = None
    net_r: Optional[float] = None
    funding_usd: float = 0.0
    fees_usd: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop)

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price


@dataclass(frozen=True)
class PaperResult:
    entered: bool
    reason: str
    position: Optional[PaperPosition] = None
    fill: Optional[Fill] = None


class PaperBroker:
    """Broker simulado. Un unico metodo publico para abrir, otro para cerrar."""

    def __init__(self, cfg: Config, model: FillModel = FillModel(),
                 funding: Optional[FundingCurve] = None,
                 fallback_funding_rate: Optional[float] = None):
        self.cfg = cfg
        self.model = model
        self.funding = funding
        self.fallback_funding_rate = fallback_funding_rate

    # -- apertura ------------------------------------------------------------

    def enter(self, signal: Signal, sizing: Sizing, day: DayState,
              c5: List[Candle], signal_bar_index: int) -> PaperResult:
        """Simula ENTER. Devuelve la posicion o el motivo por el que no hubo trade.

        Que un ENTER pueda terminar en "no hubo trade" NO es un fallo: es el
        comportamiento correcto cuando el precio se fue mas de 0.10R mientras la
        señal viajaba a la pantalla. Registrarlo importa tanto como registrar
        los trades: si un dia el 60 % de los ENTER se pierden por drift, el
        problema es la latencia, no la estrategia, y solo este dato lo dira.
        """
        if not sizing.ok:
            return PaperResult(False, f"sizing:{sizing.reason}")

        signal_bar = c5[signal_bar_index]
        next_bar = (c5[signal_bar_index + 1]
                    if signal_bar_index + 1 < len(c5) else None)

        fill = simulate_fill(signal_bar, next_bar, signal.side,
                             signal.entry_ref, signal.risk_per_unit, self.model)
        if not fill.filled:
            return PaperResult(False, fill.reason, fill=fill)

        # El stop estructural NO se mueve con el fill (PRD 17 paso 4). El TP si
        # se recalcula, porque R cambio al cambiar la entrada.
        r = abs(fill.price - signal.stop)
        tp = (fill.price + self.cfg.rr * r if signal.side == SIDE_LONG
              else fill.price - self.cfg.rr * r)

        pos = PaperPosition(
            side=signal.side, qty=sizing.qty, entry_price=fill.price,
            entry_ref=signal.entry_ref, stop=signal.stop, tp=tp,
            entry_ts_ms=fill.ts_ms, entry_bar_index=signal_bar_index + 1,
            cost_frac=signal.cost_frac,
        )
        pos.orders = self._place_protection(pos)

        # Se verifica igual que en real. Si esta guarda no se ejercita en PAPER,
        # la primera vez que corra sera con dinero encima.
        try:
            verify_protection(pos.orders, pos.stop, pos.tp, day, coin=COIN,
                              position_side=pos.side, qty=pos.qty)
        except Exception as e:
            mark_critical(day, f"paper_protection_failed:{e}")
            return PaperResult(False, f"protection_failed:{e}", fill=fill)

        day.trades_opened += 1
        day.state = TRADE_ACTIVE
        return PaperResult(True, "ok", position=pos, fill=fill)

    def _place_protection(self, pos: PaperPosition) -> List[OpenOrder]:
        """Ordenes protectoras simuladas, con la forma EXACTA que verificara la
        guarda: instrumento, lado que cierra, tamaño completo y reduce_only."""
        closing = "SHORT" if pos.side == SIDE_LONG else "LONG"
        return [
            OpenOrder(f"paper-sl-{pos.entry_ts_ms}", "stop", pos.stop, pos.qty,
                      COIN, closing, True),
            OpenOrder(f"paper-tp-{pos.entry_ts_ms}", "take_profit", pos.tp,
                      pos.qty, COIN, closing, True),
        ]

    # -- cierre --------------------------------------------------------------

    def resolve(self, pos: PaperPosition, c5: List[Candle],
                day: DayState) -> PaperPosition:
        """Resuelve la posicion contra las velas posteriores: STOP, TP o TIME STOP.

        El neto descuenta fees, spread y slippage (ya incorporados al fill y al
        precio de salida) y el funding acumulado hora a hora con su signo real.
        """
        ex = resolve_exit(c5, pos.entry_bar_index, pos.entry_price, pos.stop,
                          pos.tp, pos.side, self.cfg, self.model)

        gross = ((ex.price - pos.entry_price) * pos.qty if pos.side == SIDE_LONG
                 else (pos.entry_price - ex.price) * pos.qty)

        fees = 2.0 * self.cfg.fee_taker * pos.notional

        funding_usd = 0.0
        if self.funding is not None:
            fc = self.funding.accrue(pos.entry_ts_ms, ex.ts_ms + BAR_5M_MS,
                                     pos.side, pos.notional,
                                     self.fallback_funding_rate)
            funding_usd = fc.usd

        net = gross - fees - funding_usd
        risk_usd = pos.risk_per_unit * pos.qty

        pos.exit = ex
        pos.fees_usd = fees
        pos.funding_usd = funding_usd
        pos.net_pnl_usd = net
        pos.net_r = net / risk_usd if risk_usd > 0 else 0.0
        pos.closed = True

        register_trade_result(day, net)
        day.state = TRADE_DONE
        return pos

    # -- time stop en vivo ---------------------------------------------------

    def time_stop_reached(self, pos: PaperPosition,
                          now_ms: Optional[int] = None) -> bool:
        """Para el bucle en vivo: ¿toca cerrar por las 24 h?

        En backtest el limite se cuenta en velas; aqui en tiempo real, porque
        con huecos de datos 288 velas pueden ser mas de 24 horas de reloj.
        """
        now = int(time.time() * 1000) if now_ms is None else now_ms
        return time_stop_due(pos.entry_ts_ms, now, self.cfg)


def summarize(pos: PaperPosition) -> dict:
    """Fila del journal para una posicion cerrada (PRD 16)."""
    d = asdict(pos)
    d["orders"] = [asdict(o) for o in pos.orders]
    d["exit"] = asdict(pos.exit) if pos.exit else None
    d["notional"] = pos.notional
    d["risk_per_unit"] = pos.risk_per_unit
    return d
