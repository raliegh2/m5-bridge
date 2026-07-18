"""V14.14 live executor with engine-specific extended cost ceilings."""
from __future__ import annotations

from typing import Any, Callable, Optional

from .v14_3_live_execution import LiveSignal, pip_size
from .v14_3_research_parity_execution import ResearchParityLiveRunnerConfig
from .v14_4_profit_guard import ProfitGuardConfig
from .v14_13_cost_regime_execution import CostRegimeLiveExecutor
from .v14_13_cost_regime_profile import CostRegimeDecision, all_in_cost_r
from .v14_14_extended_cost_profile import (
    ExtendedCostRegimeConfig,
    extended_cost_regime_decision,
)


class ExtendedCostRegimeLiveExecutor(CostRegimeLiveExecutor):
    """Use extended cost limits without changing any strategy risk ceiling."""

    def __init__(
        self,
        client: Any,
        config: ResearchParityLiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
        guard_config: Optional[ProfitGuardConfig] = None,
        cost_config: Optional[ExtendedCostRegimeConfig] = None,
    ) -> None:
        super().__init__(client, config, approval_callback, guard_config)
        self.cost_config = cost_config or ExtendedCostRegimeConfig.from_env()

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
        return extended_cost_regime_decision(
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
