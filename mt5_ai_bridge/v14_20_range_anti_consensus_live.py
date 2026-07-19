"""V14.20 live boundary for range anti-consensus risk reduction.

This module does not connect to MT5 or send orders.  It exposes the stricter
broker-reconciled authorization rule used by a future demo-forward adapter.
"""
from __future__ import annotations

from typing import Any, Mapping

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_20_range_anti_consensus import live_conflict_shadow_authorized


def apply_live_range_anti_consensus(
    current: CostRegimeDecision,
    evidence_payload: Mapping[str, Any] | None,
) -> CostRegimeDecision:
    authorized, reason = live_conflict_shadow_authorized(evidence_payload)
    if not authorized or not current.funded or current.is_shadow:
        return current
    return CostRegimeDecision(
        funded=False,
        regime="SHADOW",
        risk_percent=0.0,
        reason=f"{current.reason}; V14.20 live range anti-consensus; {reason}",
        all_in_cost_r=current.all_in_cost_r,
        target_r=current.target_r,
    )


__all__ = ["apply_live_range_anti_consensus"]
