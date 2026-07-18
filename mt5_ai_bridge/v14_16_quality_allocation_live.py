"""Strict, stress-buffered live export for V14.16 quality allocation.

The implementation module resolves ``quality_risk_target`` at runtime. Replace
that symbol with the frozen-nominal wrapper so a signal already reduced by an
upstream profile cannot be promoted. Quality allocations are also capped before
entry so balance minus all open stop risk stays above the 9.40% projected-stress
floor used by the exact replay.
"""
from __future__ import annotations

from . import v14_16_quality_allocation_execution as implementation
from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_16_quality_nominal import strict_quality_risk_target

QUALITY_PROJECTED_STRESS_LIMIT_PERCENT = 9.40


def projected_quality_capacity_percent(
    *,
    balance: float,
    peak_equity: float,
    open_stop_risk_dollars: float,
    stress_limit_percent: float = QUALITY_PROJECTED_STRESS_LIMIT_PERCENT,
) -> float:
    """Return remaining admission capacity as a percentage of balance."""
    balance = max(0.0, float(balance))
    peak = max(balance, float(peak_equity))
    open_risk = max(0.0, float(open_stop_risk_dollars))
    if balance <= 0 or peak <= 0:
        return 0.0
    floor_equity = peak * (1.0 - float(stress_limit_percent) / 100.0)
    allowed_dollars = max(0.0, balance - open_risk - floor_equity)
    return allowed_dollars / balance * 100.0


implementation.quality_risk_target = strict_quality_risk_target


class QualityAllocationLiveExecutor(implementation.QualityAllocationLiveExecutor):
    """Apply frozen nominal tiers and the replay's projected-stress buffer."""

    def _decision_for_signal(self, signal):
        decision = super()._decision_for_signal(signal)
        if decision is None or decision.regime != "QUALITY_ALLOCATED":
            return decision

        account = self.client.account_info()
        if account is None:
            return decision
        balance = float(getattr(account, "balance", 0.0) or 0.0)
        equity = float(getattr(account, "equity", balance) or balance)
        peak = max(
            balance,
            equity,
            float(self.state.data.get("peak_equity", 0.0) or 0.0),
        )
        open_risk = sum(
            self._position_risk_dollars(position)
            for position in self._positions()
        )
        capacity = projected_quality_capacity_percent(
            balance=balance,
            peak_equity=peak,
            open_stop_risk_dollars=open_risk,
        )
        approved = min(float(decision.risk_percent), capacity)
        minimum = float(getattr(self.governor, "minimum_risk_percent", 0.025))
        if approved < minimum - 1e-12:
            return CostRegimeDecision(
                funded=False,
                regime="SHADOW",
                risk_percent=0.0,
                reason=(
                    f"{decision.reason}; projected-stress capacity {capacity:.4f}% "
                    f"is below minimum {minimum:.4f}%"
                ),
                all_in_cost_r=decision.all_in_cost_r,
                target_r=decision.target_r,
            )
        return CostRegimeDecision(
            funded=True,
            regime=decision.regime,
            risk_percent=approved,
            reason=(
                f"{decision.reason}; projected-stress capacity "
                f"{capacity:.4f}% at {QUALITY_PROJECTED_STRESS_LIMIT_PERCENT:.2f}% limit"
            ),
            all_in_cost_r=decision.all_in_cost_r,
            target_r=decision.target_r,
        )


__all__ = [
    "QUALITY_PROJECTED_STRESS_LIMIT_PERCENT",
    "QualityAllocationLiveExecutor",
    "projected_quality_capacity_percent",
]
