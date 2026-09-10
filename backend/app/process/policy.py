"""Deterministic execution policy; no emotions, streaks, or P&L in signal/size.

The accepted plan is a value object. Costs are estimates, never a guarantee
against gaps. Outcomes and process assessments are intentionally orthogonal.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math

DAY_MS = 86_400_000
SKIP_REASONS = frozenset({"discretionary_market_context", "personal_risk",
                          "unavailable", "fear_after_losses", "other"})
HARD_CHECKS = ("a_plus", "drift", "risk", "stop_not_widened", "no_averaging",
               "not_expired", "daily_limit", "time_stop", "decision_before_outcome",
               "sl_verified", "no_risk_override")
ZERO_TOLERANCE = ("stop_widenings", "revenge_trades", "risk_overrides",
                  "expired_entries", "outside_drift_entries", "unprotected_positions")


class ProcessViolation(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def policy_config(cfg):
    # A new observed funding rate is data, not a discretionary strategy change.
    values = asdict(cfg)
    values.pop("funding_rate_hourly", None)
    return values


def finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) for v in values)


@dataclass(frozen=True)
class Plan:
    signal_id: str
    block_id: str
    config_hash: str
    mode: str
    side: str
    signal_close_ms: int
    expires_at_ms: int
    entry_ref: float
    stop: float
    target: float
    rr: float
    qty: float
    planned_loss_usd: float
    risk_budget_usd: float
    estimated_costs_usd: float
    cost_frac: float
    max_drift_r: float
    max_hold_hours: float
    setup: str
    invalidation: str
    a_plus: bool
    fee_assumption: bool

    def validate(self):
        numbers = (self.entry_ref, self.stop, self.target, self.rr, self.qty,
                   self.planned_loss_usd, self.risk_budget_usd,
                   self.estimated_costs_usd, self.cost_frac, self.max_drift_r,
                   self.max_hold_hours, self.signal_close_ms, self.expires_at_ms)
        if not finite(*numbers):
            raise ProcessViolation("INCOMPLETE_PLAN")
        if not all((self.signal_id, self.block_id, self.config_hash, self.setup,
                    self.invalidation)) or self.side not in ("LONG", "SHORT"):
            raise ProcessViolation("INCOMPLETE_PLAN")
        if min(self.entry_ref, self.stop, self.target, self.rr, self.qty,
               self.planned_loss_usd, self.risk_budget_usd, self.max_hold_hours) <= 0:
            raise ProcessViolation("INCOMPLETE_PLAN")
        if self.estimated_costs_usd < 0 or self.cost_frac < 0:
            raise ProcessViolation("INVALID_COST_ESTIMATE")
        if self.mode not in ("RESEARCH", "PAPER", "SHADOW", "TINY", "LIVE"):
            raise ProcessViolation("INVALID_MODE")
        if not (0 <= self.max_drift_r <= 0.10 and self.max_hold_hours <= 24):
            raise ProcessViolation("RISK_OVERRIDE_PROHIBITED")
        if not 0 < self.expires_at_ms - self.signal_close_ms <= 90_000:
            raise ProcessViolation("INVALID_SIGNAL_TTL")
        ordered = (self.stop < self.entry_ref < self.target if self.side == "LONG"
                   else self.target < self.entry_ref < self.stop)
        if not ordered:
            raise ProcessViolation("INVALID_PLAN_LEVELS")
        expected = abs(self.entry_ref - self.stop) * self.rr
        if not math.isclose(abs(self.target - self.entry_ref), expected, rel_tol=1e-8):
            raise ProcessViolation("INVALID_TARGET")
        if self.planned_loss_usd > self.risk_budget_usd + 1e-9:
            raise ProcessViolation("RISK_OVER_BUDGET")
        if self.mode == "TINY" and self.risk_budget_usd > 1:
            raise ProcessViolation("RISK_OVERRIDE_PROHIBITED")
        if self.mode == "LIVE" and not 100 <= self.risk_budget_usd <= 150:
            raise ProcessViolation("RISK_OVERRIDE_PROHIBITED")
        return self

    @property
    def hash(self):
        return fingerprint(asdict(self))


def entry_checks(plan, *, now_ms, executable_price, risk_accepted, plan_hash):
    plan.validate()
    if risk_accepted is not True:
        raise ProcessViolation("RISK_NOT_ACCEPTED")
    if plan_hash != plan.hash:
        raise ProcessViolation("PLAN_CHANGED_REVIEW_AGAIN")
    if not plan.a_plus:
        raise ProcessViolation("A_PLUS_REQUIRED")
    if not finite(now_ms, executable_price) or executable_price <= 0:
        raise ProcessViolation("INVALID_MARKET_DATA")
    if now_ms < plan.signal_close_ms:
        raise ProcessViolation("SIGNAL_FROM_FUTURE")
    if now_ms > plan.expires_at_ms:
        raise ProcessViolation("SIGNAL_EXPIRED")
    if (executable_price <= plan.stop if plan.side == "LONG"
            else executable_price >= plan.stop):
        raise ProcessViolation("STRUCTURE_INVALIDATED")
    movement = ((executable_price - plan.entry_ref) if plan.side == "LONG"
                else (plan.entry_ref - executable_price))
    drift = max(0, movement) / abs(plan.entry_ref - plan.stop)
    if drift > plan.max_drift_r + 1e-10:
        raise ProcessViolation("PRICE_DRIFT")
    # The acceptance caps the planned loss displayed on this exact card.
    loss = plan.qty * (abs(executable_price - plan.stop)
                       + executable_price * plan.cost_frac)
    if loss > min(plan.risk_budget_usd, plan.planned_loss_usd) + 1e-8:
        raise ProcessViolation("RISK_OVER_BUDGET")
    return {"drift_r": drift, "planned_loss_at_fill_usd": loss}


def guard_modification(side, current_stop, requested_stop, current_qty,
                       requested_qty, current_leverage=1, requested_leverage=1):
    if side not in ("LONG", "SHORT") or not finite(
            current_stop, requested_stop, current_qty, requested_qty,
            current_leverage, requested_leverage):
        raise ProcessViolation("INVALID_MODIFICATION")
    if min(current_stop, requested_stop, current_qty, requested_qty,
           current_leverage, requested_leverage) <= 0:
        raise ProcessViolation("INVALID_MODIFICATION")
    if (requested_stop < current_stop if side == "LONG" else requested_stop > current_stop):
        raise ProcessViolation("STOP_WIDENING_PROHIBITED")
    if requested_qty > current_qty:
        raise ProcessViolation("AVERAGING_PROHIBITED")
    if requested_leverage != current_leverage or requested_qty != current_qty:
        raise ProcessViolation("RISK_OVERRIDE_PROHIBITED")
    if requested_stop != current_stop:
        raise ProcessViolation("STOP_POLICY_NOT_ENABLED")
    return True


def assess_process(checks):
    """Missing evidence is unknown; P&L is deliberately not an input."""
    values = [checks.get(key) for key in HARD_CHECKS]
    if any(v is False for v in values):
        quality = "VIOLATION"
    elif any(v is not True for v in values):
        quality = None
    else:
        # Optional documented process checks affect grade, never the signal.
        extras = [v for k, v in checks.items() if k not in HARD_CHECKS]
        score = 100 * sum(v is True for v in values + extras) / len(values + extras)
        quality = "A" if score >= 95 else "B" if score >= 85 else "C"
    all_values = list(checks.values())
    adherence = (100 * sum(v is True for v in all_values) / len(all_values)
                 if all_values else None)
    return {"process_quality": quality, "adherence_pct": adherence,
            "evidence_complete": all(v is True or v is False for v in values)}


def outcome_fields(net_pnl_usd, exit_reason, process_quality):
    if not finite(net_pnl_usd) or exit_reason not in ("SL", "TP", "TIME_STOP", "MANUAL_EXIT"):
        raise ProcessViolation("INVALID_OUTCOME")
    pnl_outcome = "WIN" if net_pnl_usd > 0 else "LOSS" if net_pnl_usd < 0 else "BREAKEVEN"
    outcome = exit_reason if exit_reason in ("TIME_STOP", "MANUAL_EXIT") else pnl_outcome
    if process_quality is None:
        classification = "PROCESS_UNASSESSED"
    elif process_quality == "VIOLATION":
        classification = "PROCESS_FAILURE"
    elif pnl_outcome == "LOSS":
        classification = "NORMAL_STRATEGY_LOSS"
    else:
        classification = "FOLLOWED_PLAN"
    return {"outcome": outcome, "pnl_outcome": pnl_outcome,
            "exit_reason": exit_reason, "classification": classification}


def promotion_gate(*, adherence_pct, validation_complete, counts,
                   statistical_gates_passed, safety_gates_passed, evidence_complete):
    reasons = []
    if not finite(adherence_pct) or adherence_pct < 95:
        reasons.append("PROCESS_ADHERENCE_BELOW_95")
    if validation_complete is not True or evidence_complete is not True:
        reasons.append("VALIDATION_INCOMPLETE")
    for key in ZERO_TOLERANCE:
        if key not in counts or counts[key] != 0:
            reasons.append(key.upper())
    if statistical_gates_passed is not True:
        reasons.append("STATISTICAL_GATES_UNPROVEN")
    if safety_gates_passed is not True:
        reasons.append("SAFETY_GATES_UNPROVEN")
    return {"eligible": not reasons, "reasons": reasons,
            "automatic_promotion": False, "live_execution": False}
