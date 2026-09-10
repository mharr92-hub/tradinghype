"""
api/signals.py — la unica superficie con la que Mark decide (PRD 16, 17).

Tres rutas y ninguna mas:

    GET  /api/signals/current          ¿cual es la mejor oportunidad AHORA?
    POST /api/signals/{id}/enter       Mark dice si
    POST /api/signals/{id}/skip        Mark dice no

DECISIONES QUE NO SON OBVIAS:

  * LEER NO PUEDE CAMBIAR NADA. `current` no recalcula la estrategia: sirve lo
    ya persistido. Si un GET pudiera generar señales, dos pestañas abiertas
    producirian dos historias distintas del mismo dia.
  * ENTER PASA SIEMPRE por `order_guard.revalidate()` y por
    `assert_can_send_orders()`. Con LIVE_EXECUTION=false eso NO es un 500: es
    un 409 con el motivo completo. La diferencia importa — un 500 se lee como
    "se rompio algo" y termina en un reintento; un 409 con
    `live_execution_disabled` se lee como "el sistema hizo su trabajo".
  * EL MOTIVO DEL GATE SE MUESTRA LITERAL. `drift_exceeded:0.137R` enseña algo;
    "no se pudo entrar" no enseña nada y erosiona la confianza en el sistema.
  * NINGUNA RESPUESTA EXPONE PROBABILIDADES. Cada payload de salida pasa por
    `scoring.assert_no_probability()` antes de serializarse, incluido el de
    error. Es una guarda de producto, no de tipos (PRD 14).
  * `Idempotency-Key` es OBLIGATORIA en ENTER y en SKIP. Repetir una alerta
    produce ruido; repetir un ENTER produciria dos posiciones.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.signal_manager import ActionResult, SignalManager
from ..strategies.hype.scoring import assert_no_probability

router = APIRouter(prefix="/api/signals", tags=["signals"])

# Motivos de SKIP: lista cerrada + texto libre. Cerrada para poder agrupar,
# con texto libre porque la razon real casi nunca cabe en una etiqueta.
SKIP_REASON_CODES = ("no_me_gusta_contexto", "noticia", "no_disponible", "otro")

# Traduccion codigo -> HTTP. Vive en una tabla y no repartida por los handlers
# para que sea evidente que ningun fallo de gate acaba en un 500.
_HTTP_FOR_CODE = {
    "entered": status.HTTP_200_OK,
    "skipped": status.HTTP_200_OK,
    "duplicate": status.HTTP_200_OK,
    "signal_not_found": status.HTTP_404_NOT_FOUND,
    "gate_rejected": status.HTTP_409_CONFLICT,
    "simulated_entry_rejected": status.HTTP_409_CONFLICT,
    "live_execution_disabled": status.HTTP_409_CONFLICT,
    "live_path_not_implemented": status.HTTP_501_NOT_IMPLEMENTED,
    "paper_executor_not_wired": status.HTTP_503_SERVICE_UNAVAILABLE,
}

_MANAGER: Optional[SignalManager] = None


def configure(manager: SignalManager) -> None:
    """Cablea el gestor. Sin esto las rutas responden 503: preferimos que la
    app diga "no estoy lista" a que sirva un estado vacio que se lea como
    "hoy no hay nada"."""
    global _MANAGER
    _MANAGER = manager


def _manager() -> SignalManager:
    if _MANAGER is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"error": "signals_not_configured"})
    return _MANAGER


def _guarded(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ultimo filtro antes de salir por el cable."""
    try:
        assert_no_probability(payload)
    except AssertionError as e:
        # Fallar ruidosamente es correcto: la alternativa seria servir el campo
        # prohibido y descubrirlo en una captura de pantalla.
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail={"error": "output_guard_failed",
                                    "message": str(e)})
    return payload


def _idempotency(key: Optional[str]) -> str:
    if not key or len(key) < 8 or len(key) > 64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"error": "idempotency_key_required",
                                    "message": "Cada clic debe traer su propia "
                                               "Idempotency-Key (8-64 chars): "
                                               "un reintento de red no puede "
                                               "abrir una segunda posicion."})
    return key


def _respond(result: ActionResult) -> Dict[str, Any]:
    body = _guarded({"ok": result.ok, "code": result.code, **result.payload})
    http = _HTTP_FOR_CODE.get(result.code, status.HTTP_409_CONFLICT)
    if result.ok and http == status.HTTP_200_OK:
        return body
    raise HTTPException(http, detail=body)


class SkipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(default="otro")
    reason_text: str = Field(default="", max_length=2000)


@router.get("/current")
def current() -> Dict[str, Any]:
    """Estado de hoy y, si existe, la señal viva.

    Devuelve SIEMPRE `server_now_ms`: el cliente calcula la cuenta atras contra
    el reloj del servidor, nunca contra el suyo, y no la reinicia en cada
    re-render. Un contador que corre con el reloj del navegador miente en
    cuanto el portatil vuelve de suspension.

    Cuando no hay señal devuelve el MOTIVO (`no_setup`, `session_warmup`,
    `regime_or_align_fail`, `cost_gate`, `target_clearance`,
    `momentum_fail:rsi`, ...). La app tiene que poder explicar por que hoy no
    hay nada; un null no explica nada.
    """
    return _guarded(_manager().current())


@router.post("/{signal_id}/enter")
def enter(signal_id: str = Path(min_length=8, max_length=64),
          idempotency_key: Optional[str] = Header(default=None,
                                                  alias="Idempotency-Key"),
          actor: str = Header(default="mark", alias="X-Actor")) -> Dict[str, Any]:
    """ENTER. Revalida con datos frescos antes de nada.

    Nada de lo que la UI tenia en pantalla se da por bueno: puede llevar 80
    segundos ahi. Las 9 comprobaciones del PRD 17 corren aqui con precio y
    velas recien pedidos, y la decision se registra pase o no el gate — un
    ENTER rechazado es informacion de primera: el humano dijo si y la maquina
    dijo no.

    Ningun gate se relaja, no se persigue la entrada y no se ensancha el stop.
    """
    key = _idempotency(idempotency_key)
    return _respond(_manager().enter(signal_id, key, actor=actor))


@router.post("/{signal_id}/skip")
def skip(body: SkipRequest,
         signal_id: str = Path(min_length=8, max_length=64),
         idempotency_key: Optional[str] = Header(default=None,
                                                 alias="Idempotency-Key"),
         actor: str = Header(default="mark", alias="X-Actor")) -> Dict[str, Any]:
    """SKIP con motivo.

    Se acepta tambien despues del TTL: un SKIP explicito y un EXPIRED por
    tiempo agotado son datos DISTINTOS y colapsarlos borraria el corte MARK
    ACCEPTED vs MARK SKIPPED del PRD 19. La señal no se borra: permanece en el
    journal con todos sus niveles y se resuelve hipoteticamente igual que las
    bloqueadas, que es lo que permitira medir si la seleccion discrecional
    mejora la estrategia o la empeora.
    """
    key = _idempotency(idempotency_key)
    code = body.reason_code if body.reason_code in SKIP_REASON_CODES else "otro"
    return _respond(_manager().skip(signal_id, key, reason_code=code,
                                    reason_text=body.reason_text, actor=actor))
