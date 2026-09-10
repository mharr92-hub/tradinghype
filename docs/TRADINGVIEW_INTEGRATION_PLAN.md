# TRADINGVIEW_INTEGRATION_PLAN.md

**Versión:** 1.0 (2026-09-09) · **Estado:** diseño. Nada de lo descrito aquí está implementado en el backend.
**Aplica bajo:** `LIVE_EXECUTION=false` · `HYPE_MODE=RESEARCH`
**Autoridad:** subordinado a `PRD_HYPE_COPILOT.md` v2.0. Donde este documento y el PRD difieran, **manda el PRD**. Donde este documento y el código Python difieran, **manda el código** (`backend/app/strategies/`).

**Documentos hermanos:** `AUDIT_001_CONFLICTOS.md` (C-05, C-13, C-14) · `HYPE_TRADING_RESEARCH_PLAN.md` (metodología) · `REVIEW_003_PAPER_BLOCKERS.md` (R03-01, R03-02) · `PLAN_PRIMERA_PRUEBA_24H.md` · `tradingview/PREVIEW_README.md` · `TEST_PLAN.md`.

---

## 0. Por qué existe este archivo — cierre explícito de `AUDIT_001` C-13

`AUDIT_001_CONFLICTOS.md` C-13 registra que este documento **se citaba como autoridad sin existir en el repo**. Tres consecuencias concretas quedaban abiertas:

| C-13 dejaba abierto | Estado tras este documento |
|---|---|
| La ventana de 90 s del webhook se atribuía a `TRADINGVIEW_INTEGRATION_PLAN §5.2`, no verificable | **Cerrado.** El TTL de 90 s es `Config.signal_ttl_seconds` y su fuente normativa es **PRD §13**, no este archivo. Este documento no lo crea: lo hereda y lo presupuesta (§6). |
| El contrato del webhook se atribuía a "PRD 11" en `.env.example:38` y en `tradingview/PREVIEW_README.md` | **Cerrado y corregido.** PRD §11 es *"Máximo 24 horas de tenencia"*. El contrato del puente se escribe contra **PRD §13** (expiración y drift), **§15** (TV es visualización + alerta), **§17** (revalidación) y **§24** (principio de seguridad). Ver §0.1. |
| El rol de TradingView quedaba definido por una sola línea del PRD | **Cerrado.** §1 de este documento. |

**Regla dura:** este archivo **no introduce ninguna regla de estrategia nueva**. Todo parámetro que aparezca aquí es una cita del código o del PRD. Si alguien encuentra aquí un número que no exista en `backend/app/strategies/hype/common.py` ni en el PRD, es un error de este documento y se corrige aquí, nunca en el motor.

**Lo que este documento NO hace:** no reabre C-13 respecto a `MULTI_STRATEGY_MASTER_PLAN.md`, `BTC_LONG_RESEARCH_PLAN.md` ni `ALT_SHORT_X_SIGNAL_RESEARCH_PLAN.md`. Esos tres siguen ausentes y `PRD_HYPE_COPILOT.md` v2.0 sigue siendo la única autoridad de producto.

### 0.1 Dos correcciones documentales que este archivo obliga a hacer

**(a) "PRD 11" es una referencia equivocada.** `.env.example:38` dice *"Webhook de TradingView. Secreto compartido para firmar el payload (PRD 11)"*. Debe decir PRD §13/§15/§17. La referencia es residuo de la numeración del PRD v1.0.

**(b) "Firmar el payload" es falso, y produce exactamente la confianza equivocada.** **TradingView no puede firmar nada.** Pine Script no tiene funciones criptográficas ni de hashing, y las alertas por webhook no admiten cabeceras HTTP personalizadas. El secreto viaja **en claro dentro del cuerpo del mensaje**, idéntico en cada envío. Es un **identificador de portador**, no una firma: no aporta integridad, ni frescura, ni resistencia a repetición.

Redacción propuesta para `.env.example`:

```
# Webhook de TradingView (PRD 13/15/17). Secreto compartido que viaja EN CLARO
# dentro del cuerpo del mensaje. NO es una firma: no aporta integridad ni
# frescura. Ver docs/TRADINGVIEW_INTEGRATION_PLAN.md seccion 5.
TRADINGVIEW_WEBHOOK_SECRET=cambiame-por-un-secreto-largo-y-aleatorio
```

---

## 1. Qué es TradingView en este sistema, y qué no es

### 1.1 Los tres roles legítimos

| Rol | Qué aporta que el backend no puede darse a sí mismo |
|---|---|
| **Visualización** | Las 12 casillas de la checklist dibujadas sobre precio, históricamente, para inspección visual (PRD §15). Mark ve el setup, no solo su resumen. |
| **Verificador cruzado** | Dos implementaciones independientes de las mismas reglas, sobre dos feeds independientes. **Su desacuerdo es el único detector barato de bugs, drift de configuración y problemas de datos que tenemos.** Es la medición del criterio 6 del MVP (PRD §25). |
| **Debugger visual / red de seguridad** | Si el scanner nativo se cae o el WS se desincroniza, una alerta de TV revela que existió un setup que el backend no vio. |

### 1.2 Lo que NO es — reglas duras

- **TradingView NO es fuente de verdad.** Python es la fuente de verdad (PRD §4). El motor de reglas es `backend/app/strategies/hype/engine.py` y **no existe ni puede existir una segunda implementación de las reglas**. El `.pine` es un *instrumento de medida*, no un actuador.
- **TradingView NO ejecuta.** Ningún campo del payload autoriza nada. **El receptor jamás lee autorización de un campo del JSON**, aunque llegue `execution_authorized: true` (eso es un incidente, no una instrucción).
- **TradingView NO es el disparador primario.** El disparador primario es el scanner nativo (`backend/app/services/scanner.py`) sobre velas de Hyperliquid. La razón es técnica, no ideológica: §6.
- **TradingView NO puede declarar `A_PLUS_READY`.** Le faltan por construcción los tres gates operativos de `scoring.OPERATIONAL_GATES` (`sizing_ok`, `daily_limit_ok`, `kill_switches_clear`) y el `cost_gate` real. Lo máximo que puede pintar es **`LONG_CANDIDATE` / `SHORT_CANDIDATE`**, con la etiqueta `RULE COMPLIANCE n/8` (LONG F1) o `n/12` (LONG F2 o SHORT).
- **El sistema debe funcionar completo con el webhook apagado.** Si apagarlo rompe algo, ese algo está mal diseñado.
- **Prohibido mostrar porcentajes de probabilidad** en el indicador, en el payload y en cualquier respuesta derivada (PRD §14). La guarda `scoring.assert_no_probability()` se aplica **también al payload entrante**, no solo a la salida hacia la UI.

### 1.3 Arquitectura

```
            +-- PRIMARIO ---------------------------------------------+
Hyperliquid | WS/REST -> candle store -> reloj de cierre 5m -> scan()  |-> riesgo -> Signal Card -> ENTER/SKIP
            +---------------------------------------------------------+            ^
                                                                                    | compara
TradingView -> POST /webhooks/tradingview/{token} -> revalidacion nativa ------------+
            +-- SECUNDARIO: verificador cruzado + red de seguridad ----+
```

**Una alerta nunca crea una señal.** Como mucho marca una señal que el scanner nativo ya produjo, o abre una fila de discrepancia si no la produjo.

---

## 2. Instalación paso a paso

Estado del artefacto: hoy el repo contiene `tradingview/hype_copilot_preview.pine`, **no certificado** (`PREVIEW_README.md`: compilación, Bar Replay y paridad con Python pendientes). El `hype_copilot_tv_v1.pine` que emite el contrato de §4 **no existe todavía** (§9). Estos pasos valen para ambos; lo que cambia es el contenido del payload.

### 2.1 Requisitos previos

| Requisito | Valor | Por qué |
|---|---|---|
| Símbolo | **`HYPERLIQUID:HYPEUSDC.P`** (verificar en pantalla, §3) | Cualquier otro feed diverge del backend de forma silenciosa |
| Temporalidad del gráfico | **5 minutos, velas estándar** | El motor se evalúa exclusivamente sobre la última vela de 5m cerrada (`engine.py`: `t = len(c5) - 1`). Heikin Ashi, Renko o Range **invalidan** el indicador entero |
| Zona horaria del gráfico | irrelevante para el cálculo | El anclaje del VWAP usa aritmética sobre `time` (epoch UTC en ms), no la sesión del exchange |
| Histórico cargado | ≥ 3 días de 5m contiguas, y el máximo de 4H/1H que sirva el plan | El régimen 4H exige ≥ 55 buckets y la semilla de EMA necesita bastante más (§8, D-05) |
| Cuenta de TradingView | plan que permita alertas por webhook | Los planes gratuitos no incluyen webhooks |
| Receptor | host público, **HTTPS puerto 443** | TradingView solo alcanza los puertos 80/443 y no resuelve `localhost` |

### 2.2 Cargar el indicador

1. Abrir el gráfico del símbolo correcto y ponerlo en **5m, velas estándar**.
2. **Pine Editor** → **Open** → **New indicator**.
3. Borrar la plantilla y pegar el archivo `.pine` **completo**.
4. **Save** (nombre: `HYPE Copilot TV v1`) → **Add to chart**.
5. Si el compilador devuelve un error: **no dar el archivo por validado**. Registrar el mensaje y la línea en el repo. Un indicador que no compila no es un indicador roto: es un indicador inexistente, y su silencio se lee igual que "hoy no hubo setups" (§8, D-15).
6. Comprobar en el panel del indicador: número de buckets 4H y 1H disponibles, velas de la sesión actual, velas de la sesión previa, contigüidad de 5m y `params_hash`. **Si el panel dice historial insuficiente, no configurar la alerta todavía.**

### 2.3 Verificación mínima antes de crear la alerta

No es opcional. Sin estos cuatro checks el indicador puede estar dibujando otra cosa:

| Check | Cómo | Resultado exigido |
|---|---|---|
| Alineación de buckets 4H | `plot(request.security(syminfo.tickerid, "240", time, lookahead = barmerge.lookahead_on) % 14400000, display = display.data_window)` | **0**. Cualquier otro valor significa que las 4H de TradingView no abren en 00/04/08/12/16/20 UTC y **el resample de Python es incompatible** (§8, D-06) |
| Unidad de volumen | Comparar el `volume` de una vela 5m concreta contra `v` de `candleSnapshot` de Hyperliquid para el mismo `ts` | Coinciden en **coins base**. Si `volume_TV ≈ v_HL · precio`, TradingView está en nocional y el VWAP es otro objeto matemático (§8, D-09) |
| Warmup de sesión | El indicador no pinta nada entre 00:00 y 01:00 UTC | La primera vela evaluable **cierra a las 01:05 UTC** (`warmup_bars = 12`, condición de paso `nbars > 12`) |
| Reproducibilidad | Recargar la página (F5) y comparar las marcas históricas de los últimos 2 días | **No deben moverse.** Si se mueven, el script repinta y no sirve como verificador |

### 2.4 Crear la alerta

1. Botón **Alert** (reloj) → pestaña **Condition**.
2. Seleccionar el indicador y, en el desplegable, **`Any alert() function call`** / *Cualquier llamada a alert()*. **No** seleccionar una condición nombrada: el JSON dinámico solo puede salir de `alert()`; `alertcondition()` exige un mensaje constante en compilación y no admite payload construido en runtime.
3. **Frecuencia:** la fija el código con `alert.freq_once_per_bar_close`. Si el diálogo deja elegir, elegir **Once per bar close**.
4. **Expiration:** las alertas de TradingView caducan. Anotar la fecha y renovarla. Una alerta caducada produce silencio, y **el silencio se confunde con NO TRADE**.
5. Pestaña **Notifications** → marcar **Webhook URL** y pegar:
   ```
   https://<host-publico>/api/v1/webhooks/tradingview/<PATH_TOKEN>
   ```
6. **Message:** dejar el cuadro **exactamente como venga por defecto**. Con `alert()` el cuerpo del POST es el string que construye el script; **cualquier texto escrito a mano ahí se ignora o corrompe el envío**. Los marcadores `{{close}}`, `{{ticker}}`, `{{timenow}}` **no se sustituyen** cuando el mensaje viene de `alert()`: llegarían literales y romperían el parseo.
7. **Una sola alerta por receptor.** Añadir además una alerta "simple" hacia el mismo endpoint produce duplicados que el dedup absorbe, pero contamina la métrica de latencia.
8. Guardar. **Tras cualquier edición del script o de sus inputs hay que recrear la alerta:** TradingView congela una copia del script y de sus inputs en el momento de crearla.

### 2.5 Probar el canal antes de fiarse de él

Antes de esperar una señal real, usar el `heartbeat`: el script emite `{"event":"heartbeat"}` en la primera vela de cada hora. Comprobar en el receptor que llega, que la IP de origen está en la allowlist, que el secreto valida y que `fired_at_ms − bar_close_ms` cae en el rango esperado. **Un canal que solo se prueba con señales reales se prueba una vez por semana, y falla el día que importa.**

### 2.6 Inputs del indicador y su correspondencia con `Config`

Los inputs del Pine **no son preferencias**: son una copia de parámetros que ya existen en Python. Cambiar uno sin cambiar el otro es la divergencia D-03, la más silenciosa de la lista.

| Input Pine | Campo en `common.Config` | Default | Regla |
|---|---|---|---|
| `rr` (1.0 / 1.6 / 2.0) | `rr` | 1.6 (E2) | Debe coincidir con `cfg.rr`: el clearance se evalúa contra `cfg.rr` |
| `bandK` | `vwap_band_atr` | 0.30 | [OPT] |
| `bufferK` | `stop_buffer_atr` | 0.10 | [OPT] |
| `gapAge` | `fvg_max_age_bars` | 12 | [OPT] |
| `levelHours` | `clearance_lookback_1h` | 48 | [OPT] |
| `longF2` | `require_momentum_long` **y** `require_vwap_slope_long` | false | En F2 se activan **los dos juntos**: F2 son cuatro condiciones (RSI + MACD + volumen + pendiente VWAP), no tres |
| `allowShort` *(a añadir)* | `allow_short` / `HYPE_ALLOW_SHORT` | **false** | Con false, el bloque SHORT se desactiva por completo. Hoy el preview emite SHORT sin equivalente de política: **el 100 % de esas alertas son falsos positivos garantizados** |
| `sessionUtcHour` *(a añadir)* | `session_utc_hour` | 0 | Hoy está hardcodeado en el Pine. Si Python lo cambia, TradingView no se entera y el VWAP, el reset de gaps, el `prev_day_level` y el warmup se desplazan en un solo lado |

**Regla dura:** todo input del Pine que corresponda a un campo de `Config` entra en el `params_hash` (§7.2). Un input fuera del hash es un input que puede derivar sin que nadie lo note.

---

## 3. El aviso del símbolo

### 3.1 El problema

El único control del preview actual es:

```pine
if barstate.isfirst and not str.contains(str.upper(syminfo.ticker), "HYPE")
    runtime.error("Este indicador es exclusivamente para HYPE...")
```

`str.contains(..., "HYPE")` acepta `BINANCE:HYPEUSDT.P`, `GATE:HYPEUSDT.P`, `BINGX:HYPEUSDT.P`, `KUCOIN:HYPEUSDT`, el **spot** `HYPERLIQUID:HYPEUSDC` sin `.P`, y cualquier ticker de cualquier venue que contenga la subcadena. El PRD dice *"HYPE Perpetual (Hyperliquid)"* y `.env.example` apunta a `api.hyperliquid.xyz`. **El guard no cierra esa distancia.**

### 3.2 Qué pasa si el feed no es el perpetuo de Hyperliquid

No es una divergencia marginal: es otro mercado.

| Componente | Efecto de usar otro venue |
|---|---|
| **Volumen** | Es de otro libro. `volume_ratio`, la media de 20 y **todo el VWAP** cambian de serie |
| **VWAP** | Cambian `hlc3` y los pesos a la vez. Se desplaza, y con él la banda `±0.30·ATR` y el test `close > VWAP` de la confirmación |
| **FVG** | Es un fenómeno **de mecha**: `low > high[2]` depende de extremos intrabar de 5m, que es justo donde más difieren dos libros. Una fracción grande de los gaps no coincide entre venues |
| **Stop estructural** | Sale de `min(low)` del tramo toque→confirmación: mecha pura. Cambia R, y con R cambian TP, `cost_r` y `clearance_r` |
| **Precio de referencia** | Un `entry_ref` de otro venue **es un precio inventado** respecto al mercado donde se ejecutaría (PRD §24: *la estrategia no puede inventar precios*) |

### 3.3 Qué hacer — reglas duras

1. **En el Pine, igualdad exacta, no `str.contains`:**
   ```pine
   if barstate.isfirst and (syminfo.tickerid != "HYPERLIQUID:HYPEUSDC.P" or syminfo.type != "swap")
       runtime.error("Solo HYPERLIQUID:HYPEUSDC.P (perpetuo). Cualquier otro feed diverge del backend.")
   ```
2. **En el receptor, allowlist explícita.** Un `market.tv_tickerid` fuera de `TRADINGVIEW_ALLOWED_TICKERIDS` ⇒ **rechazo 400 y registro como incidente**, nunca como "señal descartada". Sin allowlist configurada, el webhook no arranca (§5.4).
3. **Enviar la identidad completa en el payload:** `tv_tickerid`, `tv_exchange`, `tv_ticker`, tipo de instrumento, `syminfo.timezone` y `mintick`. Se comparan contra constantes del backend y contra el `tick_size` de `VenueSpec`.
4. **Prueba de identidad de feed, una vez y documentada:** descargar 3 días de 5m de Hyperliquid vía `candleSnapshot`, exportar los mismos 3 días desde TradingView (*Export chart data*) y comparar barra a barra. **Publicar el % de barras con OHLC idéntico al tick y la distribución de `|Δv|/v`.** Sin ese número, "es la misma fuente" es una creencia.
5. **Mientras esa prueba no exista, cualquier paridad afirmada es provisional** y así debe figurar en el panel de integridad.

> **Aviso operativo:** si el indicador se abre sobre un gráfico que no es `HYPERLIQUID:HYPEUSDC.P`, lo que se ve es plausible y es de otro mercado. No hay ningún error visible. Es exactamente el modo de fallo que puede durar semanas.
---

## 4. Contrato del webhook — `hype.tv.signal.v1`

### 4.1 Restricciones que impone el medio (no negociables)

| Restricción de TradingView | Consecuencia de diseño |
|---|---|
| Solo puertos **80 y 443**, host público (nada de `localhost`) | Necesita despliegue accesible o túnel. No bloquea PAPER local: el scanner nativo no depende del webhook |
| **Sin cabeceras HTTP personalizadas** | Toda la autenticación va en la URL o en el cuerpo |
| Límite del mensaje del orden de **4 096 caracteres** (verificar contra la doc vigente antes de congelar el esquema) | El payload de abajo pesa ~2,2–2,8 KB. El bloque `debug` es desactivable si hace falta margen |
| Con `alert()`, los marcadores `{{close}}`, `{{timenow}}` **no se sustituyen** | Todos los valores se construyen con `str.tostring()`. `fired_at_ms` sale de la variable `timenow` de Pine |
| `str.tostring(na)` produce `"NaN"`, que **no es JSON válido** | Cada campo posiblemente `na` se emite con guarda explícita a `null`. El backend **rechaza** el mensaje si falla el parseo; **nunca coacciona `NaN` a `0`** |
| `str.format()` inserta separadores de millar (`"65,432.1"`) | Prohibido para números. Todo precio se emite con `str.tostring(v, "0.##########")` |
| TradingView **ignora el cuerpo de la respuesta y no reintenta de forma fiable** | No podemos apoyarnos en reintentos. Refuerza §1.2: el scanner nativo es el primario |
| TradingView **no publica SLA de latencia de alertas** | §6 |

### 4.2 Payload completo (ejemplo real, valores coherentes entre sí)

```json
{
  "schema": "hype.tv.signal.v1",
  "event": "candidate",
  "execution_authorized": false,

  "auth": {
    "secret": "<32-64 bytes base64url, constante>",
    "token_id": "tv-01"
  },

  "source": {
    "producer": "hype_copilot_tv_v1",
    "script_version": "1.0.0",
    "script_build_utc": "2026-09-09T18:00:00Z",
    "params_hash": "9f2c41a7b0e35d8c"
  },

  "market": {
    "tv_tickerid": "HYPERLIQUID:HYPEUSDC.P",
    "tv_exchange": "HYPERLIQUID",
    "tv_ticker": "HYPEUSDC.P",
    "tv_type": "swap",
    "tv_timezone": "Etc/UTC",
    "timeframe": "5",
    "bar_ms": 300000,
    "mintick": 0.001
  },

  "time": {
    "bar_open_ms": 1757440800000,
    "bar_close_ms": 1757441100000,
    "fired_at_ms": 1757441102873,
    "advisory_expires_at_ms": 1757441190000,
    "session_start_ms": 1757376000000
  },

  "signal": {
    "tv_signal_id": "hype_copilot_tv_v1:HYPERLIQUID:HYPEUSDC.P:5:LONG:1757440800000",
    "side": "LONG",
    "entry_ref": 41.234,
    "structural_stop": 40.8125,
    "structural_level": 40.8536,
    "risk_per_unit": 0.4215,
    "rr_arm": "E2",
    "rr": 1.6,
    "target_ref": 41.9084
  },

  "bar": {
    "o": 41.101, "h": 41.258, "l": 41.09, "c": 41.234, "v": 18422.31,
    "prev_high": 41.198, "prev_low": 41.02
  },

  "context": {
    "vwap": 41.0712,
    "vwap_prev": 41.0688,
    "atr20": 0.1402,
    "vwap_band_k": 0.30,
    "vwap_distance_atr": 1.1626,
    "session_bars": 137
  },

  "fvg": {
    "lo": 41.012, "hi": 41.144,
    "formed_bar_open_ms": 1757438700000,
    "first_touch_bar_open_ms": 1757440200000,
    "age_bars_at_touch": 5,
    "width_atr": 0.9415
  },

  "momentum_5m": {
    "rsi": 58.42, "rsi_prev": 55.11,
    "macd_hist": 0.01204, "macd_hist_prev1": 0.00891, "macd_hist_prev2": 0.00412,
    "volume": 18422.31, "avg_volume_20": 14108.77, "volume_ratio": 1.3057
  },

  "htf": {
    "h4": { "bar_open_ms": 1757433600000, "close": 41.02, "ema20": 40.71,
            "ema50": 39.98, "ema50_prev3": 39.74, "rsi14": 57.3, "bars_available": 104 },
    "h1": { "bar_open_ms": 1757440800000, "close": 41.18, "ema20": 41.02,
            "ema50": 40.55, "macd_hist": 0.0233, "macd_hist_prev": 0.0198, "bars_available": 416 }
  },

  "clearance": {
    "level_price": 42.31, "level_kind": "swing_1h", "level_ts": 1757404800000,
    "clearance_r": 2.5504,
    "coverage_1h_bars": 52, "prev_session_5m_bars": 288, "contiguous_5m_bars": 861
  },

  "checks": {
    "regime_4h": true, "align_1h": true, "fvg": true, "first_retest": true,
    "vwap_band": true, "confirmation": true,
    "rsi": true, "macd": true, "volume": true, "vwap_slope": true,
    "target_clearance": true,
    "cost_gate": null
  },

  "config_echo": {
    "filter_arm": "F2", "band_k": 0.30, "buffer_atr": 0.10,
    "gap_max_age_bars": 12, "confirm_window": 3, "atr_len": 20,
    "ema_fast": 20, "ema_slow": 50, "slope_lookback": 3, "session_utc_hour": 0,
    "clearance_pivot_n": 2, "clearance_lookback_1h": 48, "allow_short": false
  }
}
```

### 4.3 Tabla de campos: tipo, unidad, obligatoriedad, validación

| Campo | Tipo | Unidad / precisión | Oblig. | Validación en el backend |
|---|---|---|---|---|
| `schema` | string enum | literal `hype.tv.signal.v1` | sí | Igualdad exacta. Otro valor ⇒ `400` + registro |
| `event` | string enum | `candidate` \| `invalidated` \| `heartbeat` | sí | Solo `candidate` produce revalidación. `heartbeat` prueba el canal sin señal |
| `execution_authorized` | boolean | literal `false` | sí | **Si llega `true` ⇒ `400` + alarma de manipulación.** Nunca se lee para decidir nada |
| `auth.secret` | string | 32–64 bytes base64url | sí | `hmac.compare_digest` contra `TRADINGVIEW_WEBHOOK_SECRET`. **Nunca aparece en logs; se redacta antes de persistir** |
| `auth.token_id` | string | ≤ 16 chars | sí | Identifica qué secreto se usó, para rotar sin cortar el canal |
| `source.producer` / `script_version` / `script_build_utc` | string | semver / ISO-8601 UTC | sí | Se guarda íntegro. Un cambio de `script_version` sin cambio en el repo es un aviso: alguien editó el Pine |
| `source.params_hash` | string | hex, 16 chars | sí | **Igualdad exacta contra el hash de la `Config` activa. Mismatch ⇒ rechazo `params_drift` + incidente** (§7.2) |
| `market.tv_tickerid` | string | `EXCHANGE:TICKER` | sí | **Allowlist explícita.** Desconocido ⇒ rechazo (§3.3) |
| `market.tv_type` / `tv_timezone` | string | — | sí | `swap`. La timezone se registra para auditar la alineación de buckets HTF |
| `market.timeframe` | string | notación TV | sí | Debe ser `"5"` |
| `market.bar_ms` | int | ms | sí | Debe ser `300000` y coincidir con el `bar_ms` de `order_guard.signal_age_seconds()` |
| `market.mintick` | float | precio | sí | Se compara con el `tick_size` de `VenueSpec`. Divergencia ⇒ `CONFIG_DRIFT` |
| `time.bar_open_ms` | int | epoch **ms UTC**, apertura | sí | **Clave de alineación.** `engine.Signal.ts` es la apertura de la vela de confirmación (`c5[t].ts`). Debe ser múltiplo exacto de `300000` |
| `time.bar_close_ms` | int | epoch ms UTC | sí | Debe ser `bar_open_ms + 300000`. Es el origen del reloj de los 90 s |
| `time.fired_at_ms` | int | epoch ms UTC (`timenow`) | sí | Mide la latencia interna de TV: `fired_at_ms − bar_close_ms`. Dato de producto (§6), no telemetría decorativa |
| `time.advisory_expires_at_ms` | int | epoch ms UTC | sí | **Advisory: el backend lo ignora para decidir** y recalcula desde `bar_close_ms`. Si difiere de `bar_close_ms + 90000` ⇒ `CONFIG_DRIFT` |
| `time.session_start_ms` | int | epoch ms UTC | sí | Debe coincidir con `indicators.session_start_ms(bar_open_ms, 0)`. Detecta un reset de VWAP mal anclado, que corrompería en silencio todo lo demás |
| `signal.tv_signal_id` | string | ≤ 128 chars | sí | Se **recalcula** en el backend y se compara; no se confía. Mismatch ⇒ discrepancia |
| `signal.side` | string enum | `LONG` \| `SHORT` | sí | Mayúsculas. `SHORT` con `HYPE_ALLOW_SHORT=false` ⇒ rechazo + alarma: el Pine tiene otra política que el backend |
| `signal.entry_ref` | float | precio quote (USD), ≤ 10 dec | sí | **Referencia, nunca un fill** (`AUDIT` C-05, PRD §13) |
| `signal.structural_stop` | float | precio | sí | Se recalcula desde datos nativos; el de TV solo se compara |
| `signal.structural_level` | float | precio | sí | El nivel **antes** del buffer ATR. Permite separar "discrepan por el nivel" de "discrepan por el ATR" |
| `signal.risk_per_unit` | float | precio | sí | `abs(entry_ref − structural_stop)`. Redundante a propósito: si no cuadra con los otros dos campos, el payload está corrupto |
| `signal.rr_arm` / `rr` | enum / float | `E1`\|`E2`\|`E3` · 1.0\|1.6\|2.0 | sí | Debe coincidir con `cfg.rr`. Divergencia ⇒ `CONFIG_DRIFT` **crítico**: el clearance se evalúa contra `cfg.rr` |
| `signal.target_ref` | float | precio | sí | Comparación |
| `bar.{o,h,l,c,v}` | float | precio · volumen en **HYPE base** | sí | **El comparador de feeds.** Contrastar la vela de TV contra la vela nativa del mismo `bar_open_ms` es lo que distingue "bug de implementación" de "feed distinto". Sin esto, toda discrepancia es inatribuible |
| `bar.prev_high` / `prev_low` | float | precio | sí | La confirmación exige `close > high[1]` (long) / `close < low[1]` (short). Sin `prev_high` el check no es reproducible con datos de TV |
| `context.vwap` / `vwap_prev` | float | precio | sí | `vwap_prev` es imprescindible: la pendiente del VWAP es obligatoria en SHORT y parte de F2 en LONG |
| `context.atr20` | float | precio | sí | **SMA de True Range de 20**, no RMA. Ver §8, D-12 |
| `context.session_bars` | int | velas | sí | Traducción de `nbars` de `vwap_at`. `> 12` es el warmup |
| `fvg.*` | float / int | precio · epoch ms · barras | sí | **`lo < hi` siempre, en ambos lados** (invariante de `common.FVG`). `formed_bar_open_ms` identifica el gap de forma invariante al slicing |
| `momentum_5m.*` | float | RSI 0–100 · MACD hist en precio · volumen base | sí | Se envían **siempre**, pasen o no el gate, y con **dos** barras de historia de MACD: la regla es "hist ascendiendo 2 tramos". Con un solo valor no es verificable |
| `htf.h4.*` | float / int | precio | sí | Incluye `ema50_prev3` (la pendiente 4H es `EMA50 > EMA50[3]`) y `rsi14` 4H (obligatorio en SHORT). `bar_open_ms` permite detectar el desfase del patrón `[1] + lookahead_on` |
| `htf.h1.*` | float / int | precio | sí | Incluye `macd_hist` y `macd_hist_prev` (SHORT exige hist `< 0` y decreciente) |
| `htf.*.bars_available` | int | buckets | sí | Detecta el problema de semilla de EMA con histórico corto (§8, D-05) |
| `clearance.level_price` / `level_kind` / `level_ts` | float / enum / int | precio · `swing_1h`\|`prev_day`\|`none` · epoch ms | sí | `level_price: null` cuando no hay nivel bloqueante |
| `clearance.clearance_r` | float \| null | R adimensional | sí | **`null` cuando no hay nivel; nunca `0`, nunca `999`.** El `999.0` que hoy serializa `engine.py` mezcla "no hay obstáculo" con "no pude mirar" |
| `clearance.coverage_1h_bars` / `prev_session_5m_bars` / `contiguous_5m_bars` | int | barras | sí | Traducción de `target_clearance.coverage_ok()`. **"No hay resistencia" y "no he podido mirar" no son lo mismo**, y el payload debe distinguirlos o el gate se vuelve fail-open silencioso |
| `checks.*` | boolean \| null | — | sí | Los 12 de `CHECKLIST_KEYS`. **`cost_gate` es siempre `null`**: TradingView no conoce el tier de fees, el spread real, el funding ni el tiempo de tenencia esperado. Un `cost_gate: true` desde TV sería un número inventado |
| `config_echo.*` | mixto | — | sí | Los inputs del Pine. Es el detector de drift: si el Pine corre con `band_k = 0.50` y Python con `0.30`, ambos "funcionan" y divergen para siempre sin que nada falle |

### 4.4 Campos prohibidos

El payload **no contiene, y el backend rechaza si aparecen**: `qty`, `size`, `notional`, `leverage`, `equity`, `balance`, `order_type`, `api_key`, `account`, `wallet`, `execute`, ni ningún nombre que contenga los términos de `scoring._FORBIDDEN` (`probability`, `prob`, `win_chance`, `confidence`, `odds`, `probabilidad`, `confianza`). Esta última guarda ya existe: `scoring.assert_no_probability()` se aplica **al payload entrante**.

### 4.5 Idempotencia y anti-repetición

**Clave canónica**, calculada por el backend (el `tv_signal_id` recibido solo se compara):

```
dedup_key = sha256(f"{strategy_id}|{market}|{side}|{bar_open_ms}")
```

con `strategy_id = "hype_vwap_fvg_retest_v2"` (`engine.STRATEGY_ID`) y `market` el mercado **interno** resuelto, no el ticker de TV. Se usa `bar_open_ms` y no `bar_close_ms` porque `Signal.ts` es la apertura y toda la alineación con Python se hace sobre ella.

**La unicidad la garantiza la base de datos**, con índice único sobre `(strategy_id, market, side, bar_open_ms)`, no un `if` en el código: dos peticiones concurrentes pasarían cualquier comprobación en memoria.

| Comprobación | Regla | Motivo |
|---|---|---|
| Rejilla | `bar_open_ms % 300000 == 0` y `bar_close_ms == bar_open_ms + 300000` | Un timestamp fuera de rejilla no viene de una vela de 5m |
| Futuro | `bar_close_ms <= now_ms + 2000` | 2 s de tolerancia de deriva de reloj. Más allá, la alerta afirma conocer el futuro |
| Frescura de aceptación | `now_ms − bar_close_ms <= 180000` | **Se acepta y registra hasta 180 s** con `verdict = ACCEPTED_LATE`, aunque el TTL de decisión sean 90 s. Descartar en el borde una alerta de 120 s destruye justo la medición de latencia que §6 necesita. **No genera tarjeta** |
| TTL de decisión | `now_ms − bar_close_ms <= signal_ttl_seconds * 1000` | Solo dentro de esta ventana se ofrece la Signal Card |
| Marca de agua | `bar_close_ms > high_water_mark[(market, side)]`, persistente | Protege contra repeticiones de cola larga, ya podada la tabla de dedup |

| Caso de duplicado | Respuesta | Registro |
|---|---|---|
| Misma clave, **mismo** `body_sha256` | `200` con el resultado original (idempotente puro) | `duplicate_count++` |
| Misma clave, **distinto** `body_sha256` | `200`, **no** se re-procesa | **Discrepancia severa.** O el script se editó a mitad de sesión, o alguien inyecta. Alarma |
| Misma barra, **lado opuesto** | `409` + alarma | `engine._direction()` garantiza exclusión mutua entre LONG y SHORT. Recibir ambos para la misma vela significa que Pine y Python discrepan en algo fundamental |
| Clave nueva, marca de agua superada | `409 stale_bar` | Repetición tardía |

**Retención:** `tv_alert` ≥ 90 días (es el registro forense). `high_water_mark` permanente.

---

## 5. Seguridad — qué puede y qué NO puede garantizar TradingView

### 5.1 Inventario honesto de mecanismos

| Mecanismo | ¿Posible? | Por qué |
|---|---|---|
| HMAC / firma criptográfica | **No** | Pine no tiene hashing ni criptografía |
| Cabecera `Authorization` / `X-Signature` | **No** | Las alertas por webhook no admiten cabeceras personalizadas |
| mTLS / certificado de cliente | **No** | No configurable |
| Nonce aleatorio por mensaje | **No** de forma útil | Pine no tiene RNG criptográfico. `bar_close_ms` sirve de nonce natural: monótono y en rejilla |
| Secreto compartido en el cuerpo | Sí | Estático, idéntico en cada mensaje |
| Token no adivinable en la URL | Sí | Estático |
| Allowlist de IPs de origen | Sí, del lado del receptor | La lista la publica TradingView y **puede cambiar sin aviso** |
| TLS | Sí (443) | Obligatorio |

**Sobre "firmar desde Pine con un hash artesanal":** es construible (una función de mezcla sobre `bar_close_ms + entry + stop` con una constante secreta) y **no se debe hacer**. La clave quedaría en texto plano dentro del script, que el titular de la cuenta puede exportar y compartir. Aportaría resistencia frente a *modificación* de un payload capturado y **ninguna frente a repetición**, que es justo lo que ya cubre el binding temporal de §4.5. Coste de mantenimiento alto, ganancia real casi nula, y crea confianza falsa: alguien leerá "firmado" y bajará la guardia en otro sitio. **No construir un HMAC falso.**

### 5.2 Recomendación concreta: cinco capas, en este orden

```
[1] HTTPS 443 obligatorio, HSTS
[2] Allowlist de IPs de TradingView        <- en el borde (nginx/CF), antes de la app
[3] Token no adivinable en la ruta          <- rechazo antes de parsear el cuerpo
[4] Secreto compartido en el cuerpo         <- comparacion en tiempo constante
[5] Esquema estricto + limite de 8 KB + rate limit + tope diario de alertas aceptadas
```

| Capa | **Sí** previene | **No** previene |
|---|---|---|
| **TLS** | Lectura y modificación en tránsito. Extracción del secreto por sniffing | Nada relativo al origen del mensaje |
| **IP allowlist** | Que quien haya obtenido URL **y** secreto (log filtrado, captura de pantalla, backup del `.env`) los use desde su máquina. Casi todo el ruido automatizado | **No** previene que otro usuario de TradingView que conozca la URL apunte su propia alerta: sale de las mismas IPs. Solo la capa 4 lo detiene. **Riesgo propio:** si TV cambia su lista, todas las alertas se caen en silencio ⇒ **el rechazo por IP debe ser un evento con alarma, no una línea de log**. Releer la lista oficial en cada despliegue; solo cubre IPv4 |
| **Token en la URL** | Descubrimiento del endpoint por escaneo. Permite rechazar en el borde sin gastar CPU ni tocar la base de datos (reduce superficie de DoS). Es rotable aparte del secreto | **No** previene nada una vez la URL se filtra, y las URLs se filtran más fácil que los cuerpos: logs de acceso, proxies, capturas de la configuración de la alerta. **Regla: el token nunca aparece en logs de acceso** |
| **Secreto en el cuerpo** | Que alguien con solo la URL envíe payloads aceptados. Distingue "nuestro TradingView" de "un TradingView" | **No** aporta integridad, **no** aporta frescura, **no** resiste repetición, y **no** protege contra nadie que haya visto un solo payload: el secreto va dentro. Es un identificador de portador |

### 5.3 El ataque que ninguna capa previene, y por qué da igual

**Quien tenga acceso a la cuenta de TradingView puede emitir payloads perfectamente válidos con contenido arbitrario.** No hay defensa técnica en este canal contra eso. Mitigaciones reales: **2FA en la cuenta**, no compartir el script, y —lo que de verdad manda— **que el payload no autorice absolutamente nada**.

Ese es el centro del diseño. Como el backend **recalcula todo desde datos nativos** (§7.1) y la ejecución exige que Mark pulse ENTER y que `order_guard.revalidate()` pase con datos propios, el peor resultado de un webhook comprometido es:

1. Una revalidación que no reproduce nada ⇒ **no aparece tarjeta** ⇒ ruido y una fila en el log de discrepancias.
2. Ruido en volumen ⇒ **DoS**, acotado por rate limit y por un tope de alertas aceptadas por sesión-día.

Nunca una orden. **La seguridad del puente no descansa en la autenticación del puente; descansa en que el puente no tiene autoridad.** Toda inversión en "endurecer el webhook" que no venga acompañada de la revalidación nativa es esfuerzo mal colocado.

### 5.4 Cambios concretos en `core/config.py`

Hoy `Settings.tradingview_webhook_secret` tiene default `""` y **nada lo comprueba**. Con la ruta activa y secreto vacío, la comparación pasaría con cualquier cuerpo. Propuesta, en el estilo fail-closed que ya usa el módulo:

```python
tradingview_webhook_enabled: bool = False          # default seguro, como live_execution
tradingview_webhook_secret: str = ""
tradingview_webhook_path_token: str = ""
tradingview_ip_allowlist: tuple = ()
tradingview_allowed_tickerids: tuple = ()

# en validate():
if self.tradingview_webhook_enabled:
    if len(self.tradingview_webhook_secret) < 32:
        raise UnsafeConfiguration("TRADINGVIEW_WEBHOOK_SECRET < 32 chars con el webhook activo.")
    if len(self.tradingview_webhook_path_token) < 24:
        raise UnsafeConfiguration("TRADINGVIEW_WEBHOOK_PATH_TOKEN < 24 chars con el webhook activo.")
    if not self.tradingview_allowed_tickerids:
        raise UnsafeConfiguration("Sin allowlist de tickerids, el webhook aceptaria cualquier mercado.")
```

### 5.5 Aislamiento estructural, no promesa

**El módulo del webhook no debe tener ninguna ruta de import hacia `app.execution`.** Se blinda con un test de arquitectura, el mismo patrón que el repo ya usa en `test_module_source_has_no_opposite_direction`:

```python
def test_webhook_module_cannot_reach_execution():
    src = Path("app/api/webhooks_tradingview.py").read_text()
    assert "execution" not in src
    assert "order" not in src.lower().replace("order_by", "")
```

Una promesa en un documento se rompe en el primer refactor. Un test no.
---

## 6. Presupuesto de latencia de los 90 segundos

`Config.signal_ttl_seconds = 90`, medido —correctamente— desde el **cierre** de la vela: `order_guard.signal_age_seconds()` suma `bar_ms` a `Signal.ts`, porque medir desde la apertura regalaría cinco minutos. El Pine emite `advisory_expires_at_ms = bar_close_ms + 90000`, misma base temporal. **La base está bien; lo que está apretado es el presupuesto.**

### 6.1 Desglose tramo a tramo

| # | Tramo | Optimista | Típico | Malo | ¿Lo controlamos? |
|---|---|---|---|---|---|
| 1 | Cierre de vela → Pine evalúa y ejecuta `alert()` | 0,5 s | **2–4 s** | 10–30 s | **No.** TradingView no da SLA de latencia de alertas |
| 2 | Cola de alertas de TV → emisión del POST | 0,2 s | **0,5–2 s** | 5–20 s | **No** |
| 3 | Red (TV us-west) → host + TLS | 0,05 s | **0,1–0,3 s** | 1–2 s | Parcial (región del host, keep-alive) |
| 4 | Borde + parseo + auth + dedup + persistir crudo | 5 ms | **10–30 ms** | 100 ms | Total |
| 5 | Leer velas nativas de Hyperliquid | 0 ms (WS caliente) | **0–50 ms** | 800 ms–3 s (REST bajo demanda) | **Total — y aquí se decide todo** |
| 6 | `engine.scan()` | 5 ms | **10–50 ms** | 200 ms | Total |
| 7 | Sizing + límites + `VenueSpec` | 1 ms | **5–20 ms** | 500 ms (metadata sin cachear) | Total |
| 8 | Persistir señal + gates | 5 ms | **10–50 ms** | 300 ms | Total |
| 9 | Push SSE → render en el navegador | 50 ms | **0,1–0,3 s** | 3 s (con polling a 3 s) | Total |
| **A** | **Subtotal máquina** | **~0,8 s** | **~3–7 s** | **~20–60 s** | |
| 10 | **Mark lee y decide** | 8 s (delante, pre-avisado) | **20–40 s** | 60 s+ / nunca | Ninguno |
| 11 | Clic ENTER → `revalidate()` → orden enviada | 0,15 s | **0,2–0,7 s** | 2 s | Casi total |
| | **TOTAL** | **~9 s** | **~25–48 s** | **> 90 s** | |

### 6.2 Veredicto honesto

**Los 90 s son realistas en un solo escenario, y hay que decirlo así de estrecho:**

> El scanner nativo dispara al cierre de la vela, con las velas ya calientes en memoria (WS), la app abierta con SSE, y Mark delante de la pantalla sabiendo que puede llegar un candidato.

En ese escenario quedan **~80 s** para la decisión humana, que sobra.

**Los 90 s NO son realistas si se cumple cualquiera de estas cuatro condiciones:**

1. **El webhook de TV es el disparador principal.** Los tramos 1+2 gastan 3–6 s típicos y hasta 30–50 s en el peor caso, **latencia que no controlamos**, sobre un presupuesto que tampoco controlamos del todo. Es la razón técnica —no ideológica— por la que §1.2 pone a TradingView como secundario.
2. **Las velas nativas se piden por REST al recibir la alerta.** El tramo 5 se dispara a segundos justo cuando el mercado se mueve y la API está más cargada. **El store por WS no es una optimización: es un requisito del presupuesto.**
3. **El flujo es "notificación → coger el móvil → abrir la app".** Entrega del push 2–10 s más reacción humana 15–120 s: el TTL se agota antes de que Mark vea la tarjeta, y el resultado dominante será `EXPIRED_UNSEEN`.
4. **La app hace polling en vez de SSE.** Un intervalo de 3 s añade hasta 3 s y, peor, mete jitter en la medición de la propia latencia.

**Dicho sin adornos: con el webhook de TradingView como disparador y Mark no presente, 90 segundos no alcanzan. Con scanner nativo + WS caliente + SSE + operador presente, sí. El diseño de §1.3 existe precisamente para estar siempre en el segundo caso.**

### 6.3 Qué hacer — cuatro medidas, tres sin coste de política

**(1) Scanner nativo primario, disparado a cierre de vela sobre el store WS.** Recupera 3–30 s del presupuesto y no cambia ninguna regla del PRD. Es la medida de mayor impacto y ya está implícita en `IMPLEMENTATION_ORDER` P0.4.

**(2) Pre-aviso en el primer retest.** `common.gap_resolution()` ya distingue `WAITING_FOR_CONFIRMATION`: hay un gap tocado, en banda de VWAP y dentro de edad, esperando confirmación en la vela del toque o las dos siguientes. Eso da **5–15 minutos de aviso** antes de que exista candidato. Emitir ahí una notificación *"HYPE: ventana de confirmación abierta, LONG, ponte delante"* convierte el escenario realista en el escenario optimista. **No autoriza nada, no muestra Entry/SL/TP, no cuenta como señal y no entra en el journal de señales.** Coste cero en política, y es con diferencia el mayor arreglo del problema humano.

**(3) Instrumentar cada tramo y decidir con datos.** Guardar `latency_sample` para **toda** señal: `t_bar_close`, `t_tv_fired`, `t_received`, `t_revalidated`, `t_persisted`, `t_first_render` (lo reporta el cliente), `t_decision`, `transport`. Con N ≥ 30 señales reales tenemos la distribución de `t_decision − t_bar_close` y la pregunta deja de ser opinión. Distinguir además `EXPIRED_UNSEEN` de `EXPIRED_SEEN`: si dominan las primeras, el problema es de entrega y lo arregla (2); si dominan las segundas, el problema es el reloj y hay que llevarle el dato a Mark.

**(4) NO subir el TTL por iniciativa de ingeniería.** `signal_ttl_seconds` es una decisión de producto del mismo rango que `max_hold_hours` o `max_trades_per_day` (`AUDIT` C-12). Si los datos muestran que la p50 de decisión supera 60 s, las opciones honestas **para que Mark elija** son:

- **(a) No tocar nada.** Aceptar menos ENTER y medirlo: el journal ya separa `MARK ACCEPTED` de `MARK SKIPPED` (PRD §19) y podrá responder si las señales expiradas habrían ganado dinero. Es la opción por defecto y la más coherente con *NO TRADE > BAD TRADE*.
- **(b) TTL a 180 s con dos topes duros.** Fundamento técnico real: **la restricción económicamente vinculante es el drift, no el reloj.** Los 90 s son un proxy de "el mercado no se ha ido y el setup sigue vivo"; `max_drift_r = 0.10` mide eso mismo de forma directa y `revalidate()` ya lo aplica. Topes obligatorios: **(i)** la señal muere al cerrar la siguiente vela de 5m pase lo que pase (máx. 300 s), porque una vela nueva puede haber invalidado la estructura; **(ii)** medir por separado el resultado de las entradas con edad > 90 s durante al menos 20 trades antes de dar la ampliación por buena.
- **(c) Rechazar (b)** por el argumento inverso: entrar dos minutos después del cierre de una vela de impulso es exactamente la familia de sesgo que `AUDIT` C-05 obligó a modelar, y el drift acota el precio pero no la degradación del contexto.

**No se elige por Mark.** Se llega a esa decisión con los datos de (3) sobre la mesa. **Mientras tanto, 90 s se quedan como están.**

---

## 7. Protocolo de verificación de paridad

El criterio 6 del MVP dice literalmente *"TradingView muestra exactamente las mismas condiciones que el backend"*. **La tasa de discrepancia es la medición de ese criterio.** Silenciarla no es limpiar ruido: es declarar cumplido un criterio del MVP sin haberlo verificado nunca.

### 7.1 Qué hace el backend con cada alerta (orden fijo)

Las fases 0–4 son baratas y se ejecutan siempre. **Persistir el payload crudo va antes que cualquier trabajo de estrategia, porque no se puede analizar lo que se descartó.**

| # | Paso | Falla ⇒ |
|---|---|---|
| 0 | TLS · IP allowlist · token de ruta · rate limit · tamaño ≤ 8 KB | `403`/`413`/`429` en el borde, contador `rejected_by_*` |
| 1 | Parseo JSON estricto: sin `NaN`, sin `Infinity`, sin claves duplicadas | `400 malformed` |
| 2 | `compare_digest` del secreto · `token_id` conocido · `params_hash` coincidente | `403` / `params_drift` + incidente |
| 3 | `execution_authorized is False` (identidad, no truthiness) · sin campos prohibidos · `assert_no_probability(payload)` | `400` + **alarma de manipulación** |
| 4 | **Persistir `tv_alert` crudo** (cuerpo íntegro con el secreto redactado, `body_sha256`, `received_at_ms`, IP, `token_id`) | — |
| 5 | Validación de esquema campo a campo (§4.3): tipos, rangos, rejilla, coherencia interna (`risk_per_unit ≈ abs(entry−stop)`, `fvg.lo < fvg.hi`) | `400` + registro |
| 6 | Ventana temporal y marca de agua (§4.5) | `409 stale` / `ACCEPTED_LATE` |
| 7 | Dedup por clave canónica | `200` idempotente / `409` + alarma |
| 8 | Resolver `tv_tickerid` → mercado interno vía allowlist | `400 unknown_market` |
| 9 | **Leer velas NATIVAS de Hyperliquid** (4H, 1H, 5m), solo cerradas, del store en memoria alimentado por WS; comprobar `data_age_seconds` contra `HYPE_MAX_DATA_AGE_SECONDS` | `BLOCKED:market_data_stale`, **se persiste igual** |
| 10 | Comprobar que la vela nativa con `ts == bar_open_ms` existe y está cerrada; si no, esperar ≤ 500 ms y reintentar una vez | `PENDING_NATIVE_BAR` ⇒ discrepancia |
| 11 | **`engine.scan(c4h, c1h, c5, cfg)`** — la misma y única función pública. **Sin variantes, sin "modo webhook", sin parámetros nuevos** | — |
| 12 | **Comparar** payload TV contra `ScanResult` nativo y escribir la fila de discrepancia **siempre, coincidan o no** | — |
| 13 | Si el scan nativo no produjo señal, o produjo otro lado, o con `ts != bar_open_ms` ⇒ **no hay tarjeta**; se persiste la discrepancia y se termina | — |
| 14–16 | Sizing · límites diarios · `scoring.score()` con los tres gates operativos ya evaluados | Gate a `False` + motivo; **la señal se persiste igual**, y si era A+ ⇒ `record_blocked_a_plus()` (`AUDIT` C-14) |
| 17–19 | Persistir señal + `gate_evaluation` + `latency_sample` en una transacción · emitir por SSE con `ttl_remaining_ms` y `server_now_ms` · responder `200` | — |

**Presupuesto interno duro: 2 500 ms.** Si se supera, responder `202`, persistir `verdict = DEFERRED` y completar en segundo plano — pero **un `DEFERRED` es de por sí una alarma de rendimiento**, no un modo normal.

### 7.2 Handshake de versión: `params_hash`

La mitigación más barata y de mayor retorno de todo el documento. `Config` tiene ~45 campos; el Pine expone 6–8 y codifica el resto en duro. Nada compara los dos conjuntos.

1. Python calcula `params_hash` = SHA-256 (truncado a 16 hex) de los campos de `Config` que afectan a la señal, canonicalizados y ordenados.
2. Se replica como constante literal en el `.pine`: `const string PARAMS_HASH = "9f2c41a7b0e35d8c"`, y viaja en `source.params_hash`.
3. **El receptor rechaza cualquier alerta cuyo `params_hash` no coincida**, con motivo `params_drift`, y lo registra como incidente.
4. **Test en CI que falle si `Config` cambia sin actualizar el literal del `.pine`.** Un `grep` del hash en el archivo Pine basta.

Esto convierte D-03 de silenciosa a imposible.

### 7.3 Clasificación de discrepancias

| Clase | Definición | Severidad | Efecto |
|---|---|---|---|
| `MATCH` | Mismo lado, misma barra, todos los campos comparados dentro de tolerancia | info | Insignia verde `TV ✓` |
| `FEED_DELTA` | Mismo lado y barra; discrepan los OHLCV **y** los derivados de forma coherente con esa diferencia | baja | Insignia ámbar + delta en ticks |
| `LEVEL_DRIFT` | OHLCV coinciden pero `entry_ref`, `structural_stop` o `clearance_r` discrepan fuera de tolerancia | **alta** | Insignia roja. **Mismos datos ⇒ mismo resultado; si no, hay un bug en una de las dos implementaciones** |
| `CONFIG_DRIFT` | `config_echo` o `params_hash` ≠ `Config` activa | **alta** | Insignia roja + el parámetro exacto |
| `SIDE_CONFLICT` | TV dice LONG y el nativo dice SHORT, o al revés | **crítica** | Sin tarjeta. Alarma |
| `TV_ONLY` | TV emitió candidato; el scan nativo no produjo señal | media | Sin tarjeta. Se registra el `ScanResult.reason` nativo (`cost_gate`, `target_clearance`, `momentum_fail:rsi`, …) |
| `NATIVE_ONLY` | El scanner nativo produjo señal y **no llegó** alerta de TV en 180 s | media | Insignia `TV silencioso`. Detecta alerta caducada, script sin recompilar, cambio en las IPs de TV |
| `PENDING_NATIVE_BAR` | TV va por delante del store nativo | baja → alta si se repite | Síntoma de WS desincronizado |
| `HEARTBEAT_LOST` | > 70 min sin `heartbeat` | media | `tv_indicator_stale`. **Sin esto, el silencio de TradingView no es información** |

**`TV_ONLY` alto es lo esperado, no una anomalía.** El Pine no implementa cost gate, política `allow_short`, límite diario ni gates operativos. El cost gate por sí solo es muy restrictivo: con `cost_frac = 2·0.00045 + 0.0002 + 0.0002 = 0.0013`, el gate equivale a exigir **stop ≥ 0,867 % del entry (LONG) / ≥ 1,083 % (SHORT)**, que en ATR20 de 5m son del orden de **2,2–5,8 ATR**. La mayoría de los candidatos visuales del Pine morirán ahí. **Esto se explica a Mark antes de conectar el canal**, o el camino corto es "el backend está roto, voy a operar lo que dice TradingView".

### 7.4 Tolerancias — declaradas, no improvisadas

| Comparación | Tolerancia |
|---|---|
| Precios (`entry_ref`, `stop`, OHLC, VWAP, niveles) | `max(1 tick, 2 bps del precio)` |
| `atr20`, `risk_per_unit` | 1 % relativo |
| `volume`, `avg_volume_20` | 5 % relativo — proveedores distintos agregan distinto; una tolerancia estrecha aquí genera ruido sin información |
| `rsi` · `macd_hist` | 0,5 puntos de RSI · 2 % relativo (efecto de semilla de EMA) |
| `clearance_r` | 0,05 R |
| Booleanos de `checks` | **Cero tolerancia.** Un booleano que difiere es siempre un hallazgo |
| `config_echo` / `params_hash` | **Cero tolerancia.** Igualdad exacta |

**Nunca comparar precios con `==`.** Los formatos de serialización difieren en ambos lados (`"0.##########"` en Pine, `round(x, 6)` en Python) y ninguno de los dos afecta a una decisión, pero impiden la igualdad exacta.

### 7.5 Cadencia de verificación

| Cuándo | Qué | Quién |
|---|---|---|
| **En cada alerta** | Pasos 11–12 de §7.1: `scan()` nativo + fila de `discrepancy`, **también cuando el resultado es `MATCH`** (sin los aciertos, la tasa no se puede calcular) | Automático |
| **En cada señal nativa sin alerta** | Ventana de 180 s; si no llega alerta ⇒ `NATIVE_ONLY` | Automático |
| **Cada hora** | `heartbeat` del indicador + watchdog | Automático |
| **Semanal** | Revisión del panel de integridad: tasa por clase a 7 días, última crítica, `script_version` en uso, latencia p50/p95 de TV | Manual, 10 min |
| **Mensual, y tras cualquier edición del `.pine` o de `Config`** | **Fixture compartido:** exportar 30 días de 5m del gráfico de TradingView, correr `engine.scan()` de Python sobre ese mismo CSV y comparar la **lista de `bar_close_ms` de candidatos**. Debe ser idéntica. Es la única prueba de paridad que vale | Manual |
| **Antes de cualquier promoción de fase** (PAPER→SHADOW→TINY) | Fixture compartido + estado `TRUSTED` + cero incidentes críticos abiertos | Manual, decisión de Mark |

**El histórico dibujado en TradingView no es evidencia.** Pine acumula estado (`var` de gaps, niveles, contadores) hacia adelante desde la primera barra que TradingView cargó, y ese punto de arranque depende del plan de suscripción y de si se recargó la pestaña. *"He revisado tres meses en Bar Replay y coincide"* no es verificable por un tercero. **El oráculo es el fixture CSV, no la pantalla.**

### 7.6 Qué se registra cuando discrepan

Una fila `discrepancy` por alerta procesada, con: `tv_alert_id`, `signal_id` (si lo hay), clase, severidad, `fields[]` con `{campo, valor_tv, valor_nativo, delta, tolerancia, pasa}`, `script_version`, `params_hash`, `config_echo_diff`, `data_age_seconds`, `tv_latency_ms = fired_at_ms − bar_close_ms`, y el `ScanResult.checks` nativo completo.

**Qué NO se hace con una discrepancia:** *"arreglarla"* acercando una implementación a la otra sin entender la causa. Copiar al Pine el fail-open de clearance que existe en Python (R03-02) haría desaparecer la alarma y conservaría el fallo. **El objetivo es explicar la diferencia, no eliminarla.**

**Qué sí se muestra:**

- **En la Signal Card: nada procedente de TV.** Los 12 campos del PRD §16 salen exclusivamente del cálculo nativo. Mezclar orígenes en la tarjeta destruiría la propiedad que hace útil el puente.
- **Una insignia** junto a la tarjeta (`TV ✓` / `TV ~ (2 campos)` / `TV ✗` / `TV silencioso`) que abre un panel de comparación campo a campo.
- **Un panel de Integridad** permanente: tasa de discrepancia a 7 días por clase, última crítica, `script_version`, latencia p50/p95.

### 7.7 Umbrales que obligan a parar — y qué significa "parar"

| Evento | Umbral | Consecuencia |
|---|---|---|
| `SIDE_CONFLICT` | **1 sola ocurrencia** | Puente a `UNTRUSTED` inmediato. Investigación antes de aceptar otra alerta como válida |
| `CONFIG_DRIFT` o `params_drift` | **1 sola ocurrencia** | `UNTRUSTED`. Se identifica el parámetro exacto y se corrige el lado equivocado |
| `LEVEL_DRIFT` con OHLCV dentro de tolerancia | **1 sola ocurrencia** | `UNTRUSTED`. **Es literalmente la prueba de que una de las dos implementaciones calcula mal.** Se abre bug con la fila de discrepancia adjunta |
| `FEED_DELTA` | **> 2 % de las barras comparadas en 7 días** con OHLC fuera de `max(1 tick, 2 bps)` | Se reabre §3.4: prueba de identidad de feed. Sospecha de símbolo equivocado |
| `NATIVE_ONLY` | **> 1 en 7 días** | Revisar alerta caducada, script sin recompilar, IPs de TV cambiadas |
| `PENDING_NATIVE_BAR` | **> 3 en 7 días** | WS nativo desincronizado. Esto **sí** toca la ruta principal y entra por el kill switch de *market data stale* que ya existe |
| `HEARTBEAT_LOST` | **> 70 min** | `tv_indicator_stale`. El puente deja de contar como red de seguridad hasta que vuelva |
| `DEFERRED` (presupuesto interno > 2 500 ms) | **> 1 %** de las alertas | Alarma de rendimiento del receptor |

**Qué significa `UNTRUSTED`, con precisión:**

- **Bloquea afirmar paridad TV/Python** (criterio 6 del MVP) y **bloquea la promoción de fase**. Se muestra en rojo permanente hasta que un humano lo limpie con `POST /integrity/tv/ack`, dejando constancia de quién y cuándo.
- **NO bloquea operar.** Tentador y equivocado: si una discrepancia detuviera las operaciones, cualquiera capaz de enviar un payload forjado —**el canal menos confiable del sistema**— podría impedir que Mark opere. Eso es un ataque de denegación de servicio por la puerta más débil. **La ruta nativa es la autoridad y sigue funcionando aunque el puente esté envenenado.** `UNTRUSTED` degrada una afirmación de calidad; no toca `can_open_new_trade()`.
- **La única discrepancia que sí detiene operaciones es la que apunta al lado nativo** (datos stale, vela no cerrada, señal cuyo `bar_open_ms` no existe en el store), y esa entra por los kill switches del PRD §17 que ya existen, **no** por el puente.

### 7.8 El ledger inverso — la casilla que decide todo

El scanner Python escribe **su** candidato aunque TradingView no haya avisado. La tabla que importa:

|  | Python: candidato | Python: no |
|---|---|---|
| **TV: alerta** | acuerdo (medir Δ numéricos) | **falsos positivos TV** — esperado alto, por el cost gate y la política SHORT |
| **TV: sin alerta** | **falsos negativos TV** ← *la casilla que decide si el puente sirve de red de seguridad* | acuerdo |

Sin esta tabla, la pregunta *"¿mi visualización refleja mi motor?"* queda incontestable para siempre — igual que sin `record_blocked_a_plus()` queda incontestable *"¿el primer A+ del día fue peor que el mejor?"* (`AUDIT` C-14).
---

## 8. Divergencias conocidas entre TradingView y el motor Python

Las magnitudes marcadas *(medido)* provienen de simulación explícita; las marcadas *(estimado)* son estimaciones honestas pendientes de medición con el ledger de §7. **Ninguna de estas divergencias es un error del Pine ni del Python: son propiedades de tener dos implementaciones sobre dos feeds.** El objetivo del ledger es medirlas, no negarlas.

### 8.1 Cuadro resumen

Orden = P(ocurrencia por día de operación) × consecuencia × opacidad.

| # | Fuente | Frecuencia | Magnitud | ¿Silenciosa? |
|---|---|---|---|---|
| D-01 | **Identidad del feed** (símbolo / exchange) | Permanente si está mal elegido | **Total**: OHLCV distinto, otro conjunto de FVG | Sí, casi total |
| D-02 | Pine no implementa cost gate, `allow_short` ni límite diario | Casi todas las alertas | Alerta ↔ NO TRADE | No: es ruidosa y se interpreta al revés |
| D-03 | Parámetros duplicados sin handshake de versión | En cada cambio de `Config` | Arbitraria, ilimitada por diseño | **Sí, 100 %** |
| D-04 | Semántica de cobertura: fail-open en Python vs fail-closed en Pine | Diaria en arranques y huecos | Trade entero aparece o desaparece | Sí |
| D-05 | Semilla de EMA + histórico corto | Continua | 0,12 % del precio en EMA50 4H con 104 buckets; **flip de régimen en 0,78 % de las barras** *(medido)* | Sí |
| D-06 | HTF: desfase de una vela + alineación de buckets | 24 barras/día (8,3 %) | Booleanos HTF opuestos en la barra frontera | Semi |
| D-07 | Huecos de velas (FVG, edad, ATR y volumen contados por índice) | Eventos de mantenimiento | FVG existe en un lado y no en el otro | Sí |
| D-08 | Pine acumula estado; Python es función pura | Cada recarga del gráfico | Histórico irreproducible | Sí |
| D-09 | VWAP: unidad de volumen y anclaje de sesión | Continua | 0,5–1,4 bps ≈ **7–19 % de la semibanda** `0,30·ATR` *(medido)* | Sí |
| D-10 | Cierre de vela y latencia de alerta contra el TTL de 90 s | Cada señal | Señal muerta al llegar | Detectable |
| D-11 | Conjunto de niveles de clearance (ventana por tiempo vs por índice) | 1–3 % de las evaluaciones *(estimado)* | `clearance_R` salta de 1,4R a ∞ | Sí |
| D-12 | ATR SMA vs RMA (**latente**) | Solo si alguien "arregla" el Pine | 3,7 % mediana / 15,6 % p99 en ATR *(medido)* | Sí |
| D-13 | Media de volumen de 20 (**latente**) | Solo si alguien "arregla" el Pine | ratio ±5 % | Sí |
| D-14 | Precisión: tick, redondeo y comparaciones estrictas | Empates en la rejilla de ticks | Booleano invertido ⇒ señal entera | Sí |
| D-15 | Límites de objetos gráficos / `runtime.error` | Sesiones largas | **Indicador muerto sin aviso** | Sí |

### 8.2 Las que necesitan explicación

**D-02 — el Pine implementa 8 de 12 gates.** Faltan cost gate, política `allow_short`, límite diario y los tres gates operativos. Consecuencia numérica: el cost gate exige `risk/entry ≥ 0,0013/0,15 = 0,867 %` en LONG y `≥ 1,083 %` en SHORT. Con HYPE en torno a $45 eso son **R ≥ $0,39 (LONG) / $0,49 (SHORT)**, es decir **2,2–5,8 ATR20 de 5m** según el régimen de volatilidad. Un stop estructural de retest de FVG en 5m rara vez está tan lejos. **La mayoría de los candidatos visuales del Pine morirán en el cost gate**, y con `allow_short = false` el **100 % de las alertas SHORT son falsos positivos garantizados**. Mitigación: portar el cost gate al Pine como *display-only* (la fórmula, no el tier real) y pintar en gris el candidato que no pasa, con la etiqueta `COST_R = x.xx > límite`; añadir el input `allowShort` con default `false`.

**D-04 — el fail-open está en Python, no en Pine.** `target_clearance.coverage_ok()` exige `min_1h = max(2·2+1, 2+1) = 5` velas 1H, con `clearance_lookback_1h = 48` declarado. Con 5 velas no hay pivotes en la ventana ⇒ `levels` vacío ⇒ `clearance_r = inf` ⇒ **el gate aprueba**. La propia docstring advierte contra ese fallo y la implementación se queda a 43 velas de evitarlo. El Pine es más estricto (exige la sesión previa al 100 %, 288/288, frente al 80 % = 230 de Python). **Esto es un bug del motor Python que la comparación con Pine ha puesto a la vista** (R03-02), y se arregla en Python: `min_1h = clearance_lookback_1h + clearance_pivot_n`. **No se relaja el Pine al 80 % para conseguir una paridad ficticia.**

**D-05 — la semilla de EMA.** `indicators.ema_series()` siembra con `vals[0]`; `ta.ema` de TradingView siembra con la SMA de `length`. El error decae como `(1−k)^N`, pero con `k(50) = 0,0392` la vida media es de 17,3 barras: con 104 buckets 4H (el techo real de `candleSnapshot`, 5 000 velas de 5m) el residual de EMA50 es ~0,12 % del precio, un 5 % del spread típico EMA20−EMA50, y **el booleano de régimen 4H difiere en el 0,78 % de las evaluaciones** *(medido sobre 4 000 trayectorias GBM)*. Son ~2,2 barras de 5m al día, concentradas justo en los cruces de EMA, que es cuando el régimen decide. Agravante interno de Python: como el driver pasa `b4[p4−400:p4]`, la semilla **se mueve** entre escaneos y el valor de EMA50 para el mismo bucket cambia. El PRD §4 exige función pura de la historia; con esa construcción no lo es. Mitigación real: sembrar con SMA(n), exigir `min_htf_bars ≈ 200` y **pedir velas 1H/4H nativas a Hyperliquid** en vez de resamplear (resuelve también D-06′ y parte de D-07).

**D-06 — el desfase de una vela es medible y conocido.** En la vela de 5m que cierra a las 12:00, Pine con `[1] + lookahead_on` usa el bucket 1H de las 10:00; Python con `completed_upto(now_close_ms = 12:00)` usa el de las 11:00, que acaba de cerrar. **Exactamente 1 barra de 5m, solo en la barra frontera:** 24 de 288 barras al día (8,3 %) usan un input 1H distinto, y 6 (2,08 %) además un 4H distinto. No es representativo de la tasa de divergencia de candidatos: probablemente la **subestima**, porque la frontera horaria es donde más confirmaciones se disparan. **Elegir cuál de los dos criterios es el correcto es una decisión de producto; tenerlos distintos no lo es.**

**D-09 — la unidad de volumen es la incógnita.** El anclaje del VWAP **sí coincide** (`floor(time/86400000)` en Pine ≡ `session_start_ms(ts, 0)` en Python) y el precio típico también (`hlc3` ≡ `tp`). Lo que no está verificado es si TradingView publica el volumen del feed de Hyperliquid en coins base o en nocional. Si fuera nocional, el VWAP pasa a ser `Σ tp·(p·q) / Σ (p·q)`, otro objeto matemático: desplazamiento de 0,49 bps mediana y 1,38 bps p90 *(medido)*, que sobre una semibanda de `0,30·ATR ≈ $0,033` es **7 % a 19 % de la banda**, y **sesgado sistemáticamente hacia arriba en días alcistas**, no ruido centrado. Verificación en §2.3.

**D-12 y D-13 son latentes, no activas.** Hoy el preview usa `ta.sma(ta.tr(true), 20)` (≡ `atr_at`, media simple de TR) y `ta.sma(volume[1], 20)` (≡ `avg_volume`, excluye la vela actual). **Están alineados.** Pero son alineaciones **accidentales, no protegidas por ningún test**: el nombre natural que cualquiera escribiría al "limpiar" el código es `ta.atr(20)` —que es RMA de Wilder— y `ta.sma(volume, 20)` —que incluye la vela actual en su propia media—. Mitigación tosca y eficaz, del mismo tipo que el repo ya usa: **test de CI que falle si el `.pine` contiene `ta.atr(` o si no contiene `ta.sma(ta.tr(true)` y `ta.sma(volume[1]`**.

**D-14 — los empates de un tick.** El motor está lleno de comparaciones **estrictas** sobre precios: `bar.c > max(g.hi, vwap_k)`, `bar.c > c5[k-1].h`, `c5[i].l > c5[i-2].h`, `closes[-1] > ef[-1] > es[-1]`. En una rejilla de ticks los empates son frecuentes y **una diferencia de un solo tick entre feeds invierte el booleano**. El caso extremo es un FVG de un tick de ancho. Mitigación que elimina la clase entera de golpe, aplicada **en los dos lados a la vez**: tolerancia explícita de un tick (`bar.c > c5[k-1].h + tick/2`) y anchura mínima de gap (`width >= 2·tick`), que además filtra gaps degenerados que no son señal de nada. **Es un cambio de reglas y por tanto una decisión de producto, no un parche de ingeniería.**

**D-15 — el fallo silencioso del propio indicador.** `max_boxes_count = 150`: al superarlo TradingView borra el box más antiguo, el `Gap` sigue en el array con un id inválido y `box.set_right()` sobre él puede detener el script. Los dos `runtime.error` de `barstate.isfirst` matan el indicador si alguien cambia de temporalidad. **Cero alertas es indistinguible de "hoy no hubo setups", que además es el resultado correcto por defecto según PRD §0.** Por eso el `heartbeat` de §7.3 no es un adorno: es lo que convierte el silencio en información.

### 8.3 Anexo — equivalencias de fórmula que el `.pine` debe respetar

Cada línea de esta lista es una divergencia latente si se escribe "la que suene mejor" en vez de "la que hace Python".

| Python (fuente de verdad) | Pine correcto | Pine **prohibido** |
|---|---|---|
| `atr_at`: media simple de 20 TR | `ta.sma(ta.tr(true), 20)` | `ta.atr(20)` (RMA de Wilder) |
| `avg_volume`: `c5[idx-20:idx]`, **excluye la actual** | `ta.sma(volume, 20)[1]` | `ta.sma(volume, 20)` |
| `vwap_at`: `hlc3` ponderado, reset 00:00 UTC | acumulador manual `pv/vv` con `newSession` sobre `floor(time/86400000)` | `ta.vwap` sin verificar anclaje |
| `completed_upto`: último HTF cerrado | `request.security(tickerid, tf, expr[1], lookahead = barmerge.lookahead_on)` | cualquier otra combinación (repinta o retrasa) |
| `rsi_series`: Wilder, primer valor válido en índice 14 | `ta.rsi(src, 14)` | — |
| `ema_series`: sembrada con `vals[0]` | EMA manual sembrada en la primera barra, o `ta.ema` con la divergencia documentada | — (decisión consciente, ver D-05) |
| Pivotes 1H, `n=2`, comparaciones **no estrictas** | `high[2] >= high, high[1], high[3], high[4]` escrito a mano | `ta.pivothigh(2,2)` sin verificar la estrictez |
| Cobertura de sesión previa: **230** velas (80 % de 288) | el umbral que decida el arreglo de D-04, **igual en los dos lados** | `dayBars == 288` en un solo lado |
| Toque LONG: `low <= g.hi`, no estricto | idéntico | exigir entrada en la zona |
| Invalidación: **antes** que el toque, en la misma barra | idéntico, y también dentro de la ventana de confirmación | comprobarla después |
| Edad ≤ 12: **solo se comprueba en el toque** | idéntico; un gap no tocado no muere por edad | envejecer gaps no tocados |
| Ventana de confirmación: `touch_j`, `+1`, `+2` | idéntico; muerte en `+3` | ventanas de 4 |
| Varios gaps confirmando en la misma barra | gana el de `i3` **mayor** (el más reciente) | el primero |

---

## 9. Lo que NO se construye todavía

Ningún elemento de esta lista existe hoy en el repo. Se declara para que nadie asuma lo contrario.

| # | Artefacto | Estado | Bloqueante |
|---|---|---|---|
| 1 | `tradingview/hype_copilot_tv_v1.pine` (emisor de `hype.tv.signal.v1`) | **No existe.** Solo hay `hype_copilot_preview.pine`, sin compilar ni verificar | — |
| 2 | `backend/app/api/webhooks_tradingview.py` | **No existe.** `backend/app/api/` solo contiene `__init__.py` | Sí, para todo §7 |
| 3 | `backend/app/db/models.py` (tablas `tv_alert`, `signal`, `gate_evaluation`, `decision`, `trade`, `hypothetical_outcome`, `discrepancy`, `latency_sample`, `day_state`) | **No existe.** El journal actual es JSONL append-only en `services/scanner.py` | Sí |
| 4 | `backend/app/services/discrepancy.py` (comparador con las tolerancias de §7.4) | **No existe** | Sí |
| 5 | `params_hash` en ambos lados + test de CI | **No existe** | Sí, para §7.2 |
| 6 | Store de velas alimentado por WS | **No existe.** Sin él, el tramo 5 de §6.1 se dispara a segundos | Sí, para el presupuesto de 90 s |
| 7 | SSE (`GET /signals/stream`) | **No existe** | Sí, para el presupuesto de 90 s |
| 8 | Pre-aviso de `WAITING_FOR_CONFIRMATION` (§6.3-2) | **No existe** | No, pero es el mayor arreglo del problema humano |
| 9 | Panel de integridad y `POST /integrity/tv/ack` | **No existe** | No |
| 10 | Prueba de identidad de feed TV ↔ Hyperliquid (§3.3-4) | **No hecha** | Sí, antes de afirmar paridad |
| 11 | Fixture CSV compartido y test de paridad de `bar_close_ms` | **No existe**, y el `TEST_PLAN.md` no lo contempla todavía | Sí, para el criterio 6 del MVP |
| 12 | Despliegue público del receptor | **No existe.** Sin host en 443, TradingView no puede entregar nada | Sí, para conectar el canal |

### 9.1 Defectos abiertos del backend que este puente **no** arregla y que lo bloquean

| Ref | Defecto | Efecto sobre el puente |
|---|---|---|
| `risk/limits.py:24` | `from ..indicators import session_start_ms` — el módulo está en `app/strategies/indicators.py`. `app.risk.limits` y `app.execution.order_guard` **fallan al importar** | El handler del webhook depende de ambos. **Corregir antes de escribir una línea del puente** |
| R03-02 | `coverage_ok` acepta 5 velas 1H con lookback declarado de 48 (fail-open de clearance) | Produce `TV_ONLY` y `LEVEL_DRIFT` sistemáticos y **contamina la tasa base de discrepancia** |
| R03-01 | `multi_timeframe(now_ms=None)` deja que cada consulta capture su reloj; puede mezclar cierres de 03:55 y 04:00 | Falsea la comparación en la barra frontera, justo donde D-06 ya divergía |
| — | `engine.scan()` no verifica que `c5[-1]` esté cerrada | Lookahead latente. Precondición dura: `c5[-1].ts + 300000 <= now_ms`, o `reason="last_bar_not_closed"` |
| — | `risk/limits.DayState` es un dataclass en memoria | Un reinicio resetea `trades_opened`, `consecutive_losses` y el flag `critical`, que **por diseño no debe tener recuperación automática** |
| `AUDIT` C-14 | `DayState.blocked_a_plus` vive en una lista en memoria | El A+ bloqueado por límite diario se pierde al reiniciar |

**Ninguno de estos seis es trabajo de paridad. Son defectos del motor que la comparación con Pine puso a la vista — y ése es, por sí solo, el mejor argumento para mantener el indicador conectado como instrumento de medida.**

### 9.2 Orden de trabajo sugerido (mayor retorno primero)

| # | Acción | Coste | Elimina |
|---|---|---|---|
| 1 | Corregir el import de `risk/limits.py` | 5 min | **Bloqueante duro** |
| 2 | `params_hash` + validación de `tickerid` exacto | 1 h | D-01, D-03 |
| 3 | Arreglar `coverage_ok` y separar `inf` de `not_evaluable` | 1 h | Bug real + D-04, D-11 |
| 4 | Precondición "última vela cerrada" en `scan()` | 30 min | Lookahead latente + D-10 |
| 5 | Corregir `.env.example` (§0.1) y las referencias a "PRD 11" | 15 min | Deuda documental de C-13 |
| 6 | Guardas de CI sobre el texto del `.pine` (`ta.atr(`, `ta.sma(volume,`) | 1 h | D-12, D-13 |
| 7 | Sembrar `ema_series` con SMA(n) + `min_htf_bars` | 2 h | D-05 |
| 8 | Velas 1H/4H **nativas** de Hyperliquid en vez de `resample()` | 3 h | D-05, D-06′, D-07 (parcial) |
| 9 | Payload enriquecido + ledger de discrepancias en el receptor | 1 día | Hace medibles D-05/06/09/11 |
| 10 | Detección de contigüidad + velas sintéticas marcadas | 1 día | D-07 |
| 11 | Fixture CSV compartido + test de paridad de `bar_close_ms` | 2 días | Verifica todo lo anterior |

---

## 10. Resumen de una línea

**TradingView dibuja y avisa; Python decide; Mark ejecuta.** El puente no tiene autoridad, y por eso puede estar conectado sin ser un riesgo. Su valor no es disparar señales: es **discrepar** — y cada discrepancia registrada es información que ningún sistema de un solo motor puede darse a sí mismo.
