"""Process evidence and net outcomes, with denominators and missing data visible."""
from .policy import HARD_CHECKS, ZERO_TOLERANCE, promotion_gate


def performance(rows):
    complete = [r for r in rows if r.get("net_r") is not None and r.get("net_pnl_usd") is not None]
    values = [r["net_pnl_usd"] for r in complete]
    gains = sum(max(0, p) for p in values)
    losses = -sum(min(0, p) for p in values)
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"sample_size": len(complete), "missing_outcomes": len(rows) - len(complete),
            "net_pnl_usd": sum(values) if complete else None,
            "net_r": sum(r["net_r"] for r in complete) if complete else None,
            "net_expectancy_r": sum(r["net_r"] for r in complete) / len(complete) if complete else None,
            "net_profit_factor": gains / losses if losses else None,
            "profit_factor_status": "NO_LOSSES" if complete and not losses else "AVAILABLE" if losses else "NO_DATA",
            "win_rate": sum(p > 0 for p in values) / len(complete) if complete else None,
            "max_drawdown_usd": drawdown if complete else None}


def summarize(events, *, mode="PAPER", block_id=None, counterfactual_model=None,
              statistical_gates_passed=False, safety_gates_passed=False):
    blocks = [e for e in events if e["kind"] == "BLOCK_STARTED" and e["payload"]["mode"] == mode]
    selected = {e["block_id"] for e in blocks if block_id is None or e["block_id"] == block_id}
    scoped = [e for e in events if e["block_id"] in selected]
    signals = {e["signal_id"]: e["payload"]["plan"] for e in scoped if e["kind"] == "SIGNAL"}
    decisions = {e["signal_id"]: e["payload"] for e in scoped if e["kind"] == "DECISION"}
    results = [e for e in scoped if e["kind"] == "RESULT"]
    payloads = [e["payload"] for e in results]
    checks = [p["checks"] for p in payloads]
    # Missing mandatory checks count in denominator, and prevent promotion.
    denominator = sum(len(set(HARD_CHECKS) | set(c)) for c in checks)
    numerator = sum(sum(v is True for v in c.values()) for c in checks)
    adherence = 100 * numerator / denominator if denominator else None
    complete = bool(results) and all(p.get("evidence_complete") is True for p in payloads)
    violation_map = dict(stop_widenings="stop_not_widened", revenge_trades="daily_limit",
                         risk_overrides="no_risk_override", expired_entries="not_expired",
                         outside_drift_entries="drift", unprotected_positions="sl_verified")
    counts = {key: sum(c.get(check) is False for c in checks) for key, check in violation_map.items()}
    completed = {e["block_id"] for e in scoped if e["kind"] == "BLOCK_COMPLETED"}
    # An incomplete/aborted block cannot hide its violations behind an earlier good block.
    validation_complete = bool(selected) and selected == completed
    gate = promotion_gate(adherence_pct=adherence, validation_complete=validation_complete,
                          counts=counts, statistical_gates_passed=statistical_gates_passed,
                          safety_gates_passed=safety_gates_passed, evidence_complete=complete)
    quadrants = {"good_trade_win": 0, "good_trade_loss": 0,
                 "bad_trade_win": 0, "bad_trade_loss": 0}
    for p in payloads:
        if p.get("process_quality") and p.get("pnl_outcome") in ("WIN", "LOSS"):
            prefix = "bad" if p["process_quality"] == "VIOLATION" else "good"
            quadrants[f"{prefix}_trade_{p['pnl_outcome'].lower()}"] += 1
    manual = [p for p in payloads if p["exit_reason"] == "MANUAL_EXIT"]
    attempts = [e["payload"]["code"] for e in scoped if e["kind"] == "ATTEMPT"]
    cf = {e["signal_id"]: e["payload"] for e in scoped if e["kind"] == "COUNTERFACTUAL"
          and counterfactual_model is not None and e["payload"]["model_version"] == counterfactual_model}
    a_plus = {sid for sid, p in signals.items() if p["a_plus"]}
    accepted = {sid for sid, d in decisions.items() if d["decision"] == "ENTER"} & a_plus
    skipped = {sid for sid, d in decisions.items() if d["decision"] == "SKIP"
               and d["response"]["skipped"]} & a_plus
    actual = {e["signal_id"]: e["payload"] for e in results}
    paired = accepted & set(cf) & set(actual)
    days = [e["payload"] for e in events if e["kind"] == "DAY_CLOSED"
            and e["payload"]["mode"] == mode]
    status = ("ELIGIBLE" if gate["eligible"] else "VALIDATION" if statistical_gates_passed
              else "EARLY" if results else "UNPROVEN")
    return dict(performance(payloads), **quadrants,
                process_adherence_pct=adherence, process_checks_passed=numerator,
                process_checks_required=denominator, evidence_complete=complete,
                process_violations=sum(p["process_quality"] == "VIOLATION" for p in payloads),
                unassessed_trades=sum(p["process_quality"] is None for p in payloads),
                manual_exit_count=len(manual),
                average_manual_exit_r=sum(p["net_r"] for p in manual) / len(manual) if manual else None,
                fomo_attempts=attempts.count("PRICE_DRIFT"),
                missed_drift_signals=sum(e["kind"] == "MISSED" for e in scoped),
                stop_move_attempts=sum(a in ("STOP_WIDENING_PROHIBITED", "STOP_POLICY_NOT_ENABLED") for a in attempts),
                risk_override_attempts=sum(a in ("RISK_OVERRIDE_PROHIBITED", "AVERAGING_PROHIBITED", "RISK_OVER_BUDGET") for a in attempts),
                skipped_a_plus_trades=len(skipped), accepted_a_plus_signals=len(accepted),
                all_system_a_plus_signals=len(a_plus), execution_blocks_completed=len(completed),
                no_trade_days=sum(d["no_trade"] and d["coverage_complete"] for d in days),
                incomplete_data_days=sum(not d["coverage_complete"] for d in days),
                all_system_a_plus=performance([cf.get(sid, {}) for sid in sorted(a_plus)]),
                mark_accepted=performance([actual.get(sid, {}) for sid in sorted(accepted)]),
                mark_skipped_hypothetical=performance([cf.get(sid, {}) for sid in sorted(skipped)]),
                paired_system_vs_mark_pnl_usd=(sum(actual[s]["net_pnl_usd"] - cf[s]["net_pnl_usd"] for s in paired)
                                                if paired else None),
                paired_sample_size=len(paired), counterfactual_model=counterfactual_model,
                counterfactual_warning="Hypothetical per-signal outcomes are not a feasible portfolio equity curve.",
                sample_status=status, promotion=gate, zero_tolerance_counts=counts)
