# HYPE Copilot — decisiones de Mark y coordinación propuesta

Actualizado: 2026-09-09, tras el historial compartido por Mark. Este registro conserva las decisiones explícitas del usuario; no es una certificación del sistema ni una promoción a LIVE. La prioridad actual permite avanzar con PAPER sin esperar a cerrar las decisiones de herramientas o modos automáticos futuros.

## Decisiones confirmadas

1. El alcance incluye los tres componentes: indicador TradingView con alertas, integración con controles de riesgo y app con gestión de posiciones.
2. Único venue inicial: **Hyperliquid**. Binance queda fuera del alcance inicial.
3. Mark confirma cada entrada por defecto. Se desean también activación automática por sesión y funcionamiento automático continuo como capacidades futuras, con habilitación separada y los mismos límites. La sesión automática todavía requiere definir horario, vencimiento de autorización, reinicio y revocación.
4. **Prioridad actual: PAPER end-to-end esta noche.** ENTER simula la operación. La petición anterior de TINY con $1 máximo de pérdida planificada queda como objetivo posterior, después de cumplir los gates; no autoriza órdenes reales esta noche.
5. Se mantienen: HYPE únicamente, LONG y SHORT estricto, E2=1.6R, primer retest, clearance, costos, 1 trade máximo por día, no forzar señales ni frecuencia.
6. Tenencia máxima de **24 horas desde el fill**. Al llegar el plazo se inicia el cierre obligatorio; no se prolonga la posición esperando un mejor precio. Un fallo o retraso de ejecución debe quedar visible, no representarse como un fill inventado a tiempo.
7. Mark está dispuesto a conseguir las herramientas necesarias. Presupuesto, cuentas, dominio, servidor y acceso a Linear aún no están definidos.
8. `LIVE_EXECUTION=false` se mantiene. La solicitud de preparar una primera prueba real no sustituye las validaciones pendientes ni modifica automáticamente los gates de promoción del PRD/research plan.
9. Fees provisionales: **4.5 bps taker por lado como ASSUMPTION**, configurables, etiquetados como no confirmados. Reemplazar por el tier real cuando se consulte la cuenta; no confundir el default con datos reales de Mark.
10. **Sin gasto AWS autorizado.** Priorizar APIs públicas o proxy Bybit validado frente a Hyperliquid. Cualquier uso de S3 con costo requiere primero explicar datos adicionales y costo estimado.
11. Mark autorizó versionar en `https://github.com/mharr92-hub/tradinghype`. Antes de push: verificar remote, branch, historia remota y ausencia de secretos/archivos sensibles. No sobrescribir trabajo remoto.
12. TradingView es visualización/alerta; **no ejecuta órdenes**. SHORT puede habilitarse en RESEARCH/PAPER sin habilitar dinero real. No declarar rentabilidad por una prueba mecánica aprobada.

## Estado comprobado del workspace

HEAD al revisar: `96c384e` (precedido por `d020d67` y `f83a5ea`). Ya existe Git; los informes anteriores que indicaban ausencia de `.git` corresponden a una pasada previa.

Existe motor Python LONG/SHORT, helpers de riesgo/límites, settings y guardas; hay indicador Pine preliminar. Siguen pendientes la integración de datos, API, persistencia, app, paper completo y adaptador de órdenes. No considerar títulos de commits como evidencia de una orden real.

Se ejecutó con Python 3.12.13, desde `backend`:

```powershell
& 'C:/Users/Mark/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe' -B -m unittest discover -s tests -t . -v
```

Resultado: **25/25 OK**, con ResourceWarning heredado de un archivo de fuente abierto sin cerrar. El comando sin `-t .` falla porque no inicializa el paquete `tests`, donde se registra el alias de compatibilidad. Cambio mínimo propuesto: documentar y usar la invocación correcta en README/CI; no duplicar la estrategia para arreglar imports.

Estos tests no acreditan la ejecución completa ni cobertura de todas las condiciones SHORT, sizing del venue, fills, funding, reinicios, concurrencia y protección post-fill.

La guarda de protección inspeccionada todavía verifica presencia de TP pero no su precio, y no comprueba cantidad, lado, instrumento y reduce-only contra la posición. Debe corregirse y probarse antes de integrar órdenes reales.

## Arquitectura recomendada, conservando el PRD

- TradingView: visualización, alerta de candidato y enlace al flujo de revisión; Python sigue siendo fuente de verdad.
- Backend Python/FastAPI: datos nativos, estrategia, validación de señal, riesgo, estado persistente, ENTER/SKIP y supervisión de posición.
- Frontend Next.js: interfaz web responsive, sin aplicación móvil nativa inicial.
- SQLite local de desarrollo; PostgreSQL en el despliegue previsto.
- Un backend siempre encendido para las posiciones de hasta 24 h. No depender del navegador abierto ni de un portátil que entre en suspensión.
- Secretos del venue solo en el backend. Señal/alerta nunca contiene autorización suficiente para ejecutar por sí sola.
- Preparar y verificar PAPER/testnet antes de realizar la prueba TINY deseada. No activar automáticamente mainnet al aprobar tests unitarios.

## Reparto confirmado por Mark para Claude y Codex

**Mark confirmó este reparto en el texto compartido.** El historial muestra que Claude leyó este documento y comenzó a corregir protección. No existe un canal de mensajes directo entre las dos sesiones; coordinación por archivos y revisión de commits.

Claude: confirmar los archivos que está editando; continuar como responsable principal de la implementación existente de backend. Corregir protección, persistencia y ejecución simulada con sus pruebas antes de abrir la ruta de escritura del venue. No cambiar el PRD a Binance.

Codex: consolidar decisiones de producto, planificar dependencias y criterios de aceptación, auditar los cambios y validar tests/diffs; mantener el preview de TradingView separado de la versión final hasta compilar y certificar paridad. No editar módulos concurrentemente con Claude sin acordar propiedad.

Ambos: un responsable por archivo; commits pequeños con tests; registrar el estado real y limitaciones; no atribuir cobertura a módulos todavía ausentes. Usar la última versión de los hallazgos, ya que algunos de AUDIT_002 fueron corregidos posteriormente. Evitar `git add -A` mientras la otra sesión escribe: seleccionar explícitamente los archivos propios para no publicar una revisión a medio escribir.

Codex mantiene `backend/review_tests/test_review_003.py` y `docs/REVIEW_003_PAPER_BLOCKERS.md`. Claude corrige los módulos señalados, manteniendo las pruebas del reviewer como contrato. No reducir requisitos para volverlas verdes.

## Orden de entrega de esta noche

1. Adaptador Hyperliquid read-only.
2. Scanner continuo con velas cerradas y mismo instante de evaluación en todos los TF.
3. Forward logger, sin esperar el backtest histórico.
4. Persistencia de señales, decisiones, posición y límites.
5. PAPER engine con fills/costos/funding declarados.
6. Signal Card mínima: precio, 4H/1H, VWAP, estados LONG/SHORT y FVG, espera/toque/confirmación/ready, Entry/SL/TP1.6R/Risk/beneficio estimado/costos/edad, ENTER/SKIP.
7. TradingView visual con líneas 1R/1.6R/2R y paridad pendiente explícita.
8. Prueba PAPER end-to-end. Objetivo: verificar mecánica, no demostrar edge.

## Preguntas de producto todavía abiertas

- Cuenta/wallet Hyperliquid disponible y capital separado para TINY; otras posiciones/órdenes en esa cuenta.
- Dispositivo principal para confirmar y canal de notificación.
- Política ante fill confirmado sin SL protector verificable: cierre de emergencia automático propuesto.
- Cierre manual anticipado y alcance de edición de órdenes durante una posición; no ampliar stop/riesgo.
- Presupuesto mensual para servidor, dominio y TradingView; compras no realizadas.
- Disponibilidad de Linear, equipo/proyecto de destino y conexión para crear issues.
- Reglas completas de los modos automáticos futuros y etapas de habilitación.

No crear un backlog final a partir de respuestas inventadas. Tras cerrar la entrevista, reconciliar el PRD, preparar 20+ issues enfocados con dependencias, criterios de aceptación y pruebas, crear los issues en Linear si hay acceso y comenzar por los prerrequisitos. Actualmente no hay herramienta conectada de Linear en esta sesión.
