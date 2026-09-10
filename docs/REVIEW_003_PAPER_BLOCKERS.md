# Review 003 — pruebas independientes antes de PAPER end-to-end

2026-09-09. Revisor: Codex. Base inspeccionada: `addf80a`, incluyendo scanner y guarda de protección. Claude conserva propiedad de los módulos del backend; este review no los modifica. Los resultados corresponden a esta instantánea, no a correcciones posteriores.

Prioridad confirmada por Mark: circuito PAPER esta noche, fee taker **4.5 bps/lado como ASSUMPTION**, sin AWS, sin órdenes desde TradingView, `LIVE_EXECUTION=false`. No esperar al backtest histórico para comenzar el forward logger. No afirmar rentabilidad.

## Reproducciones disponibles para Claude

Archivo independiente: `backend/review_tests/test_review_003.py`. Sin llamadas de red ni órdenes; el adaptador usa `httpx.MockTransport`.

Desde `backend`, con el intérprete que ya tiene httpx instalado:

```powershell
& 'C:/Users/Mark/AppData/Local/Programs/Python/Python312/python.exe' -B -m unittest discover -s review_tests -v
```

Corrida original de 19 pruebas en `addf80a`: **13 fallos de aserción, 6 pruebas aprobadas**. Los fallos son intencionalmente visibles y afirman el comportamiento requerido; no están ocultos con `expectedFailure` ni significan que el test se haya aprobado. La suite normal se ejecuta aparte con `discover -s tests -t .`: **25/25 aprobadas**.

Se añadió una prueba número 20 para el feed 5m retrasado incluso con reloj común. Claude está corrigiendo estos módulos; ver la actualización al final antes de interpretar los hallazgos como estado actual.

## R03-01 · P1 · Contexto HTF posterior a la señal 5m

**Fallo:** `HyperliquidData.multi_timeframe(now_ms=None)` deja que cada consulta capture su propio reloj. Si 5m se consulta antes de las 04:00 y 1H/4H después, se mezclan cierres de 03:55 y 04:00. `engine.scan()` puede usar la nueva hora para decidir sobre una vela anterior.

**Pruebas:** `test_fetches_cannot_leak_later_htf_into_earlier_5m` simula el cambio de hora con transporte local. Se recibe 1H posterior al cierre efectivo de 5m. `test_common_clock_also_respects_lagging_5m_feed` reproduce el problema restante si 5m viene retrasado aunque las tres consultas compartan reloj.

**Cambio mínimo propuesto:** fijar un único `as_of` al inicio y filtrar 1H/4H adicionalmente por el cierre de la última 5m realmente disponible. Considerar retrasos del venue incluso con reloj común. No completar una ventana faltante con datos posteriores a la señal.

## R03-02 · P1 · Clearance todavía aprueba historia incompleta

**Fallo:** el cambio anterior introdujo cobertura, pero acepta el 80% de la sesión previa y solo cinco barras 1H, aunque el lookback declarado sea 48. No se puede saber si el high faltante bloqueaba el trade.

**Pruebas:** con día previo completo, high 100.5 bloquea entry 100 / stop 99 / target 101.6. Al borrar únicamente esa vela, las otras 287 bastan para que el gate apruebe. Otra prueba demuestra que cinco barras 1H pasan una configuración de 48 horas.

**Cambio mínimo propuesto:** validar cobertura exacta, continuidad, unicidad y alineación de todas las ventanas necesarias, incluyendo el contexto del pivote. Datos insuficientes ⇒ rechazo explícito. No relajar el preview Pine a 80% para conseguir una paridad ficticia.

## R03-03 · P1 · Sizing acepta colateral cero y stop en el lado incorrecto

**Fallo:** `if available_collateral_usd > 0` omite la validación precisamente cuando no hay fondos; `abs(entry-stop)` acepta LONG con stop encima de entrada y SHORT con stop debajo. `tick_size` está declarado pero no se aplica.

**Pruebas:** `test_zero_collateral_rejects` y `test_long_stop_above_entry_rejects`. La prueba de una posición TINY válida sí pasa y respeta el presupuesto planificado.

**Cambio mínimo propuesto:** cero colateral es insuficiente; ausencia de dato debe representarse explícitamente y no autorizar una entrada. En PAPER aportar saldo simulado explícito. Validar `stop < entry` para LONG y `stop > entry` para SHORT, números finitos y positivos. Validar precios y cantidad representables por el venue antes de permitir ENTER.

## R03-04 · P1 · El kill switch manual se desactiva a medianoche

**Fallo:** `DayState.rollover_if_needed()` reinicializa el estado y conserva critical/consecutive_losses, pero no `manual_kill`.

**Prueba:** `test_manual_kill_survives_midnight` comienza bloqueado, cruza 00:00 UTC y termina desbloqueado.

**Cambio mínimo propuesto:** preservar el bloqueo manual hasta una acción explícita del usuario; persistirlo también al reiniciar el proceso. El rollover solo reinicia contadores diarios, no autorizaciones operativas.

## R03-05 · P1 · Protecciones con datos desconocidos se consideran verificadas

**Fallo:** la ampliación de `_matches()` comprueba instrumento/lado solo si esos campos vienen informados; valores vacíos pasan. Además `abs(NaN - expected) > tolerance` es falso y permite un trigger SL no numérico.

**Pruebas:** `test_missing_exchange_instrument_or_side_is_not_verified` y `test_nan_stop_trigger_is_not_verified`. La protección SHORT completa y válida sí pasa.

**Cambio mínimo propuesto:** requerir instrumento, lado de cierre, cantidad positiva, trigger finito, reduce-only y datos de la posición completos; campos desconocidos no equivalen a coincidencia. SL no verificable ⇒ CRITICAL. Los datos deben provenir de la consulta al venue/simulador, no completarse silenciosamente con los valores esperados.

## R03-06 · P1 · Entrada aprobada después de atravesar el stop estructural

**Fallo:** `revalidate()` solo mide drift adverso y confía en un sizing anterior y `signal.cost_r`. LONG entry 100 / stop 99, cotización actual 98: drift adverso se recorta a cero y el gate aprueba.

**Prueba:** `test_favorable_drift_past_structural_stop_rejects_entry`.

**Cambio mínimo propuesto:** comprobar niveles contra precio ejecutable actual, rechazo si la estructura quedó invalidada, y recalcular sizing/costos/clearance con datos actuales. Mantener stop estructural; no moverlo para rescatar la entrada. La definición de drift debe coincidir en backtest/paper/guardas, sin excepciones no declaradas.

## R03-07 · P1 · NaN no provoca rechazo en fronteras de datos/riesgo

**Fallo:** el adaptador convierte `"NaN"` a float sin validarlo; `can_open_new_trade(data_age_seconds=NaN)` pasa porque las comparaciones no detectan el valor.

**Pruebas:** `test_nonfinite_closed_candle_rejects` y `test_nan_data_age_rejects`.

**Cambio mínimo propuesto:** validar finitud y rangos al leer OHLCV/cotizaciones/funding y al entrar en riesgo/protección; rechazar edades negativas/no finitas. Propagar motivo de datos inválidos al journal y kill switch, sin defaults que aparenten frescura.

## R03-08 · P1 · Scanner presenta una señal expirada como ejecutable

**Fallo:** el scanner verifica frescura de datos con un umbral de 420 segundos, pero no el TTL de señal de 90 segundos. Una candidata con 91 segundos queda `executable=true`. Frescura del feed y vigencia de la señal son condiciones distintas.

**Prueba:** `test_expired_candidate_cannot_be_marked_executable`, con saldo PAPER explícito y candidato aislado mediante mock.

**Cambio mínimo propuesto:** comprobar edad desde cierre de confirmación contra `signal_ttl_seconds`, tanto al construir la tarjeta como nuevamente al pulsar ENTER; usar el mismo reloj de evaluación. Expirada implica bloqueo con motivo visible.

## R03-09 · P2 · Errores de datos desaparecen del forward journal

**Fallo:** `Scanner.run()` captura errores de mercado/frescura y los imprime, pero no escribe el evento en el logger persistente. El historial omite precisamente intervalos donde no se pudo evaluar.

**Prueba:** `test_data_failure_is_written_to_journal` fuerza un error del adaptador sin red y comprueba el registro duradero.

**Cambio mínimo propuesto:** registrar también evaluaciones fallidas con timestamp, fuente, motivo y ejecución bloqueada; no inventar precios ni señal. Conservar el contador de errores y permitir distinguir ausencia de setup de ausencia de datos.

## Comprobaciones positivas y pendientes de integración

Las seis pruebas positivas verifican: exclusión de vela en curso; rechazo del segundo trade diario; time stop exacto por tiempo transcurrido; sizing TINY válido ≤ $1 planificado; protección SHORT completa; y `LIVE_EXECUTION=false` bloqueando permiso de órdenes en los cinco modos. No hay un adaptador de escritura integrado que permita probar todavía el bloqueo end-to-end.

El scanner ya registra `fee_taker_confirmed=false`. Completar su presentación en tarjeta/journal como tasa configurable 0.00045 con etiqueta ASSUMPTION. El valor por defecto no puede presentarse como fee real de Mark. Verificar además el horizonte de funding esperado: el default de cero horas deja ese costo fuera del gate.

El adaptador expone `6 - szDecimals` como decimales, pero eso no basta para validar precios: Hyperliquid limita además a **5 cifras significativas**, con excepción de precios enteros. La [documentación oficial](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size) distingue ambos límites. Por ejemplo, con szDecimals=2, 83.1234 tiene cuatro decimales pero seis cifras significativas. Falta validar esta propiedad al integrar sizing/órdenes.

La paridad Pine/Python sigue pendiente de compilar y ejecutar Pine en TradingView. El preview usa HTF confirmados con desfase conservador documentado; no se certifica paridad por inspección de texto.

## Coordinación

Claude: corregir sus módulos, ejecutar estas pruebas sin rebajar sus requisitos y reportar el commit. Codex: verificar el diff, las reproducciones y la integración; añadir casos de review en este directorio sin editar archivos de Claude. Continuar forward logger y trabajo PAPER independiente mientras se corrigen los gates.

## Revisión de correcciones en curso sobre `6dc3200`

El diff de Claude ya preserva `manual_kill`, rechaza OHLC no finito y edad NaN, valida el lado del stop y fija un reloj común. Las pruebas específicas de esos casos pasaron al releer. Todavía hace falta recortar HTF al último cierre 5m realmente recibido: la nueva prueba de feed retrasado falla.

El nuevo `requires_collateral_check=False` deja el chequeo apagado por defecto incluso si el saldo explícito es cero. No cierra R03-03: PAPER necesita saldo simulado explícito para probar restricciones. Si se permite un modo de investigación sin cuenta, debe ser explícito y sus resultados no autorizar ENTER.

Durante una corrida con archivos en edición, `order_guard.py` utilizaba `math.isfinite` sin `import math`, provocando cuatro errores. Esto es un problema de ejecución, no una prueba de rechazo correcto. Añadir el import y volver a probar las rutas positivas y negativas. No marcar R03-05/06 corregidos por el mero hecho de que PAPER capture esa excepción y rechace todas las entradas.

Los diffs de TTL y registro de errores del scanner también están en curso; requieren una nueva corrida cuando termine la edición. Ver REVIEW_004 para los fallos del motor PAPER recién incorporado.

**Revalidación posterior de esos cambios:** desaparecieron los cuatro errores de importación; pasan protección SHORT válida, rechazos de datos incompletos/NaN, precio atravesando SL, TTL del scanner y registro del fallo de datos. REVIEW_003 queda en **20 pruebas: 17 aprobadas y 3 fallos**, correspondientes a feed 5m retrasado, high faltante en sesión previa y colateral cero. El caso de cinco barras 1H ahora se rechaza. Esto valida los casos ejecutados, no toda combinación posible de datos/riesgo.

En la misma pasada, REVIEW_004 da **14 pruebas: 3 aprobadas y 11 fallos**. Total independiente: **34 pruebas, 20 aprobadas y 14 fallos**, sin errores de ejecución.

**Corte verificado: 2026-09-09 20:26:35 America/Bogota (2026-09-10 01:26:35 UTC).** Se ejecutaron ambas suites en un mismo proceso: suite de Claude ampliada a **45/45 aprobadas**; independiente **20/34 aprobadas, 14 fallos, cero errores y cero skips**. Se comparó el SHA-256 agregado de fuentes y tests antes/después: los archivos no cambiaron durante esa corrida. Digest de la instantánea: `47c5c898064ddfd6417b0b5ac6b2e88f0a7fc36de60429c17d1d4cfe4d280449`. No extrapolar este resultado a cambios posteriores.
