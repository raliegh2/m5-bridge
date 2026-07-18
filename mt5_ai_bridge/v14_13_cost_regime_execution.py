"""V14.13 cost-regime executor layered over the V14.4 live profit guard."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .v14_3_live_execution import ExecutionResult, LiveSignal, pip_size
from .v14_3_profit_preserving_profile import SETUP_RISK_PERCENT
from .v14_3_research_parity_execution import ResearchParityLiveRunnerConfig
from .v14_4_profit_guard import ProfitGuardConfig
from .v14_4_profit_guard_execution import ProfitGuardedLiveExecutor
from .v14_13_cost_regime_profile import (
    CostRegimeConfig,
    CostRegimeDecision,
    all_in_cost_r,
    cost_regime_decision,
)


class CostRegimeLiveExecutor(ProfitGuardedLiveExecutor):
    """Preserve V14.3 at low cost and fail closed as execution cost rises."""

    def __init__(
        self,
        client: Any,
        config: ResearchParityLiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
        guard_config: Optional[ProfitGuardConfig] = None,
        cost_config: Optional[CostRegimeConfig] = None,
    ) -> None:
        super().__init__(client, config, approval_callback, guard_config)
        self.cost_config = cost_config or CostRegimeConfig.from_env()
        self._pending_decisions: dict[str, CostRegimeDecision] = {}
        self._recent_decisions: dict[str, dict[str, Any]] = {}

    def _static_base_risk(self, signal: LiveSignal) -> float:
        if signal.mode.upper() == "ICT":
            return float(
                SETUP_RISK_PERCENT.get(
                    (signal.symbol.upper(), signal.setup),
                    signal.requested_risk_percent,
                )
            )
        return float(signal.requested_risk_percent)

    def _decision_for_signal(self, signal: LiveSignal) -> CostRegimeDecision | None:
        info = self.client.symbol_info(signal.broker_symbol)
        tick = self.client.symbol_info_tick(signal.broker_symbol)
        if info is None or tick is None:
            return None
        pip = pip_size(info, signal.symbol)
        if pip <= 0:
            return None
        spread_pips = (float(tick.ask) - float(tick.bid)) / pip
        timeframe = str(signal.metadata.get("timeframe", signal.mode)).upper()
        cost_r = all_in_cost_r(
            spread_pips,
            float(signal.stop_pips),
            timeframe,
            self.cost_config,
        )
        target_r = float(signal.target_pips) / float(signal.stop_pips)
        return cost_regime_decision(
            symbol=signal.symbol,
            engine=signal.engine,
            setup=signal.setup,
            mode=signal.mode,
            side=signal.side,
            entry_time=signal.signal_time,
            base_risk_percent=self._static_base_risk(signal),
            all_in_cost=cost_r,
            target_r=target_r,
            config=self.cost_config,
        )

    def _remember_decision(
        self,
        signal: LiveSignal,
        decision: CostRegimeDecision,
        now: datetime,
    ) -> None:
        self._recent_decisions[signal.key] = {
            **asdict(decision),
            "symbol": signal.symbol,
            "engine": signal.engine,
            "setup": signal.setup,
            "side": signal.side,
            "signal_time": signal.signal_time.astimezone(timezone.utc).isoformat(),
            "evaluated_at": now.astimezone(timezone.utc).isoformat(),
        }
        if len(self._recent_decisions) > 500:
            keys = list(self._recent_decisions)[-400:]
            self._recent_decisions = {
                key: self._recent_decisions[key]
                for key in keys
            }

    def _ict_admission_risk(
        self,
        signal: LiveSignal,
        drawdown_percent: float,
    ) -> float:
        # V14.4 expectancy, post-loss and drawdown reductions remain authoritative.
        guarded = super()._ict_admission_risk(signal, drawdown_percent)
        decision = self._pending_decisions.get(signal.key)
        if decision is None:
            decision = self._decision_for_signal(signal)
        if decision is None or decision.is_shadow:
            return 0.0
        return min(float(guarded), float(decision.risk_percent))

    def place(
        self,
        signal: LiveSignal,
        now: Optional[datetime] = None,
    ) -> ExecutionResult:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        decision = self._decision_for_signal(signal)
        if decision is None:
            # The inherited path emits the canonical market-data error.
            return super().place(signal, now=now)

        self._remember_decision(signal, decision, now)
        if decision.is_shadow:
            return ExecutionResult(
                False,
                "V14_13_COST_REGIME_SHADOW",
                (
                    f"{decision.reason}: all-in cost {decision.all_in_cost_r:.4f}R, "
                    f"target {decision.target_r:.2f}R; candidate retained for "
                    "diagnostics but no broker order is funded"
                ),
                risk_percent=0.0,
                proposal={
                    "signal": asdict(signal),
                    "cost_regime": asdict(decision),
                    "execution_mode": self.config.execution_mode,
                },
            )

        metadata = dict(signal.metadata)
        metadata["v14_13_cost_regime"] = asdict(decision)
        governed_signal = replace(
            signal,
            requested_risk_percent=min(
                float(signal.requested_risk_percent),
                float(decision.risk_percent),
            ),
            metadata=metadata,
        )

        self._pending_decisions[governed_signal.key] = decision
        try:
            result = super().place(governed_signal, now=now)
        finally:
            self._pending_decisions.pop(governed_signal.key, None)
        return result

    def cost_regime_snapshot(self) -> dict[str, Any]:
        return {
            "config": asdict(self.cost_config),
            "recent_decisions": list(self._recent_decisions.values())[-100:],
        }
