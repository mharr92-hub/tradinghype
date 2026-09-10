# AUDITORÍA 001 — Conflictos entre el modelo vigente y el nuevo modelo de producto

**Fecha:** 2026-09-09 · **Disparador:** decisión de producto de Mark (nuevo objetivo: mejor setup LONG o SHORT del día, ~$125 de riesgo, ~1.6R, máx. 1 trade/día, máx. 24 h de tenencia).

Este documento **solo audita**. Las decisiones resultantes viven en `PRD_HYPE_COPILOT.md` v2.0 y `HYPE_TRADING_RESEARCH_PLAN.md`.

Convención: **REVOCADO** = la política deja de aplicar · **MODIFICA** = sobrevive con cambios · **HUECO** = no existe y hace falta.

---

## C-01 · HYPE LONG ONLY — **REVOCADO**

| Dónde | Qué dice |
|---|---|
| `docs/HYPE_LONG_RESEARCH_PLAN.md:7` | "Política inmutable (MULTI_STRATEGY_MASTER_PLAN §0): **HYPE es LONG ONLY. Nunca short.**" |
| `backend/app/strategies/hype_long/rules.py:11-13` | docstring: "Este módulo no contiene ningún camino de código que produzca otra dirección." |
| `.../rules.py:29` | `SIDE_LONG = "LONG"  # única dirección que existe en este módulo` |
| `.../rules.py:122` | `_assert_long_only()` lanza `PolicyViolation` |
| `backend/tests/…:187-190` | `test_module_source_has_no_opposite_direction` afirma que la cadena `"SHORT"` **no aparece en el código fuente** |

La cláusula de escape del propio documento (`"Reabrir eso sería un cambio de política explícito de Mark"`) es exactamente lo que se ejerce ahora.

**Impacto en código:** el test de la línea 187 es un candado a nivel de *texto del archivo*. Cualquier motor bidireccional debe vivir en un módulo distinto, o el test falla. Decisión: `hype_long/rules.py` **se congela intacto** como referencia de reversión y su test sigue verde; el motor nuevo vive en `strategies/hype/`.

**Riesgo abierto:** `MULTI_STRATEGY_MASTER_PLAN.md`, la fuente citada de la política, **no está en el repo** (ver C-13). La revocación solo queda registrada aquí.

---

## C-02 · R:R 1:1 obligatorio — **MODIFICA**

| Dónde | Qué dice |
|---|---|
| `HYPE_LONG_RESEARCH_PLAN.md §3` | "Objetivo · E1: +1R (base)" |
| `HYPE_LONG_RESEARCH_PLAN.md §4` | "**E1 = 1:1 (base)** · E2 = 2R · E3 = trailing" |
| `hype_long/rules.py:71` | `rr: float = 1.0` |
| `tests/…:128` | `assertAlmostEqual(p.tp - p.entry, p.entry - p.stop)` — afirma 1:1 **exacto** |

El nuevo modelo ($125 riesgo → ~$200 objetivo) implica **≈1.6R**. E1 baja a brazo de control; **E2 = 1.6R** pasa a candidato principal, E3 = 2R.

**Nota a favor del cambio:** el propio research plan ya avisaba de que 1:1 es frágil — con taker 4.5 bps el break-even está en 54.5–55.5 % de acierto. Subir el payoff es coherente con esa observación, no la contradice.

**Impacto en tests:** `tests/…:128` pasa de afirmar 1:1 a afirmar `tp - entry == rr · (entry - stop)` con `rr` leído de la config.

---

## C-03 · Ausencia total de la rama SHORT — **HUECO**

No existe detección de FVG bajista (`find_session_gaps` solo evalúa `c5[i].l > c5[i-2].h`), ni régimen bajista (`regime_4h_ok` devuelve un booleano long), ni RSI 4H ≤ 45, ni MACD 1H < 0, ni volumen 1.20×, ni cost gate 0.12R.

Además, el research plan §1 declara explícitamente eliminadas esas mismas reglas: *"Toda la rama corta de la propuesta externa (reglas 4H RSI≤45, MACD<0, riesgo 0.125 %, volumen 1.2×, umbrales de costo para cortos) queda **eliminada, no endurecida**."* Se reinstauran, endurecidas, por C-01.

---

## C-04 · Funding no contabilizado — **REVOCADO (ya no es aceptable)**

| Dónde | Qué dice |
|---|---|
| `tools/backtest_hype_long_v0.py:8` | "**SIN funding** (el motor completo lo acumula por hora de tenencia)" |
| `hype_long/rules.py:408` | comentario: cost gate "SIN funding" |

Era una limitación tolerable cuando la tenencia típica eran minutos. Con `max_hold_hours = 24` un trade puede cruzar **hasta 3 ventanas de funding** de Hyperliquid (cada hora en HL, no cada 8 h como en otros venues — a confirmar contra la API). En un largo de HYPE con funding positivo eso es viento en contra directo sobre el neto.

**Veredicto:** cualquier número de backtest producido hasta hoy sobre tenencias largas es **no concluyente**, no "aproximado". El backtester nuevo debe modelar funding por hora de tenencia real o no se reportan resultados.

---

## C-05 · Fill al cierre de la vela de señal — **MODIFICA**

| Dónde | Qué dice |
|---|---|
| `hype_long/rules.py:400` | `entry = c5[t].c` |
| `backtest_hype_long_v0.py:10` | "Entrada al cierre de la vela de confirmación" |

El propio research plan §3 ya exigía lo contrario en ejecución (`drift máx. señal→fill 0.10R`, `recalcular R con el fill real`), pero el backtest no lo modelaba. **El backtest era más optimista que la especificación de ejecución.**

**Impacto:** `entry = close` es la fuente de sesgo optimista más probable de todo el motor v0, porque la vela de confirmación es por definición una vela de impulso a favor: se compra justo en su cierre, el punto más caro del tramo. Hace falta un modelo de fill explícito (`research/fills.py`) con delay, spread, slippage y **trade perdido** cuando el drift supera 0.10R.

---

## C-06 · Sin límite de trades por día — **HUECO**

`backtest_hype_long_v0.py:190,199,240` implementa "una posición a la vez" (`in_pos_until`), que **no es** lo mismo que "un trade al día": con setups de 5m se pueden encadenar varios trades en una sesión.

Nuevo modelo: **máx. 1 nueva posición HYPE por día**, con máquina de estados `WAITING → LONG_CANDIDATE/SHORT_CANDIDATE → A_PLUS_READY → TRADE_ACTIVE → TRADE_DONE | NO_TRADE`.

**Consecuencia metodológica no obvia:** el límite diario descarta señales válidas, así que el `N` de *señales* y el `N` de *trades* divergen. Los kill criteria del research plan (`N ≥ 100 IS`, `N ≥ 60 total`) estaban escritos sobre señales. Deben medirse **ambos** por separado, o el límite diario matará brazos por falta de muestra que en realidad sí tenían frecuencia.

---

## C-07 · Sin límite de tenencia — **HUECO**

`backtest_hype_long_v0.py:153` — `resolve_forward(..., max_bars: Optional[int] = None)` camina hasta el final de los datos y devuelve `open_at_end`. No hay time stop.

Nuevo modelo: `max_hold_hours = 24` = **288 velas de 5m**. Tercera vía de salida además de SL y TP.

---

## C-08 · Modelo de riesgo incompatible — **MODIFICA**

| Fuente | Riesgo |
|---|---|
| `HYPE_LONG_RESEARCH_PLAN §3` y `rules.py:80` | `risk_pct = 0.0025` (0.25 % del equity) |
| Nuevo modelo, TINY | **$1.00** fijo por trade |
| Nuevo modelo, PRODUCTION | **$100–$150**, default **$125** |

Tres regímenes distintos, no uno parametrizable. Requiere `risk_mode ∈ {pct_equity, fixed_usd}` y un tope duro por modo. El sizing nunca sube el riesgo para alcanzar el notional mínimo del venue: si no cabe, **SKIP** (regla que ya estaba y se conserva).

---

## C-09 · Cost gate único — **MODIFICA**

`hype_long/rules.py:77` — `max_cost_r: float = 0.15`, un solo valor. Nuevo modelo: **0.15R long / 0.12R short**, y ambos declarados explícitamente como *parámetros de investigación, no dogma*.

Se conserva intacta la regla dura: **nunca ensanchar el stop para pasar el filtro**.

---

## C-10 · `target_clearance` no existe — **HUECO**

Concepto nuevo: antes de ofrecer el trade hay que verificar que no haya una resistencia (long) o soporte (short) estructural **antes** del target mínimo. Sin esto, subir de 1R a 1.6R simplemente aumenta la tasa de trades que mueren a mitad de camino.

Exige una definición **determinista** de nivel — swing high/low 1H confirmado, previous day high/low — o no entra. Nada subjetivo.

---

## C-11 · Estatus del momentum (RSI/MACD/volumen) — **CONFLICTO INTERNO RESUELTO**

- `HYPE_LONG_RESEARCH_PLAN §1` lo **degrada** a variante F2: *"Ambos derivan del precio (la propia guía lo admite); deben ganarse su lugar contra la base."*
- El nuevo prompt §5 lo confirma para LONG: *"No asumir que MACD + RSI mejoran resultados hasta demostrarlo."*
- Pero el nuevo prompt §6 lo hace **obligatorio** dentro de la confirmación SHORT.

**Resolución adoptada:** asimetría deliberada, no descuido. En LONG el momentum es el brazo **F2**, comparable contra F1. En SHORT es **parte de la definición del setup** y no se puede apagar — es uno de los mecanismos por los que el SHORT es más difícil de disparar (C-01). Queda documentado para que nadie lo "arregle" después por simetría.

---

## C-12 · Presupuesto de parámetros libres — **RIESGO NUEVO**

`HYPE_LONG_RESEARCH_PLAN §3` congelaba **4** parámetros optimizables (`k=0.30`, edad `12`, buffer `0.10·ATR`, tope de costo `0.15R`) citando `BTC_LONG_RESEARCH_PLAN §9`: *"prohíbe optimizar más de 4"*.

El nuevo modelo introduce como mínimo: `rr` objetivo, lookback de `target_clearance`, `max_hold_hours`, límite diario, cost gate short. Si todos entran a la rejilla de sensibilidad, el presupuesto se dispara de 4 a 9 y la muestra (decenas-cientos de trades) no lo sostiene.

**Resolución adoptada:** separar **parámetros optimizables** (máx. 4, entran a sensibilidad) de **decisiones de producto fijadas por Mark** (`max_hold_hours=24`, `1 trade/día`, `risk=$125`), que se declaran, se documentan y **no se tocan** en la búsqueda. `rr` no se optimiza: se prueba como tres brazos discretos predeclarados (1R / 1.6R / 2R).

---

## C-13 · Documentos fuente ausentes — **BLOQUEANTE MENOR**

`HYPE_LONG_RESEARCH_PLAN.md` cita como autoridad cuatro documentos que **no están en este repo**: `MULTI_STRATEGY_MASTER_PLAN.md`, `BTC_LONG_RESEARCH_PLAN.md`, `ALT_SHORT_X_SIGNAL_RESEARCH_PLAN.md`, `TRADINGVIEW_INTEGRATION_PLAN.md`.

Consecuencias reales:
1. La política LONG ONLY se revoca sin poder editar su documento de origen.
2. La regla "máximo 4 parámetros optimizables" (C-12) se cita de un documento que no podemos leer.
3. La ventana de 90 s del webhook se atribuye a `TRADINGVIEW_INTEGRATION_PLAN §5.2`, no verificable.

**Acción:** si esos documentos existen en `github.com/mharr92-hub/tradinghype` u otro repo, hay que traerlos antes de dar por cerrada la migración. Mientras tanto, `PRD_HYPE_COPILOT.md` v2.0 es la **única** autoridad vigente y así se declara en su cabecera.

---

## C-14 · "Mejor trade del día" vs. límite de 1 trade/día — **TENSIÓN ACEPTADA**

El propio prompt (§14) ya lo reconoce: el sistema no puede saber a las 9 AM cuál será el mejor setup de las siguientes 15 horas. Con **máx. 1 trade/día**, el sistema toma el **primer** A+, no el mejor.

**Acción medible, no retórica:** el journal debe registrar **todos** los candidatos A+ del día, incluidos los que el límite diario bloqueó, con su resultado hipotético. A los N meses eso responde con datos si "primero" fue peor que "mejor", y si el límite diario cuesta o ahorra dinero. Sin ese registro la pregunta es incontestable para siempre.

---

## C-15 · Scanner externo sin auditar — **PENDIENTE, HEREDADO**

`HYPE_LONG_RESEARCH_PLAN §7 fase 0` y §9 dejan abierto: *"`[PENDIENTE AUDITORÍA]`: integración con los módulos existentes de `hype-vol-bot` cuando Mark comparta el ZIP"*. Sigue pendiente y sigue siendo un gate antes de que nada toque una API con claves.

---

## Resumen

| ID | Tema | Veredicto |
|---|---|---|
| C-01 | HYPE LONG ONLY | REVOCADO |
| C-02 | 1:1 obligatorio | MODIFICA → E2 = 1.6R principal |
| C-03 | Rama SHORT | HUECO → construir, endurecida |
| C-04 | Funding | REVOCADO → obligatorio con 24 h |
| C-05 | Fill al close | MODIFICA → modelo de fill realista |
| C-06 | Trades por día | HUECO → máx. 1, con máquina de estados |
| C-07 | Límite de tenencia | HUECO → 24 h / time stop |
| C-08 | Riesgo | MODIFICA → 3 regímenes |
| C-09 | Cost gate | MODIFICA → 0.15 long / 0.12 short |
| C-10 | target_clearance | HUECO → determinista o no entra |
| C-11 | Momentum | Asimetría deliberada: F2 en long, obligatorio en short |
| C-12 | Presupuesto de parámetros | RIESGO → separar optimizables de decisiones de producto |
| C-13 | Documentos ausentes | BLOQUEANTE MENOR → traer o declarar el PRD como única autoridad |
| C-14 | Primero vs. mejor | TENSIÓN → registrar los A+ bloqueados |
| C-15 | Scanner externo | PENDIENTE heredado |
