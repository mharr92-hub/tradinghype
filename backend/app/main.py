"""
main.py — API del dashboard. Responde UNA pregunta (PRD 16):

    "¿Cual es la mejor oportunidad de HYPE ahora?"

Y cuando la respuesta es "ninguna", lo dice claramente en vez de enseñar una
pantalla vacia: NO TRADE es un resultado, no un error.

MODO PAPER: `ENTER` SIMULA. Ninguna ruta de este modulo llama al exchange.
`LIVE_EXECUTION` sigue en false y, si algun dia se enciende, la ejecucion real
entra por `execution/hyperliquid.py` detras de `assert_can_send_orders()`, no
por aqui.

Estado en memoria a proposito: esta capa sirve la pantalla. La evidencia vive
en el journal JSONL, que es append-only, sobrevive a un reinicio y no depende
de que este proceso siga vivo. Si el servidor se cae, se pierde la vista, no
los datos.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .adapters.hyperliquid_data import HyperliquidData, MarketDataError
from .core.config import load_settings
from .execution.paper import PaperBroker, PaperPosition, summarize
from .research.fills import FillModel
from .research.funding import FundingCurve
from .risk.limits import DayState, can_open_new_trade
from .risk.sizing import VenueSpec, expected_profit_usd, size_position
from .services.scanner import ForwardLogger, ScanRecord, Scanner
from .strategies.hype.common import SIDE_LONG, Config
from .strategies.hype.scoring import assert_no_probability

app = FastAPI(title="HYPE Copilot", version="2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
)

SETTINGS = load_settings()
CFG = Config(
    allow_short=SETTINGS.allow_short,
    risk_mode="fixed_usd" if SETTINGS.mode in ("TINY",) else "pct_equity",
    risk_usd=1.00 if SETTINGS.mode == "TINY" else 125.0,
)


class _State:
    """Estado vivo del proceso. Deliberadamente pequeño."""

    def __init__(self):
        self.data = HyperliquidData(SETTINGS.hyperliquid_api_url)
        self.scanner = Scanner(CFG, mode=SETTINGS.mode,
                               equity=SETTINGS.equity_usd, data=self.data,
                               logger=ForwardLogger("journal"))
        self.last: Optional[ScanRecord] = None
        self.last_scan_ms: int = 0
        self.position: Optional[PaperPosition] = None
        self.closed: List[dict] = []
        self.skipped: List[dict] = []
        self.error: Optional[str] = None

    @property
    def day(self) -> DayState:
        return self.scanner.day


S = _State()


# ---------------------------------------------------------------------------
# Modelos de respuesta
# ---------------------------------------------------------------------------

class Card(BaseModel):
    """La Signal Card del PRD 16. Sin un solo campo de probabilidad."""
    side: str
    entry: float
    stop: float
    tp: float
    rr: float
    risk_usd_planned: float          # PLANIFICADO, no maximo. Un hueco lo supera.
    expected_profit_usd: Optional[float]
    qty: float
    notional: float
    cost_r: float
    estimated_fees_usd: float
    estimated_funding_usd: float
    clearance_r: float
    signal_age_seconds: float
    seconds_remaining: float
    executable: bool
    block_reason: Optional[str]
    checklist: Dict[str, bool]
    fee_taker_confirmed: bool        # False = Cost_R provisional


class Dashboard(BaseModel):
    mode: str
    live_execution: bool
    price: Optional[float]
    vwap: Optional[float]
    vwap_state: str
    regime_4h: str
    alignment_1h: str
    long_state: str
    short_state: str
    setup_state: str
    day_state: str
    trades_today: int
    fvg: Optional[Dict[str, float]]
    card: Optional[Card]
    position: Optional[dict]
    data_age_seconds: Optional[float]
    error: Optional[str]
    warnings: List[str]
    # Que esta esperando el sistema, no solo que no opera.
    long_blocked_by: Optional[str] = None
    short_blocked_by: Optional[str] = None
    a_plus_today: int = 0
    near_misses_today: int = 0
    scans_today: int = 0
    data_errors_today: int = 0
    forward_log_running: bool = False
    strategy_arm: Optional[str] = None
    strategy_fingerprint: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _regime_label(rec: ScanRecord) -> str:
    if rec.regime_4h_long:
        return "BULLISH"
    if rec.regime_4h_short:
        return "BEARISH"
    return "NEUTRAL"


def _vwap_state(rec: ScanRecord) -> str:
    if rec.price is None or rec.vwap is None:
        return "—"
    if rec.price > rec.vwap:
        return "ABOVE"
    return "BELOW" if rec.price < rec.vwap else "AT"


def _side_state(rec: ScanRecord, side: str) -> str:
    """Estado por direccion. Solo la direccion evaluada puede estar en curso:
    el regimen 4H no puede ser alcista y bajista a la vez."""
    if rec.side != side:
        return "NO_SETUP"
    return rec.state


def _blocked_by(rec: ScanRecord, side: str) -> Optional[str]:
    """QUE esta impidiendo esta direccion ahora mismo.

    "NO TRADE" a secas no dice nada util: no distingue "el mercado no tiene
    tendencia" de "hay un setup montado esperando la vela de confirmacion".
    Son situaciones opuestas — en una no va a pasar nada en horas, en la otra
    puede saltar en cinco minutos — y el operador necesita saber en cual esta.
    """
    if side == "SHORT" and not SETTINGS.allow_short:
        return "SHORT deshabilitado por politica"

    regime_ok = rec.regime_4h_long if side == "LONG" else rec.regime_4h_short
    if not regime_ok:
        return "regimen 4H"
    if rec.side != side:
        # El regimen 4H permite esta direccion pero el motor evaluo la otra, o
        # ninguna: el que falla es el 1H.
        return "alineacion 1H"

    if rec.state == "WAITING_FOR_RETEST":
        return None                       # hay FVG vivo; no es un bloqueo
    if rec.state == "WAITING_FOR_CONFIRMATION":
        return None                       # tocado, esperando la vela
    if rec.reason.startswith("momentum_fail:"):
        return f"momentum ({rec.reason.split(':', 1)[1]})"
    if rec.reason == "target_clearance":
        cr = rec.clearance_r
        return f"sin espacio hasta el objetivo ({cr:.2f}R)" if cr else "target clearance"
    if rec.reason == "cost_gate":
        return f"costos ({rec.cost_r:.3f}R)" if rec.cost_r else "costos"
    if rec.reason == "no_setup":
        return "sin FVG en la sesion"
    if rec.reason == "session_warmup":
        return f"warmup de sesion ({rec.session_bars} velas)"
    return rec.reason


# Motivos que significan "el setup existia y se quedo a un paso". Sirven para
# contar NEAR MISSES: si hay muchos, el sistema esta viendo el mercado pero
# algun umbral esta demasiado apretado — o justo bien. Sin el conteo no se
# puede distinguir "no hay oportunidades" de "las hay y las estoy filtrando".
_NEAR_MISS_PREFIXES = ("momentum_fail:", "target_clearance", "cost_gate",
                       "rules_incomplete:")


def _today_stats() -> Dict[str, object]:
    """Recuento del dia leido del JOURNAL, no de memoria.

    Se lee del archivo a proposito: si el proceso se reinicio hace diez minutos,
    la memoria dice cero y el journal dice la verdad. El journal es la evidencia.
    """
    import json as _json
    import os as _os

    day = time.strftime("%Y-%m-%d", time.gmtime())
    path = _os.path.join("journal", f"scans-{day}.jsonl")
    out = {"a_plus": 0, "near_misses": 0, "scans": 0, "errors": 0,
           "forward_log_running": False, "last_write_age_s": None}
    if not _os.path.isfile(path):
        return out
    try:
        age = time.time() - _os.path.getmtime(path)
        out["last_write_age_s"] = round(age, 1)
        # El escaner escribe una vez por vela de 5m. Si el ultimo registro tiene
        # mas de 11 minutos, se perdio al menos una vela: no esta corriendo.
        out["forward_log_running"] = age < 11 * 60
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = _json.loads(line)
                out["scans"] += 1
                reason = r.get("reason", "")
                if r.get("has_signal"):
                    out["a_plus"] += 1
                elif any(reason.startswith(p) for p in _NEAR_MISS_PREFIXES):
                    out["near_misses"] += 1
                if reason.startswith(("market_data_error", "stale_data")):
                    out["errors"] += 1
    except (OSError, ValueError):
        pass
    return out


def _build_card(rec: ScanRecord, now_ms: int) -> Optional[Card]:
    if not rec.has_signal or rec.entry_ref is None:
        return None
    age = rec.signal_age_seconds if rec.signal_age_seconds is not None else 0.0
    notional = rec.notional or 0.0
    fees = 2.0 * (rec.fee_taker or 0.0) * notional
    funding = (rec.funding_rate_hourly or 0.0) * 4.0 * notional
    if rec.side != SIDE_LONG:
        funding = -funding
    return Card(
        side=rec.side or "?", entry=rec.entry_ref, stop=rec.stop or 0.0,
        tp=rec.tp or 0.0, rr=rec.rr or CFG.rr,
        risk_usd_planned=rec.risk_usd or 0.0,
        expected_profit_usd=rec.expected_profit_usd,
        qty=rec.qty or 0.0, notional=notional,
        cost_r=rec.cost_r or 0.0,
        estimated_fees_usd=fees, estimated_funding_usd=funding,
        clearance_r=rec.clearance_r or 0.0,
        signal_age_seconds=age,
        seconds_remaining=max(0.0, CFG.signal_ttl_seconds - age),
        executable=bool(rec.executable),
        block_reason=rec.execution_block_reason,
        checklist={k: bool(rec.checks.get(k)) for k in
                   ("regime_4h", "align_1h", "fvg", "first_retest", "vwap_band",
                    "confirmation", "rsi", "macd", "volume", "target_clearance",
                    "cost_gate")},
        fee_taker_confirmed=rec.fee_taker_confirmed,
    )


def _warnings(rec: Optional[ScanRecord]) -> List[str]:
    """Avisos que la UI debe mostrar SIEMPRE, no enterrar en un tooltip."""
    w = ["Riesgo PLANIFICADO, no maximo: un hueco de precio puede superarlo."]
    if not SETTINGS.live_execution:
        w.append(f"LIVE_EXECUTION=false — modo {SETTINGS.mode}: ENTER simula.")
    if rec and not rec.fee_taker_confirmed:
        w.append("Fee taker es un SUPUESTO (4.5 bps), no el tier real: Cost_R provisional.")
    if rec and rec.data_age_seconds and rec.data_age_seconds > 420:
        w.append(f"Datos con {rec.data_age_seconds:.0f}s de antiguedad.")
    return w


def _refresh(force: bool = False) -> Optional[ScanRecord]:
    """Escanea como mucho una vez cada 20 s: la vela de 5m no cambia mas rapido
    y no tiene sentido castigar la API porque alguien deje la pestaña abierta."""
    now = int(time.time() * 1000)
    if not force and S.last and now - S.last_scan_ms < 20_000:
        return S.last
    try:
        S.last = S.scanner.scan_once(now)
        S.last_scan_ms = now
        S.error = None
    except MarketDataError as e:
        S.error = str(e)
    return S.last


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "mode": SETTINGS.mode,
            "live_execution": SETTINGS.live_execution,
            "allow_short": SETTINGS.allow_short}


@app.get("/api/dashboard", response_model=Dashboard)
def dashboard():
    rec = _refresh()
    now = int(time.time() * 1000)
    if rec is None:
        return Dashboard(
            mode=SETTINGS.mode, live_execution=SETTINGS.live_execution,
            price=None, vwap=None, vwap_state="—", regime_4h="—",
            alignment_1h="—", long_state="—", short_state="—",
            setup_state="—", day_state=S.day.state, trades_today=0,
            fvg=None, card=None, position=None, data_age_seconds=None,
            error=S.error, warnings=_warnings(None))

    stats = _today_stats()
    fvg = None
    if rec.checks.get("fvg_lo") is not None:
        fvg = {"lo": float(rec.checks["fvg_lo"]), "hi": float(rec.checks["fvg_hi"])}

    payload = Dashboard(
        mode=SETTINGS.mode, live_execution=SETTINGS.live_execution,
        price=rec.price, vwap=rec.vwap, vwap_state=_vwap_state(rec),
        regime_4h=_regime_label(rec),
        alignment_1h=("ALIGNED" if rec.checks.get("align_1h") else "NOT_ALIGNED"),
        long_state=_side_state(rec, "LONG"),
        short_state=_side_state(rec, "SHORT") if SETTINGS.allow_short else "DISABLED",
        setup_state=rec.state, day_state=S.day.state,
        trades_today=S.day.trades_opened, fvg=fvg,
        card=_build_card(rec, now),
        position=summarize(S.position) if S.position else None,
        data_age_seconds=rec.data_age_seconds, error=S.error,
        warnings=_warnings(rec),
        long_blocked_by=_blocked_by(rec, "LONG"),
        short_blocked_by=_blocked_by(rec, "SHORT"),
        a_plus_today=stats["a_plus"], near_misses_today=stats["near_misses"],
        scans_today=stats["scans"], data_errors_today=stats["errors"],
        forward_log_running=bool(stats["forward_log_running"]),
        strategy_arm=CFG.arm_label(), strategy_fingerprint=CFG.fingerprint(),
    )
    # PRD 14: la API no puede exponer nada que se lea como probabilidad.
    assert_no_probability(payload.model_dump())
    return payload


class EnterRequest(BaseModel):
    confirm: bool = True


@app.post("/api/enter")
def enter(_: EnterRequest):
    """ENTER. En PAPER simula; nunca envia dinero.

    Se REVALIDA aunque la pantalla dijera que era ejecutable: la pantalla puede
    llevar 80 segundos ahi y el mercado no espera (PRD 17).
    """
    if S.position is not None:
        raise HTTPException(409, "ya hay una posicion abierta")

    rec = _refresh(force=True)
    if rec is None or not rec.has_signal:
        raise HTTPException(409, f"no hay señal viva: {S.error or (rec.reason if rec else 'sin datos')}")
    if not rec.executable:
        raise HTTPException(409, f"señal no ejecutable: {rec.execution_block_reason}")

    if SETTINGS.can_send_real_orders:
        # Este camino no existe todavia. Fallar es lo correcto: mejor un error
        # explicito que una orden real por un modo mal configurado.
        raise HTTPException(501, "ejecucion real no implementada; usar PAPER")

    c4h, c1h, c5 = S.data.multi_timeframe()
    from .strategies.hype import engine
    r = engine.scan(c4h, c1h, c5, S.scanner.cfg)
    if r.signal is None:
        raise HTTPException(409, f"la señal ya no existe al revalidar: {r.reason}")

    venue = S.scanner.venue_spec()
    z = size_position(r.signal.entry_ref, r.signal.stop, r.signal.side,
                      S.scanner.cfg, SETTINGS.equity_usd, venue, r.signal.cost_frac)
    if not z.ok:
        raise HTTPException(409, f"sizing invalido: {z.reason}")

    broker = PaperBroker(S.scanner.cfg, FillModel(),
                         funding=None, fallback_funding_rate=None)
    res = broker.enter(r.signal, z, S.day, c5, len(c5) - 1)
    if not res.entered:
        # Un ENTER que no entra NO es un fallo del sistema: normalmente es el
        # drift. Se devuelve 200 con el motivo para que la UI lo explique.
        return {"entered": False, "reason": res.reason}

    S.position = res.position
    return {"entered": True, "position": summarize(res.position)}


class SkipRequest(BaseModel):
    reason: str = "manual"


@app.post("/api/skip")
def skip(req: SkipRequest):
    """SKIP se REGISTRA. Comparar lo que Mark acepta con lo que salta es una de
    las dos preguntas que solo el journal puede responder (PRD 16)."""
    rec = S.last
    if rec is None or not rec.has_signal:
        raise HTTPException(409, "no hay señal que saltar")
    entry = {"ts_ms": int(time.time() * 1000), "reason": req.reason,
             "side": rec.side, "entry_ref": rec.entry_ref, "stop": rec.stop,
             "tp": rec.tp, "cost_r": rec.cost_r}
    S.skipped.append(entry)
    S.scanner.logger.write(ScanRecord(
        schema=2, ts_scan_ms=entry["ts_ms"], ts_bar_ms=rec.ts_bar_ms,
        mode=SETTINGS.mode, reason=f"mark_skipped:{req.reason}",
        state=rec.state, side=rec.side, has_signal=True,
        entry_ref=rec.entry_ref, stop=rec.stop, tp=rec.tp, cost_r=rec.cost_r,
        executable=False, execution_block_reason="skipped_by_mark"))
    return {"skipped": True}


@app.post("/api/position/resolve")
def resolve_position():
    """Cierra la posicion PAPER contra las velas posteriores (SL / TP / 24 h)."""
    if S.position is None:
        raise HTTPException(409, "no hay posicion abierta")
    _, _, c5 = S.data.multi_timeframe()
    broker = PaperBroker(S.scanner.cfg, FillModel())
    pos = broker.resolve(S.position, c5, S.day)
    S.closed.append(summarize(pos))
    S.position = None
    return {"closed": True, "result": S.closed[-1]}


@app.get("/api/journal")
def journal(limit: int = 50):
    return {"closed": S.closed[-limit:], "skipped": S.skipped[-limit:],
            "blocked_by_daily_limit": S.day.blocked_a_plus[-limit:]}
