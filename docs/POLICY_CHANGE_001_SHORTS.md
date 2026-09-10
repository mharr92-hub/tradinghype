# POLICY CHANGE 001 — Reapertura de la rama SHORT en HYPE

**Fecha:** 2026-09-09 · **Autor de la decisión:** Mark · **Estado:** ACTIVO

## Qué cambia

`HYPE_LONG_RESEARCH_PLAN.md` §0 y `MULTI_STRATEGY_MASTER_PLAN` §0 declaraban:

> **HYPE es LONG ONLY. Nunca short.** [...] Reabrir eso sería un cambio de política explícito de Mark, nunca un default heredado de un documento externo.

El `PRD_HYPE_COPILOT.md` v2.0 §2.1 ejerce exactamente esa cláusula:

> Los shorts existen nuevamente por decisión explícita del usuario.

Este documento registra el cambio para que quede trazable y no parezca un default heredado.

## Qué NO cambia

1. **La preferencia estructural sigue siendo LONG.** "70/30" no es una cuota que el sistema deba cumplir; significa que el SHORT es estructuralmente más difícil de disparar. El sistema nunca fuerza un short para equilibrar una proporción.
2. **El SHORT no hereda evidencia del LONG.** H-R1 (la hipótesis del primer retest VWAP/FVG) está **UNPROVEN** en nuestros datos incluso para LONG. La rama corta empieza en el mismo punto: sin evidencia.
3. **Los gates de promoción son idénticos y se aplican por separado.** `IDEA → RESEARCH → IS → OOS → PAPER → SHADOW → TINY → PROD`, con métricas propias de la rama SHORT. Un LONG promovido no promueve el SHORT.
4. **Los controles de `HYPE_LONG_RESEARCH_PLAN §5` son obligatorios para SHORT**, con el control de drift invertido: el SHORT debe batir "estar corto siempre que el régimen 4H esté bajista", no solo a buy & hold.

## Cómo está implementado el gate

Tres cerrojos independientes, todos en `backend/app/`:

| Cerrojo | Dónde | Efecto |
|---|---|---|
| `Config.allow_short` |  `strategies/hype/common.py` | Con `False`, `scan()` ni siquiera evalúa gaps bajistas. Es el default del `Config()` desnudo. |
| `HYPE_ALLOW_SHORT` | `core/config.py` (env) | Controla el `Config` que usa la app. |
| `LIVE_EXECUTION` | `core/config.py` (env) | **Interruptor maestro, `false` por defecto en el código, no solo en `.env`.** Ninguna dirección — larga ni corta — puede enviar una orden real mientras esté apagado. |

El tercero es el importante: permite recoger evidencia de shorts en RESEARCH/PAPER durante semanas **sin** que ninguna orden real sea posible. Encenderlo exige editar el entorno a mano *y* haber superado SHADOW, que es precisamente el acto explícito que la política pide.

## Umbrales diferenciales del SHORT

Heredados del PRD §4 y §6, todos más estrictos que su equivalente LONG:

| Parámetro | LONG | SHORT |
|---|---|---|
| Filtro HTF extra | — | `RSI(14) 4H ≤ 45` |
| Filtro MTF extra | — | MACD hist 1H `< 0` y descendiendo |
| Banda RSI 5m | 50–68 subiendo | 32–48 descendiendo |
| Volumen | `≥ 1.00 ×` media 20 | `≥ 1.20 ×` media 20 |
| Dirección VWAP | no exigida en base | **descendente, obligatoria** |
| Cost gate | `≤ 0.15R` | `≤ 0.12R` |

## Kill criteria específicos de la rama SHORT

Cualquiera mata la rama; se documenta en `rejected` y no se reintenta sin evidencia nueva:

- PF neto OOS `< 1.2`
- No bate entradas aleatorias emparejadas con `p < 0.05`
- No bate el drift del régimen bajista
- Depende de menos de 5 trades (leave-k-out)
- `N < 60` señales totales, o frecuencia `< 1 señal/semana` con `N` proyectado `< 60` en 6 meses
- `> 50 %` de las señales rechazadas por el cost gate

## Reversión

Para volver a LONG ONLY basta con `HYPE_ALLOW_SHORT=false`. El módulo long-only original
(`backend/app/strategies/hype_long/rules.py`) se conserva **congelado**, con su test que verifica por
inspección de código fuente que no existe camino a SHORT. Ese módulo **no** se ha tocado
en este cambio, y sigue siendo la referencia si se decide revertir.
