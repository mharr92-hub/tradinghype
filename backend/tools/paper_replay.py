"""
paper_replay.py — recorrido PAPER de punta a punta con datos SINTETICOS.

QUE DEMUESTRA: que la mecanica completa funciona — motor de estrategia, sizing,
kill switches, fill degradado, colocacion y verificacion de protecciones,
resolucion por SL/TP/time-stop, contabilidad neta y journal.

QUE NO DEMUESTRA, y conviene decirlo antes que nada: NADA sobre rentabilidad.
Las velas de este archivo estan construidas a mano para que la señal exista.
Un circuito que funciona no es una estrategia que gana. Cada registro que
escribe queda marcado con `mode="PAPER_REPLAY_SYNTHETIC"` para que nadie lo
confunda despues con una observacion de mercado.

Se usan los componentes REALES, no dobles de prueba: si esto pasa, es el mismo
codigo que correra con dinero. Ese es el unico motivo por el que el ejercicio
vale algo.

Uso:  python -m tools.paper_replay
"""

from __future__ import annotations

import datetime as dt
import sys

from app.execution.paper import PaperBroker, summarize
from app.research.fills import FillModel
from app.research.funding import constant_curve
from app.risk.limits import DayState, can_open_new_trade, record_blocked_a_plus
from app.risk.sizing import VenueSpec, expected_profit_usd, size_position
from app.services.scanner import ForwardLogger, ScanRecord
from app.strategies.hype import engine
from app.strategies.hype.common import SIDE_LONG, Config
from app.strategies.indicators import BAR_1H_MS, BAR_4H_MS, BAR_5M_MS, Candle

UTC = dt.timezone.utc
SESSION = int(dt.datetime(2026, 9, 9, 0, 0, tzinfo=UTC).timestamp() * 1000)
PREV = SESSION - 24 * 3_600_000
MODE = "PAPER_REPLAY_SYNTHETIC"


def rising(n, start, step, tf_ms, ts0, vol=100.0):
    out, prev = [], start
    for i in range(n):
        o = prev
        c = o + step
        out.append(Candle(ts0 + i * tf_ms, o, c + 0.05, o - 0.05, c, vol))
        prev = c
    return out


def build_5m():
    """Sesion previa completa (288 velas, necesarias para target_clearance) +
    sesion actual con warmup, impulso que deja un FVG, retest y confirmacion."""
    bars = []

    # Sesion anterior: 288 velas. El gate de clearance exige >=98 % de cobertura,
    # asi que no se puede escatimar aqui.
    prev_c = 46.0
    for i in range(288):
        o = prev_c
        c = o + 0.008
        bars.append(Candle(PREV + i * BAR_5M_MS, o, c + 0.03, o - 0.03, c, 60.0))
        prev_c = c

    # Sesion actual: 20 velas de warmup suave (el motor bloquea las 12 primeras).
    i = 0
    for _ in range(20):
        o = prev_c
        c = o + 0.012
        bars.append(Candle(SESSION + i * BAR_5M_MS, o, c + 0.02, o - 0.02, c, 50.0))
        prev_c = c
        i += 1

    b = prev_c
    # A-B-C: el low de C queda por encima del high de A -> FVG alcista.
    A = Candle(SESSION + i * BAR_5M_MS, b, b + 0.05, b - 0.03, b + 0.03, 120.0); i += 1
    B = Candle(SESSION + i * BAR_5M_MS, b + 0.03, b + 0.55, b + 0.01, b + 0.50, 400.0); i += 1
    C = Candle(SESSION + i * BAR_5M_MS, b + 0.50, b + 0.75, b + 0.25, b + 0.68, 350.0); i += 1
    bars += [A, B, C]                       # zona del gap: (b+0.05, b+0.25)

    # D: primer retest, entra en la zona sin cerrar por debajo.
    D = Candle(SESSION + i * BAR_5M_MS, b + 0.68, b + 0.70, b + 0.20, b + 0.32, 150.0); i += 1
    # E: confirmacion — cierra sobre el gap, sobre el VWAP, sobre el high previo
    #    y sobre su apertura.
    E = Candle(SESSION + i * BAR_5M_MS, b + 0.32, b + 0.80, b + 0.28, b + 0.76, 300.0); i += 1
    bars += [D, E]

    # Velas posteriores: el fill ocurre en la apertura de la siguiente, y luego
    # el precio sube hasta tocar el TP de 1.6R.
    for k in range(12):
        o = bars[-1].c
        c = o + 0.10
        bars.append(Candle(SESSION + i * BAR_5M_MS, o, c + 0.06, o - 0.04, c, 200.0))
        i += 1
    return bars


def main() -> int:
    cfg = Config(allow_short=False, rr=1.6, risk_mode="fixed_usd", risk_usd=1.00,
                 qty_decimals=2, funding_rate_hourly=-0.00000573,
                 funding_expected_hours=2.0)

    c4h = rising(120, 30.0, 0.22, BAR_4H_MS, SESSION - 120 * BAR_4H_MS)
    c1h = rising(120, 44.0, 0.025, BAR_1H_MS, SESSION - 120 * BAR_1H_MS)
    c5 = build_5m()

    print("=" * 74)
    print("RECORRIDO PAPER — DATOS SINTETICOS, NO ES UNA OBSERVACION DE MERCADO")
    print("=" * 74)
    print(f"velas: 4h={len(c4h)}  1h={len(c1h)}  5m={len(c5)}")

    # El motor solo ve hasta la vela de confirmacion. Todo lo posterior existe
    # para resolver la posicion, y pasarselo al scan seria lookahead.
    confirm_idx = len(c5) - 13
    r = engine.scan(c4h, c1h, c5[:confirm_idx + 1], cfg)

    print(f"\n1. MOTOR      estado={r.state}  motivo={r.reason}")
    if r.signal is None:
        print("   sin señal; el replay no puede continuar.")
        for k in ("regime_4h", "align_1h", "fvg", "first_retest", "vwap_band",
                  "target_clearance", "cost_gate"):
            print(f"     {k:18s} {r.checks.get(k)}")
        return 1

    s = r.signal
    print(f"   {s.side}  entry={s.entry_ref:.4f}  stop={s.stop:.4f}  "
          f"tp={s.tp:.4f}  rr={s.rr}")
    print(f"   cost_R={s.cost_r:.4f}  clearance={s.clearance_r:.2f}R  "
          f"checklist={len(s.checklist)} gates")

    day = DayState(session_start_ms=SESSION)
    venue = VenueSpec(min_notional_usd=10.0, size_decimals=2, max_leverage=10.0,
                      tick_size=0.0)
    z = size_position(s.entry_ref, s.stop, s.side, cfg, 1000.0, venue, s.cost_frac)
    print(f"\n2. SIZING     ok={z.ok}  qty={z.qty}  notional=${z.notional:.2f}  "
          f"riesgo_planificado=${z.risk_usd:.4f}  ({z.reason})")
    if not z.ok:
        return 1

    gate = can_open_new_trade(day, cfg, 1000.0, has_open_position=False,
                              data_age_seconds=5.0)
    print(f"3. GATES      permitido={bool(gate)}  ({gate.reason})")

    broker = PaperBroker(cfg, FillModel(),
                         constant_curve(cfg.funding_rate_hourly, PREV,
                                        SESSION + 48 * 3_600_000), 0.0)
    res = broker.enter(s, z, day, c5, confirm_idx)
    print(f"\n4. ENTER      entrado={res.entered}  ({res.reason})")
    if not res.entered:
        print("   sin trade: correcto si el drift supero 0.10R.")
        return 0

    pos = res.position
    slip = pos.entry_price - s.entry_ref
    print(f"   fill={pos.entry_price:.4f} vs referencia {s.entry_ref:.4f} "
          f"(deslizamiento {slip:+.4f})")
    print(f"   stop estructural INTACTO={pos.stop:.4f}  tp recalculado={pos.tp:.4f}")
    print(f"5. PROTECC.   {len(pos.orders)} ordenes verificadas contra el 'exchange'")
    for o in pos.orders:
        print(f"     {o.kind:12s} @{o.trigger_price:.4f} x{o.size} "
              f"{o.coin} {o.side} reduce_only={o.reduce_only}")

    pos = broker.resolve(pos, c5, day)
    ex = pos.exit
    print(f"\n6. SALIDA     {ex.kind} @{ex.price:.4f}  tras {ex.hours_held:.2f}h")
    print(f"   MFE={ex.mfe_r:.2f}R  MAE={ex.mae_r:.2f}R")
    print(f"7. NETO       pnl=${pos.net_pnl_usd:+.4f}  netoR={pos.net_r:+.3f}  "
          f"fees=${pos.fees_usd:.4f}  funding=${pos.funding_usd:+.4f}")
    print(f"8. DIA        estado={day.state}  trades={day.trades_opened}  "
          f"perdidas_seguidas={day.consecutive_losses}")

    # Segundo A+ del mismo dia: debe quedar bloqueado y REGISTRADO (AUDIT C-14).
    gate2 = can_open_new_trade(day, cfg, 1000.0, has_open_position=False,
                              data_age_seconds=5.0)
    print(f"9. 2o TRADE   permitido={bool(gate2)}  ({gate2.reason})")
    if not gate2:
        record_blocked_a_plus(day, {"ts": s.ts, "side": s.side,
                                    "entry": s.entry_ref, "note": "replay"})
        print(f"   A+ bloqueados registrados: {len(day.blocked_a_plus)}")

    logger = ForwardLogger("journal")
    rec = ScanRecord(
        schema=2, ts_scan_ms=pos.entry_ts_ms, ts_bar_ms=c5[confirm_idx].ts,
        mode=MODE, reason="replay_closed", state="TRADE_DONE", side=pos.side,
        has_signal=True, entry_ref=s.entry_ref, stop=pos.stop, tp=pos.tp,
        rr=cfg.rr, clearance_r=s.clearance_r, cost_r=s.cost_r,
        cost_frac=s.cost_frac, funding_rate_hourly=cfg.funding_rate_hourly,
        fee_taker=cfg.fee_taker, fee_taker_confirmed=cfg.fee_taker_confirmed,
        qty=pos.qty, notional=pos.notional, risk_usd=z.risk_usd,
        expected_profit_usd=expected_profit_usd(pos.entry_price, pos.tp, pos.qty,
                                                pos.side, pos.cost_frac),
        sizing_ok=True, sizing_reason="ok", executable=True,
        checks={"exit": ex.kind, "net_r": pos.net_r, "synthetic": True})
    path = logger.write(rec)
    print(f"\n10. JOURNAL   escrito en {path}  (mode={MODE})")

    print("\n" + "=" * 74)
    print("CIRCUITO COMPLETO. Esto prueba MECANICA, no rentabilidad:")
    print("las velas estan hechas a mano para que la señal exista.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
