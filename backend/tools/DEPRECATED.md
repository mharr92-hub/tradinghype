# backtest_hype_long_v0.py — DEPRECADO (2026-09-09)

Se conserva como referencia historica y como oraculo de comparacion contra el
motor nuevo (`app/research/backtest.py`, pendiente).

**Sus resultados NO son concluyentes para el modelo v2.** Limitaciones que el
nuevo modelo hace descalificantes (ver `docs/AUDIT_001_CONFLICTOS.md`):

| Limitacion | Conflicto |
|---|---|
| Sin funding | C-04 — con tenencias de hasta 24 h el funding pesa |
| Entrada al cierre de la vela de confirmacion | C-05 — sesgo optimista sistematico |
| Sin limite de trades por dia | C-06 |
| Sin time stop de 24 h | C-07 |
| Solo LONG, solo 1:1 | C-01, C-02 |

No usar sus numeros para decidir nada. No borrarlo: la comparacion contra el
motor nuevo es la forma de saber cuanto sesgo aportaba cada una de esas
limitaciones.
