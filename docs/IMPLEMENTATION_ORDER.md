# ORDEN DE IMPLEMENTACIÓN — HYPE Copilot v2

**Fecha:** 2026-09-09 · Deriva de `PRD_HYPE_COPILOT.md` v2.0 y `MIGRATION_PLAN.md`.

**Regla que atraviesa todo el plan:** `LIVE_EXECUTION=false`. Nada puede enviar una orden real hasta aprobación explícita posterior de Mark.

---

## §0 · Bloqueante de entorno (antes de P0)

**Este equipo no tiene Python instalado** (verificado 2026-09-09: ni `python`, ni `python3`, ni `py`, ni Anaconda). Node v24.13.1 sí está.

Sin Python no se puede ejecutar ni un solo test del backend, que es donde vive la fuente de verdad. Instalación sugerida:

```powershell
winget install Python.Python.3.12
# reabrir la terminal, luego:
python --version
```

Hasta entonces, el código Python de este repo está **escrito pero no ejecutado**, y así se reporta.

---

## P0 — Fundamentos (nada tiene valor sin esto)

### P0.1 · Strategy engine LONG + SHORT

`strategies/hype/{common,long,short,target_clearance,scoring,engine}.py`

- Un único `scan()` público, compartido por backtest / paper / shadow / tiny / live.
- **Gate de avance:** test #27 (paridad numérica con el módulo congelado `hype_long/rules.py`). Si no hay paridad, no se avanza — es la única prueba de que la migración no rompió lo que ya funcionaba.
- Después: tests #1–#12, #32.

### P0.2 · Backtester realista

`research/{fills,funding,metrics,backtest,controls}.py`

Debe modelar, y sin esto **no se reporta ningún número**:

- funding por hora de tenencia real, con signo (largo paga / corto cobra cuando la tasa es positiva);
- fill posterior al cierre de la vela de confirmación, con delay, spread y slippage;
- **missed trade** cuando el drift supera 0.10R;
- time stop a 24 h;
- límite de 1 trade/día, registrando aparte los A+ bloqueados.

**Gate:** tests #19–#23, #29. Y el estrés de fill del `RESEARCH_PLAN` §4 debe correr, aunque su resultado aún no condicione nada.

> **Por qué P0 y no P1:** el motor v0 tiene sesgo optimista conocido y documentado (`AUDIT` C-05: entrada al cierre de una vela de impulso a favor). Construir producto sobre sus números es construir sobre una medición sesgada.

### P0.3 · Visualización en TradingView

`tradingview/hype_copilot_tv_v1.pine`

EMAs 4H/1H, VWAP + banda ATR, FVG de ambos lados, primer retest, RSI/MACD/volumen, `target clearance`, Entry, SL y las líneas **1R / 1.6R / 2R**, `LONG READY` / `SHORT READY`.

Su función real no es decorativa: es el **debugger visual** que permite ver por qué el motor rechazó algo. Paraleliza sin bloquear a P0.1.

### P0.4 · Flujo de señal en PAPER + forward logger

`api/signals.py`, `db/models.py`, servicio de escaneo.

- **Puede arrancar hoy en modo log-only**, incluso antes de que el backtest termine (`RESEARCH_PLAN` §6 fase 1). El reloj de las 4 semanas de PAPER empieza cuando arranca, no cuando el research acaba.
- Cada señal se escribe **antes** de conocer el resultado, incluidas las saltadas y las bloqueadas por el límite diario.

---

## P1 — Producto utilizable

### P1.1 · App con ENTER / SKIP

`frontend/` — home que responde *"¿cuál es la mejor oportunidad de HYPE ahora?"*, Signal Card con los 12 campos del PRD §16, cuenta atrás de 90 s, botones ENTER / SKIP / VIEW CHART.

**Gate:** test #31 — la UI no muestra ni puede mostrar un porcentaje de probabilidad.

### P1.2 · Guardas de ejecución

`execution/order_guard.py` — las 9 revalidaciones del PRD §17, verificación de SL/TP contra el exchange, estado `CRITICAL`.

**Se implementa y se testea antes que el adaptador real.** Las guardas primero, la capacidad de enviar órdenes después. **Gate:** tests #13, #14, #17, #25, #26, #30.

### P1.3 · Adaptador Hyperliquid TINY

`execution/hyperliquid.py` con `LIVE_EXECUTION=false`.

Lectura (velas, funding, metadatos de tick/size, posiciones) implementada y usable. La ruta de escritura existe pero **lanza excepción** mientras la variable esté en `false`.

**Gate para ponerla en `true`:** aprobación explícita de Mark **más** los gates de fase 6 del `RESEARCH_PLAN` (SHADOW superado). No es una decisión de ingeniería.

### P1.4 · Verificación de TP/SL

Ciclo de vida post-fill: confirmar fill real → recalcular R → **mantener el stop estructural** → recalcular TP → colocar SL → colocar TP → verificar ambas contra el exchange → guardar order IDs → monitorizar → time stop 24 h.

---

## P2 — Analytics

`research/metrics.py` conectado al journal: las 17 métricas del PRD §19 con los cortes LONG / SHORT / COMBINED / MARK ACCEPTED / MARK SKIPPED.

Incluye las dos preguntas que solo el journal puede responder:

1. ¿La selección discrecional de Mark mejora o empeora la estrategia? (ACCEPTED vs SKIPPED)
2. ¿El límite de 1 trade/día cuesta o ahorra dinero? (`AUDIT` C-14)

---

## P3 — Meta-modelo AI

Solo cuando exista muestra suficiente. Estima `P(TP antes que SL | setup)`, entrenado, validado OOS y **calibrado**.

Podrá **rankear**. Nunca podrá aumentar riesgo, ensanchar el SL, saltarse kill switches, promover de modo ni inventar precios.

---

## Qué NO se construye ahora

Optimizador automático · ML de entradas · bot autónomo live · múltiples monedas · social trading · marketplace · dashboard complejo · móvil nativo.

---

## Resumen de dependencias

```
P0.1 engine ──┬──> P0.2 backtester ──> (números fiables) ──> P2 analytics
              │
              ├──> P0.4 paper + forward logger ──> P1.1 app ENTER/SKIP
              │                                          │
              └──> P0.3 TradingView (paralelo)           │
                                                          v
                                       P1.2 guardas ──> P1.3 adaptador (LIVE=false)
                                                          │
                                                          └──> P1.4 verificación TP/SL

P3 AI: bloqueado hasta que P2 acumule muestra.
```

Las únicas dos secuencias verdaderamente rígidas: **paridad antes de construir sobre el motor nuevo**, y **guardas antes que capacidad de enviar órdenes**.
