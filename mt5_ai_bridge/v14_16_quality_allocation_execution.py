"""V14.16 live executor for cost-efficient quality allocation.

The exact replay may authorize frozen historical profiles.  Live uplift is more
conservative: the individual engine and its symbol/mode sleeve must both have a
mature, positive broker-net record.  Existing reductions and all portfolio caps
remain authoritative.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .v14_3_live_execution import ExecutionResult, LiveSignal
from .v14_3_research_parity_execution import (
    PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
    ResearchParityLiveRunnerConfig,
)
from .v14_4_profit_guard import ProfitGuardConfig
from .v14_4_profit_guard_execution import ProfitGuardedLiveExecutor
from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from .v14_15_unified_reasoning_execution import UnifiedReasoningLiveExecutor
from .v14_16_quality_allocation import (
    FULL_STRENGTH_TOLERANCE,
    QUALITY_RISK_PERCENT,
    apply_quality_allocation,
    live_quality_evidence,
    quality_risk_target,
)


class QualityAllocationLiveExecutor(UnifiedReasoningLiveExecutor):
    """Promote only broker-confirmed full-strength candidates to 0.80% risk."""

    def __init__(
        self,
        client: Any,
        config: ResearchParityLiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
        guard_config: Optional[ProfitGuardConfig] = None,
        cost_config: Optional[ExtendedCostRegimeConfig] = None,
    ) -> None:
        super().__init__(
            client,
            config,
            approval_callback,
            guard_config,
            cost_config,
        )
        self._recent_quality: dict[str, dict[str, Any]] = {}

    def _quality_evidence(self, signal: LiveSignal) -> tuple[bool, str]:
        return live_quality_evidence(
            self.state.engine_results(signal.engine),
            self.state.symbol_mode_results(signal.symbol, signal.mode),
        )

    def _decision_for_signal(self, signal: LiveSignal) -> CostRegimeDecision | None:
        current = super()._decision_for_signal(signal)
        if current is None or current.is_shadow:
            return current

        evidence_ok, evidence_reason = self._quality_evidence(signal)
        nominal = self._static_base_risk(signal)
        target, profile_reason = quality_risk_target(
            symbol=signal.symbol,
            engine=signal.engine,
            setup=signal.setup,
            mode=signal.mode,
            side=signal.side,
            entry_time=signal.signal_time,
            all_in_cost_r=current.all_in_cost_r,
            nominal_risk_percent=nominal,
            current_risk_percent=current.risk_percent,
            current_decision=current,
            historical_profile_authorized=False,
            live_evidence_authorized=evidence_ok,
        )
        final = apply_quality_allocation(
            current,
            target_risk_percent=target,
            reason=f"{profile_reason}; {evidence_reason}",
        )
        self._recent_quality[signal.key] = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": signal.symbol,
            "engine": signal.engine,
            "mode": signal.mode,
            "setup": signal.setup,
            "profile_reason": profile_reason,
            "evidence_reason": evidence_reason,
            "previous_regime": current.regime,
            "previous_risk_percent": current.risk_percent,
            "final_regime": final.regime,
            "final_risk_percent": final.risk_percent,
        }
        if len(self._recent_quality) > 500:
            keys = list(self._recent_quality)[-400:]
            self._recent_quality = {key: self._recent_quality[key] for key in keys}
        return final

    def _ict_admission_risk(
        self,
        signal: LiveSignal,
        drawdown_percent: float,
    ) -> float:
        """Retain pressure/expectancy/DD reductions before any quality uplift."""
        guarded = ProfitGuardedLiveExecutor._ict_admission_risk(
            self,
            signal,
            drawdown_percent,
        )
        decision = self._pending_decisions.get(signal.key)
        if decision is None:
            decision = self._decision_for_signal(signal)
        if decision is None or decision.is_shadow:
            return 0.0

        nominal = self._static_base_risk(signal)
        if guarded + FULL_STRENGTH_TOLERANCE < nominal:
            return min(float(guarded), float(decision.risk_percent))
        if decision.regime == "QUALITY_ALLOCATED":
            return min(QUALITY_RISK_PERCENT, float(decision.risk_percent))
        return min(float(guarded), float(decision.risk_percent))

    def place(
        self,
        signal: LiveSignal,
        now: Optional[datetime] = None,
    ) -> ExecutionResult:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        preview = self._decision_for_signal(signal)
        if preview is None or preview.regime != "QUALITY_ALLOCATED":
            return super().place(signal, now=now)

        # V12 does not pass through the ICT cap path, so enforce the retained
        # combined-risk ceiling explicitly before increasing its request.
        if signal.mode.upper() == "V12":
            account = self.client.account_info()
            if account is not None:
                combined_open, _ = self._admission_open_risk(account)
                if (
                    combined_open + preview.risk_percent
                    > PARITY_MAX_COMBINED_OPEN_RISK_PERCENT + 1e-12
                ):
                    return ExecutionResult(
                        False,
                        "COMBINED_OPEN_RISK_CAP",
                        "V14.16 quality allocation would exceed 3.25% combined risk",
                        risk_percent=0.0,
                        proposal={
                            "signal": asdict(signal),
                            "quality_decision": asdict(preview),
                        },
                    )

        metadata = dict(signal.metadata)
        metadata["v14_16_quality_allocation"] = asdict(preview)
        promoted = replace(
            signal,
            requested_risk_percent=min(
                QUALITY_RISK_PERCENT,
                max(float(signal.requested_risk_percent), preview.risk_percent),
            ),
            metadata=metadata,
        )
        return super().place(promoted, now=now)

    def quality_allocation_snapshot(self) -> dict[str, Any]:
        return {
            "quality_risk_ceiling_percent": QUALITY_RISK_PERCENT,
            "recent_quality_decisions": list(self._recent_quality.values())[-100:],
            "reasoning": self.reasoning_snapshot(),
        }
