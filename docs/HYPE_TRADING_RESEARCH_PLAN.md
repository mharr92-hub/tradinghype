# HYPE_TRADING_RESEARCH_PLAN.md

**Estado:** v1.0 (2026-09-09). Sucede a `HYPE_LONG_RESEARCH_PLAN.md` v0.1, archivado íntegro en `archive/HYPE_LONG_RESEARCH_PLAN_v0.1_SUPERSEDED.md`.
**Renombrado** porque el alcance ya no es long-only. Documentos hermanos: `PRD_HYPE_COPILOT.md` v2.0 (producto), `AUDIT_001_CONFLICTOS.md` (qué cambió y por qué).

---

## 0. Historial de política — por qué existe este documento

| Fecha | Documento | Política |
|---|---|---|
| 2026-09-09 (mañana) | `HYPE_LONG_RESEARCH_PLAN.md` v0.1 | **HYPE es LONG ONLY. Nunca short.** Toda la rama corta de la propuesta externa "HYPE Copilot" queda *eliminada, no endurecida*. Cláusula de escape: *"Reabrir eso sería un cambio de política explícito de Mark."* |
| 2026-09-09 (tarde) | Decisión de producto de Mark | **Cláusula ejercida.** LONG ONLY queda revocada. HYPE admite LONG y SHORT, con SHORT sustancialmente más estricto. |

Lo que **no** cambió con la revocación, y sigue siendo la columna vertebral del método:

1. Solo velas completadas; anti-repintado; nada de lookahead.
2. Sin porcentajes de confianza inventados; checklist de condiciones objetivas.
3. Filtro de costos en R; **jamás ensanchar el stop para pasarlo**.
4. Stop estructural primero, sizing desde el stop.
5. Registrar **todas** las señales antes de conocer el resultado, aceptadas y saltadas.
6. Congelar la definición y comparar **brazos predeclarados**; nunca buscar cientos de combinaciones.
7. Control anti-data-mining: presupuesto duro de parámetros optimizables.
8. `IDEA → RESEARCH → IS → OOS → PAPER → SHADOW → TINY → PROD` sin saltos ni promoción automática.

Lo que sí cambió, con su justificación, está en `AUDIT_001_CONFLICTOS.md` (C-01 a C-15). Los cambios de mayor consecuencia metodológica: funding ahora es obligatorio (C-04), el fill al cierre de la vela de señal queda prohibido (C-05), y el objetivo migra de 1R a 1.6R (C-02).

---

## 1. Las dos hipótesis, evaluadas por separado

### H-R1 — LONG: "primer retest VWAP/FVG en tendencia alcista"

**Mecanismo.** En tendencia 4H alcista, un impulso de 5m deja una ineficiencia (FVG) y aleja el precio del ancla de flujo de la sesión (VWAP). El primer retroceso que toca esa zona y la **reclama** (cierre por encima del gap y del VWAP) combina compradores que se quedaron fuera del impulso, cortos intradía atrapados por el fallo del retroceso, y flujo pasivo anclado a VWAP. Es continuación tras retroceso, **no** reversión.

### H-R2 — SHORT: "primer retest VWAP/FVG en tendencia bajista con momentum confirmado"

**Mecanismo.** Simétrico en forma, **asimétrico en exigencia**. HYPE es un activo cuyo régimen dominante y cuyo funding han favorecido al largo; un corto necesita más que una tendencia bajista de EMAs: necesita que el HTF esté además debilitado (`RSI 4H ≤ 45`), que el MTF esté acelerando a la baja (`MACD hist < 0` y descendiendo), y que la vela de confirmación llegue con **volumen superior a lo normal** (1.20×), no simplemente con volumen medio.

**H-R2 no hereda ni una sola unidad de evidencia de H-R1.** Se mide con sus propios controles, sus propios gates y su propio `N`.

### Predicciones falsables (idénticas en estructura para ambas)

Condicionado al régimen HTF+MTF alineado, el retorno **neto** (fees + spread + slippage + **funding por tenencia real**) de las señales confirmadas:

1. es positivo en IS **y** en OOS;
2. **bate a entradas aleatorias emparejadas** (mismo número, mismas barras elegibles del mismo régimen, mismo stop %, misma salida) con `p < 0.05`;
3. **bate al drift del régimen**: aporta algo sobre "estar largo mientras el régimen 4H sea alcista" (H-R1) o "estar corto mientras sea bajista" (H-R2). Si (3) falla, el producto correcto es una posición de régimen, no una máquina de entradas de 5m — más barata y más simple, y se documenta como tal.

### Falsación explícita

Se rechaza la hipótesis si en IS+OOS: (a) PF neto OOS `< 1.2`; o (b) no bate random matched con `p < 0.05`; o (c) no bate el drift del régimen; o (d) el resultado depende de menos de 5 trades (leave-k-out); o (e) `N < 60` señales.

**Hoy H-R1 y H-R2 son ambas UNPROVEN en nuestros datos.** La evidencia previa (VWAP como ancla institucional, continuación tras retroceso) es de mecanismo, no cuantitativa propia.

---

## 2. Definición operativa v2 (congelada)

Timeframes 4H / 1H / 5m. Todo con velas **completadas**. VWAP de sesión: reset 00:00 UTC (19:00 Panamá), precio típico ponderado por volumen.

### 2.1 Común a ambas direcciones

| Bloque | Regla |
|---|---|
| Warmup de sesión | sin señales en las primeras 12 velas de 5m tras el reset |
| Localización | al **primer toque**, la zona del gap solapa `VWAP ± 0.30·ATR20(5m)` |
| Vigencia del gap | primer toque a ≤ 12 velas de la formación; cierre a través del lado lejano = invalidado **para siempre** |
| Ventana de confirmación | vela del toque + 2 siguientes; sin confirmación ⇒ gap consumido, el primer retest no se reintenta |
| Entrada | **primera observación de mercado tras el cierre de la vela de confirmación**, con delay/spread/slippage modelados (§4.3) |
| Target clearance | `clearance_R ≥ rr_objetivo` del brazo, contra niveles deterministas (PRD §7.1) |
| Filtro de costos | LONG `≤ 0.15R`, SHORT `≤ 0.12R` — **nunca se ensancha el stop para pasarlo** |
| Time stop | 24 h = 288 velas de 5m |
| Límite operativo | 1 posición a la vez; **máx. 1 trade nuevo por día**; parar el día tras 3 pérdidas seguidas o −1 % de equity |

### 2.2 LONG

| Bloque | Regla |
|---|---|
| Régimen 4H | `close > EMA20 > EMA50` y `EMA50 > EMA50[3]` |
| Alineación 1H | `close > EMA20 > EMA50` |
| Setup | FVG alcista de 3 velas (`low[3ª] > high[1ª]`) formado en la sesión vigente |
| Confirmación | `close > max(gap_hi, VWAP)`, `close > high[1]`, `close > open` |
| Momentum | **brazo F2, no base** (ver §3) |
| Stop | `min(gap_lo, low del tramo toque→confirmación) − 0.10·ATR20(5m)` |

### 2.3 SHORT (estricto)

| Bloque | Regla — **todas obligatorias** |
|---|---|
| Régimen 4H | `close < EMA20 < EMA50` · `EMA50 < EMA50[3]` · `RSI(14) ≤ 45` |
| Alineación 1H | `close < EMA20 < EMA50` · MACD hist `< 0` · MACD hist descendiendo |
| Setup | FVG bajista (`high[3ª] < low[1ª]`) formado en la sesión vigente |
| Confirmación | `close < min(gap_lo, VWAP)`, `close < low[1]`, `close < open` |
| Momentum | **obligatorio, no es brazo**: `RSI ∈ [32,48]` descendiendo · MACD hist descendiendo 2 barras · **VWAP descendente** · volumen `≥ 1.20 ×` media 20 |
| Stop | `max(gap_hi, high del tramo toque→confirmación) + 0.10·ATR20(5m)` |

> **Asimetría deliberada.** En LONG el momentum debe *ganarse* su lugar contra F1 (deriva del precio; ver v0.1 §1). En SHORT forma parte de la definición del setup y no se puede apagar: es uno de los mecanismos por los que el corto es estructuralmente más difícil de disparar. Documentado en `AUDIT_001_CONFLICTOS.md` C-11 para que nadie lo "corrija" después por simetría.

### 2.4 Presupuesto de parámetros

| Optimizables — máx. 4, entran a sensibilidad ±30 % | Fijados por decisión de producto — se declaran, no se buscan |
|---|---|
| banda VWAP `k = 0.30` | `max_hold_hours = 24` |
| edad máxima del gap `= 12` | `1 trade/día` |
| buffer del stop `= 0.10·ATR` | `risk = $125` producción / `$1` TINY |
| lookback de `target_clearance` | `Cost_R` inicial `0.15 / 0.12` |

Se exige **meseta**: ≥ 70 % de las combinaciones de la rejilla con PF `> 1.1`. Un pico aislado es un rechazo, no un hallazgo.

`rr` **no se optimiza**: se prueba como tres brazos discretos (E1/E2/E3). `warmup_bars`, longitudes de EMA/RSI/MACD/ATR y la ventana de confirmación son **convención fija**, no parámetros.

### 2.5 Costos base

Taker 4.5 bps/lado (**confirmar el tier real de la cuenta antes de reportar nada**), spread+slippage estimados 4 bps ida+vuelta, y **funding por hora de tenencia real**. Hyperliquid liquida funding horariamente: verificado contra la API el 2026-09-09. Con funding positivo el largo **paga** y el corto **cobra**; con tenencias de hasta 24 h esto ya no es ruido y se modela con su signo.

> **Medición del 2026-09-09 — corrige un supuesto de la v0.1.** El plan anterior afirmaba: *"con funding positivo el largo PAGA — en HYPE alcista suele ser positivo, es viento en contra"*. La lectura real de `metaAndAssetCtxs` da **−0.00000573/h ≈ −5.0 % anualizado**: el funding está **negativo**, es decir, ahora mismo los largos **cobran** y los cortos **pagan**.
>
> Consecuencias que no son cosméticas:
> 1. El cost gate de la rama LONG es **más permisivo** de lo que suponía el plan, y el de la SHORT **más estricto** — justo al revés de lo asumido.
> 2. Un funding persistentemente negativo es información de mercado por sí misma: indica presión corta pagando por mantenerse. Conviene registrarlo como feature del journal desde el día uno (§19 del PRD ya lo lista).
> 3. **Una sola lectura no es una serie.** El signo puede invertirse. Esto no autoriza a asumir viento a favor: obliga a descargar `fundingHistory` completo y a modelar el funding con su signo real hora a hora, nunca con una constante. Es exactamente la razón por la que C-04 lo hizo obligatorio.

---

## 3. Matriz predeclarada

Se corre **entera** sobre los mismos datos, se compara, y **no se añade ningún brazo hasta cerrar este set**.

| Eje | Brazos |
|---|---|
| **FILTER** | `F0` tendencia + primer retest de VWAP (sin exigir FVG) · **`F1` F0 + FVG (base)** · `F2` F1 + momentum |
| **EXIT** | `E1` 1R (control, era la base en v0.1) · **`E2` 1.6R (candidato principal)** · `E3` 2R · `E4` trailing estructural *(solo si existe implementación clara)* |
| **DIRECTION** | `D1` LONG · `D2` STRICT SHORT — **nunca agregadas; siempre reportadas por separado** |
| **BTC GATE** | **`G0` sin gate (base)** · `G1` régimen BTC compatible (cuando exista `btc_regime.py`) |

Primaria LONG: **F1·E2·D1·G0**. Primaria SHORT: **F2·E2·D2·G0** (en SHORT el momentum es parte de la definición, así que su base ya es F2).

**Preguntas que responde la matriz:**

- ¿El FVG aporta sobre el simple retest de VWAP? (F1 vs F0)
- ¿El momentum paga su complejidad en LONG? (F2 vs F1)
- ¿1.6R sobrevive a los costos mejor que 1:1, o el edge vive en 2R? (E2 vs E1/E3)
- ¿La rama SHORT tiene edge propio, o solo parece funcionar en el tramo bajista de la muestra? (D2, con sus propios controles)
- ¿El régimen BTC filtra pérdidas? (G1 vs G0)

---

## 4. Controles obligatorios

Se aplican **por separado a D1 y a D2**. Un LONG promovido no promueve el SHORT.

| Control | Qué mide | Cómo |
|---|---|---|
| Drift del régimen | ¿Gana solo porque HYPE se mueve cuando el régimen está activo? | LONG: contra "largo mientras régimen alcista" y buy & hold. SHORT: contra "corto mientras régimen bajista" |
| Random matched | ¿El timing aporta algo? | ≥ 1.000 réplicas: mismo nº de entradas, barras elegibles del mismo régimen, mismo stop %, misma salida; percentil del resultado real |
| Sensibilidad | ¿Meseta o pico? | Rejilla ±30 % en los 4 parámetros de §2.4 |
| Outliers | ¿Depende de 3 trades? | PF sin el top-5 de ganadores; leave-k-out |
| Sub-periodos | ¿Solo funcionó un trimestre? | Por mes y por régimen de volatilidad |
| Walk-forward | ¿Sobrevive fuera de muestra? | Parámetros fijados en ventana N, aplicados en N+1 |
| Estrés de costos | ¿Muere con fees peores? | Repetir con costos ×1.5 **y con funding ×2** |
| **Estrés de fill** | ¿El edge vive solo en el fill perfecto? | Repetir con delay y slippage al doble; medir cuántos trades se pierden por drift > 0.10R |
| **Coste del límite diario** | ¿"El primer A+" es peor que "el mejor A+"? | Contabilizar los A+ bloqueados por el límite diario y su resultado hipotético (`AUDIT` C-14) |

Los dos últimos son nuevos en v1.0 y responden directamente a C-05 y C-14.

**Muestra mínima para promover:** ≥ 100 señales IS y ≥ 50 OOS, **por dirección**. Con menos, etiqueta máxima `PROMISING`.

> **`N` de señales vs. `N` de trades.** El límite de 1 trade/día descarta señales válidas, así que ambos números divergen. Los umbrales de muestra se miden sobre **señales**; la contabilidad económica, sobre **trades**. Confundirlos mata brazos que sí tenían frecuencia (`AUDIT` C-06).

---

## 5. Datos

| Dato | Fuente | Estado |
|---|---|---|
| Velas 5m HYPE, histórico largo | HL `candleSnapshot` da ~5.000 velas ≈ 17 días en 5m | Insuficiente por sí solo |
| Proxy 5m largo | Bybit listó el perp HYPEUSDT el **5-dic-2024** → ~21 meses de klines por API pública. Se valida contra HL en el solape (~17 días): error medio de cierres y correlación; si diverge sobre el umbral, no se usa | A descargar y validar |
| Velas HL nativas hacia adelante | Grabador continuo + forward logger (fase 1) | Arranca ya |
| Reconstrucción HL histórica | Archivo S3 `hyperliquid-archive` (requester-pays) → velas 5m desde fills | Opcional; tiene costo AWS — **no se incurre sin autorización de Mark** |
| **Funding HYPE histórico** | HL `fundingHistory` (completo) | **Ahora obligatorio**, no opcional (C-04) |
| Velas BTC (para G1) | Misma fuente que HYPE | Necesario solo para el brazo G1 |

**Regla:** no se inventan velas. Si el proxy no valida, el backtest largo espera a la reconstrucción S3, o se reduce el alcance y se documenta.

---

## 6. Protocolo de prueba (fases, gates, kill criteria)

"Prueba real" = el pipeline completo, no dinero en vivo mañana.

| Fase | Qué pasa | Gate para avanzar |
|---|---|---|
| **0. Higiene** | Auditar el scanner externo; verificar read-only; confirmar el tier de fees real; `LIVE_EXECUTION=false` verificado | El scanner corre y no puede enviar órdenes |
| **1. Forward log** *(empieza hoy, en paralelo)* | Motor en modo log-only registrando **cada** señal — aceptada, saltada y bloqueada por el límite diario — más velas 5m HL y funding | ≥ 4 semanas y ≥ 30 señales |
| **2. Datos** | Descargar 5m Bybit dic-2024→hoy; validar contra HL en el solape; inventario de huecos; descargar `fundingHistory` | Proxy validado o decisión S3 |
| **3. IS** | Correr la matriz §3 con el backtester nuevo (funding + fill realista); controles §4. **Split predeclarado: IS = dic-2024→feb-2026, OOS = mar-2026→hoy, intocado hasta el final** | Algún brazo con PF IS ≥ 1.3 neto, que bata drift y random con `p<0.05` y muestre meseta |
| **4. OOS + walk-forward** | Solo los brazos supervivientes, parámetros congelados de IS | PF OOS ≥ 1.2 neto, `N` OOS ≥ 50, sigue batiendo controles |
| **5. PAPER formal** | Cruzar el backtest con el log forward de la fase 1; comparar señales del sistema vs. selección discrecional de Mark | ≥ 4 semanas, ≥ 30 señales, expectancy neta ≥ 0 |
| **6. SHADOW → TINY** | Fills simulados contra libro real; luego $1 de riesgo con kill switches y SL/TP verificados contra el mark price | **Aprobación explícita de Mark. El código nunca promueve.** |

### Kill criteria

Cualquiera mata el brazo. Se documenta en `rejected/` y no se reintenta sin evidencia nueva.

- PF neto OOS `< 1.2`
- No bate random matched o drift del régimen
- Depende de menos de 5 trades
- `> 50 %` de las señales rechazadas por el filtro de costos *(síntoma de que 5m + fees no da)*
- `> 40 %` de las señales rechazadas por `target_clearance` *(síntoma de que 1.6R no cabe en este mercado — nuevo en v1.0)*
- Frecuencia `< 1 señal/semana` con `N` proyectado `< 60` en 6 meses
- **El edge desaparece bajo el estrés de fill** *(nuevo en v1.0: si solo vive con el fill perfecto, no existe)*

**Resultado válido posible: "ningún brazo llega".** Se documenta y se cierra. Es información, no fracaso.

---

## 7. Qué NO se construye

Optimizador automático · ML de entradas en V1 · bot autónomo live · más brazos que los de §3 · optimización continua de umbrales · ensanchar stops para pasar el filtro de costos · porcentajes de probabilidad sin modelo calibrado OOS · ejecución en vivo antes de TINY con aprobación explícita · confiar los stops solo a TradingView · un dashboard bonito antes de tener números.
