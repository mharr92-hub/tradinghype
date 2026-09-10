# Review 004 — PAPER, fills y funding

2026-09-09. Codex revisa `6dc3200`. Son módulos recién añadidos por Claude, todavía en integración. No se modificaron sus archivos. Este informe amplía REVIEW_003; no invalida correcciones posteriores.

Reproducción desde `backend`:

```powershell
& 'C:/Users/Mark/AppData/Local/Programs/Python/Python312/python.exe' -B -m unittest discover -s review_tests -p test_review_004_paper.py -v
```

Resultado inicial: **13 pruebas, 11 fallos de aserción y 2 aprobadas**. Tras añadir una prueba positiva de entrada válida y revisar los ajustes de guardas de Claude: **14 pruebas, 11 fallos y 3 aprobadas**, sin errores. No hay red ni órdenes reales. Cada prueba expresa el comportamiento requerido; un fallo sigue siendo un pendiente, no un test aprobado. Los rechazos por excepción de protección no cuentan como validación de límite diario/riesgo: las pruebas también detectan ese falso positivo.

## R04-01 · P1 · PAPER realiza P&L al terminar los datos y puede contarlo dos veces

**Fallo:** `PaperBroker.resolve()` marca `closed=True`, registra resultado y pasa el día a DONE incluso cuando `resolve_exit()` devuelve `open_at_end`. Al repetir `resolve()` sobre una posición cerrada, vuelve a sumar su P&L. La prueba con stop pasa de -1.09 a -2.18 sin otro trade.

**Cambio mínimo:** `open_at_end` conserva posición activa y P&L no realizado. Una salida real simulada se registra una sola vez con identidad persistente y transacción; repetir la evaluación devuelve el mismo estado sin otra contabilización. En backtest, distinguir valoración al final de muestra de trade completado.

**Pruebas:** `test_end_of_available_data_is_not_realized_exit`, `test_resolving_closed_position_cannot_book_pnl_twice`.

## R04-02 · P1 · Se ignora el stop de la vela de entrada

**Fallo:** la entrada se asigna a `signal_bar_index + 1`, pero la búsqueda de salidas empieza en `entry_index + 1`. Toda la vela de entrada queda fuera. Con delay cero, entrada 100 y mínimo 98 en esa vela, SL 99 nunca se evalúa.

**Cambio mínimo:** evaluar desde el fill, incluida su vela. Si solo se dispone de OHLC y el delay es intrabar, declarar la incertidumbre y aplicar una política conservadora compartida por PAPER/backtest. No usar extremos anteriores al fill como si su secuencia fuera conocida. Datos de menor intervalo pueden resolver esa ambigüedad posteriormente.

**Prueba:** `test_entry_bar_stop_cannot_be_ignored` usa delay cero para aislar el fallo sin ambigüedad temporal.

## R04-03 · P1 · Time stop termina a 24 h y 5 minutos

**Fallo:** el recorrido de 288 barras después de la barra de entrada devuelve una tenencia de `24.083333` horas con datos continuos. Con huecos, contar barras amplía aún más la duración.

**Cambio mínimo:** fijar deadline `fill_ts + 24h` y activar cierre al vencer. Calcular timestamps y tenencia con la misma convención. Si no hay observación ejecutable al deadline, registrar el retraso y usar la siguiente disponible; no inventar un fill anterior. El helper `time_stop_due()` correcto no basta si el resolver no lo aplica.

**Prueba:** `test_time_stop_does_not_hold_24h_plus_one_bar`.

## R04-04 · P1 · TTL sin aplicar y fill fechado antes de su observación

**Fallo:** `FillModel.ttl_seconds` no se consulta. Un delay de 91 segundos aún llena. Si falta la vela siguiente y la primera observación llega diez minutos después, se usa ese precio pero se fecha el fill tres segundos después de la señal. El drift se mide antes de añadir spread/slippage: 0.09R se convierte en aproximadamente 0.110018R y se acepta.

**Cambio mínimo:** usar timestamp real de la observación, validar disponibilidad posterior a la señal y expiración antes del fill. Compartir TTL/drift con Config para evitar dos parámetros independientes. Evaluar el precio ejecutable estimado al aplicar el límite de 0.10R. Un fill posterior real que supere la estimación debe registrarse como incidente; no borrarse del historial.

**Pruebas:** `test_delay_beyond_ttl_cannot_fill`, `test_missing_next_bar_cannot_backdate_later_observation`, `test_drift_limit_includes_execution_degradation`.

## R04-05 · P1 · ENTER evita el límite diario y conserva size previo al fill

**Fallo:** la entrada pública del broker comprueba únicamente `sizing.ok`; acepta abrir el segundo trade del día. Con presupuesto $1 y fill dentro de 0.10R, conserva la cantidad original y la pérdida al stop más solo fees llega a **$1.03805**, antes de incluir costo de salida adicional/funding.

**Cambio mínimo:** revalidar ENTER usando estado persistido y actualizado, límites/posición/edad/drift/costos/clearance. Dimensionar antes de llenar con el peor precio permitido por la orden y costos previstos; en PAPER usar ese mismo contrato. No mover el stop ni ocultar fills ya ocurridos para encajar el presupuesto. Una vez confirmado el fill, reconciliar el riesgo y TP efectivos y manejar explícitamente cualquier exceso. El frontend no sustituye esta validación.

**Pruebas:** `test_enter_rechecks_daily_limit`, `test_post_fill_risk_stays_inside_budget`.

## R04-06 · P2 · Fee de salida calculada sobre notional de entrada

**Fallo:** `2 * fee * entry_notional` ignora que la salida tiene otro precio. Para 1 HYPE, entrada 100 y salida 99, se carga 0.09 en vez de 0.08955 con la tasa supuesta. También falta aplicar explícitamente spread/slippage adverso en salidas SL/TP; el parámetro `model` solo degrada time stop/open-at-end.

**Cambio mínimo:** sumar fee de cada fill sobre su propio precio/cantidad y tasa configurada. Aplicar política de costo por tipo de salida sin duplicar costos ya incluidos en precios. Mantener **4.5 bps/lado ASSUMPTION** hasta consultar el tier; los [fees oficiales](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) dependen de la cuenta.

**Prueba:** `test_exit_fee_uses_exit_notional`. El pendiente de degradación SL/TP se observó en el diff; esta prueba no lo cubre.

## R04-07 · P1 · Funding con huecos se considera histórico completo

**Fallo:** `rate_at()` prolonga indefinidamente el último punto. Una liquidación faltante usa la tasa anterior sin fallback explícito ni `used_fallback=True`. PAPER además omite funding si `funding=None` y usa notional de entrada constante cuando sí hay curva.

**Cambio mínimo:** exigir cobertura de cada liquidación atravesada y registrar explícitamente cualquier aproximación autorizada. La [documentación oficial de funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) establece pago horario y cálculo `cantidad × precio oracle × tasa`. El signo actual LONG/SHORT es correcto, pero notional fijo de entrada no acredita costo real. Si faltan oracle/rates, conservar etiqueta de estimación/no disponible y evitar presentar neto definitivo. Definir una sola convención de timestamp de salida: sumar una vela indiscriminadamente puede cambiar los cortes de funding.

**Prueba:** `test_missing_settlement_does_not_silently_reuse_old_rate`. Valoración oracle y timestamps exactos de salida requieren pruebas de integración adicionales.

## Qué sí pasó y siguiente paso

Pasan la prioridad conservadora de SL cuando una vela posterior toca ambos niveles, el signo opuesto del funding LONG/SHORT y la entrada PAPER válida. No acreditan el resto del circuito.

Claude mantiene propiedad de `paper.py`, `fills.py` y `funding.py`. Codex mantiene este informe y `backend/review_tests/test_review_004_paper.py`. Corregir primero R04-01/02/03/05 y los gates pendientes de REVIEW_003, después completar persistencia y la prueba de ENTER → posición activa → salida → journal → reinicio. Mantener `LIVE_EXECUTION=false` y TradingView visual.
