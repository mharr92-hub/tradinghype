# HYPE COPILOT

Trade decision system especializado en **HYPE Perpetual (Hyperliquid)**. Encuentra el mejor setup LONG o SHORT del día, calcula Entry/SL/TP/Size/Cost, y deja la decisión final a Mark: **ENTER** o **SKIP**.

> **CALIDAD > CANTIDAD** · **NO TRADE > BAD TRADE**

```
LIVE_EXECUTION=false
```

Ninguna ruta de código puede enviar una orden real. El default vive en el código (`backend/app/core/config.py`), no solo en `.env`, para que un fichero de entorno ausente falle hacia el lado seguro.

---

## Estado actual

| Área | Estado |
|---|---|
| Documentación (PRD v2, research plan, auditoría, migración, tests, orden) | ✅ completa |
| Strategy engine LONG + SHORT (P0.1) | 🟡 escrito, **sin ejecutar** — no hay Python en este equipo |
| Riesgo, cost gate, límites diarios, guardas de ejecución | 🟡 escrito, sin ejecutar |
| Backtester realista (P0.2) | ⬜ especificado, no implementado |
| TradingView Pine (P0.3) | ⬜ especificado, no implementado |
| API + forward logger (P0.4) | ⬜ especificado, no implementado |
| Frontend, adaptador Hyperliquid, analytics, AI | ⬜ P1–P3 |

**Bloqueante:** este equipo no tiene Python instalado. Nada del backend se ha ejecutado ni testeado.

```powershell
winget install Python.Python.3.12
# reabrir terminal
python --version
cd backend && python -m unittest discover -s tests -v
```

---

## Empezar por aquí

1. **`docs/PRD_HYPE_COPILOT.md`** — qué es el producto. Única especificación vigente.
2. **`docs/AUDIT_001_CONFLICTOS.md`** — qué cambió respecto al modelo anterior y por qué (C-01 … C-15).
3. **`docs/HYPE_TRADING_RESEARCH_PLAN.md`** — cómo se decide si esto tiene edge.
4. **`docs/IMPLEMENTATION_ORDER.md`** — en qué orden construirlo.

---

## Estructura

```
docs/                             especificación y metodología
  archive/                        versiones superadas, conservadas íntegras
backend/
  app/
    strategies/
      indicators.py               EMA, RSI, MACD, ATR, VWAP de sesión
      hype/
        common.py                 Config, FVG, ciclo de vida del gap, costos
        long.py                   rama LONG (momentum = brazo F2, apagado)
        short.py                  rama SHORT estricta (momentum obligatorio)
        target_clearance.py       niveles deterministas, clearance en R
        scoring.py                A+ y rule compliance (nunca probabilidades)
        engine.py                 scan() — ÚNICA fuente de verdad
      hype_long/rules.py          CONGELADO: referencia long-only, no tocar
    risk/                         sizing, cost gate, límites diarios y kill switches
    execution/order_guard.py      revalidación pre-ENTER, verificación SL/TP
    core/config.py                LIVE_EXECUTION y modos
  tools/                          backtest v0 (DEPRECADO, ver DEPRECATED.md)
  tests/                          16 tests congelados + los nuevos del TEST_PLAN
```

---

## Las reglas que el código no puede romper

Están repartidas por el repo como guardas ejecutables, no solo como comentarios:

| Regla | Dónde se hace cumplir |
|---|---|
| Nunca ensanchar el stop para pasar el cost gate | `risk/cost_gate.py::sanity_check_no_stop_widening` |
| Nunca subir el riesgo para alcanzar el notional mínimo | `risk/sizing.py` → devuelve `SKIP` |
| El stop no depende del objetivo | `long.py`/`short.py::structural_stop` no reciben `rr` |
| Sin probabilidades inventadas | `scoring.py::assert_no_probability` |
| Sin órdenes reales | `execution/order_guard.py::assert_can_send_orders` |
| SHORT deshabilitado por defecto | `Config.allow_short = False` |
| Un SL ausente es CRITICAL, no un aviso | `order_guard.py::verify_protection` |

---

## Política de dirección

`HYPE = LONG ONLY` fue **revocada** el 2026-09-09 por decisión explícita de Mark, ejerciendo la cláusula de escape del propio documento que la establecía. Trazabilidad en `docs/POLICY_CHANGE_001_SHORTS.md` y `docs/AUDIT_001_CONFLICTOS.md` C-01.

"70/30" **no es una cuota**. Significa que el SHORT es estructuralmente más difícil de disparar. El sistema nunca inventa un short para equilibrar una proporción.

Para revertir: `HYPE_ALLOW_SHORT=false`. El módulo long-only original sigue congelado e intacto.

---

## Advertencia

Ninguna de las dos hipótesis (H-R1 long, H-R2 short) está probada en nuestros datos. Ambas son **UNPROVEN**. El backtest v0 que existe en `backend/tools/` tiene sesgo optimista conocido y documentado — sus números no sirven para decidir nada.

Hasta que el backtester realista (P0.2) corra con funding, fills realistas, time stop y límite diario, **este repo no ha medido nada**.
