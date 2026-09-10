"""
backtest_hype_long_v0.py — Backtest mínimo de hype_long_vwap_retest · v0.1 (2026-09-09)

PROPÓSITO: validar la tubería y producir la primera tabla IS con controles.
NO es el motor definitivo (ese vive en el repo, MULTI_STRATEGY_MASTER_PLAN §6).

Limitaciones documentadas de esta v0:
  - SIN funding (el motor completo lo acumula por hora de tenencia).
  - Regla conservadora: si stop y TP se tocan en la misma vela -> cuenta PÉRDIDA.
  - Entrada al cierre de la vela de confirmación (latencia de 1 barra implícita:
    la señal usa solo velas cerradas).
  - Régimen 4H/1H calculado sobre ventanas de 400 buckets (EMA numéricamente
    estable; diferencia vs serie completa < 1e-3 tras 400 barras de warmup).

Uso:
  python3 backtest_hype_long_v0.py --csv hype_5m.csv [--equity 1000]
      [--momentum] [--random-reps 200] [--out-dir resultados]

CSV esperado (con o sin cabecera): ts_ms,open,high,low,close,volume
  ts_ms = epoch en milisegundos de la APERTURA de la vela de 5m, UTC.

Salidas (escritura incremental, no solo al final):
  <out>/trades.csv   una fila por trade, escrita al cerrarse cada trade
  <out>/report.txt   resumen + controles
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import statistics
import sys
from typing import List, Optional

from hype_long_vwap_retest import (BAR_5M_MS, Candle, Config, ScanResult,
                                   ema_series, scan)

H1_MS = 60 * 60 * 1000
H4_MS = 4 * H1_MS
HTF_WINDOW = 400          # buckets de contexto que se pasan a scan()
PRE_SESSION_PAD = 64      # velas 5m previas a la sesión para ATR/confirmaciones


# ---------------------------------------------------------------------------
# Carga y resampleo
# ---------------------------------------------------------------------------

def load_csv(path: str) -> List[Candle]:
    out: List[Candle] = []
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            try:
                ts = int(float(row[0]))
            except ValueError:
                continue  # cabecera
            o, h, l, c, v = (float(x) for x in row[1:6])
            out.append(Candle(ts=ts, o=o, h=h, l=l, c=c, v=v))
    out.sort(key=lambda x: x.ts)
    dedup: List[Candle] = []
    for cd in out:
        if dedup and dedup[-1].ts == cd.ts:
            dedup[-1] = cd
        else:
            dedup.append(cd)
    return dedup


def resample(c5: List[Candle], tf_ms: int) -> List[Candle]:
    """Agrega 5m -> tf. Cada bucket queda indexado por su apertura (floor)."""
    buckets: List[Candle] = []
    cur_key = None
    o = h = l = c = v = None
    for cd in c5:
        key = (cd.ts // tf_ms) * tf_ms
        if key != cur_key:
            if cur_key is not None:
                buckets.append(Candle(cur_key, o, h, l, c, v))
            cur_key, o, h, l, c, v = key, cd.o, cd.h, cd.l, cd.c, cd.v
        else:
            h = max(h, cd.h)
            l = min(l, cd.l)
            c = cd.c
            v += cd.v
    if cur_key is not None:
        buckets.append(Candle(cur_key, o, h, l, c, v))
    return buckets


def completed_upto(buckets: List[Candle], tf_ms: int, now_close_ms: int,
                   ptr: int) -> int:
    """Avanza ptr hasta el último bucket cuyo CIERRE <= now_close_ms. Devuelve
    el nuevo ptr (índice exclusivo)."""
    while ptr < len(buckets) and buckets[ptr].ts + tf_ms <= now_close_ms:
        ptr += 1
    return ptr


# ---------------------------------------------------------------------------
# Motor v0
# ---------------------------------------------------------------------------

class Trade:
    __slots__ = ("ts_in", "ts_out", "entry", "stop", "tp", "qty", "cost_r",
                 "outcome", "net_r", "bars_held", "equity_after", "stop_pct")

    def as_row(self):
        return [self.ts_in, self.ts_out, f"{self.entry:.6f}", f"{self.stop:.6f}",
                f"{self.tp:.6f}", f"{self.qty:.4f}", f"{self.cost_r:.4f}",
                self.outcome, f"{self.net_r:.4f}", self.bars_held,
                f"{self.equity_after:.2f}"]


def regime_flags(c5: List[Candle], cfg: Config):
    """Precomputa por vela de 5m si el régimen 4H y la alineación 1H estaban
    activos AL CIERRE de esa vela (solo buckets completados; sin lookahead).
    Se usa para el early-out y para el control de entradas aleatorias."""
    b4 = resample(c5, H4_MS)
    b1 = resample(c5, H1_MS)

    def flag_series(buckets, need_slope):
        closes = [x.c for x in buckets]
        ef = ema_series(closes, cfg.ema_fast)
        es = ema_series(closes, cfg.ema_slow)
        flags = []
        for i in range(len(buckets)):
            if i < cfg.ema_slow + cfg.slope_lookback + 1:
                flags.append(False)
                continue
            ok = closes[i] > ef[i] > es[i]
            if need_slope:
                ok = ok and es[i] > es[i - cfg.slope_lookback]
            flags.append(ok)
        return flags

    f4 = flag_series(b4, True)
    f1 = flag_series(b1, False)
    out = []
    p4 = p1 = 0
    for cd in c5:
        close_ms = cd.ts + BAR_5M_MS
        p4 = completed_upto(b4, H4_MS, close_ms, p4)
        p1 = completed_upto(b1, H1_MS, close_ms, p1)
        ok = (p4 >= 1 and f4[p4 - 1]) and (p1 >= 1 and f1[p1 - 1])
        out.append(ok)
    return out, b4, b1


def resolve_forward(c5: List[Candle], start: int, entry: float, stop: float,
                    tp: float, max_bars: Optional[int] = None):
    """Camina desde start+1. Conservador: stop y TP en la misma vela = pérdida.
    Devuelve (outcome, exit_idx, exit_price)."""
    end = len(c5) if max_bars is None else min(len(c5), start + 1 + max_bars)
    for j in range(start + 1, end):
        bar = c5[j]
        hit_stop = bar.l <= stop
        hit_tp = bar.h >= tp
        if hit_stop:                      # prioridad conservadora
            return "stop", j, stop
        if hit_tp:
            return "tp", j, tp
    return "open_at_end", end - 1, c5[end - 1].c


def run_backtest(c5: List[Candle], cfg: Config, equity0: float,
                 out_dir: str, random_reps: int = 200,
                 progress: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    trades_path = os.path.join(out_dir, "trades.csv")
    tf = open(trades_path, "w", newline="")
    tw = csv.writer(tf)
    tw.writerow(["ts_in", "ts_out", "entry", "stop", "tp", "qty", "cost_r",
                 "outcome", "net_r", "bars_held", "equity_after"])
    tf.flush()

    flags, b4, b1 = regime_flags(c5, cfg)
    cost_frac = 2.0 * cfg.fee_taker + cfg.spread_rt + cfg.slippage_rt

    equity = equity0
    peak = equity
    max_dd = 0.0
    trades: List[Trade] = []
    gap_cache: dict = {}
    eligible_idx: List[int] = []

    warm = cfg.atr_len + 3
    in_pos_until = -1
    p4 = p1 = 0
    n = len(c5)
    for t in range(warm, n):
        close_ms = c5[t].ts + BAR_5M_MS
        p4 = completed_upto(b4, H4_MS, close_ms, p4)
        p1 = completed_upto(b1, H1_MS, close_ms, p1)
        if progress and t % 20000 == 0:
            print(f"  ... vela {t}/{n}", file=sys.stderr)
        if t <= in_pos_until:
            continue                       # una posición a la vez
        if not flags[t]:
            continue                       # early-out (idéntico a scan)
        eligible_idx.append(t)
        # ventana de 5m: sesión + pad (los gaps y el VWAP son de sesión)
        from hype_long_vwap_retest import session_start_ms
        start_ms = session_start_ms(c5[t].ts, cfg.session_utc_hour)
        i0 = t
        while i0 > 0 and c5[i0 - 1].ts >= start_ms:
            i0 -= 1
        lo = max(0, i0 - PRE_SESSION_PAD)
        res: ScanResult = scan(b4[max(0, p4 - HTF_WINDOW):p4],
                               b1[max(0, p1 - HTF_WINDOW):p1],
                               c5[lo:t + 1], cfg, equity, gap_cache=gap_cache)
        if res.plan is None:
            continue
        p = res.plan
        outcome, j, _ = resolve_forward(c5, t, p.entry, p.stop, p.tp)
        risk_usd = equity * cfg.risk_pct
        if outcome == "tp":
            net_r = cfg.rr - p.cost_r
        elif outcome == "stop":
            net_r = -1.0 - p.cost_r
        else:
            gross = (c5[j].c - p.entry) / (p.entry - p.stop)
            net_r = gross - p.cost_r
        equity += risk_usd * net_r
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)

        tr = Trade()
        tr.ts_in, tr.ts_out = c5[t].ts, c5[j].ts
        tr.entry, tr.stop, tr.tp, tr.qty = p.entry, p.stop, p.tp, p.qty
        tr.cost_r, tr.outcome, tr.net_r = p.cost_r, outcome, net_r
        tr.bars_held = j - t
        tr.equity_after = equity
        tr.stop_pct = (p.entry - p.stop) / p.entry
        trades.append(tr)
        tw.writerow(tr.as_row())
        tf.flush()                         # guardado incremental
        in_pos_until = j
    tf.close()

    # --- métricas ---
    wins = [x.net_r for x in trades if x.net_r > 0]
    losses = [x.net_r for x in trades if x.net_r <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf") if wins else 0.0
    report = {
        "bars": n,
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "profit_factor_netR": pf,
        "expectancy_netR": statistics.fmean(x.net_r for x in trades) if trades else 0.0,
        "sum_netR": sum(x.net_r for x in trades),
        "max_dd_pct": max_dd * 100.0,
        "equity_final": equity,
        "avg_bars_held": statistics.fmean(x.bars_held for x in trades) if trades else 0.0,
        "eligible_bars": len(eligible_idx),
    }

    # --- controles ---
    report["ctrl_buy_hold_pct"] = (c5[-1].c / c5[0].c - 1.0) * 100.0
    # régimen-long: largo mientras flags==True, con costos por cada round trip
    ret = 1.0
    entry_px = None
    switches = 0
    for t in range(n):
        if flags[t] and entry_px is None:
            entry_px = c5[t].c
        elif not flags[t] and entry_px is not None:
            ret *= (c5[t].c / entry_px) * (1.0 - cost_frac)
            entry_px = None
            switches += 1
    if entry_px is not None:
        ret *= (c5[-1].c / entry_px) * (1.0 - cost_frac)
        switches += 1
    report["ctrl_regime_long_pct"] = (ret - 1.0) * 100.0
    report["ctrl_regime_long_roundtrips"] = switches

    # random matched: mismas nº de entradas, mismos stop_pct, mismo rr,
    # entradas aleatorias en barras elegibles
    if trades and len(eligible_idx) > len(trades):
        rng = random.Random(42)
        sums = []
        for _ in range(random_reps):
            s = 0.0
            for tr in trades:
                t0 = rng.choice(eligible_idx)
                e = c5[t0].c
                st = e * (1.0 - tr.stop_pct)
                tpx = e + cfg.rr * (e - st)
                oc, j, _ = resolve_forward(c5, t0, e, st, tpx)
                cr = cost_frac / tr.stop_pct
                if oc == "tp":
                    s += cfg.rr - cr
                elif oc == "stop":
                    s += -1.0 - cr
                else:
                    s += (c5[j].c - e) / (e - st) - cr
            sums.append(s)
        sums.sort()
        real = report["sum_netR"]
        pct = sum(1 for x in sums if x < real) / len(sums)
        report["ctrl_random_reps"] = random_reps
        report["ctrl_random_mean_sumR"] = statistics.fmean(sums)
        report["ctrl_random_pctile_of_real"] = pct * 100.0
    else:
        report["ctrl_random_reps"] = 0

    # --- volcado ---
    lines = ["REPORTE BACKTEST v0 — hype_long_vwap_retest_v1",
             "(sin funding; conservador: stop+TP misma vela = perdida)",
             ""]
    for k, v in report.items():
        lines.append(f"{k}\t{v:.4f}" if isinstance(v, float) else f"{k}\t{v}")
    txt = "\n".join(lines)
    with open(os.path.join(out_dir, "report.txt"), "w") as f:
        f.write(txt + "\n")
    print(txt)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--momentum", action="store_true",
                    help="activar variante F2 (MACD/RSI/vol/VWAP asc.)")
    ap.add_argument("--rr", type=float, default=1.0,
                    help="reward:risk (1.0 = E1 base, 2.0 = E2)")
    ap.add_argument("--random-reps", type=int, default=200)
    ap.add_argument("--out-dir", default="resultados_backtest")
    ap.add_argument("--max-bars", type=int, default=0,
                    help="cap de velas para pruebas rápidas (0 = todas)")
    args = ap.parse_args(argv)

    c5 = load_csv(args.csv)
    if args.max_bars:
        c5 = c5[:args.max_bars]
    if len(c5) < 3000:
        print(f"AVISO: solo {len(c5)} velas de 5m; el régimen 4H necesita "
              f"~2700 para calentar. Resultados poco significativos.",
              file=sys.stderr)
    cfg = Config(require_momentum=args.momentum, rr=args.rr)
    run_backtest(c5, cfg, args.equity, args.out_dir,
                 random_reps=args.random_reps)


if __name__ == "__main__":
    main()
