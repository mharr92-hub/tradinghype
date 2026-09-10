# PRD — HYPE COPILOT

**Versión:** 2.0 (2026-09-09) · **Reemplaza:** PRD v1.0 en su totalidad
**Producto:** HYPE Copilot · **Mercado:** HYPE Perpetual (Hyperliquid) · **Único activo**

**Autoridad.** Mientras `MULTI_STRATEGY_MASTER_PLAN.md` y `TRADINGVIEW_INTEGRATION_PLAN.md` no estén en este repo (ver `AUDIT_001_CONFLICTOS.md` C-13), **este documento es la única especificación vigente del producto**. Documentos hermanos: `AUDIT_001_CONFLICTOS.md` (qué cambió y por qué), `HYPE_TRADING_RESEARCH_PLAN.md` (metodología), `MIGRATION_PLAN.md`, `TEST_PLAN.md`, `IMPLEMENTATION_ORDER.md`.

**Estado de ejecución:** `LIVE_EXECUTION=false`. Nada en este repo puede enviar una orden real hasta una aprobación explícita posterior de Mark.

---

## 0. Principio final (va primero, porque manda sobre todo lo demás)

El producto no existe para "hacer $200 todos los días". Existe para **encontrar solamente trades de HYPE que justifiquen arriesgar $100–$150 con posibilidad estructural razonable de capturar ~$200, y evitar operar cuando no hay edge**.

> **CALIDAD > CANTIDAD** · **NO TRADE > BAD TRADE**

`NO TRADE` es un resultado correcto, no un fallo del sistema. Ningún componente puede relajar una regla para producir una operación.

---

## 1. Objetivo del producto

HYPE Copilot deja de ser un scanner de HYPE LONG. El objetivo es:

> Encontrar el mejor setup **LONG o SHORT** de HYPE del día, con fuerte sesgo hacia LONG, presentar **Entry + SL + TP + Size + Risk + razones objetivas**, y permitir que Mark decida **ENTER** o **SKIP**.

No es un bot autónomo. Es un **trade decision system**:

```
Market Data -> Strategy Engine -> Scoring -> Risk Engine -> Signal Card -> ENTER/SKIP -> Execution -> SL/TP/TimeStop -> Journal -> Analytics
```

La computadora encuentra la oportunidad y calcula los números. **Mark toma la decisión.**

### Objetivo económico de producción (deseado, no obligatorio)

| Concepto | Valor |
|---|---|
| Riesgo por trade | $100–$150 · **default $125** |
| Beneficio buscado | ~$200 |
| Payoff implícito | **≈1.6R** |
| Trades por día | **máximo 1** (V1) |
| Duración máxima | **24 horas** |

**$200 diarios es un promedio deseado, no una obligación del algoritmo.** Nunca tomar un trade malo para cumplir el objetivo diario.

---

## 2. Política de dirección

### 2.1 `HYPE = LONG ONLY` queda REVOCADA

La política anterior (`HYPE_LONG_RESEARCH_PLAN` §0, citando `MULTI_STRATEGY_MASTER_PLAN` §0) queda **revocada por decisión explícita de Mark**, ejerciendo su propia cláusula de escape. Trazabilidad completa en `AUDIT_001_CONFLICTOS.md` C-01.

HYPE ahora permite **LONG** y **SHORT**.

### 2.2 Qué significa "70/30"

**Nunca una cuota matemática.** Significa:

- LONG tiene reglas A+ normales.
- SHORT tiene reglas **más estrictas** (§6).
- Estadísticamente esperamos muchos más longs.
- **El sistema nunca inventa un short para mantener una proporción.**

Distribuciones válidas: 5L/0S · 3L/1S · 0 trades · cualquier cosa que produzca el mercado. Si un componente cuenta longs y shorts para "equilibrar", es un bug.

---

## 3. Modelo de trade

Ya no se buscan muchos trades 1:1. Se busca **un trade A+ o ninguno**.

| Brazo | Objetivo | Rol |
|---|---|---|
| E1 | 1.0R | control / investigación (era la base en v1) |
| **E2** | **1.6R** | **candidato principal de producción** |
| E3 | 2.0R | brazo a probar |
| E4 | trailing estructural | experimental, solo si hay implementación clara |

**El TP nunca se fija en $200 artificialmente.** Orden de cálculo, sin excepciones:

1. Calcular el **stop estructural** (§8).
2. Derivar `R = |entry − stop|`.
3. Verificar **target clearance** (§7): ¿hay espacio limpio hasta el objetivo mínimo?
4. Aceptar el trade **solo si** el mercado permite al menos el R:R mínimo exigido por el brazo activo.

Si una resistencia (long) o soporte (short) relevante bloquea el target: **REJECT TRADE**. No se reduce la calidad para alcanzar una meta monetaria.

---

## 4. Timeframes y reproducibilidad

| TF | Rol |
|---|---|
| 4H | régimen principal |
| 1H | alineación |
| 5m | ejecución |

Reglas duras:

- Todas las señales usan **exclusivamente velas completadas**.
- **Nada de lookahead.** Ninguna decisión puede usar información posterior al cierre de la vela que la produce.
- El **mismo Strategy Engine** corre en BACKTEST, PAPER, SHADOW, TINY y LIVE. No existen implementaciones paralelas de la estrategia.
- Python es la **fuente de verdad**. TradingView es visualización + alerta (§15).

---

## 5. LONG setup v1 — *trend-aligned first VWAP/FVG retest*

### 5.1 Régimen 4H

`close > EMA20 > EMA50` y `EMA50 > EMA50[3]`. Si falla: **LONG DISABLED**.

### 5.2 Alineación 1H

`close > EMA20 > EMA50`.

Puede incorporarse fuerza de tendencia más adelante, pero **no se añaden parámetros sin test** (§18, §12 del audit).

### 5.3 Setup 5m

FVG alcista: `low[vela 3] > high[vela 1]`. Debe:

- pertenecer a la **sesión actual** (reset VWAP 00:00 UTC = 19:00 Panamá);
- tener como máximo **12 velas** de edad al primer toque;
- ser el **PRIMER retest** — el gap se consume al resolverse y no revive;
- **solapar `VWAP ± 0.30·ATR20(5m)`** en el momento del primer toque.

### 5.4 Confirmación

En la vela del toque o las 2 siguientes: `close > VWAP` · `close > límite superior del FVG` · `close > high[1]` · `close > open`.

### 5.5 Momentum — **candidato, no dogma**

`RSI(14) ∈ [50,68]` y subiendo · MACD hist ascendiendo 2 barras · VWAP ascendente · volumen ≥ media 20.

> **En LONG el momentum es el brazo F2 y debe poder probarse por separado.** No se asume que RSI + MACD mejoren resultados hasta demostrarlo contra F1. En SHORT sí es obligatorio (§6) — la asimetría es deliberada y está documentada en `AUDIT_001_CONFLICTOS.md` C-11.

---

## 6. Rama SHORT — separada y más estricta

Rama **claramente identificada**, no una inversión mecánica del long.

### 6.1 Régimen 4H SHORT — TODOS requeridos

`close < EMA20 < EMA50` · `EMA50 < EMA50[3]` · `RSI(14) ≤ 45`

### 6.2 Alineación 1H SHORT — TODOS requeridos

`close < EMA20 < EMA50` · MACD hist `< 0` · MACD hist **descendiendo**

### 6.3 Setup 5m SHORT

FVG bajista: `high[vela 3] < low[vela 1]`. Primer retest únicamente. Zona solapando `VWAP ± 0.30·ATR20`.

### 6.4 Confirmación SHORT — TODOS obligatorios

`close < VWAP` · `close < límite inferior del FVG` · `close < low[1]` · `close < open` · `RSI ∈ [32,48]` y descendiendo · MACD hist descendiendo 2 barras · **VWAP descendente** · volumen **≥ 1.20 × media 20**

**Una sola condición fallida ⇒ NO TRADE.** No existe el short "casi bueno".

### 6.5 Gate de contexto BTC

El SHORT debe **poder** usar un gate adicional: p. ej. *BTC no fuertemente bullish*. Es el brazo **G1** de §18. **Se prueba; no se asume como edge sin evidencia.** Con `G0` (sin gate) como base.

---

## 7. Target clearance

Concepto nuevo y obligatorio antes de ofrecer un trade.

- **LONG:** verificar que no exista una **resistencia estructural relevante** antes del target mínimo.
- **SHORT:** verificar que no exista un **soporte relevante** antes del target mínimo.

El trade debe disponer de espacio razonablemente limpio para alcanzar al menos **1.5R o 1.6R**, según el brazo probado. Si el obstáculo aparece antes: **REJECT**.

### 7.1 Definición determinista de nivel

Solo cuentan niveles calculables sin criterio humano:

| Nivel | Definición exacta |
|---|---|
| Swing high/low 1H confirmado | pivote de `n=2` barras a cada lado, sobre velas 1H **cerradas** (confirmado ⇒ requiere 2 barras posteriores, sin lookahead) |
| Previous day high/low | extremos de la sesión anterior (mismo reset 00:00 UTC que el VWAP) |
| *(otros)* | **solo** si se definen de forma igualmente determinista y se declaran aquí |

**Prohibido el análisis subjetivo.** Nada de "zona de oferta", "nivel psicológico" ni líneas dibujadas a mano.

### 7.2 Regla

`clearance_R = distancia(entry → primer nivel bloqueante) / R`

Se acepta si `clearance_R ≥ rr_objetivo` del brazo activo. El lookback de niveles es un parámetro declarado (§18) y entra a sensibilidad.

---

## 8. Stop

El stop es **estructural** y se calcula **antes** que el objetivo.

- **LONG:** `min(lado inferior del FVG, low del tramo retest→confirmación) − buffer·ATR`
- **SHORT:** `max(lado superior del FVG, high del tramo retest→confirmación) + buffer·ATR`

**Nunca:**

- ampliar el stop para pasar el filtro de costos;
- mover el stop para conseguir el R:R deseado;
- promediar contra una posición perdedora.

**Primero estructura. Después R:R.**

---

## 9. Modelo de riesgo

Tres regímenes distintos, no un parámetro continuo.

### 9.1 RESEARCH / PAPER / SHADOW

Equity configurable, riesgo en **porcentaje** configurable (histórico: 0.25 %).

### 9.2 TINY — primeras pruebas reales

**Máximo $1.00 de pérdida planificada por trade.**

$1 es el **risk budget**, **no** una posición nocional de $1.

```
quantity = risk_budget / (price_risk_por_unidad + costos_esperados_del_trade_perdedor)
```

Debe respetar el **minimum notional** del exchange, tick size, size precision, colateral disponible y notional máximo. Si no se puede construir una posición válida sin superar $1 de riesgo: **SKIP**. Jamás se sube el riesgo para poder operar.

### 9.3 PRODUCTION

Risk budget **$100–$150**, default **$125**. Target económico ~$200, **derivado de un trade de ≈1.6R**, nunca de un TP monetario artificial.

---

## 10. Máximo 1 trade al día

V1 permite **máximo 1 nueva posición HYPE por día**. Configurable en el futuro; en V1 el valor es 1.

**Esto no es una obligación de operar cada día.**

### Máquina de estados del día

```
WAITING → LONG_CANDIDATE | SHORT_CANDIDATE → A_PLUS_READY → TRADE_ACTIVE → TRADE_DONE
                                    ↘ NO_TRADE (fin del día sin A+)
```

| Estado | Significado |
|---|---|
| `WAITING` | sin setup en curso |
| `LONG_CANDIDATE` / `SHORT_CANDIDATE` | gap identificado, esperando retest o confirmación |
| `A_PLUS_READY` | todos los gates pasados; Signal Card viva |
| `TRADE_ACTIVE` | posición abierta (`DAILY_TRADE_ACTIVE`) |
| `TRADE_DONE` | cerrada; no se abre otra hoy (`DAILY_TRADE_DONE`) |
| `NO_TRADE` | el día terminó sin A+ |

**Obligación de registro (`AUDIT` C-14):** los candidatos A+ que el límite diario bloquee **se registran igual** en el journal, con su resultado hipotético. Sin ese registro nunca se podrá saber si "el primer A+" fue peor que "el mejor A+ del día".

---

## 11. Máximo 24 horas de tenencia

`max_hold_hours = 24` (= 288 velas de 5m). Tres mecanismos de salida:

1. **STOP LOSS**
2. **TAKE PROFIT**
3. **TIME STOP** — al llegar a 24 h se cierra según la política de ejecución del sistema

### Variante futura: *thesis invalidation exit*

Candidatas a probar: pérdida clara de estructura 1H · pérdida/reclaim inverso de VWAP · cambio HTF significativo.

**No se activa como regla principal hasta backtestearla.**

---

## 12. Costos

Con operaciones de hasta 24 h, el modelo de costos v0 ya no sirve (`AUDIT` C-04). El motor completo debe modelar:

- fees taker/maker **reales** del tier de la cuenta;
- spread;
- slippage;
- **funding según tiempo real de tenencia** (Hyperliquid liquida funding por hora; confirmar contra la API antes de fijar el modelo);
- entrada realista posterior a la confirmación (§13).

### Filtro Cost_R

| Dirección | Límite inicial |
|---|---|
| LONG | `Cost_R ≤ 0.15R` |
| SHORT | `Cost_R ≤ 0.12R` |

**Estos valores son parámetros de investigación, no dogma.** Lo que sí es dogma: nunca ensanchar el stop para pasar el filtro.

---

## 13. Entrada realista

El backtest v0 entraba al cierre de la vela de confirmación. Eso es **sesgo optimista sistemático**: la vela de confirmación es por definición una vela de impulso a favor, y comprar en su cierre es comprar en el punto más caro del tramo.

**Backtest serio:**

- señal generada al **cierre de la vela de 5m**;
- entrada en la **primera observación de mercado disponible después** del cierre;
- modelar delay, spread, slippage, drift y **trade perdido** (missed trade).

**Live:**

- drift máximo **0.10R**;
- expiración de la señal **90 segundos**;
- si el precio se aleja más de 0.10R: **SKIP**. **No se persigue la entrada.**

---

## 14. "Best trade of the day" — definición honesta

El sistema **no puede saber** a las 9 AM cuál será literalmente el mejor trade de las siguientes 15 horas. No se vende el concepto como si pudiera.

> **Best Trade Candidate = el primer setup que alcance el nivel A+ definido por reglas objetivas.**

Si ninguno lo alcanza: **NO TRADE**.

### Scoring

Se mantienen simultáneamente un **Long Candidate Score** y un **Short Candidate Score**. El score representa **cumplimiento y calidad de reglas**, no una probabilidad de éxito.

- ✅ `RULE COMPLIANCE 8/9 · clearance 1.9R · vol 1.4×`
- ❌ `87% probability`

**Prohibido mostrar cualquier porcentaje de probabilidad** hasta que exista un modelo probabilístico entrenado, validado OOS y **calibrado** (§22).

### Definición de A+ (todos obligatorios)

Régimen HTF · alineación MTF · FVG en sesión y dentro de edad · primer retest · solape con banda VWAP · vela de confirmación · momentum (obligatorio en short; brazo F2 en long) · **target clearance** · cost gate · sizing válido en el venue · límite diario disponible · kill switches limpios.

---

## 15. TradingView — `HYPE COPILOT TV v1`

Debe visualizar: EMA20/50 4H · EMA20/50 1H · VWAP · banda ATR del VWAP · FVG alcista · FVG bajista · primer retest · RSI · MACD · condición de volumen · **target clearance** · Entry · SL · **1R** · **1.6R** · **2R** · `LONG READY` · `SHORT READY`.

Cada señal queda dibujada históricamente para inspección visual.

**TradingView NO es fuente de ejecución.** Es visualización + alerta. Cuando llega un webhook, el backend **revalida con datos nativos de Hyperliquid** antes de considerar nada. Ver §17.

---

## 16. App

La home responde **una sola pregunta**: *"¿Cuál es la mejor oportunidad de HYPE ahora?"*

```
HYPE COPILOT

Current HYPE Price          4H Regime        1H Alignment      VWAP State
LONG SETUP STATE            SHORT SETUP STATE
Today:  WAITING | TRADE READY | ACTIVE | DONE
```

Cuando existe setup: **`HYPE LONG A+`** o **`HYPE SHORT A+`**, mostrando:

| Campo | | Campo | |
|---|---|---|---|
| Entry | | Quantity | |
| SL | | Notional | |
| TP | | Estimated Fees | |
| RR | | Estimated Funding | |
| Dollar Risk | | Cost_R | |
| Expected Profit at TP | | Signal Age / Time Remaining | |

Botones: **ENTER** · **SKIP** · **VIEW CHART**.

**La decisión final de entrada permanece manual.**

### Stack

Frontend **Next.js + React**. Backend **Python + FastAPI** (la estrategia ya está en Python y debe seguir siendo el único motor de reglas). DB **PostgreSQL**; SQLite permitido en desarrollo local.

---

## 17. Ejecución

> `LIVE_EXECUTION=false`. Nada puede enviar una orden real hasta aprobación explícita posterior.

Cuando Mark presiona **ENTER**, el backend **revalida** — no confía en lo que la UI tenía en pantalla:

- señal no expirada;
- drift ≤ 0.10R;
- no existe otra posición HYPE;
- límite diario de trades disponible;
- límites de riesgo diario OK;
- market data fresca;
- size válido;
- **costos siguen válidos**;
- **target clearance sigue válido**.

Después, en orden:

1. enviar entry
2. confirmar **fill real**
3. recalcular R con el fill real
4. **mantener el stop estructural** (no se recalcula desde el fill)
5. recalcular TP
6. colocar SL
7. colocar TP
8. **verificar ambas órdenes** contra el exchange
9. registrar los exchange order IDs
10. monitorizar posición
11. time stop a las 24 h

**Si falla la colocación o la verificación del SL: estado `CRITICAL`.** No se abre ninguna otra operación hasta resolución manual.

### Kill switches

Detener nuevas operaciones ante: 3 pérdidas consecutivas · daily loss ≥ 1 % del equity · market data stale · API errors · position mismatch · **protective SL missing** · unknown open order · unexpected HYPE position · `Cost_R` fuera de límite · señal expirada · límite diario agotado · kill switch manual.

---

## 18. Matriz de investigación (predeclarada)

No se optimiza infinitamente. Se corre esta matriz completa y **no se añade ningún brazo hasta cerrarla**.

| Eje | Brazos |
|---|---|
| **FILTER** | `F0` trend + VWAP retest · `F1` F0 + FVG · `F2` F1 + RSI/MACD/dirección VWAP/volumen |
| **EXIT** | `E1` 1R (control) · **`E2` 1.6R** · `E3` 2R · `E4` trailing estructural *(solo si hay implementación clara)* |
| **DIRECTION** | `D1` LONG · `D2` STRICT SHORT — **analizadas por separado, nunca agregadas** |
| **BTC GATE** | `G0` sin gate · `G1` régimen BTC compatible |

### Presupuesto de parámetros (`AUDIT` C-12)

| Optimizables (máx. 4, entran a sensibilidad ±30 %) | Fijados por decisión de producto (se declaran, no se buscan) |
|---|---|
| banda VWAP `k = 0.30` | `max_hold_hours = 24` |
| edad máxima del gap `= 12` | `1 trade/día` |
| buffer del stop `= 0.10·ATR` | `risk = $125` (producción) / `$1` (TINY) |
| lookback de `target_clearance` | `Cost_R` inicial 0.15 / 0.12 |

`rr` **no se optimiza**: se prueba como tres brazos discretos (E1/E2/E3).

---

## 19. Métricas

Siempre separadas: **LONG · SHORT · COMBINED · MARK ACCEPTED · MARK SKIPPED**.

Mínimo obligatorio:

Win Rate · Net Profit Factor · Expectancy R · Net PnL · Max Drawdown · Average Win R · Average Loss R · Average Cost_R · Average Holding Time · Median Holding Time · Trades per Week · Trades per Month · Consecutive Losses · MFE · MAE · **Funding Paid** · **Fees Paid**.

**El win rate nunca se usa como métrica única.**

---

## 20. Objetivos de investigación

**No son resultados garantizados.** Son criterios objetivo deseados:

| Criterio | Valor deseado |
|---|---|
| Riesgo medio en producción | ~$125 |
| Target medio | ~$200 |
| Payoff preferido | ~1.6R |
| Net Profit Factor objetivo | ≥ 1.5 idealmente |
| Win rate | el suficiente para expectancy positiva **después de costos** |
| Frecuencia | preferiblemente 3–5 oportunidades A+ por semana |

La frecuencia **real** es un resultado de investigación. **No se alteran las reglas para forzar frecuencia.**

---

## 21. Validación

Pipeline, sin saltos: `IDEA → RESEARCH → IS → OOS → PAPER → SHADOW → TINY → PRODUCTION`.

Se permite **paralelizar** cuando es seguro:

- el **forward logger** puede empezar inmediatamente;
- la investigación histórica corre en paralelo;
- la visualización de TradingView se construye en paralelo;
- la app en **PAPER** se construye sin esperar a live.

**Nunca hay promoción automática entre fases.** La promoción es un acto manual y explícito de Mark.

---

## 22. AI

**V1 es determinista. No hay LLM en el loop de ejecución.**

Cuando haya data suficiente, se crea un **Meta Model** que estime `P(TP antes que SL | setup)`.

Features candidatas: VWAP distance · ATR · FVG width · RSI · RSI slope · MACD hist · MACD slope · volume ratio · fuerza 4H · fuerza 1H · régimen BTC · funding · OI · liquidaciones · hora del día.

Solo se muestran probabilidades cuando el modelo esté **entrenado, validado OOS y calibrado**.

El AI podrá **rankear** setups. **Nunca** podrá: aumentar riesgo · ensanchar el SL · saltarse kill switches · cambiar de TINY a PROD · inventar Entry/SL/TP.

---

## 23. Fuera de alcance por ahora

No se construye: optimizador automático · machine learning de entradas · bot autónomo live · múltiples monedas · social trading · marketplace · dashboard complejo innecesario · móvil nativo.

Primero: **HYPE solamente. Una estrategia. Una entrada A+. Un trade máximo al día. Una experiencia impecable.**

---

## 24. Principio de seguridad

La estrategia puede **sugerir** una operación. La estrategia **no puede**:

- aumentar riesgo
- ensanchar un stop
- saltarse un kill switch
- promover de un modo a otro
- ejecutar una señal expirada
- inventar precios

**Mark conserva la decisión final de entrada.**

---

## 25. Definición de MVP

El MVP está terminado cuando:

1. market data de Hyperliquid funciona y se detecta cuando está *stale*;
2. la rama LONG funciona con el mismo motor en backtest y en vivo;
3. la rama SHORT estricta funciona y se mide por separado;
4. `target_clearance` bloquea trades de forma determinista y reproducible;
5. el backtester modela funding, fees, spread, slippage y fill realista;
6. TradingView muestra exactamente las mismas condiciones que el backend;
7. las señales aparecen en la app con Entry/SL/TP/Size/Cost;
8. ENTER/SKIP funciona y **todas** las señales quedan registradas antes de conocer el resultado;
9. PAPER funciona de punta a punta;
10. el límite de 1 trade/día y el time stop de 24 h están activos y testeados;
11. TINY funciona con máximo $1 de riesgo *(solo tras aprobación explícita; hoy `LIVE_EXECUTION=false`)*;
12. SL y TP se verifican contra el exchange después de cada fill, y su ausencia produce `CRITICAL`;
13. el dashboard reporta resultados reales separados por LONG/SHORT/ACCEPTED/SKIPPED.
