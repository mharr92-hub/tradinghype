# HYPE Copilot — indicador visual preliminar

Archivo: `hype_copilot_preview.pine` (Pine Script v6). Es un entregable separado del `hype_copilot_tv_v1.pine` previsto en la migración, para no interferir con su implementación. No cambia el backend.

Estado: revisión estática local realizada; **compilación en TradingView, reproducción Bar Replay y paridad con Python pendientes**. No se presenta como indicador v1 validado ni como señal ejecutable A+.

## Montaje

1. Abrir el gráfico del perpetual HYPE del proveedor elegido, con velas estándar de **5 minutos**. Confirmar el mercado/proveedor; el indicador solo comprueba que el ticker contenga HYPE. Un proxy de otro exchange no es el feed nativo de Hyperliquid.
2. Abrir Pine Editor, crear un indicador vacío y reemplazar su contenido por el archivo completo.
3. Guardar y pulsar **Add to chart / Añadir al gráfico**. Si el compilador informa un error, no dar por validado el archivo: registrar el mensaje y su línea.
4. Cargar al menos tres días de velas 5m continuas para la cobertura local, además del historial HTF de warmup. El panel muestra historial insuficiente mientras no se cubran las ventanas necesarias.
5. Para notificaciones simples: crear alerta "HYPE candidato visual LONG" o "SHORT", con frecuencia **una vez al cierre de la vela**. Para el JSON dinámico, dejar activado el input de alerta JSON y elegir **Any alert() function call / Cualquier llamada a alert()**. Este último incluye ambos lados en una sola alerta y fija la frecuencia al cierre desde el código. No configurar ambas alternativas hacia el mismo receptor salvo que elimine duplicados. Tras editar el script o sus inputs, recrear las alertas si se desea que usen la nueva versión.

## Webhook del candidato

La alerta dinámica utiliza `hype.candidate.v1`: envía `signal_id`, símbolo/proveedor, lado, cierre de señal, vencimiento a 90 s, referencia de entrada, stop estructural, RR y target de referencia. Siempre incluye `execution_authorized:false`; no contiene qty, claves ni instrucciones para enviar una orden. La primera conexión debe ser a un receptor de pruebas. **No se desplegó ni conectó un receptor en esta entrega.**

El receptor debe autenticar la petición, tratar todos sus campos como datos no confiables, deduplicar `signal_id`, comprobar vencimiento contra su reloj, revalidar con datos nativos y comprobar tamaño, precio, posición, límite diario y protecciones. No debe aceptar la autorización de órdenes desde un campo del JSON, aunque alguien lo cambie a `true`.

**Binance directo:** este JSON NO es el formato de Webhook Signal Trading de Binance. Su configuración genera una URL y una plantilla específicas de cuenta/señal; debe usarse esa plantilla si se elige ese destino, sin inventar credenciales ni mezclarla con nuestro contrato. La [guía oficial de Binance](https://www.binance.com/en/support/faq/detail/3f57291b56474f5e900cc4b754f61ff3), actualizada el 2026-02-05, admite alertas e indica configuración manual de TP/SL. Una integración directa de entrada no acredita gestión automática del stop estructural, expiración, límites ni time stop de nuestro PRD. Elegir Binance también exige validar su instrumento, feed, fees, funding y reglas de sizing; no hereda la validación de Hyperliquid.

## Qué muestra

- EMA20/50 4H y 1H cerradas, VWAP con reset UTC y banda SMA(TR,20).
- FVG alcista/bajista, primer toque, confirmación en toque o dos velas siguientes; consumo definitivo del gap.
- LONG F1 o F2; SHORT con RSI 4H, MACD 1H, RSI/MACD/volumen/VWAP 5m obligatorios.
- Clearance visual contra pivotes 1H n=2 confirmados y high/low de la sesión UTC previa completa.
- Candidato técnico, referencia de entrada, stop estructural, líneas 1R/1.6R/2R. RSI, MACD y volumen relativo en la ventana de datos.

## Límites que deben mantenerse visibles

- **CANDIDATO VISUAL no equivale a A_PLUS_READY.** No consulta fondos, posiciones, límites diarios, sizing del venue, funding ni fees reales. Tampoco verifica órdenes TP/SL. No modela fills ni produce métricas de backtest.
- Se conservan todos los candidatos para inspección; no se limita a una señal diaria. El máximo de un **trade** al día se controla en la app, que conoce fills y estado persistente.
- La expiración de 90 s, drift de 0.10R y máximo hold de 24 h pertenecen a la app. La edad mostrada corresponde al cierre del último candidato; una marca histórica no es una oferta vigente.
- Los HTF usan valores `[1]` con `lookahead_on`, el patrón documentado para datos HTF confirmados. Al evaluar en cierre de 5m, esto introduce un retraso conservador de una vela 5m en el uso de un HTF recién cerrado respecto al scanner Python. **No hay paridad exacta certificada.** Corregir y probar la sincronización antes de convertir este preview en v1.
- Pine y Python pueden diferir por proveedor, historial inicial, semillas de indicadores y huecos. No debe copiarse al Pine el fail-open de clearance detectado en Python para "hacerlos coincidir".
- TradingView conserva un número limitado de objetos gráficos: se configuran 150 boxes, 300 líneas y 100 labels; las marcas plotshape no usan esa cuota. No es un journal persistente.
- No se habilitó ejecución real. `LIVE_EXECUTION=false` sigue siendo obligatorio.

Validación pendiente: compilar; comprobar SHORT condición por condición; Bar Replay antes/después de cada confirmación; pivotes disponibles solo tras dos barras; falta de datos bloquea; comparar timestamps y niveles con fixtures comunes de Python sobre la misma fuente; probar transiciones de sesión y alertas en tiempo real. Un gráfico visualmente razonable no sustituye estas comprobaciones.

Referencias oficiales: [HTF y repainting](https://www.tradingview.com/pine-script-docs/concepts/repainting/), [alertas y snapshot del script](https://www.tradingview.com/pine-script-docs/concepts/alerts/).
