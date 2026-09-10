"""
quick_backtest.py — ¿que pasa DESPUES de la entrada? Sobre datos reales de HL.

Responde exactamente tres preguntas, que son las que importan antes de creerse
nada:

  1. De las señales que entraron, ¿cuantas llegaron al TP en menos de 24 h?
  2. De las que tocaron el SL, ¿POR CUANTO se paso? Es decir, el precio real de
     ejecucion frente al stop teorico. Es la medida del hueco, y es lo que
     convierte un riesgo "planificado" de 1R en una perdida de 2R o 4R.
  3. De las que ni TP ni SL en 24 h, ¿a que % de la entrada cerro el time stop?

Y con eso, la media de rentabilidad neta.

ALCANCE HONESTO: `candleSnapshot` de Hyperliquid da ~5000 velas de 5m, que son
unos 17 dias. Con maximo 1 trade/dia, el techo absoluto de muestra son 17
trades, y en la practica seran muchos menos. Eso NO alcanza para afirmar que la
estrategia tiene edge — los kill criteria del proyecto piden >=60 señales solo
para etiquetar un brazo. Sirve para ver la FORMA de los resultados: si los
stops se ejecutan lejos, si el TP de 1.6R se alcanza alguna vez, si el time
stop cierra cerca o lejos.

Lo que SI es real aqui, y no lo es en TradingView: el funding hora a hora con
su signo, el fill posterior al cierre de la señal, los trades perdidos por
drift > 0.10R, el time stop de 24 h y el limite de 1 trade/dia.

Uso:  python -m tools.quick_backtest [--days 17] [--short] [--rr 1.6]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from typing import List, Optional

from app.adapters.hyperliquid_data import HyperliquidData
from app.research.fills import FillModel, resolve_exit, simulate_fill
from app.research.funding import FundingCurve
from app.strategies.hype import engine
from app.strategies.hype.common import SIDE_LONG, Config
from app.strategies.indicators import (BAR_1H_MS, BAR_4H_MS, BAR_5M_MS, Candle,
                                       completed_upto, session_start_ms)

PRE_PAD = 900          # sesion previa completa (288) + sesion actual (288) + warmup.
HTF_WINDOW = 220       # buckets HTF que se pasan a scan()


@dataclass
class Trade:
    ts_entry: int
    side: str
    entry_ref: float
    fill: float
    stop: float
    tp: float
    r_unit: float
    exit_kind: str
    exit_px: float
    hours: float
    mfe_r: float
    mae_r: float
    overshoot_r: float      # cuanto se paso del stop, en R (0 si no lo toco)
    close_pct: float        # cierre vs entrada, en %
    gross_r: float
    funding_r: float
    fees_r: float
    net_r: float


def fetch(days: int):
    hl = HyperliquidData()
    bars5 = min(5000, days * 288 + 50)
    c5 = hl.closed_candles("5m", bars5)
    c1h = hl.closed_candles("1h", min(1000, days * 24 + 120))
    c4h = hl.closed_candles("4h", min(500, days * 6 + 80))
    if not c5:
        raise SystemExit("sin velas de 5m")
    start = c5[0].ts
    funding = hl.funding_history(start - 3_600_000)
    hl.close()
    return c5, c1h, c4h, FundingCurve(funding)


def run(cfg: Config, c5, c1h, c4h, curve: FundingCurve,
        model: FillModel) -> tuple:
    trades: List[Trade] = []
    missed = 0
    signals = 0
    blocked_daily = 0
    gap_cache: dict = {}

    p1 = p4 = 0
    busy_until = -1
    last_day: Optional[int] = None
    traded_today = 0

    for t in range(PRE_PAD, len(c5) - 1):
        close_ms = c5[t].ts + BAR_5M_MS
        p1 = completed_upto(c1h, BAR_1H_MS, close_ms, p1)
        p4 = completed_upto(c4h, BAR_4H_MS, close_ms, p4)
        if p1 < 60 or p4 < 60:
            continue

        day = session_start_ms(c5[t].ts, cfg.session_utc_hour)
        if day != last_day:
            last_day = day
            traded_today = 0

        if t <= busy_until:
            continue

        lo = max(0, t - PRE_PAD)
        res = engine.scan(c4h[max(0, p4 - HTF_WINDOW):p4],
                          c1h[max(0, p1 - HTF_WINDOW):p1],
                          c5[lo:t + 1], cfg, gap_cache=gap_cache)
        if res.signal is None:
            continue

        signals += 1
        if traded_today >= cfg.max_trades_per_day:
            blocked_daily += 1
            continue

        s = res.signal
        fill = simulate_fill(c5[t], c5[t + 1], s.side, s.entry_ref,
                             s.risk_per_unit, model)
        if not fill.filled:
            missed += 1            # el drift se lo comio: NO es un trade
            continue

        # El stop estructural NO se mueve con el fill; el TP si, porque R cambio.
        r = abs(fill.price - s.stop)
        tp = (fill.price + cfg.rr * r if s.side == SIDE_LONG
              else fill.price - cfg.rr * r)

        ex = resolve_exit(c5, t + 1, fill.price, s.stop, tp, s.side, cfg, model)
        busy_until = ex.bar_index
        traded_today += 1

        gross = ((ex.price - fill.price) if s.side == SIDE_LONG
                 else (fill.price - ex.price))
        gross_r = gross / r if r > 0 else 0.0

        # Cuanto se paso del stop. Positivo = ejecuto PEOR que el stop teorico.
        over = 0.0
        if ex.kind == "stop":
            diff = (s.stop - ex.price) if s.side == SIDE_LONG else (ex.price - s.stop)
            over = max(0.0, diff) / r if r > 0 else 0.0

        notional = fill.price
        fc = curve.accrue(c5[t + 1].ts, ex.ts_ms + BAR_5M_MS, s.side, notional,
                          fallback_rate=0.0)
        funding_r = (fc.usd / r) if r > 0 else 0.0
        fees_r = (2.0 * cfg.fee_taker * notional) / r if r > 0 else 0.0

        trades.append(Trade(
            ts_entry=c5[t + 1].ts, side=s.side, entry_ref=s.entry_ref,
            fill=fill.price, stop=s.stop, tp=tp, r_unit=r,
            exit_kind=ex.kind, exit_px=ex.price, hours=ex.hours_held,
            mfe_r=ex.mfe_r, mae_r=ex.mae_r, overshoot_r=over,
            close_pct=(ex.price / fill.price - 1.0) * 100.0,
            gross_r=gross_r, funding_r=funding_r, fees_r=fees_r,
            net_r=gross_r - fees_r - funding_r))

    return trades, signals, missed, blocked_daily


def pct(xs, p):
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((len(s) - 1) * p))))
    return s[k]


def report(trades: List[Trade], signals, missed, blocked, days, cfg):
    print("=" * 76)
    print(f"BACKTEST RAPIDO — {days} dias de velas 5m NATIVAS de Hyperliquid")
    print(f"brazo {cfg.arm_label()}  huella {cfg.fingerprint()}  objetivo {cfg.rr}R")
    print("=" * 76)
    print(f"señales generadas        {signals}")
    print(f"  bloqueadas por 1/dia   {blocked}")
    print(f"  perdidas por drift     {missed}   (>0.10R antes de poder entrar)")
    print(f"  TRADES efectivos       {len(trades)}")

    if not trades:
        print("\nNinguna señal llego a trade. Con esta muestra eso no significa")
        print("que la estrategia sea mala: significa que el setup es raro y que")
        print("~17 dias no bastan para verlo. La frecuencia real es un dato a")
        print("medir con el forward log, no a inferir de aqui.")
        return

    tp = [t for t in trades if t.exit_kind == "tp"]
    sl = [t for t in trades if t.exit_kind == "stop"]
    ts = [t for t in trades if t.exit_kind == "time_stop"]
    other = [t for t in trades if t.exit_kind not in ("tp", "stop", "time_stop")]
    n = len(trades)

    print()
    print("1) ¿LLEGO AL TP EN MENOS DE 24 H?")
    print(f"   TP alcanzado           {len(tp):3d} / {n}  ({100*len(tp)/n:.1f} %)")
    if tp:
        hrs = [t.hours for t in tp]
        print(f"     tiempo hasta el TP   mediana {statistics.median(hrs):.1f} h   "
              f"min {min(hrs):.1f} h   max {max(hrs):.1f} h")

    print()
    print("2) LOS QUE TOCARON EL SL — ¿POR CUANTO SE PASO?")
    print(f"   SL alcanzado           {len(sl):3d} / {n}  ({100*len(sl)/n:.1f} %)")
    if sl:
        ov = [t.overshoot_r for t in sl]
        peor = max(sl, key=lambda t: t.overshoot_r)
        print(f"     exceso medio         {statistics.fmean(ov):.3f} R")
        print(f"     mediana              {statistics.median(ov):.3f} R")
        print(f"     p90                  {pct(ov, 0.90):.3f} R")
        print(f"     PEOR caso            {peor.overshoot_r:.3f} R  "
              f"(perdida bruta {peor.gross_r:.2f} R)")
        limpios = sum(1 for x in ov if x <= 1e-9)
        print(f"     ejecutados EN el stop {limpios}/{len(sl)}  "
              f"({100*limpios/len(sl):.0f} %) — el resto se paso")

    print()
    print("3) LOS QUE NO LLEGARON NI A TP NI A SL EN 24 H")
    print(f"   time stop              {len(ts):3d} / {n}  ({100*len(ts)/n:.1f} %)")
    if ts:
        cp = [t.close_pct if t.side == SIDE_LONG else -t.close_pct for t in ts]
        rr_ = [t.gross_r for t in ts]
        print(f"     cierre vs entrada    media {statistics.fmean(cp):+.3f} %   "
              f"mediana {statistics.median(cp):+.3f} %")
        print(f"     rango                {min(cp):+.3f} % .. {max(cp):+.3f} %")
        print(f"     en R                 media {statistics.fmean(rr_):+.3f} R")
        arriba = sum(1 for x in cp if x > 0)
        print(f"     cerraron a favor     {arriba}/{len(ts)}")
    if other:
        print(f"   sin resolver al final  {len(other)} (datos agotados)")

    print()
    print("4) RENTABILIDAD MEDIA")
    net = [t.net_r for t in trades]
    wins = [x for x in net if x > 0]
    loss = [x for x in net if x <= 0]
    pf = (sum(wins) / abs(sum(loss))) if loss and sum(loss) != 0 else float("inf")
    print(f"   expectancy neta        {statistics.fmean(net):+.4f} R por trade")
    print(f"   suma neta              {sum(net):+.3f} R")
    print(f"   win rate               {100*len(wins)/n:.1f} %")
    print(f"   profit factor          {pf:.3f}")
    print(f"   media ganadora         {statistics.fmean(wins):+.3f} R" if wins else "   media ganadora   —")
    print(f"   media perdedora        {statistics.fmean(loss):+.3f} R" if loss else "   media perdedora  —")
    print(f"   fees medios            {statistics.fmean([t.fees_r for t in trades]):.4f} R")
    print(f"   funding medio          {statistics.fmean([t.funding_r for t in trades]):+.4f} R")
    print(f"   tenencia mediana       {statistics.median([t.hours for t in trades]):.1f} h")
    print(f"   MFE mediana            {statistics.median([t.mfe_r for t in trades]):.2f} R")
    print(f"   MAE mediana            {statistics.median([t.mae_r for t in trades]):.2f} R")

    print()
    print("DETALLE")
    print(f"   {'#':>2} {'lado':5s} {'fill':>9s} {'stop':>9s} {'salida':>10s} "
          f"{'px':>9s} {'h':>5s} {'exceso':>7s} {'netoR':>7s}")
    for i, t in enumerate(trades, 1):
        print(f"   {i:2d} {t.side:5s} {t.fill:9.4f} {t.stop:9.4f} "
              f"{t.exit_kind:>10s} {t.exit_px:9.4f} {t.hours:5.1f} "
              f"{t.overshoot_r:7.3f} {t.net_r:+7.3f}")

    print()
    print("=" * 76)
    print(f"MUESTRA: {n} trades. Los kill criteria del proyecto piden >=60 señales")
    print("solo para ETIQUETAR un brazo. Esto muestra la FORMA de los resultados,")
    print("no si hay edge. Para eso hace falta el historico largo y el OOS.")
    print("=" * 76)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=17)
    ap.add_argument("--rr", type=float, default=1.6)
    ap.add_argument("--short", action="store_true", help="habilitar SHORT")
    ap.add_argument("--momentum", action="store_true", help="brazo F2 en LONG")
    ap.add_argument("--no-clearance", action="store_true")
    args = ap.parse_args(argv)

    cfg = Config(allow_short=args.short, rr=args.rr,
                 require_momentum_long=args.momentum,
                 require_vwap_slope_long=args.momentum,
                 require_target_clearance=not args.no_clearance,
                 risk_mode="pct_equity")

    print("descargando velas nativas de Hyperliquid...", file=sys.stderr)
    c5, c1h, c4h, curve = fetch(args.days)
    real_days = (c5[-1].ts - c5[0].ts) / 86_400_000
    print(f"5m={len(c5)}  1h={len(c1h)}  4h={len(c4h)}  "
          f"funding={len(curve)} puntos  ({real_days:.1f} dias)", file=sys.stderr)

    trades, signals, missed, blocked = run(cfg, c5, c1h, c4h, curve, FillModel())
    report(trades, signals, missed, blocked, round(real_days, 1), cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
