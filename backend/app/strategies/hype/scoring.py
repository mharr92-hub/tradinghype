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


@dataclass(frozen=True)
class Score:
    """Cumplimiento de reglas. Deliberadamente NO contiene probabilidades."""
    side: str
    passed: int
    total: int
    is_a_plus: bool
    failed: Tuple[str, ...] = field(default_factory=tuple)
    quality: Dict[str, float] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"RULE COMPLIANCE {self.passed}/{self.total}"

    def as_dict(self) -> Dict[str, object]:
        return {"side": self.side, "passed": self.passed, "total": self.total,
                "is_a_plus": self.is_a_plus, "failed": list(self.failed),
                "quality": dict(self.quality), "label": self.label}


def mandatory_for(side: str) -> Tuple[str, ...]:
    """En SHORT el momentum es obligatorio; en LONG es el brazo F2 (AUDIT C-11)."""
    return MANDATORY_SHORT if side == "SHORT" else MANDATORY_LONG


def score(side: str, checks: Mapping[str, object],
          require_momentum_long: bool = False) -> Score:
    keys = list(mandatory_for(side))
    if side == "LONG" and require_momentum_long:
        keys += ["rsi", "macd", "volume"]
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

    return Score(side=side, passed=len(passed), total=len(keys),
                 is_a_plus=(len(failed) == 0), failed=failed, quality=quality)


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
