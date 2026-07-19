"""Strict live boundary for V14.17 cost-adjusted consensus.

The historical controller is not installed in live execution. Live metadata
must explicitly contain reconciled broker-net contextual evidence before a
V14.17 contextual demotion is allowed. Missing evidence preserves the inherited
V14.16 decision and never authorizes additional risk.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_16_quality_allocation_live import QualityAllocationLiveExecutor
from .v14_17_cost_adjusted_consensus import (
    CONTEXT_DEMOTION_MULTIPLIER,
    MINIMUM_RISK_PERCENT,
    live_context_evidence_authorized,
)


class CostAdjustedConsensusLiveExecutor(QualityAllocationLiveExecutor):
    """Apply only broker-reconciled negative context; never add live uplift."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._recent_v14_17: list[dict[str, Any]] = []

    def _decision_for_signal(self, signal):
        current = super()._decision_for_signal(signal)
        if current is None or current.is_shadow:
            return current

        payload = dict(signal.metadata).get("v14_17_context_evidence")
        authorized, authorization_reason = live_context_evidence_authorized(payload)
        final = current
        action = "V14_16_DECISION_RETAINED"
        if authorized and str(signal.mode).upper() == "V12":
            direction = dict(payload.get("direction", {}))
            mean_r = float(direction.get("mean_r", 0.0) or 0.0)
            profit_factor = float(direction.get("profit_factor", 0.0) or 0.0)
            if mean_r < -0.05 and profit_factor < 0.95:
                risk = max(
                    MINIMUM_RISK_PERCENT,
                    float(current.risk_percent) * CONTEXT_DEMOTION_MULTIPLIER,
                )
                final = CostRegimeDecision(
                    funded=risk > 0,
                    regime="REASONING_REDUCED",
                    risk_percent=risk,
                    reason=(
                        f"{current.reason}; V14.17 live broker-net contextual "
                        f"demotion; {authorization_reason}"
                    ),
                    all_in_cost_r=current.all_in_cost_r,
                    target_r=current.target_r,
                )
                action = "LIVE_CONTEXT_DEMOTED"

        self._recent_v14_17.append(
            {
                "signal_key": signal.key,
                "symbol": signal.symbol,
                "engine": signal.engine,
                "mode": signal.mode,
                "action": action,
                "authorization_reason": authorization_reason,
                "parent": asdict(current),
                "final": asdict(final),
            }
        )
        self._recent_v14_17 = self._recent_v14_17[-200:]
        return final

    def v14_17_snapshot(self) -> dict[str, Any]:
        return {
            "live_requires_reconciled_context": True,
            "recent_decisions": self._recent_v14_17[-100:],
            "v14_16": self.quality_allocation_snapshot(),
        }


__all__ = ["CostAdjustedConsensusLiveExecutor"]
