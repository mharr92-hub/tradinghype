# PLAN DE MIGRACIÓN — v1 (long-only, 1R) → v2 (long+short, 1.6R, 1 trade/día, 24 h)

**Fecha:** 2026-09-09 · Deriva de `AUDIT_001_CONFLICTOS.md` y `PRD_HYPE_COPILOT.md` v2.0.

Leyenda: **KEEP** intacto · **FREEZE** intacto y además protegido contra cambios · **MODIFY** sobrevive con cambios · **CREATE** nuevo · **DELETE** se elimina · **DEPRECATE** se conserva como referencia pero deja de usarse.

---

## 1. Documentos

| Archivo | Acción | Detalle |
|---|---|---|
| `docs/PRD_HYPE_COPILOT.md` | **MODIFY → v2.0** | Reescrito de principio a fin. Sin addendums contradictorios. ✅ hecho |
| `docs/HYPE_LONG_RESEARCH_PLAN.md` | **DELETE (renombrado)** | → `docs/HYPE_TRADING_RESEARCH_PLAN.md`. ✅ hecho |
| `docs/archive/HYPE_LONG_RESEARCH_PLAN_v0.1_SUPERSEDED.md` | **CREATE** | Copia íntegra del v0.1. Preserva el historial y el motivo del cambio. ✅ hecho |
| `docs/HYPE_TRADING_RESEARCH_PLAN.md` | **CREATE** | v1.0, con §0 Historial de política. ✅ hecho |
| `docs/AUDIT_001_CONFLICTOS.md` | **CREATE** | C-01…C-15. ✅ hecho |
| `docs/POLICY_CHANGE_001_SHORTS.md` | **MODIFY** | Apuntar al PRD v2.0; el cerrojo de ejecución pasa a ser `LIVE_EXECUTION=false` (global), no solo `HYPE_SHORT_EXECUTION_MODES` |
| `docs/MIGRATION_PLAN.md` · `TEST_PLAN.md` · `IMPLEMENTATION_ORDER.md` | **CREATE** | Este documento y sus hermanos |
| `MULTI_STRATEGY_MASTER_PLAN.md` · `TRADINGVIEW_INTEGRATION_PLAN.md` · `BTC_LONG_RESEARCH_PLAN.md` | **PENDIENTE** | No están en el repo (`AUDIT` C-13). Si existen en `github.com/mharr92-hub/tradinghype`, traerlos y reconciliar §0 de cada uno |

---

## 2. Motor de estrategia

| Archivo | Acción | Detalle |
|---|---|---|
| `backend/app/strategies/hype_long/rules.py` | **FREEZE** | Referencia de reversión a LONG ONLY. **No se toca.** Su test verifica por inspección del fuente que la cadena `"SHORT"` no aparece; cualquier edición lo rompe. Sirve además como oráculo de paridad numérica del motor nuevo |
| `backend/app/strategies/indicators.py` | **KEEP** | EMA/RSI/MACD/ATR/VWAP/resample, extraídos sin cambio de comportamiento |
| `backend/app/strategies/hype/rules.py` | **DELETE** | Borrador intermedio de esta sesión, sustituido por el desglose de abajo |
| `backend/app/strategies/hype/common.py` | **CREATE** | `Config`, `Candle`, `FVG`, ciclo de vida del gap (toque/confirmación/invalidación), banda VWAP, estados del setup. Todo lo compartido por las dos direcciones |
| `backend/app/strategies/hype/long.py` | **CREATE** | Régimen 4H, alineación 1H, detección de FVG alcista, confirmación long, stop estructural long. Momentum como **brazo F2** |
| `backend/app/strategies/hype/short.py` | **CREATE** | Rama estricta: `RSI 4H ≤ 45`, MACD 1H `< 0` y descendiendo, FVG bajista, confirmación short, momentum **obligatorio**, volumen 1.20×, VWAP descendente |
| `backend/app/strategies/hype/target_clearance.py` | **CREATE** | Swing high/low 1H confirmado (pivote n=2, sin lookahead) y previous day high/low. `clearance_R` |
| `backend/app/strategies/hype/scoring.py` | **CREATE** | Definición de A+ y *rule compliance score*. **Prohibido emitir probabilidades** |
| `backend/app/strategies/hype/engine.py` | **CREATE** | Orquestador: única `scan()` pública que usan backtest, paper, shadow, tiny y live |

---

## 3. Riesgo y límites

| Archivo | Acción | Detalle |
|---|---|---|
| `backend/app/risk/sizing.py` | **CREATE** | Tres regímenes: `pct_equity` (research), `fixed_usd $1` (TINY), `fixed_usd $125` (producción). Valida notional mínimo/máximo, tick size, size precision, colateral. **`SKIP` si no cabe; nunca sube el riesgo** |
| `backend/app/risk/cost_gate.py` | **CREATE** | `Cost_R` con fees + spread + slippage + **funding estimado por tenencia**, límite 0.15 long / 0.12 short. **Nunca ensancha el stop** |
| `backend/app/risk/limits.py` | **CREATE** | Límite de 1 trade/día con la máquina de estados del PRD §10, time stop 24 h, 3 pérdidas seguidas, −1 % diario, kill switches |

---

## 4. Ejecución

| Archivo | Acción | Detalle |
|---|---|---|
| `backend/app/execution/hyperliquid.py` | **CREATE** | Adaptador con **`LIVE_EXECUTION=false` por defecto**. En esta fase solo implementa lectura y una ruta de orden que **lanza excepción** si se invoca sin aprobación explícita |
| `backend/app/execution/order_guard.py` | **CREATE** | Revalidación previa a ENTER (9 comprobaciones del PRD §17), verificación de SL/TP contra el exchange, estado `CRITICAL` si falta el SL |
| `backend/app/execution/paper.py` | **CREATE** | Fills simulados con el mismo modelo de `research/fills.py`, para que PAPER y BACKTEST coincidan |

---

## 5. Research

| Archivo | Acción | Detalle |
|---|---|---|
| `backend/tools/backtest_hype_long_v0.py` | **DEPRECATE** | Se conserva como referencia histórica y para comparar contra el motor nuevo. Añadir cabecera: sin funding, fill al close, sin límite diario, sin time stop ⇒ **sus resultados no son concluyentes** para el modelo v2 |
| `backend/app/research/fills.py` | **CREATE** | Fill realista: delay tras el cierre, spread, slippage, drift máx 0.10R, **missed trade** cuando se supera |
| `backend/app/research/funding.py` | **CREATE** | Funding por hora de tenencia real, con signo (largo paga / corto cobra cuando la tasa es positiva). Carga `fundingHistory` de HL |
| `backend/app/research/metrics.py` | **CREATE** | Las 17 métricas del PRD §19, con cortes LONG/SHORT/COMBINED/ACCEPTED/SKIPPED |
| `backend/app/research/backtest.py` | **CREATE** | Motor v1: matriz F×E×D×G, límite diario, time stop 24 h, funding, fills realistas, controles del research plan §4 |
| `backend/app/research/controls.py` | **CREATE** | Random matched, drift de régimen, sensibilidad, outliers, walk-forward, estrés de costos, **estrés de fill**, **coste del límite diario** |

---

## 6. API, datos y app

| Archivo | Acción | Detalle |
|---|---|---|
| `backend/app/core/config.py` | **CREATE** | Settings por env. `LIVE_EXECUTION=false`, `HYPE_MODE=RESEARCH`, riesgo por modo, `allow_short` |
| `backend/app/adapters/hyperliquid_data.py` | **CREATE** | `candleSnapshot`, `fundingHistory`, metadatos de tick/size, detección de datos *stale* |
| `backend/app/db/models.py` | **CREATE** | `signals` (escrita **antes** del resultado), `trades`, `daily_state`, `kill_switch_events` |
| `backend/app/api/signals.py` | **CREATE** | `GET /api/signals/current`, `POST /api/signals/{id}/enter`, `POST /api/signals/{id}/skip` |
| `backend/app/api/tradingview.py` | **CREATE** | `POST /api/tradingview/signal` — **solo notifica; nunca ejecuta**. Revalida con datos HL |
| `frontend/` | **CREATE** | Next.js: home con la pregunta única del PRD §16, Signal Card, ENTER/SKIP |
| `tradingview/hype_copilot_tv_v1.pine` | **CREATE** | PRD §15, incluyendo las líneas 1R / 1.6R / 2R y `target clearance` |

---

## 7. Tests

| Archivo | Acción | Detalle |
|---|---|---|
| `backend/tests/test_hype_long_vwap_retest.py` | **FREEZE** | 16 tests del módulo congelado. Deben seguir verdes tras toda la migración: son la garantía de que la reversión a LONG ONLY sigue siendo posible |
| `backend/tests/test_hype_long.py` | **CREATE** | Rama long del motor nuevo, incluida la **paridad numérica** contra el módulo congelado |
| `backend/tests/test_hype_short.py` | **CREATE** | Rama short estricta, incluida cada condición fallando por separado |
| `backend/tests/test_target_clearance.py` · `test_risk.py` · `test_execution_guards.py` · `test_no_lookahead.py` · `test_research_costs.py` | **CREATE** | Ver `TEST_PLAN.md` |

Un test del v0.1 debe **MODIFICARSE**: `test_confirmation_emits_long_plan` (línea 128) afirma `tp − entry == entry − stop`, es decir 1:1 exacto. En el módulo congelado se queda como está; su equivalente en el motor nuevo debe afirmar `tp − entry == rr · (entry − stop)` leyendo `rr` de la config.

---

## 8. Orden seguro de ejecución de la migración

1. Documentos (hecho) — nadie escribe código contra una especificación obsoleta.
2. `common.py` + `long.py`, con el test de **paridad** contra el módulo congelado verde. Sin esa paridad no se sigue: es la única prueba de que no se rompió lo que ya funcionaba.
3. `target_clearance.py` + `scoring.py` + `short.py`.
4. `risk/` completo.
5. `research/fills.py` + `funding.py`, y solo entonces `research/backtest.py`. **Ningún número se reporta antes de este punto.**
6. API + forward logger (puede arrancar en paralelo desde el paso 2, en modo log-only).
7. TradingView y frontend, en paralelo.
8. `execution/` — el último, y con `LIVE_EXECUTION=false` hasta aprobación explícita de Mark.
