# TEST PLAN — HYPE Copilot v2

**Fecha:** 2026-09-09 · Cubre los 26 tests obligatorios del nuevo modelo más los heredados que siguen siendo válidos.

Principio: **fail-closed**. Un test que no puede evaluar su condición **falla**; no se salta.

---

## Parte 1 — Tests existentes: veredicto

`backend/tests/test_hype_long_vwap_retest.py` (16 tests, módulo congelado `hype_long/rules.py`).

| Test | Veredicto | Motivo |
|---|---|---|
| `test_vwap_hand_computed` | **VÁLIDO** | VWAP calculado a mano; la fórmula no cambia |
| `test_fvg_detection` | **VÁLIDO** | Detección de FVG alcista |
| `test_ema_follows_rising_series` | **VÁLIDO** | Propiedad de la EMA |
| `test_confirmation_emits_long_plan` | **VÁLIDO en el módulo congelado / MODIFICAR en el motor nuevo** | Afirma 1:1 exacto (`tp−entry == entry−stop`). En el motor nuevo pasa a `tp−entry == rr·(entry−stop)` |
| `test_no_signal_before_confirmation` | **VÁLIDO** | Anti-lookahead básico |
| `test_first_retest_is_consumed` | **VÁLIDO** | Primer retest único |
| `test_invalidated_gap_never_signals` | **VÁLIDO** | Gap invalidado no revive |
| `test_no_touch_no_signal` | **VÁLIDO** | |
| `test_cost_gate_rejects_expensive_trade` | **VÁLIDO / AMPLIAR** | Falta la variante short a 0.12R |
| `test_stale_gap_rejected` | **VÁLIDO** | |
| `test_session_warmup_blocks_early_signals` | **VÁLIDO** | |
| `test_bearish_regime_never_trades` | **VÁLIDO en el congelado / REINTERPRETAR en el nuevo** | En v2 un régimen bajista **no** bloquea todo: bloquea el LONG y habilita la evaluación SHORT. Se desdobla en dos tests (#4 y #5 de la parte 2) |
| `test_module_source_has_no_opposite_direction` | **VÁLIDO — y debe seguir verde** | Es el candado que mantiene viable la reversión a LONG ONLY. Por eso el motor bidireccional vive en otro módulo |
| `test_policy_guard_raises_on_tampered_plan` | **VÁLIDO** | |
| `test_cache_gives_identical_results` | **VÁLIDO / PORTAR** | Equivalencia del cache de gaps; debe replicarse en el motor nuevo con gaps de ambos lados |
| `test_momentum_flag_gates_signal` | **VÁLIDO / AMPLIAR** | En short el momentum no es opcional |

---

## Parte 2 — Los 26 tests obligatorios

| # | Test | Archivo | Cómo se prueba |
|---|---|---|---|
| 1 | Long válido produce LONG | `test_hype_long.py` | Escenario sintético: régimen 4H+1H alcista, FVG alcista en sesión, primer retest en la banda VWAP, vela de confirmación ⇒ `plan.side == LONG` |
| 2 | Short válido produce SHORT | `test_hype_short.py` | Simétrico con **todas** las condiciones estrictas satisfechas, incluido volumen 1.20× y VWAP descendente |
| 3 | Condición short incompleta nunca produce SHORT | `test_hype_short.py` | **Paramétrico**: partiendo del escenario válido, romper *una sola* condición cada vez (RSI 4H = 50, MACD 1H ascendente, volumen 1.10×, VWAP plano, `close > open`, …) y afirmar `plan is None` en los N casos. Este es el test más importante de la rama short |
| 4 | Régimen bajista bloquea longs | `test_hype_long.py` | 4H bajista ⇒ ningún plan LONG, sea cual sea el 5m |
| 5 | Régimen alcista bloquea shorts | `test_hype_short.py` | 4H alcista ⇒ ningún plan SHORT |
| 6 | Primer retest único | `test_hype_long.py`, `test_hype_short.py` | Tras confirmar, el precio vuelve a la zona y "confirmaría" otra vez ⇒ `no_setup` |
| 7 | Gap invalidado nunca revive | ambos | Cierre a través del lado lejano ⇒ `dead` permanente, aunque el precio vuelva |
| 8 | Cost gate long | `test_risk.py` | `fee_taker` alto ⇒ rechazo con motivo `cost_gate`; y `Cost_R` calculado a mano coincide |
| 9 | Cost gate short | `test_risk.py` | Un `Cost_R` de 0.13 **pasa** en long y **falla** en short |
| 10 | Stop estructural | ambos | Long: `stop < min(gap_lo, low del tramo)`. Short: `stop > max(gap_hi, high del tramo)`. Y el stop **no** cambia al cambiar `rr` |
| 11 | Target = 1.6R con E2 activo | `test_hype_long.py` | `tp − entry == 1.6 · (entry − stop)` con tolerancia numérica |
| 12 | Target clearance bloquea trades | `test_target_clearance.py` | Colocar un swing high 1H confirmado a 1.2R por encima del entry con `rr=1.6` ⇒ rechazo `target_clearance`. Y con el nivel a 2.0R ⇒ pasa |
| 13 | Signal expiry | `test_execution_guards.py` | Señal con 91 s de antigüedad ⇒ `ENTER` rechazado con `signal_expired` |
| 14 | Drift > 0.10R bloquea entry | `test_execution_guards.py` | Precio actual desplazado 0.11R ⇒ rechazo `drift_exceeded`; a 0.09R ⇒ pasa |
| 15 | Sizing TINY de $1 | `test_risk.py` | Pérdida planificada hasta el stop, **incluidos costos**, ≤ $1.00. Verificar que no es una posición nocional de $1 |
| 16 | No se aumenta riesgo para cumplir el notional mínimo | `test_risk.py` | Venue con notional mínimo que exigiría $1.40 de riesgo ⇒ resultado `SKIP`, **no** una posición mayor |
| 17 | Máximo una posición | `test_execution_guards.py` | Con posición HYPE abierta, `ENTER` rechazado |
| 18 | Máximo un trade diario | `test_risk.py` | Tras `TRADE_DONE`, un segundo A+ del mismo día se registra pero no se puede ejecutar. Y el día siguiente (cruce de las 00:00 UTC) se resetea |
| 19 | Time stop ≤ 24 h | `test_research_costs.py` | Posición que no toca SL ni TP en 288 velas de 5m ⇒ salida `time_stop` en la barra 288, no más tarde |
| 20 | Funding contabilizado | `test_research_costs.py` | Trade de 10 h con tasa horaria conocida: el funding del resultado neto coincide con el calculado a mano, **y el signo se invierte entre long y short** |
| 21 | Fees contabilizados | `test_research_costs.py` | Fees = `2 × fee_taker × notional`, comprobado a mano |
| 22 | Slippage contabilizado | `test_research_costs.py` | El fill difiere del precio de señal en la dirección adversa, nunca a favor |
| 23 | No lookahead | `test_no_lookahead.py` | **Test de propiedad**: para cada `t`, `scan(velas[:t])` debe dar el mismo resultado que `scan(velas[:t+k])` truncado a `t`. Ninguna decisión en `t` puede cambiar al añadir barras futuras |
| 24 | Misma señal en backtest / paper / live | `test_no_lookahead.py` | Dada la misma secuencia de velas, los tres caminos invocan el mismo `scan()` y producen `Plan` idénticos campo a campo |
| 25 | Alerta de TradingView no ejecuta sin revalidación | `test_execution_guards.py` | `POST /api/tradingview/signal` con payload válido ⇒ **nunca** produce una orden; solo un registro pendiente de revalidación contra datos HL |
| 26 | SL ausente genera estado crítico | `test_execution_guards.py` | Fill confirmado + colocación de SL que falla o no aparece al verificar ⇒ estado `CRITICAL` y bloqueo de nuevas operaciones |

---

## Parte 3 — Tests adicionales que la auditoría hace necesarios

| # | Test | Motivo |
|---|---|---|
| 27 | **Paridad con el módulo congelado** | Con `require_momentum=False`, `rr=1.0` y short deshabilitado, el motor nuevo debe producir planes **idénticos** a `hype_long/rules.py` sobre las mismas velas. Es la única prueba de que la migración no rompió lo que ya funcionaba (`MIGRATION_PLAN` §8.2) |
| 28 | **Equivalencia del cache de gaps con dos direcciones** | Portado del v0.1, ahora con gaps alcistas y bajistas simultáneos en la misma sesión |
| 29 | **El límite diario no altera la generación de señales** | Las señales se generan y registran igual; el límite solo afecta a la ejecución. Necesario para que el control "coste del límite diario" (`RESEARCH_PLAN` §4) sea medible |
| 30 | **`LIVE_EXECUTION=false` es efectivo** | Con la variable en `false`, cualquier ruta que intente enviar una orden real lanza excepción. Se prueba con un mock del adaptador que registra si fue invocado |
| 31 | **No se emiten probabilidades** | El `scoring` nunca devuelve un campo interpretable como probabilidad, y la API no expone ninguno. Protege la regla del PRD §14 frente a futuras "mejoras" |
| 32 | **El stop no depende del objetivo** | Calcular el plan con `rr` = 1.0, 1.6 y 2.0 sobre las mismas velas: `stop` idéntico en los tres. Blinda la regla "primero estructura, después R:R" del PRD §8 |

---

## Parte 4 — Ejecución

```bash
cd backend
python -m unittest discover -s tests -v
```

**Gates de CI:**

- Los 16 tests del módulo congelado deben pasar **sin modificación** del módulo.
- El test #27 (paridad) es bloqueante: si falla, la migración se detiene.
- El test #30 (`LIVE_EXECUTION`) es bloqueante en cualquier rama que toque `execution/`.

**Nota de entorno:** este equipo no tiene Python instalado (verificado 2026-09-09). Los tests están escritos pero **no ejecutados**. Ver `IMPLEMENTATION_ORDER.md` §0.
