"""
Paquete de tests.

Registra un alias de modulo antes de que se importe cualquier test.

Motivo: `test_hype_long_vwap_retest.py` esta CONGELADO junto al modulo que
verifica (MIGRATION_PLAN 7). Uno de sus tests comprueba por inspeccion del
codigo fuente que la cadena "SHORT" no aparece en el modulo, asi que ni el
modulo ni el test pueden editarse sin romper la garantia de reversion a
LONG ONLY. Pero la migracion movio el archivo a
`app/strategies/hype_long/rules.py`, y el test lo importa por su nombre
original.

El alias resuelve el conflicto sin tocar ninguno de los dos archivos, y hace
que funcione el comando documentado en TEST_PLAN 4:

    cd backend && python -m unittest discover -s tests -v
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)

if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _alias(name: str, relpath: str) -> None:
    path = os.path.join(_BACKEND, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registrar ANTES de ejecutar: los dataclass del modulo consultan
    # sys.modules[cls.__module__] durante su propia definicion, y sin esta
    # linea fallan con AttributeError sobre None.
    sys.modules[name] = module
    spec.loader.exec_module(module)


_alias("hype_long_vwap_retest",
       os.path.join("app", "strategies", "hype_long", "rules.py"))
