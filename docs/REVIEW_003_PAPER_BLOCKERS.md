# Review 003 — pruebas independientes antes de PAPER end-to-end

2026-09-09. Revisor: Codex. Base inspeccionada: `f8b2e69`, más el cambio en curso de `execution/order_guard.py`. Claude conserva propiedad de los módulos del backend; este review no los modifica. Los resultados corresponden a esta instantánea, no a correcciones posteriores.

Prioridad confirmada por Mark: circuito PAPER esta noche, fee taker **4.5 bps/lado como ASSUMPTION**, sin AWS, sin órdenes desde TradingView, `LIVE_EXECUTION=false`. No esperar al backtest histórico para comenzar el forward logger. No afirmar rentabilidad.

## Reproducciones disponibles para Claude

Archivo independiente: `backend/review_tests/test_review_003.py`. Sin llamadas de red ni órdenes; el adaptador usa `httpx.MockTransport`.

Desde `backend`, con el intérprete que ya tiene httpx instalado:

```powershell
& 'C:/Users/Mark/AppData/Local/Programs/Python/Python312/python.exe' -B -m unittest discover -s review_tests -v
```

Primera corrida de las 17 pruebas: **11 fallos de aserción, 6 pruebas aprobadas**. Los fallos son intencionalmente visibles y afirman el comportamiento requerido; no están ocultos con `expectedFailure` ni significan que el test se haya aprobado. La suite normal se ejecuta aparte con `discover -s tests -t .`.

## R03-01 · P1 · Contexto HTF posterior a la señal 5m

**Fallo:** `HyperliquidData.multi_timeframe(now_ms=None)` deja que cada consulta capture su propio reloj. Si 5m se consulta antes de las 04:00 y 1H/4H después, se mezclan cierres de 03:55 y 04:00. `engine.scan()` puede usar la nueva hora para decidir sobre una vela anterior.

**Prueba:** `test_fetches_cannot_leak_later_htf_into_earlier_5m` simula el cambio de hora con transporte local. Se recibe 1H posterior al cierre efectivo de 5m.

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

## Comprobaciones positivas y pendientes de integración

Las seis pruebas positivas verifican: exclusión de vela en curso; rechazo del segundo trade diario; time stop exacto por tiempo transcurrido; sizing TINY válido ≤ $1 planificado; protección SHORT completa; y `LIVE_EXECUTION=false` bloqueando permiso de órdenes en los cinco modos. No hay un adaptador de escritura integrado que permita probar todavía el bloqueo end-to-end.

Revisar también la integración de fee provenance: tasa configurable 0.00045 con etiqueta ASSUMPTION, tier no confirmado, visible en tarjeta/journal. El valor por defecto no puede presentarse como fee real de Mark.

El adaptador expone `6 - szDecimals` como decimales, pero eso no basta para validar precios: Hyperliquid limita además a **5 cifras significativas**, con excepción de precios enteros. La [documentación oficial](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size) distingue ambos límites. Por ejemplo, con szDecimals=2, 83.1234 tiene cuatro decimales pero seis cifras significativas. Falta validar esta propiedad al integrar sizing/órdenes.

La paridad Pine/Python sigue pendiente de compilar y ejecutar Pine en TradingView. El preview usa HTF confirmados con desfase conservador documentado; no se certifica paridad por inspección de texto.

## Coordinación

Claude: corregir sus módulos, ejecutar estas pruebas sin rebajar sus requisitos y reportar el commit. Codex: verificar el diff, las reproducciones y la integración; añadir casos de review en este directorio sin editar archivos de Claude. Continuar forward logger y trabajo PAPER independiente mientras se corrigen los gates.
