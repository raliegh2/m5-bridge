"""V14.15 live executor with unified reasoning across every V12/ICT engine."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .v14_3_live_execution import LiveSignal, pip_size
from .v14_3_research_parity_execution import ResearchParityLiveRunnerConfig
from .v14_4_profit_guard import ProfitGuardConfig
from .v14_4_profit_guard_execution import ProfitGuardedState
from .v14_13_cost_regime_profile import CostRegimeDecision, all_in_cost_r
from .v14_14_extended_cost_execution import ExtendedCostRegimeLiveExecutor
from .v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from .v14_15_unified_reasoning import (
    evidence_multiplier,
    unified_cost_reasoning_decision,
)


def _history_key(*parts: str) -> str:
    return "|".join(str(part).upper() for part in parts)


class UnifiedReasoningState(ProfitGuardedState):
    """Persist broker-net R evidence for every engine and symbol/mode pair."""

    def _default(self) -> dict[str, Any]:
        payload = super()._default()
        payload.setdefault("engine_stats", {})
        payload.setdefault("symbol_mode_stats", {})
        return payload

    @staticmethod
    def _append_bounded(registry: dict[str, list[float]], key: str, value: float) -> None:
        values = registry.setdefault(key, [])
        values.append(round(float(value), 4))
        if len(values) > 120:
            registry[key] = values[-120:]

    def record_closed(
        self,
        position: dict[str, Any],
        pnl: float,
        closed_at: datetime,
    ) -> None:
        risk_dollars = float(position.get("risk_dollars", 0.0) or 0.0)
        if risk_dollars > 0:
            result_r = float(pnl) / risk_dollars
            engine = _history_key(str(position.get("engine", "UNKNOWN")))
            symbol_mode = _history_key(
                str(position.get("symbol", "UNKNOWN")),
                str(position.get("mode", "UNKNOWN")),
            )
            self._append_bounded(
                self.data.setdefault("engine_stats", {}),
                engine,
                result_r,
            )
            self._append_bounded(
                self.data.setdefault("symbol_mode_stats", {}),
                symbol_mode,
                result_r,
            )
        super().record_closed(position, pnl, closed_at)

    def engine_results(self, engine: str) -> list[float]:
        return [
            float(value)
            for value in self.data.get("engine_stats", {}).get(
                _history_key(engine), []
            )
        ]

    def symbol_mode_results(self, symbol: str, mode: str) -> list[float]:
        return [
            float(value)
            for value in self.data.get("symbol_mode_stats", {}).get(
                _history_key(symbol, mode), []
            )
        ]


class UnifiedReasoningLiveExecutor(ExtendedCostRegimeLiveExecutor):
    """Evaluate costs, live evidence and cross-engine conflicts before funding."""

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
        self.state = UnifiedReasoningState(config.state_path)

    def _cross_engine_context(self, signal: LiveSignal) -> tuple[bool, str]:
        """Reject same-symbol V12/ICT disagreement; recognize aligned exposure."""
        aligned = False
        for position in self.state.data.get("positions", {}).values():
            if str(position.get("symbol", "")).upper() != signal.symbol.upper():
                continue
            if str(position.get("mode", "")).upper() == signal.mode.upper():
                continue
            existing_side = str(position.get("side", "")).upper()
            if existing_side and existing_side != signal.side.upper():
                return False, "CROSS_ENGINE_DIRECTION_CONFLICT"
            if existing_side == signal.side.upper():
                aligned = True
        return True, "CROSS_ENGINE_ALIGNED" if aligned else "CROSS_ENGINE_NEUTRAL"

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
        cost_decision = unified_cost_reasoning_decision(
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
        if cost_decision.is_shadow:
            return cost_decision

        compatible, context = self._cross_engine_context(signal)
        if not compatible:
            return CostRegimeDecision(
                funded=False,
                regime="SHADOW",
                risk_percent=0.0,
                reason=context,
                all_in_cost_r=cost_decision.all_in_cost_r,
                target_r=cost_decision.target_r,
            )

        multiplier, evidence = evidence_multiplier(
            self.state.engine_results(signal.engine),
            self.state.symbol_mode_results(signal.symbol, signal.mode),
        )
        if multiplier <= 0:
            return CostRegimeDecision(
                funded=False,
                regime="SHADOW",
                risk_percent=0.0,
                reason=f"{evidence}; {context}",
                all_in_cost_r=cost_decision.all_in_cost_r,
                target_r=cost_decision.target_r,
            )

        risk = min(
            float(cost_decision.risk_percent),
            float(cost_decision.risk_percent) * float(multiplier),
            self._static_base_risk(signal),
        )
        regime = cost_decision.regime
        if multiplier < 1.0:
            regime = "REASONING_REDUCED" if multiplier > 0.25 else "REASONING_DEFENSIVE"
        return CostRegimeDecision(
            funded=risk > 0,
            regime=regime,
            risk_percent=max(0.0, risk),
            reason=f"{cost_decision.reason}; {evidence}; {context}",
            all_in_cost_r=cost_decision.all_in_cost_r,
            target_r=cost_decision.target_r,
        )

    def reasoning_snapshot(self) -> dict[str, Any]:
        return {
            "engine_stats": self.state.data.get("engine_stats", {}),
            "symbol_mode_stats": self.state.data.get("symbol_mode_stats", {}),
            "cost_regime": self.cost_regime_snapshot(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
