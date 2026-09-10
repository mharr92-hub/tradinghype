# CONGELAMIENTO — Strategy v2

**Fecha:** 2026-09-09 · **Decisión de Mark** · **Estado:** ACTIVO

> A partir de aquí, cualquier cambio de parámetros es un **brazo de investigación**, no una mejora. Necesitamos saber si esta versión funciona antes de enseñarle treinta trucos nuevos.

## Qué queda congelado

| Brazo primario | Huella | Objetivo |
|---|---|---|
| **F1·E2** | `a3e991b4a3b1` | 1.6R, LONG con momentum apagado |

Brazos declarados de la matriz, todos con huella estable:

| Brazo | `rr` | Huella | Rol |
|---|---|---|---|
| F1·E1 | 1.0 | `862dc3387dc9` | control (era la base en v1) |
| **F1·E2** | **1.6** | **`a3e991b4a3b1`** | **primario** |
| F1·E3 | 2.0 | `ae33ddf48674` | brazo |
| F2·E1 | 1.0 | `3b53e444b8dc` | control con momentum |
| F2·E2 | 1.6 | `8d4b36b5598b` | primario SHORT |
| F2·E3 | 2.0 | `8b99ca4c4cb5` | brazo |

## Cómo se hace cumplir

El congelamiento **no es una promesa, es un dato**. `Config.fingerprint()` calcula un SHA-256 de los parámetros que definen la estrategia, y esa huella viaja con **cada registro del journal**.

```python
Config().fingerprint()                      # a3e991b4a3b1
Config(rsi_long_min=49.0).fingerprint()     # bf5c55025a4c  <- cambió
Config(vwap_band_atr=0.35).fingerprint()    # 6c9c2c0ec752  <- cambió
```

Si alguien mueve un umbral a mitad de la muestra, las dos mitades quedan **separables en el análisis** en vez de mezclarse en silencio. Es la diferencia entre perder el `N` acumulado y poder segmentarlo.

**Excluidos de la huella a propósito** (cambian legítimamente y no definen la estrategia): tasa de funding vigente, precisión del venue, presupuesto de riesgo, flags de dirección, y si el fee está confirmado. Incluirlos daría una huella distinta cada hora y no serviría para nada.

```python
Config(funding_rate_hourly=-0.0001).fingerprint()  # a3e991b4a3b1  <- NO cambia
Config(risk_usd=125.0).fingerprint()               # a3e991b4a3b1  <- NO cambia
```

## Qué SÍ se puede cambiar sin romper el congelamiento

- Modo (`RESEARCH` / `PAPER` / `SHADOW`)
- Presupuesto de riesgo y topes de nocional/apalancamiento
- `allow_short` (habilita la evaluación; no cambia las reglas)
- La tasa de funding, que es **dato de mercado, no creencia** — ver abajo
- El tier de fees, cuando se confirme el real

## Qué NO se cambia

Umbrales de RSI, MACD, volumen · banda de VWAP · edad máxima del gap · buffer del stop · ventana de confirmación · lookback de clearance · longitudes de EMA/ATR/RSI · warmup de sesión · `rr` fuera de los tres brazos declarados.

Si aparece una idea, **se escribe como brazo nuevo y se corre contra los existentes**. No se sustituye el actual.

## El funding es dato, no tesis

El research plan v0.1 afirmaba que en HYPE *"con funding positivo el largo PAGA — suele ser positivo, es viento en contra"*. La lectura real del 2026-09-09 dio **−0.00000573/h ≈ −5.0 % anualizado**: los largos **cobran**.

**No se codifica ninguna creencia sobre el signo del funding.** Se lee del venue en cada escaneo, se guarda en el journal con su signo, y se modela hora a hora en el backtest. Mañana puede invertirse; el sistema no debe notarlo como una sorpresa.

## Gate antes de dinero real

```
paridad TradingView ✓ → backtest histórico ✓ → OOS ✓ → PAPER forward ✓ → SHADOW ✓ → TINY
```

Ninguno se salta. `LIVE_EXECUTION=false` hasta el final.

## Riesgo planificado, nunca pérdida máxima

Medido en PAPER: un hueco por debajo del stop produjo **−4.03R**. Consecuencias ya implementadas:

1. La UI dice **"riesgo planificado"**. Nunca "pérdida máxima".
2. `Config.max_notional_usd` y `max_leverage_used` acotan la exposición **independientemente del stop**, porque un stop no limita matemáticamente una pérdida durante un hueco. Con $200 de nocional, un hueco del 10 % cuesta $20 — sin tope, el mismo hueco sobre un stop muy ajustado costaría $30 o más para el mismo $1 de riesgo planificado.
3. El tope recorta la **cantidad**, no rechaza el trade: bajar el tamaño reduce el riesgo, así que no viola la regla de no aumentarlo nunca.

## Qué falta antes de poder afirmar nada

Este repo **todavía no ha medido si la estrategia gana**. Lo que existe es mecánica verificada. Pendiente: paridad Pine↔Python sobre señales históricas, descarga y validación del histórico, backtester v2 realista, y la matriz con OOS separado.
