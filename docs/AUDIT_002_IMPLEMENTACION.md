# Auditoría 002 — implementación contra PRD v2

Fecha: 2026-09-09. Revisión local de código, documentos y pruebas sintéticas; no se enviaron órdenes ni se evaluó rentabilidad con datos de mercado. Autoridad: `PRD_HYPE_COPILOT.md` v2.0.

El workspace cambia durante la revisión: aparecieron `common.py`, `long.py`, `short.py`, `target_clearance.py` y `scoring.py`, y se restauró la sección 17 del PRD. Se inspeccionaron también esos módulos nuevos. Los hallazgos describen los archivos inspeccionados, no certifican cambios posteriores. No hay repositorio `.git`: no es posible comparar contra HEAD ni identificar autoría de los cambios externos.

## Resultado

La implementación sigue incompleta para el PRD v2. El motor LONG congelado conserva sus 16 pruebas funcionales, pero el comando documentado no consigue importarlas. El backtest v0 no sirve para validar el modelo económico nuevo. Los módulos nuevos son trabajo parcial; no constituyen todavía un motor compartido entre backtest, paper y ejecución.

Único cambio de configuración de esta revisión: añadir `LIVE_EXECUTION=false` a `.env.example`. Esto expresa el valor requerido; todavía no existe un lector de configuración ni una guarda de órdenes que lo hagan efectivo. No se modificaron arquitectura, reglas congeladas, estrategia ni backtest.

## Hallazgos y cambio mínimo propuesto

### A02-01 · P1 · PnL desconectado del tamaño calculado

**Fallo:** `backend/tools/backtest_hype_long_v0.py:218` calcula `risk_usd = equity * risk_pct` y lo multiplica por `net_r` en línea 226. La estrategia ya descontó costos y redondeó la cantidad al calcular `qty` (`hype_long/rules.py:418`). El backtest ignora esa cantidad para actualizar equity.

**Reproducción:** escenario `scenario_5m()` de los tests, equity 1000 y configuración default: cantidad 3.2; pérdida al stop con el propio modelo de costos = **2.4996896**; pérdida reportada por la fórmula del backtest = **2.7321639677**, sobre presupuesto 2.50. También se distorsionan ganancias, compounding y drawdown. El signo del error no es siempre optimista.

**Cambio mínimo propuesto:** en el backtester nuevo previsto, calcular PnL bruto con cantidad ejecutada y diferencia entre fills; restar fees, funding y costos modelados exactamente una vez. Definir explícitamente el denominador de R. No reutilizar `risk_budget * net_r` como sustituto del PnL de la posición. Mantener el v0 como referencia deprecada, identificando esta limitación.

### A02-02 · P1 · Fills imposibles y señal fechada antes de estar disponible

**Fallo:** `resolve_forward()` devuelve el precio exacto del stop aunque una vela abra y transcurra totalmente por debajo del stop LONG (`backtest_hype_long_v0.py:161`). La entrada se supone al cierre de confirmación, pero `ts_in` registra la apertura de esa vela (línea 229). No hay delay, expiración ni drift. Spread y slippage se descuentan como una fracción fija, sin cambiar el fill.

**Reproducción:** stop **50.345225**; siguiente vela OHLC **48 / 49 / 47 / 48.5**. El simulador devuelve salida a **50.345225**, fuera del rango negociado. Priorizar SL cuando una vela toca SL y TP es conservador, pero no corrige este caso.

**Cambio mínimo propuesto:** usar `research/fills.py`, ya previsto, compartido con paper; separar cierre de señal, primera observación posterior y timestamp del fill. Para gaps adversos, usar precio ejecutable y costos declarados. Rechazar entrada expirada o con drift > 0.10R; conservar stop estructural, recalcular R/TP y validar size, costos y clearance con el fill. Con datos solo de 5m no se puede afirmar validación intrabar de una ventana de 90 s sin declarar la resolución y sus límites.

### A02-03 · P1 · El límite diario y las 24 horas no se aplican

**Fallo:** `in_pos_until` evita solapamiento pero permite nuevas posiciones el mismo día; `resolve_forward(..., max_bars=None)` no tiene time stop. El bucle salta el scanner mientras hay posición abierta, perdiendo candidatos para el journal.

**Reproducción:** al inyectar candidatos válidos y salidas TP en un mismo día, `run_backtest()` registra **2 trades**. Una observación 25 horas después de la señal, sin tocar SL/TP, acaba como `open_at_end`, sin salida al cumplir 24 h.

**Cambio mínimo propuesto:** aplicar el estado diario en la ejecución simulada y real, manteniendo independiente la generación/registro de candidatos. Medir el plazo desde el fill en tiempo UTC, no solo contar 288 barras si existen huecos. Probar medianoche con posición abierta, solicitudes ENTER concurrentes, reintentos y reinicio del proceso. Si falta una observación ejecutable al vencimiento, registrar retraso; no inventar un fill a las 24 h.

### A02-04 · P1 · Contrato de datos cerrados sin validación suficiente

**Fallo:** `scan()` del motor congelado usa todas las velas HTF entregadas; el contrato delega al llamador que estén cerradas. `indicators.resample()` agrega buckets con huecos y `completed_upto()` solo comprueba su hora de cierre nominal.

**Reproducción:** trasladar los timestamps HTF del escenario alcista 100 días al futuro conserva una señal `ok`. Un bucket 1H formado con una única vela 5m se acepta como completado al llegar la hora.

**Alcance:** esto prueba ausencia de validación en esa frontera, no un lookahead demostrado en el recorrido normal del backtest. Su `completed_upto()` sí descarta buckets futuros; EMA es causal. La prueba heredada de "no señal antes de confirmación" no verifica timestamps HTF, huecos, pivotes ni invariancia al futuro.

**Cambio mínimo propuesto:** validar timestamps, orden, duplicados, continuidad, OHLCV finitos y cierre respecto a `as_of` en la frontera compartida de datos/motor v2. No rellenar huecos silenciosamente. Probar el mismo prefijo mediante los adaptadores de backtest/paper y pivotes n=2 disponibles únicamente tras cerrar las dos barras posteriores. Preservar la referencia congelada.

### A02-05 · P1 · Funding y fees aún no representan la posición real

**Fallo:** v0 omite funding. `common.cost_fraction()` usa una tasa constante por horas esperadas, por defecto cero, y recorta créditos netos a cero. Por tanto no es un ledger de funding realizado. Los fees v0 se aproximan con dos veces el nocional de entrada, aunque el nocional de salida cambia.

**Reproducción del helper nuevo:** tasa sintética 0.001/h, 24 h, SHORT: base 0.0013; costo firmado esperado **−0.0227**; helper devuelve **0**. Puede ser una cota conservadora para admisión, pero no debe alimentar el PnL como costo real. Todavía no hay consumidor implementado que permita afirmar ese error de integración.

**Cambio mínimo propuesto:** separar estimación conservadora del cost gate y contabilidad realizada; sumar eventos de funding dentro de la tenencia con signo, timestamps y base nocional documentados. Cobrar fees de cada fill según su precio, cantidad y tier. No usar créditos futuros inciertos para aumentar el size de TINY. Verificar fee tier e historial del venue antes de reportar resultados; esta auditoría no los verificó.

### A02-06 · P1 · F2 LONG permite omitir dirección VWAP

**Fallo:** en `backend/app/strategies/hype/long.py`, `momentum()` solo comprueba pendiente VWAP si `require_vwap_slope_long=True`. `Config(require_momentum_long=True)` deja ese segundo flag en `False`, incumpliendo el conjunto F2 del PRD §5.5. La fábrica `research_config('F2')` sí activa ambos, pero la configuración directa sigue admitida.

**Reproducción:** con RSI, MACD y volumen aprobados, y un mock de VWAP preparado con pendiente negativa, el helper devuelve `reason=None` y consulta VWAP **cero veces**. Esto demuestra la omisión en el helper; el orquestador v2 aún no estaba disponible al revisar.

**Cambio mínimo propuesto:** exigir dirección VWAP cuando se active F2, sin un segundo interruptor que pueda relajar la regla. Añadir regresión para la configuración directa y para la fábrica. Mantener momentum opcional únicamente para F1 LONG; SHORT conserva todos sus requisitos obligatorios.

### A02-07 · P1 · Tests y CLI rotos por imports antiguos

**Fallo:** tests línea 11 y backtest líneas 37/205 importan `hype_long_vwap_retest`, pero el archivo existe como `app/strategies/hype_long/rules.py`. El comando documentado falla antes de ejecutar las 16 pruebas.

**Cambio mínimo propuesto:** un módulo de compatibilidad que reexporte la referencia congelada, incluida `_assert_long_only`, o corregir exclusivamente los imports con trazabilidad explícita. No copiar ni reimplementar la estrategia. Actualizar la invocación de CLI según su ubicación en `backend/tools`.

### A02-08 · P1 · Gates del MVP pendientes de implementación

Al inspeccionar, solo existían piezas parciales de v2. `risk/`, `execution/`, `research/`, `api/` y `db/` contenían únicamente `__init__.py`; frontend y TradingView carecían de implementación. No había pruebas v2 ni workflows CI. Se trata de trabajo pendiente, no de pruebas aprobadas.

**Cambio mínimo propuesto:** seguir los módulos y orden ya establecidos por `MIGRATION_PLAN.md`; no rediseñar arquitectura. Antes de considerar utilizable el sistema deben existir y probarse:

- Motor único LONG/SHORT, asimetría estricta y E2=1.6R, con clearance de swings confirmados y sesión previa; rechazo de niveles bloqueantes y datos insuficientes.
- Sizing por modo: TINY ≤ $1 de pérdida planificada con costos, producción $100–150, precisión/tick, nocional mínimo/máximo y colateral; SKIP si no cabe.
- Expiración 90 s, drift 0.10R, límite diario persistente, time stop 24 h, datos frescos, pérdidas consecutivas y límite de pérdida diaria.
- Webhook de TradingView sin órdenes y revalidación nativa; journal previo al resultado, incluidas señales SKIP/bloqueadas.
- Guarda global `LIVE_EXECUTION=false` comprobada mediante mock no invocado, independiente de la dirección y el modo.
- Confirmación de fill, stop estructural invariable, TP recalculado, protección verificada contra el exchange y order IDs. SL ausente → CRITICAL. Probar también TP ausente, fills parciales, órdenes duplicadas, cantidades/lados incorrectos y protección tras reinicio.

Los números 90/0.10/24/1 dentro de `Config` no son evidencia de que estas restricciones se apliquen.

### A02-09 · P2 · Documentación que puede inducir pruebas incorrectas

**Fallos:** `POLICY_CHANGE_001_SHORTS.md` afirma que existen gates en archivos ausentes. `TEST_PLAN.md` #21 exige `2 × fee × notional`, que solo es exacto con ambos nocionales iguales. #27 exige paridad de planes completos con v0, incompatible con nuevos rechazos obligatorios de clearance/sizing si se aplica sin acotar. `AUDIT_001_CONFLICTOS.md` C-04 dice a la vez "hasta 3 ventanas" y periodicidad horaria para 24 h. Los documentos de entorno afirman que no existe Python, pero está instalado mediante uv.

**Cambio mínimo propuesto:** marcar gates como pendientes hasta su prueba; calcular fees por fill; limitar paridad al comportamiento común y escenarios válidos en ambos motores, manteniendo tests independientes de nuevos rechazos sin deshabilitar protecciones del runtime. Corregir la contradicción de ventanas a eventos efectivos de tenencia y documentar el intérprete encontrado. La paridad no debe obligar a conservar bugs del oráculo.

### A02-10 · P1 · Clearance aprueba cuando falta historial

**Fallo:** `target_clearance.prev_day_level()` no exige cobertura de la sesión previa: una sola vela basta para producir un supuesto high/low diario. Sin historial, `collect_levels()` devuelve vacío y `check()` devuelve `(True, inf, None)`. El código confunde ausencia de obstáculos comprobada con imposibilidad de comprobarlos. Un high omitido por datos incompletos puede ocultar la resistencia que debería bloquear el LONG; aplica análogamente a SHORT.

**Reproducción:** `check([], [vela_actual], 0, 'LONG', 100, 99, Config())` aprueba con infinito. Con una única vela de la sesión previa de high 91 devuelve `Level(price=91, kind='prev_day', ts=0)` sin verificar las otras 287 velas de 5m.

**Cambio mínimo propuesto:** devolver estado explícito de datos insuficientes y rechazar la oferta del trade hasta cubrir las fuentes de niveles habilitadas y el lookback declarado. Solo interpretar lista vacía como clearance libre después de validar cobertura. Probar ausencia total, sesión parcial, hueco interior y cobertura completa sin obstáculos, en ambos lados. El detector de pivotes sí espera n=2 barras posteriores por índice; se comprobó esa propiedad, quedando pendiente la validación de cierre real por timestamp.

### A02-11 · P1 · A+ puede ignorar gates fallidos

**Fallo:** `scoring.score()` omite `vwap_slope` en LONG F2. Sus listas obligatorias tampoco incluyen sizing válido, límite diario disponible ni kill switches limpios, requeridos por PRD §14 para A+. Puede emitir `is_a_plus=True` aunque esos valores sean falsos en `checks`.

**Reproducción:** ocho gates LONG en `True`, RSI/MACD/volumen en `True`, `vwap_slope=False` y verificaciones operativas fallidas: devuelve **RULE COMPLIANCE 11/11**, `is_a_plus=True`, `failed=[]`. El hecho de que scoring no envíe órdenes no hace correcta esa etiqueta para la Signal Card.

**Cambio mínimo propuesto:** incluir VWAP en F2 y validar los gates operativos antes de exponer `A_PLUS_READY`. Si se conserva un score técnico previo al riesgo, identificarlo como candidato y reservar la etiqueta A+ ejecutable para el estado completo. Seguir registrando los candidatos bloqueados para analizar el costo del límite diario, conforme al PRD §10. Verificar que la ausencia de un gate obligatorio también descalifique.

## Pruebas ejecutadas

Intérprete: `C:/Users/Mark/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe` (Python 3.12.13). Localizado con `uv --no-cache python find --offline`; no hubo instalación ni descarga.

1. Desde `backend`, `python -B -m unittest discover -s tests -v`: **ERROR de import**, `ModuleNotFoundError: hype_long_vwap_retest`. No cuenta como 16 tests fallidos ni como suite aprobada.
2. Diagnóstico sin modificar archivos: alias temporal del módulo en memoria y mismo discovery: **16/16 OK**.
3. Sondas sintéticas descritas arriba: reproducen discrepancia contable, gap de stop, falta de time stop, dos trades diarios, aceptación de HTF futuro, bucket incompleto, recorte de crédito funding y omisión VWAP en F2. No son resultados de rentabilidad ni validación end-to-end.
4. Nuevos módulos: reproducidos clearance sin historial, high diario con una única vela y A+ con gates fallidos. Cuatro aserciones adicionales pasan: pivote ausente antes de la segunda barra posterior, presente después, y clearance 1.2R menor que 1.6R en LONG y SHORT.

Repetir el diagnóstico heredado desde `backend`, usando el intérprete indicado:

```python
import sys, unittest
from app.strategies.hype_long import rules
sys.modules['hype_long_vwap_retest'] = rules
suite = unittest.defaultTestLoader.discover('tests')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
```

## Revisión de cambios y continuidad

Se comprobó que el único cambio de `.env.example` respecto a su contenido previo es `LIVE_EXECUTION=false`. Se comparó un inventario SHA-256 de los archivos inspeccionados durante esta pasada: las modificaciones propias son esa línea y este informe. No hay diff de Git disponible. Los cambios ajenos que aparecen durante la auditoría requieren su propia comprobación; no se atribuyen a esta revisión.

En cada nueva pasada: leer el PRD vigente y los cambios, explicar primero cada fallo, proponer el mínimo ajuste en los módulos existentes, reproducir con pruebas relevantes y revisar el diff final. Mantener `LIVE_EXECUTION=false`. Registrar qué se probó, qué falló y qué sigue ausente. Esta pasada no instala ni acredita un proceso automático de vigilancia en segundo plano.
