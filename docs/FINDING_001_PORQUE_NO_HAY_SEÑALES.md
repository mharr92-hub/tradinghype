# HALLAZGO 001 — Por qué el sistema no produce señales

**Fecha:** 2026-09-09 · **Datos:** 17.2 días de velas 5m **nativas de Hyperliquid** (4.946 velas), funding real (413 puntos), brazo F1·E2 con LONG y SHORT habilitados.

**Resultado:** 10 setups confirmados, **0 señales**. Y no es un bug.

---

## El embudo

| Etapa | Velas | % |
|---|---|---|
| Régimen 4H alcista | 2.923 | 63.6 % |
| 1H alineado con el 4H | 2.011 | 43.7 % |
| FVG vivo esperando retest | 1.418 | 30.8 % |
| Precio tocó el gap, esperando confirmación | 36 | 0.8 % |
| **Setup CONFIRMADO** | **10** | **0.2 %** |
| **Señal emitida** | **0** | **0 %** |

**El setup aparece ~0.6 veces al día.** La frecuencia no es el problema. Lo que mata a las diez es la economía, y por dos razones estructurales distintas.

---

## Las diez, una por una

| Fecha UTC | Entry | Stop % | Clearance | Nivel que bloquea | Cost_R | Muere por |
|---|---|---|---|---|---|---|
| 08-28 14:50 | 84.368 | 1.27 % | 0.38R | 84.777 swing 1H | — | clearance |
| 08-30 06:10 | 83.116 | **0.19 %** | 2.76R | — | **0.675** | costos |
| 08-30 09:15 | 83.474 | 0.50 % | 0.20R | 83.557 swing 1H | — | clearance |
| 09-01 11:35 | 83.851 | 0.43 % | 0.59R | 84.064 swing 1H | — | clearance |
| 09-01 14:00 | 83.896 | 0.59 % | 0.24R | 84.014 swing 1H | — | clearance |
| 09-01 14:15 | 84.119 | 0.52 % | **0.08R** | 84.155 swing 1H | — | clearance |
| 09-04 02:10 | 87.213 | 0.51 % | 2.21R | — | 0.254 | costos |
| 09-04 03:45 | 87.355 | 0.35 % | 2.77R | — | 0.373 | costos |
| 09-04 09:30 | 86.796 | 0.55 % | 2.96R | — | 0.238 | costos |
| 09-06 03:00 | 85.648 | 0.33 % | 0.25R | 85.717 swing 1H | — | clearance |

Las diez son LONG. **Cero setups SHORT en 17 días**, consistente con un 4H alcista el 63.6 % del tiempo y una rama corta deliberadamente estricta.

---

## Diagnóstico 1 — el stop es demasiado ajustado para pagar las comisiones

Las distancias al stop van de **0.19 % a 1.27 %**, con mediana ~0.50 %.

El coste modelado ida y vuelta es `2×4.5 bps (fees) + 2 bps (spread) + 2 bps (slippage) = 13 bps`. Sobre un stop del 0.50 %:

```
Cost_R = 0.0013 / 0.0050 = 0.26R
```

El límite es **0.15R**. El coste se lleva más de una cuarta parte del riesgo antes de que el trade empiece.

Esto es **exactamente** el kill criterion que el research plan anticipó: *"> 50 % de las señales rechazadas por el filtro de costos (síntoma de que 5m + fees no da)"*. Sin el gate de clearance, el de costos mata 9 de 10 — el 90 %.

> **Los tres números del coste son SUPUESTOS sin confirmar.** El fee de 4.5 bps no es el tier real de la cuenta, y el spread y slippage de 2 bps cada uno son estimaciones. Confirmarlos es la acción con mejor relación coste/beneficio de todo el proyecto ahora mismo: si el coste real fuese de 9 bps en vez de 13, el `Cost_R` de la mediana bajaría de 0.26 a 0.18.

## Diagnóstico 2 — las entradas caen justo debajo de resistencia

Las seis rechazadas por clearance tienen espacio de **0.08R, 0.20R, 0.24R, 0.25R, 0.38R y 0.59R**. Todas por debajo de 1.0R.

Por eso bajar el objetivo de 1.6R a 1.2R o a 1.0R **no cambió absolutamente nada**: el obstáculo no está a 1.3R, está a 0.2R. En una de ellas, a 0.08R — el swing high estaba a ocho centésimas de R por encima de la entrada.

**El gate está funcionando como se diseñó.** Está rechazando comprar el retest de un FVG que ocurre justo debajo de un máximo reciente de 1H. Eso no es un filtro demasiado estricto; es la descripción de un mal trade.

## El único trade que sobrevive

Quitando el gate de clearance, una señal llega a ejecución:

```
LONG  fill 84.3849  stop 83.2936  →  SL en 0.8 h
MFE 0.66R    MAE 1.02R    neto −1.071R
```

Nunca se acercó a 1.6R. Llegó a 0.66R a favor y se dio la vuelta. `N=1` no significa nada estadísticamente, pero la forma es coherente con el diagnóstico.

---

## Qué NO se debe hacer con esto

**Aflojar los gates hasta que salgan trades diarios.** Eso significaría, literalmente y con estos números: pagar entre el 24 % y el 68 % de R en comisiones para comprar a 0.08R por debajo de una resistencia. No es una estrategia menos exigente; es la descripción de una pérdida sistemática.

El PRD lo dice en su §0: **NO TRADE > BAD TRADE**. Este es el caso concreto para el que se escribió esa regla.

## Qué SÍ hacer — brazos declarables que atacan el diagnóstico real

El diagnóstico es específico, así que las respuestas también pueden serlo. Ninguna es "bajar un umbral hasta que aparezcan trades":

| # | Brazo | Ataca | Por qué podría funcionar |
|---|---|---|---|
| 1 | **Confirmar los costes reales** | Diagnóstico 1 | No es un brazo, es medir. Los 13 bps son un supuesto y el gate depende linealmente de ellos |
| 2 | **Entrada limit en vez de market** | Diagnóstico 1 | Fee maker en lugar de taker cambia `cost_frac` de golpe. A cambio: fills perdidos, que hay que modelar |
| 3 | **Stop al lado lejano del gap** | Diagnóstico 1 | Stops más anchos → menor `Cost_R`. A cambio, menor tamaño de posición para el mismo riesgo |
| 4 | **Setup en 15m o 1H** | Diagnóstico 1 | Los stops estructurales escalan con el timeframe; los costes no |
| 5 | **Exigir que el FVG esté libre de resistencia por diseño** | Diagnóstico 2 | En vez de rechazar a posteriori, buscar solo gaps con espacio arriba |

Los brazos 3 y 4 son los que atacan la causa raíz. El 1 es gratis y debe hacerse primero.

## Y una posibilidad que hay que dejar escrita

Puede que **HYPE en 5m no ofrezca este setup de forma rentable**. El research plan contempla explícitamente ese desenlace: *"Resultado válido posible: ningún brazo llega. Se documenta y se cierra. Es información, no fracaso."*

17 días no bastan para afirmarlo — hacen falta ≥60 señales. Pero la dirección del hallazgo es clara y es un dato, no una opinión.

---

## Reproducir

```bash
cd backend
python -m tools.quick_backtest --days 17 --short                 # embudo completo
python -m tools.quick_backtest --days 17 --short --no-clearance  # sin el gate de clearance
```

**Nota metodológica:** la primera versión de esta herramienta usaba una ventana de 400 velas de 5m y el gate de clearance fallaba por falta de cobertura de la sesión previa, no por resistencia. Se detectó porque el número de rechazos era idéntico con 1.0R, 1.2R y 1.6R — algo imposible si el motivo fuera la distancia al objetivo. Corregido a 900 velas. La lección: cuando un gate rechaza exactamente lo mismo bajo condiciones que deberían cambiarlo, el sospechoso es la herramienta, no el mercado.
