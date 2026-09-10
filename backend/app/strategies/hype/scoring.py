"""
strategies/hype/scoring.py — definicion de A+ y score de cumplimiento (PRD 14).

DOS REGLAS QUE NO SE NEGOCIAN:

1. El score mide CUMPLIMIENTO DE REGLAS, no probabilidad de exito. El sistema
   no sabe si el trade va a ganar. Mostrar "87% probability" sin un modelo
   entrenado, validado OOS y calibrado es inventarse un numero, y un numero
   inventado con dos decimales es peor que no dar ninguno: se siente preciso.

2. "Best trade of the day" NO significa el mejor trade de las proximas 15
   horas: eso requeriria conocer el futuro. Significa el PRIMER setup que
   alcanza A+. Si ninguno lo alcanza, el resultado correcto es NO TRADE.

`assert_no_probability()` existe para que el test 31 del TEST_PLAN pueda
verificar que nadie ha añadido un campo de probabilidad "para que se vea mejor".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Tuple

# Gates que DEBEN pasar para que un setup sea A+. La ausencia de cualquiera
# no baja el score: descalifica.
MANDATORY_LONG = ("regime_4h", "align_1h", "fvg", "first_retest", "vwap_band",
                  "confirmation", "target_clearance", "cost_gate")
MANDATORY_SHORT = MANDATORY_LONG + ("rsi", "macd", "volume", "vwap_slope")

# Palabras prohibidas en cualquier payload que llegue a la UI.
_FORBIDDEN = ("probability", "prob", "win_chance", "confidence", "odds",
              "probabilidad", "confianza")


# Gates OPERATIVOS. El motor de estrategia no puede evaluarlos: dependen del
# venue (sizing), del estado del dia (limite diario) y de la salud del sistema
# (kill switches). Por eso A+ no se puede declarar dentro de engine.py.
OPERATIONAL_GATES = ("sizing_ok", "daily_limit_ok", "kill_switches_clear")


@dataclass(frozen=True)
class Score:
    """Cumplimiento de reglas. Deliberadamente NO contiene probabilidades.

    Dos niveles, y la distincion importa:

      `rules_complete` — todos los gates que el motor SI puede evaluar con las
          velas en la mano. Corresponde al estado LONG_CANDIDATE/SHORT_CANDIDATE
          del PRD 10.
      `is_a_plus` — ademas, los gates operativos (sizing valido, limite diario
          disponible, kill switches limpios) comprobados y en verde. Es el
          A_PLUS_READY del PRD 10 y lo unico que puede etiquetarse "A+" en la
          Signal Card.

    Un score sin gates operativos evaluados NUNCA es A+, aunque las reglas de
    mercado esten perfectas: un setup impecable que no cabe en el tamaño minimo
    del venue no es un trade A+, es un trade imposible.
    """
    side: str
    passed: int
    total: int
    rules_complete: bool
    is_a_plus: bool
    failed: Tuple[str, ...] = field(default_factory=tuple)
    quality: Dict[str, float] = field(default_factory=dict)
    operational_evaluated: bool = False

    @property
    def label(self) -> str:
        return f"RULE COMPLIANCE {self.passed}/{self.total}"

    @property
    def stage(self) -> str:
        if self.is_a_plus:
            return "A_PLUS_READY"
        if self.rules_complete:
            return f"{self.side}_CANDIDATE"
        return "WAITING"

    def as_dict(self) -> Dict[str, object]:
        return {"side": self.side, "passed": self.passed, "total": self.total,
                "rules_complete": self.rules_complete, "is_a_plus": self.is_a_plus,
                "operational_evaluated": self.operational_evaluated,
                "failed": list(self.failed), "quality": dict(self.quality),
                "label": self.label, "stage": self.stage}


def mandatory_for(side: str) -> Tuple[str, ...]:
    """En SHORT el momentum es obligatorio; en LONG es el brazo F2 (AUDIT C-11)."""
    return MANDATORY_SHORT if side == "SHORT" else MANDATORY_LONG


def score(side: str, checks: Mapping[str, object],
          require_momentum_long: bool = False,
          operational: Mapping[str, object] | None = None) -> Score:
    """Calcula el cumplimiento. `operational` son los gates que el motor de
    estrategia no puede evaluar; sin ellos el resultado nunca es A+.

    Un gate AUSENTE cuenta como fallido, no como aprobado: `checks.get(k) is
    True` es deliberadamente estricto para que una clave que nadie escribio no
    se cuele como verde.
    """
    keys = list(mandatory_for(side))
    if side == "LONG" and require_momentum_long:
        # F2 son cuatro condiciones, no tres: la direccion del VWAP entra con
        # las otras (PRD 5.5). Omitirla producia un "A+" al que le faltaba un
        # cuarto del filtro.
        keys += ["rsi", "macd", "volume", "vwap_slope"]
    passed = [k for k in keys if checks.get(k) is True]
    failed = tuple(k for k in keys if checks.get(k) is not True)

    # Dimensiones de calidad: hechos observados, no predicciones. Sirven para
    # ordenar candidatos y para el journal; nunca para relajar un gate.
    quality: Dict[str, float] = {}
    for k in ("clearance_r", "volume_ratio", "cost_r", "fvg_width_atr",
              "vwap_distance_atr"):
        v = checks.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            quality[k] = float(v)

    rules_complete = (len(failed) == 0)

    op_evaluated = operational is not None
    op_failed: Tuple[str, ...] = ()
    if op_evaluated:
        op_failed = tuple(k for k in OPERATIONAL_GATES
                          if operational.get(k) is not True)
    else:
        # Sin gates operativos evaluados no se puede afirmar A+. Se marcan como
        # pendientes para que la razon aparezca en `failed` y en el journal.
        op_failed = tuple(f"{k}:not_evaluated" for k in OPERATIONAL_GATES)

    return Score(side=side, passed=len(passed), total=len(keys),
                 rules_complete=rules_complete,
                 is_a_plus=(rules_complete and op_evaluated and not op_failed),
                 failed=failed + op_failed, quality=quality,
                 operational_evaluated=op_evaluated)


def assert_no_probability(payload: Mapping[str, object]) -> None:
    """Falla si un payload expone algo que un usuario leeria como probabilidad.

    Recorre en profundidad. Es una guarda de producto, no una comprobacion de
    tipos: protege la regla del PRD 14 frente a futuras "mejoras" bienintencionadas.
    """
    def walk(node, path=""):
        if isinstance(node, Mapping):
            for k, v in node.items():
                lk = str(k).lower()
                if any(f in lk for f in _FORBIDDEN):
                    raise AssertionError(
                        f"Campo prohibido {path}{k!r}: el sistema no expone "
                        f"probabilidades sin un modelo calibrado OOS (PRD 14)."
                    )
                walk(v, f"{path}{k}.")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}].")

    walk(payload)
