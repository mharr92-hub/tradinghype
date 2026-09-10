"""
run_forward_logger.py — arranca el forward log y NO lo para.

El reloj de las 4 semanas de PAPER empieza cuando esto arranca
(HYPE_TRADING_RESEARCH_PLAN 6, fase 1). Cuanto antes corra, antes hay muestra.

REGLA MIENTRAS RECOGE: no se tocan parametros. Si a mitad de la muestra se
cambia un umbral, la mitad anterior deja de ser comparable con la posterior y
el N util vuelve a cero. Cualquier cambio de parametro es un brazo nuevo, no
una mejora del actual.

Escribe SIEMPRE, tambien cuando no hay señal y cuando falla la red: un journal
con huecos invisibles se lee como un journal completo.
"""
import sys, time
sys.path.insert(0, ".")

from app.core.config import load_settings
from app.services.scanner import ForwardLogger, Scanner
from app.strategies.hype.common import Config

s = load_settings()
cfg = Config(allow_short=s.allow_short,
             risk_mode="fixed_usd" if s.mode == "TINY" else "pct_equity")

print(f"[forward] modo={s.mode} allow_short={s.allow_short} "
      f"live_execution={s.live_execution}", flush=True)
print(f"[forward] estrategia CONGELADA — no tocar parametros durante la muestra",
      flush=True)

sc = Scanner(cfg, mode=s.mode, equity=s.equity_usd, journal_dir="journal")

def report(rec):
    tag = "SEÑAL" if rec.has_signal else rec.reason[:28]
    print(f"[forward] {time.strftime('%H:%M:%S')} {rec.state:22s} {tag:30s} "
          f"px={rec.price} 4H={'L' if rec.regime_4h_long else 'S' if rec.regime_4h_short else '-'}",
          flush=True)

sc.run(iterations=None, on_record=report)
