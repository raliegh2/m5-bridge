"""Strict live boundary for the V14.18 hierarchical regime meta-labeler.

Live execution never reconstructs research evidence from modeled outcomes.  A
non-FULL label requires an explicitly chronological, broker-reconciled evidence
payload with larger samples than the exact replay.  Missing or immature evidence
preserves V14.17 risk and can never authorize an uplift.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_17_cost_adjusted_consensus_live import CostAdjustedConsensusLiveExecutor
from .v14_18_hierarchical_regime_meta import (
    FULL_MULTIPLIER,
    HIERARCHICAL_POSITIVE_OVERRIDE_R,
    MINIMUM_RISK_PERCENT,
    HierarchicalPosterior,
    classify_market_regime,
    live_hierarchy_authorized,
    meta_label_from_evidence,
)


class HierarchicalRegimeMetaLiveExecutor(CostAdjustedConsensusLiveExecutor):
    """Apply only authorized no-uplift V14.18 live meta-labels."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._recent_v14_18: list[dict[str, Any]] = []

    @staticmethod
    def _posterior_from_payload(payload: dict[str, Any]) -> HierarchicalPosterior:
        hierarchy = dict(payload.get("hierarchy", {}))
        return HierarchicalPosterior(
            score_r=float(hierarchy.get("score_r", 0.0) or 0.0),
            confidence=float(hierarchy.get("confidence", 0.0) or 0.0),
            mature_negative_nodes=int(
                hierarchy.get("mature_negative_nodes", 0) or 0
            ),
            node_count=int(hierarchy.get("node_count", 0) or 0),
            effective_trades=int(hierarchy.get("effective_trades", 0) or 0),
            nodes=dict(hierarchy.get("nodes", {})),
        )

    def _decision_for_signal(self, signal):
        current = super()._decision_for_signal(signal)
        if current is None or current.is_shadow:
            return current

        payload = dict(signal.metadata).get("v14_18_hierarchical_meta")
        authorized, authorization_reason = live_hierarchy_authorized(payload)
        final = current
        label = "FULL"
        meta_reason = authorization_reason
        market_regime = classify_market_regime(
            mode=signal.mode,
            engine=signal.engine,
            setup=signal.setup,
            consensus=(payload or {}).get("consensus", "UNAVAILABLE"),
            parent_regime=current.regime,
        )

        if authorized:
            posterior = self._posterior_from_payload(dict(payload))
            if posterior.score_r >= HIERARCHICAL_POSITIVE_OVERRIDE_R:
                meta_reason = "STRONG_POSITIVE_HIERARCHY_OVERRIDE"
            else:
                meta = meta_label_from_evidence(
                    current=current,
                    mode=signal.mode,
                    all_in_cost_r=current.all_in_cost_r,
                    market_regime=market_regime,
                    posterior=posterior,
                    direction_evidence=dict(payload.get("direction", {})),
                )
                label = meta.label
                meta_reason = f"{authorization_reason}; {meta.reason}"
                if label == "SHADOW":
                    final = CostRegimeDecision(
                        funded=False,
                        regime="SHADOW",
                        risk_percent=0.0,
                        reason=f"{current.reason}; V14.18 live SHADOW; {meta_reason}",
                        all_in_cost_r=current.all_in_cost_r,
                        target_r=current.target_r,
                    )
                elif meta.multiplier < FULL_MULTIPLIER:
                    risk = min(
                        float(current.risk_percent),
                        max(
                            MINIMUM_RISK_PERCENT,
                            float(current.risk_percent) * float(meta.multiplier),
                        ),
                    )
                    final = CostRegimeDecision(
                        funded=risk > 0,
                        regime="REASONING_REDUCED",
                        risk_percent=risk,
                        reason=(
                            f"{current.reason}; V14.18 live {market_regime}/{label}; "
                            f"{meta_reason}"
                        ),
                        all_in_cost_r=current.all_in_cost_r,
                        target_r=current.target_r,
                    )

        self._recent_v14_18.append(
            {
                "signal_key": signal.key,
                "symbol": signal.symbol,
                "engine": signal.engine,
                "mode": signal.mode,
                "market_regime": market_regime,
                "meta_label": label,
                "authorization_reason": authorization_reason,
                "meta_reason": meta_reason,
                "parent": asdict(current),
                "final": asdict(final),
            }
        )
        self._recent_v14_18 = self._recent_v14_18[-200:]
        return final

    def v14_18_snapshot(self) -> dict[str, Any]:
        return {
            "live_requires_reconciled_hierarchy": True,
            "risk_uplift_allowed": False,
            "range_mean_reversion_engine_implemented": False,
            "recent_decisions": self._recent_v14_18[-100:],
            "v14_17": self.v14_17_snapshot(),
        }


__all__ = ["HierarchicalRegimeMetaLiveExecutor"]
