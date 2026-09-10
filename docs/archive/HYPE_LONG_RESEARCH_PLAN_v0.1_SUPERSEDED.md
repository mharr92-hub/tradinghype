# HYPE_LONG_RESEARCH_PLAN.md

Estado: BORRADOR v0.1 (2026-09-09). Documento hermano de `MULTI_STRATEGY_MASTER_PLAN.md`, `BTC_LONG_RESEARCH_PLAN.md`, `ALT_SHORT_X_SIGNAL_RESEARCH_PLAN.md` y `TRADINGVIEW_INTEGRATION_PLAN.md`.

Origen: propuesta externa "HYPE Copilot" (guía + scanner de otro asistente), **depurada** para cumplir la política y la metodología del proyecto. Este documento la reemplaza como especificación.

Política inmutable (MULTI_STRATEGY_MASTER_PLAN §0): **HYPE es LONG ONLY. Nunca short.** Toda la rama corta de la propuesta externa (reglas 4H RSI≤45, MACD<0, riesgo 0.125 %, volumen 1.2×, umbrales de costo para cortos) queda **eliminada, no endurecida**. Con ella muere también la discusión del "70/30": la mezcla es 100/0 por política. Reabrir eso sería un cambio de política explícito de Mark, nunca un default heredado de un documento externo.

---

## 1. Auditoría de la propuesta externa

| Elemento de la guía | Veredicto | Motivo |
|---|---|---|
| Solo velas COMPLETADAS (anti-repintado) | **SE CONSERVA** | Coincide con `lookahead=off` y alertas a cierre de barra del plan TV |
| Sin porcentajes de confianza inventados; checklist de condiciones | **SE CONSERVA** | Coincide con la disciplina anti-humo del proyecto |
| Filtro de costos en R (rechazar trades que las comisiones se comen) | **SE CONSERVA** | Es la mejor idea del documento; la matemática (break-even ≈ (1+c)/2 con 1:1) es correcta |
| Stop estructural primero → sizing desde el stop (incluyendo costo del trade perdedor) | **SE CONSERVA** | Correcto |
| Expiración de señal 90 s + drift máximo 0.10R | **SE CONSERVA** | Coincide con la ventana de 90 s del webhook (TRADINGVIEW_INTEGRATION_PLAN §5.2) |
| Registrar TODAS las señales (aceptadas y saltadas) antes de conocer el resultado | **SE CONSERVA** | Permite medir el sesgo de selección discrecional |
| Congelar v1 y comparar variantes predeclaradas (no buscar cientos de combos) | **SE CONSERVA** | Coincide con la política anti data-mining |
| Rama SHORT de HYPE completa | **ELIMINADA** | Viola la política inmutable |
| "Cerca de VWAP" sin definir | **CORREGIDO** | Ahora: la zona del gap debe solapar VWAP ± 0.30·ATR20(5m) al primer toque (§3) |
| ~12+ umbrales libres (EMAs, RSI banda, MACD 2 deltas, volumen, edad FVG, distancia VWAP, cruces de VWAP, tamaño de vela, liquidez visible, nivel 1H cercano…) | **REDUCIDO** | Sobreparametrización letal para una muestra de decenas-cientos de trades. v1 base tiene el mecanismo desnudo; momentum y filtros extra son **brazos** de test (§4), no defaults |
| MACD + RSI como confirmación por defecto | **DEGRADADO a variante F2** | Ambos derivan del precio (la propia guía lo admite); deben *ganarse* su lugar contra la base |
| 1:1 fijo como única salida | **DEGRADADO a brazo E1** | Con taker 4.5 bps/lado en 5m, break-even ≈ 54.5–55.5 % (tabla de la guía, correcta). Un setup de continuación de tendencia rara vez sostiene eso; E2 (2R) y E3 (trailing) se prueban en paralelo |
| Régimen solo con EMAs de HYPE | **AMPLIADO como brazo G1** | El máster plan define `btc_regime.py` compartido; cuando exista, se testea el gate BTC como brazo (§4). No se asume que ayuda: se mide |
| Scanner: "no verificada la conexión live"; 30 tests offline | **PENDIENTE AUDITORÍA** | Mark solo compartió la guía .md, no el ZIP. Antes de correrlo: auditar código, desactivar cortos, verificar que es read-only de verdad |

---

## 2. Hipótesis H-R1: "Primer retest VWAP/FVG en tendencia"

**Mecanismo (por qué subiría HYPE tras la señal):** en tendencia 4H alcista, un impulso de 5m deja una ineficiencia (FVG) y aleja el precio del ancla de flujo de la sesión (VWAP). El primer retroceso que toca esa zona y la **reclama** (cierre por encima del gap y del VWAP) combina: compradores que se quedaron fuera del impulso, cortos intradía atrapados por el fallo del retroceso, y flujo pasivo anclado a VWAP. Es una hipótesis de *continuación tras retroceso*, no de reversión.

**Predicciones falsables.** Si H-R1 es cierta, condicionado al régimen 4H+1H alcista, el retorno neto de las señales confirmadas:

1. es positivo tras costos (fees+spread+slippage+funding) en IS y OOS;
2. **bate a entradas aleatorias emparejadas** (mismo nº, mismas barras elegibles, mismo stop %, misma salida) con p < 0.05;
3. **bate al drift del régimen**: aporta algo sobre "estar largo HYPE siempre que el régimen 4H esté activo". Si (3) falla, el producto correcto es una posición de régimen, no una máquina de entradas de 5m — más barata y más simple, y se documenta como tal.

**Falsación explícita.** H-R1 se rechaza si en IS+OOS: (a) PF neto OOS < 1.2, o (b) no bate random matched con p < 0.05, o (c) no bate el drift del régimen, o (d) el resultado depende de < 5 trades (leave-k-out), o (e) N total < 60 señales (entonces ni siquiera hay muestra: máx. UNPROVEN).

Hoy H-R1 es **UNPROVEN en nuestros datos**. La evidencia previa (VWAP como ancla institucional, continuación tras retroceso en tendencia) es de mecanismo, no cuantitativa propia.

---

## 3. Definición operativa v1 (congelada)

Timeframes 4H / 1H / 5m. Todo con velas **completadas**. Sesión VWAP: reset 00:00 UTC (19:00 Panamá), precio típico ponderado por volumen.

| Bloque | Regla v1 |
|---|---|
| Régimen 4H | cierre > EMA20 > EMA50, y EMA50 > EMA50 de hace 3 barras |
| Alineación 1H | cierre > EMA20 > EMA50 |
| Warmup de sesión | sin señales en las primeras 12 velas de 5m tras el reset |
| Setup | FVG alcista de 3 velas (low de la 3ª > high de la 1ª) formado en la sesión vigente |
| Localización | al **primer toque**, la zona del gap solapa VWAP ± 0.30·ATR20(5m) |
| Vigencia del gap | primer toque a ≤ 12 velas de 5m de la formación; cierre bajo el lado lejano del gap = invalidado para siempre |
| Confirmación | en la vela del toque o las 2 siguientes: cierre > máx(gap, VWAP), > máximo de la vela previa, y cierre > apertura. Sin confirmación en la ventana → gap consumido (el primer retest falló y no se reintenta) |
| Entrada | cierre de la vela de confirmación |
| Stop | mín(lado inferior del gap, mínimo del tramo toque→confirmación) − 0.10·ATR20(5m) |
| Objetivo | E1: +1R (base) |
| Filtro de costos | (2·fee_taker + spread + slippage) / distancia_stop% ≤ **0.15R**; si no pasa, el trade se salta — **nunca se ensancha el stop para pasar el filtro** |
| Sizing | riesgo 0.25 % del equity; cantidad = riesgo_USD / (distancia_stop + costo_por_unidad_perdedora) |
| Ejecución | expiración de señal 90 s; drift máx. señal→fill 0.10R; recalcular R con el fill real y el stop estructural intacto |
| Límites manuales | 1 posición a la vez; parar el día tras 3 pérdidas seguidas o −1 % de equity |

**Parámetros libres declarados** (todo lo demás es convención fija): banda VWAP `k=0.30`, edad máxima del gap `12`, buffer del stop `0.10·ATR`, tope de costo `0.15R`. Solo estos cuatro entran a la sensibilidad (±30 %, se exige meseta: ≥ 70 % de las combinaciones con PF > 1.1). Cuatro parámetros, no doce: BTC_LONG_RESEARCH_PLAN §9 prohíbe optimizar más de 4.

Costos base (a confirmar con el tier real de la cuenta): taker 4.5 bps/lado, spread+slippage estimados 4 bps ida+vuelta, **funding por hora de tenencia en el backtest completo** (con funding positivo el largo PAGA — en HYPE alcista suele ser positivo, es viento en contra y se modela, no se ignora).

---

## 4. Variantes predeclaradas (y nada más)

Se corren TODAS de una vez sobre los mismos datos, se comparan, y no se añade ninguna otra sin cerrar antes este set. Primaria: **F1·E1·G0**.

| Eje | Brazos |
|---|---|
| Filtro | F0 = tendencia + primer retest de VWAP (sin exigir FVG) · **F1 = F0 + FVG (base)** · F2 = F1 + momentum (RSI 50–68 subiendo, MACD hist subiendo 2 velas, VWAP ascendente, volumen ≥ media 20) |
| Salida | **E1 = 1:1 (base)** · E2 = 2R · E3 = trailing estructural (mínimos crecientes 5m) |
| Gate | **G0 = sin gate BTC (base)** · G1 = + régimen BTC no bajista (cuando exista `btc_regime.py` v1) |

Preguntas que responde la matriz: ¿el FVG aporta sobre el simple retest de VWAP (F1 vs F0)? ¿El momentum paga su complejidad (F2 vs F1)? ¿1:1 sobrevive a los costos o el edge vive en 2R/trailing (E1 vs E2/E3)? ¿El régimen BTC filtra pérdidas (G1 vs G0)?

---

## 5. Controles obligatorios (idénticos en espíritu a BTC_LONG_RESEARCH_PLAN §5)

| Control | Qué mide | Cómo |
|---|---|---|
| Drift del régimen | ¿Gana solo porque HYPE sube cuando el régimen está activo? | Comparar contra "largo mientras régimen activo" y contra buy & hold |
| Random matched | ¿El timing aporta algo? | ≥ 1.000 réplicas: mismas nº de entradas, barras elegibles del mismo régimen, mismo stop %, misma salida; percentil del resultado real |
| Sensibilidad | ¿Meseta o pico? | Grid ±30 % en los 4 parámetros declarados |
| Outliers | ¿Depende de 3 trades? | PF sin el top-5 de ganadores; leave-k-out |
| Sub-periodos | ¿Solo funcionó un trimestre? | Por mes y por régimen de volatilidad |
| Walk-forward | ¿Sobrevive fuera de muestra? | Parámetros fijados en ventana N, aplicados en N+1 |
| Estrés de costos | ¿Muere con fees peores? | Repetir con costos ×1.5 |

Muestra mínima para promover: ≥ 100 señales IS, ≥ 50 OOS. Con menos, etiqueta máxima PROMISING (la frecuencia real del setup es un **resultado** a medir en semana 1, no un objetivo).

---

## 6. Datos (la restricción real)

| Dato | Fuente | Estado |
|---|---|---|
| Velas 5m HYPE, histórico largo | HL `candleSnapshot` solo da las últimas ~5.000 velas ≈ 17 días en 5m | Insuficiente por sí solo |
| Proxy 5m largo | **Bybit listó el perp HYPEUSDT el 5-dic-2024** ([anuncio Bybit](https://announcements.bybit.com/article/listing-of-hypeusdt-on-bybit-perpetual-pre-market-on-dec-5-2024-12-00pm-utc-blta2c0e79eb2afa939/)) → ~21 meses de velas 5m por API pública de klines. Se valida contra las velas HL en el periodo solapado (~17 días): error medio de cierres y correlación; si diverge más del umbral, no se usa | A descargar y validar (paso 2 del protocolo) |
| Velas HL nativas hacia adelante | El scanner + el grabador continuo del máster plan (§7: "debe empezar a correr ya") | Arranca con el paso 1 |
| Reconstrucción HL histórica | Archivo S3 `hyperliquid-archive` (requester-pays) → velas 5m desde fills | Opcional; tiene costo AWS — **no se incurre sin autorización de Mark** |
| Funding HYPE histórico | HL `fundingHistory` (completo) | Disponible |

Regla: no se inventan velas. Si el proxy no valida, el backtest largo espera a la reconstrucción S3 o se reduce el alcance y se documenta.

---

## 7. Protocolo de prueba real (fases, gates y kill criteria)

"Prueba real" = el pipeline del proyecto, no dinero en vivo mañana. IDEA → RESEARCH → **IS → OOS → PAPER → SHADOW → TINY → PROD**, sin saltos (MULTI_STRATEGY_MASTER_PLAN §10).

| Fase | Qué pasa | Gate para avanzar |
|---|---|---|
| 0. Higiene | Auditar el ZIP del scanner; **eliminar/desactivar toda lógica corta**; verificar read-only; TEST.bat; confirmar tier de fees real | Scanner corre y no puede ni proponer cortos |
| 1. Forward log (empieza HOY) | Scanner en modo log-only registrando cada señal (aceptada y saltada) + velas 5m HL. El reloj de PAPER arranca aquí, en paralelo al backtest | ≥ 4 semanas y ≥ 30 señales acumuladas (se cosechan en fase 4) |
| 2. Datos | Descargar 5m Bybit dic-2024→hoy; validar vs HL en el solape; inventario de huecos | Proxy validado o decisión S3 |
| 3. IS | Correr la matriz §4 con `backtest_hype_long_v0.py` (y luego el motor del repo con funding); controles §5; **split predeclarado**: IS = dic-2024→feb-2026, OOS = mar-2026→hoy, intocado hasta el final | Algún brazo con PF IS ≥ 1.3 neto, bate drift y random p<0.05, meseta de sensibilidad |
| 4. OOS + walk-forward | Solo los brazos supervivientes, parámetros congelados de IS | PF OOS ≥ 1.2 neto, N OOS ≥ 50, sigue batiendo controles |
| 5. PAPER formal | Cruzar el backtest con el log forward de la fase 1; comparar señales del scanner vs selección discrecional de Mark | ≥ 4 semanas, ≥ 30 señales, expectancy neta ≥ 0 |
| 6. SHADOW → TINY | Fills simulados contra libro real; luego tamaño mínimo con kill switch, TP/SL verificados contra mark price (trigger de HL) | Aprobación explícita de Mark en `promotion.yaml` — el código nunca promueve |

**Kill criteria (cualquiera mata el brazo, se documenta en rejected y no se reintenta sin evidencia nueva):** PF OOS < 1.2; no bate random/drift; depende de < 5 trades; > 50 % de las señales rechazadas por el filtro de costos (síntoma de que 5m + fees + 1:1 no da — conclusión que la propia guía anticipa); frecuencia < 1 señal/semana con N proyectado < 60 en 6 meses.

Resultado válido posible: "ningún brazo llega". Se documenta y el libro HYPE vuelve a su hipótesis preferida del máster plan (régimen BTC + estructura + deleveraging + OI/funding), que sigue siendo la candidata principal del track — H-R1 compite con ella, no la sustituye.

---

## 8. Qué NO se construye

Cortos de HYPE bajo ningún nombre; porcentajes de confianza; más brazos que los de §4; optimización continua de umbrales; ensanchar stops para pasar el filtro de costos; ejecución en vivo antes de TINY con aprobación; confiar stops solo a TradingView; un dashboard bonito antes de tener números.

---

## 9. Archivos entregados (2026-09-09) y destino en el repo

| Archivo | Qué es | Destino |
|---|---|---|
| `HYPE_LONG_RESEARCH_PLAN.md` | Este documento | `docs/` |
| `hype_long_vwap_retest.py` | Implementación de referencia: reglas v1 deterministas, long-only por construcción (no existe camino de código corto; test lo verifica), misma función para backtest/paper/live, variante F2 y cache de gaps incluidos | `strategies/hype_long/rules.py` |
| `test_hype_long_vwap_retest.py` | 16 tests: política, end-to-end sintético, primer-retest único, invalidación, filtro de costos, warmup, equivalencia del cache | `tests/` |
| `backtest_hype_long_v0.py` | Backtest v0 sobre CSV de 5m (resamplea 1H/4H internamente): contabilidad en R neta, DD, guardado incremental de trades, controles drift/régimen-long/random matched. Sin funding (documentado); el motor del repo lo añade | `tools/` |

`[PENDIENTE AUDITORÍA]`: integración con los módulos existentes de `hype-vol-bot` cuando Mark comparta el ZIP del repo y el ZIP del scanner externo.
