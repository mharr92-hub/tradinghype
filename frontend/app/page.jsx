"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const CHECK_LABELS = {
  regime_4h: "4H",
  align_1h: "1H",
  fvg: "FVG",
  first_retest: "1er retest",
  vwap_band: "VWAP",
  confirmation: "Confirm",
  rsi: "RSI",
  macd: "MACD",
  volume: "Vol",
  target_clearance: "Clearance",
  cost_gate: "Costos",
};

const usd = (n) =>
  n === null || n === undefined ? "—" : `$${Number(n).toFixed(2)}`;
const px = (n) =>
  n === null || n === undefined ? "—" : Number(n).toFixed(3);

function Cell({ k, v, tone }) {
  return (
    <div className="cell">
      <div className="k">{k}</div>
      <div className={`v ${tone || ""}`}>{v ?? "—"}</div>
    </div>
  );
}

function regimeTone(r) {
  if (r === "BULLISH") return "long";
  if (r === "BEARISH") return "short";
  return "muted";
}

function SignalCard({ card, onEnter, onSkip, busy }) {
  // La cuenta atrás corre en el cliente para que el segundero se mueva, pero
  // la autoridad sobre si la señal vive es del backend: aquí solo se muestra.
  const [left, setLeft] = useState(card.seconds_remaining);
  useEffect(() => {
    setLeft(card.seconds_remaining);
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [card.seconds_remaining, card.signal_age_seconds]);

  const dead = left <= 0;
  const side = card.side === "LONG" ? "long" : "short";

  return (
    <div className={`card ${side}`}>
      <div className="card-head">
        <div className="card-title">HYPE {card.side} · CANDIDATO</div>
        <div
          className={`countdown ${dead ? "dead" : left < 30 ? "urgent" : ""}`}
        >
          {dead ? "EXPIRADA" : `${Math.floor(left)}s restantes`}
        </div>
      </div>

      <div className="checks">
        {Object.entries(CHECK_LABELS).map(([k, label]) => (
          <span
            key={k}
            className={`chk ${card.checklist[k] ? "on" : "off"}`}
            title={k}
          >
            {label}
          </span>
        ))}
      </div>

      <div className="levels">
        <div className="lvl">
          <div className="k">Entry</div>
          <div className="v">{px(card.entry)}</div>
        </div>
        <div className="lvl">
          <div className="k">Stop</div>
          <div className="v">{px(card.stop)}</div>
        </div>
        <div className="lvl">
          <div className="k">TP {card.rr}R</div>
          <div className="v">{px(card.tp)}</div>
        </div>
        <div className="lvl">
          <div className="k">Riesgo plan.</div>
          <div className="v">{usd(card.risk_usd_planned)}</div>
        </div>
        <div className="lvl">
          <div className="k">Benef. en TP</div>
          <div className="v">{usd(card.expected_profit_usd)}</div>
        </div>
        <div className="lvl">
          <div className="k">Cantidad</div>
          <div className="v">{card.qty}</div>
        </div>
        <div className="lvl">
          <div className="k">Nocional</div>
          <div className="v">{usd(card.notional)}</div>
        </div>
        <div className="lvl">
          <div className="k">Cost_R</div>
          <div className="v">
            {card.cost_r?.toFixed(3)}
            {!card.fee_taker_confirmed && "*"}
          </div>
        </div>
        <div className="lvl">
          <div className="k">Fees / Funding</div>
          <div className="v">
            {usd(card.estimated_fees_usd)} / {usd(card.estimated_funding_usd)}
          </div>
        </div>
        <div className="lvl">
          <div className="k">Clearance</div>
          <div className="v">{card.clearance_r?.toFixed(2)}R</div>
        </div>
      </div>

      <div className="actions">
        <button
          className="enter"
          disabled={!card.executable || dead || busy}
          onClick={onEnter}
        >
          ENTER
        </button>
        <button className="skip" disabled={busy} onClick={onSkip}>
          SKIP
        </button>
      </div>

      {!card.executable && card.block_reason && (
        <p className="block">Bloqueada: {card.block_reason}</p>
      )}
    </div>
  );
}

export default function Home() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/dashboard`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setD(await r.json());
      setErr(null);
    } catch (e) {
      setErr(`No se pudo hablar con el backend (${API}): ${e.message}`);
    }
  }, []);

  useEffect(() => {
    load();
    timer.current = setInterval(load, 5000);
    return () => clearInterval(timer.current);
  }, [load]);

  const post = async (path, body) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const j = await r.json();
      setMsg(r.ok ? JSON.stringify(j) : j.detail || `HTTP ${r.status}`);
      await load();
    } catch (e) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (err) {
    return (
      <div className="wrap">
        <div className="brand">HYPE COPILOT</div>
        <ul className="warnings">
          <li>{err}</li>
        </ul>
        <p className="question">
          Arranca el backend: <code>cd backend &amp;&amp; uvicorn app.main:app --reload</code>
        </p>
      </div>
    );
  }

  if (!d) return <div className="wrap">Cargando…</div>;

  return (
    <div className="wrap">
      <div className="top">
        <div className="brand">HYPE COPILOT</div>
        <div className="badges">
          <span className="badge">{d.mode}</span>
          <span className={`badge ${d.live_execution ? "warn" : "safe"}`}>
            {d.live_execution ? "LIVE EXECUTION ON" : "ejecución real apagada"}
          </span>
          <span className="badge">día: {d.day_state}</span>
          <span className="badge">trades hoy: {d.trades_today}</span>
        </div>
      </div>
      <p className="question">¿Cuál es la mejor oportunidad de HYPE ahora?</p>

      {d.warnings?.length > 0 && (
        <ul className="warnings">
          {d.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      <div className="grid">
        <Cell k="Precio HYPE" v={px(d.price)} />
        <Cell k="VWAP" v={`${px(d.vwap)} · ${d.vwap_state}`} />
        <Cell k="Régimen 4H" v={d.regime_4h} tone={regimeTone(d.regime_4h)} />
        <Cell
          k="Alineación 1H"
          v={d.alignment_1h}
          tone={d.alignment_1h === "ALIGNED" ? "long" : "muted"}
        />
        <Cell
          k="Estado LONG"
          v={d.long_state}
          tone={d.long_state === "NO_SETUP" ? "muted" : "long"}
        />
        <Cell
          k="Estado SHORT"
          v={d.short_state}
          tone={
            d.short_state === "NO_SETUP" || d.short_state === "DISABLED"
              ? "muted"
              : "short"
          }
        />
        <Cell
          k="FVG actual"
          v={d.fvg ? `${px(d.fvg.lo)} – ${px(d.fvg.hi)}` : "ninguno"}
          tone={d.fvg ? "" : "muted"}
        />
        <Cell
          k="Datos"
          v={
            d.data_age_seconds === null
              ? "—"
              : `hace ${Math.round(d.data_age_seconds)}s`
          }
          tone={d.data_age_seconds > 420 ? "short" : "muted"}
        />
      </div>

      {d.card ? (
        <SignalCard
          card={d.card}
          busy={busy}
          onEnter={() => post("/api/enter", { confirm: true })}
          onSkip={() => post("/api/skip", { reason: "manual" })}
        />
      ) : (
        <div className="empty">
          <div className="big">NO TRADE</div>
          <p className="sub">
            No hay ningún setup A+ ahora mismo. Es un resultado válido, no un
            fallo: el sistema no fuerza operaciones.
          </p>
          <div className="waiting">
            <div className="wrow">
              <span className="wk">LONG</span>
              <span className={`wv ${d.long_blocked_by ? "blocked" : "live"}`}>
                {d.long_blocked_by
                  ? `BLOQUEADO: ${d.long_blocked_by}`
                  : `EN CURSO · ${d.long_state}`}
              </span>
            </div>
            <div className="wrow">
              <span className="wk">SHORT</span>
              <span className={`wv ${d.short_blocked_by ? "blocked" : "live"}`}>
                {d.short_blocked_by
                  ? `BLOQUEADO: ${d.short_blocked_by}`
                  : `EN CURSO · ${d.short_state}`}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Lo que el sistema ha visto hoy. Un contador de "casi" alto con cero
          señales significa algo muy distinto de un día sin oportunidades. */}
      <div className="grid" style={{ marginTop: 18 }}>
        <Cell k="Señales A+ hoy" v={d.a_plus_today} tone={d.a_plus_today ? "long" : "muted"} />
        <Cell k="Casi (near miss)" v={d.near_misses_today} tone={d.near_misses_today ? "" : "muted"} />
        <Cell k="Velas escaneadas" v={d.scans_today} tone="muted" />
        <Cell
          k="Forward log"
          v={d.forward_log_running ? "CORRIENDO ✓" : "PARADO ✗"}
          tone={d.forward_log_running ? "long" : "short"}
        />
        <Cell
          k="Errores de datos"
          v={d.data_errors_today}
          tone={d.data_errors_today ? "short" : "muted"}
        />
        <Cell
          k="Estrategia"
          v={`${d.strategy_arm || "—"} · ${(d.strategy_fingerprint || "").slice(0, 8)}`}
          tone="muted"
        />
      </div>

      {d.position && (
        <div className="card" style={{ marginTop: 18 }}>
          <div className="card-head">
            <div className="card-title">POSICIÓN ABIERTA (PAPER)</div>
            <button disabled={busy} onClick={() => post("/api/position/resolve")}>
              Resolver
            </button>
          </div>
          <div className="levels">
            <div className="lvl">
              <div className="k">Lado</div>
              <div className="v">{d.position.side}</div>
            </div>
            <div className="lvl">
              <div className="k">Fill</div>
              <div className="v">{px(d.position.entry_price)}</div>
            </div>
            <div className="lvl">
              <div className="k">Stop</div>
              <div className="v">{px(d.position.stop)}</div>
            </div>
            <div className="lvl">
              <div className="k">TP</div>
              <div className="v">{px(d.position.tp)}</div>
            </div>
          </div>
        </div>
      )}

      {msg && <p className="foot">respuesta: {msg}</p>}
      <p className="foot">
        Python es la fuente de verdad · TradingView solo visualiza · Cost_R con *
        usa un fee supuesto, no confirmado
      </p>
    </div>
  );
}
