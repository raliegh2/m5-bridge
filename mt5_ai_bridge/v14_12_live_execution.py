"""V14.12 cost-robust, net-positive live executor.

The executor layers the historically profitable V14.5.2 retail-cost allocation
onto V14.4 and then requires recent broker-net evidence before full risk is
used. Commission, swap and fee values are captured by the existing MT5 deal
reconciliation. Current spread plus configurable commission/slippage/swap
reserves must also fit inside the stop and target economics.

No signal receives more risk than V14.5.2. AUTO remains restricted to a
confirmed demo account by the inherited research-parity executor.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .v14_3_live_execution import ExecutionResult, LiveSignal, pip_size
from .v14_3_research_parity_execution import ResearchParityLiveRunnerConfig
from .v14_4_profit_guard_execution import (
    ProfitGuardedLiveExecutor,
    ProfitGuardedState,
)
from .v14_5_2_profit_filter_profile import v14_5_2_risk_percent
from .v14_12_net_positive_guard import (
    NetPositiveGuardConfig,
    all_in_cost_reason,
    apply_net_positive_tier,
    net_positive_tier,
    rolling_performance,
)


class NetPositiveState(ProfitGuardedState):
    """Persist after-cost R histories for every setup and symbol."""

    def _default(self) -> dict[str, Any]:
        payload = super()._default()
        payload.setdefault("symbol_stats", {})
        return payload

    @staticmethod
    def _append_bounded(container: dict[str, list[float]], key: str, value: float) -> None:
        results = container.setdefault(key, [])
        results.append(round(float(value), 4))
        if len(results) > 200:
            container[key] = results[-200:]

    def record_closed(
        self,
        position: dict[str, Any],
        pnl: float,
        closed_at: datetime,
    ) -> None:
        risk_dollars = float(position.get("risk_dollars", 0.0) or 0.0)
        if risk_dollars > 0:
            result_r = float(pnl) / risk_dollars
            symbol = str(position.get("symbol", "")).upper()
            setup = str(position.get("setup", ""))
            mode = str(position.get("mode", "")).upper()
            if mode != "ICT":
                self._append_bounded(
                    self.data.setdefault("setup_stats", {}),
                    f"{symbol}/{setup}",
                    result_r,
                )
            self._append_bounded(
                self.data.setdefault("symbol_stats", {}), symbol, result_r
            )
        super().record_closed(position, pnl, closed_at)

    def symbol_results(self, symbol: str) -> list[float]:
        return [
            float(value)
            for value in self.data.get("symbol_stats", {}).get(symbol.upper(), [])
        ]


class NetPositiveLiveExecutor(ProfitGuardedLiveExecutor):
    """V14.4 executor with V14.5.2 allocation and V14.12 evidence gates."""

    def __init__(
        self,
        client: Any,
        config: ResearchParityLiveRunnerConfig,
        approval_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
        guard_config=None,
        net_guard_config: Optional[NetPositiveGuardConfig] = None,
    ) -> None:
        super().__init__(client, config, approval_callback, guard_config)
        self.net_guard = net_guard_config or NetPositiveGuardConfig.from_env()
        self.state = NetPositiveState(config.state_path)

    def _static_cost_robust_risk(self, signal: LiveSignal) -> float:
        return float(
            v14_5_2_risk_percent(
                signal.engine,
                signal.mode,
                signal.signal_time,
            )
        )

    def _net_tier(self, signal: LiveSignal) -> str:
        return net_positive_tier(
            self.state.setup_results(signal.symbol, signal.setup),
            self.state.symbol_results(signal.symbol),
            self.net_guard,
        )

    def _ict_admission_risk(
        self,
        signal: LiveSignal,
        drawdown_percent: float,
    ) -> float:
        inherited = super()._ict_admission_risk(signal, drawdown_percent)
        base = min(inherited, self._static_cost_robust_risk(signal))
        return apply_net_positive_tier(base, self._net_tier(signal), self.net_guard)

    def _all_in_cost_reason(self, signal: LiveSignal) -> str | None:
        info = self.client.symbol_info(signal.broker_symbol)
        tick = self.client.symbol_info_tick(signal.broker_symbol)
        if info is None or tick is None:
            return None
        pip = pip_size(info, signal.symbol)
        if pip <= 0:
            return None
        spread_pips = (float(tick.ask) - float(tick.bid)) / pip
        return all_in_cost_reason(
            spread_pips,
            float(signal.stop_pips),
            float(signal.target_pips),
            self.net_guard,
        )

    def place(
        self,
        signal: LiveSignal,
        now: Optional[datetime] = None,
    ) -> ExecutionResult:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        if signal.key in self.state.data.get("seen", {}):
            return super().place(signal, now=now)

        reason = self._all_in_cost_reason(signal)
        if reason is not None:
            return ExecutionResult(False, "V14_12_ALL_IN_COST_GUARD", reason)

        if signal.mode.upper() != "ICT":
            static_risk = min(
                float(signal.requested_risk_percent),
                self._static_cost_robust_risk(signal),
            )
            tier = self._net_tier(signal)
            adjusted = apply_net_positive_tier(static_risk, tier, self.net_guard)
            signal = replace(
                signal,
                requested_risk_percent=adjusted,
                metadata={
                    **dict(signal.metadata or {}),
                    "v14_12_tier": tier,
                    "v14_12_static_risk_percent": static_risk,
                },
            )

        return super().place(signal, now=now)

    def net_positive_snapshot(self) -> dict[str, Any]:
        setup_stats = self.state.data.get("setup_stats", {})
        symbol_stats = self.state.data.get("symbol_stats", {})
        return {
            "config": {
                "setup_window": self.net_guard.setup_window,
                "symbol_window": self.net_guard.symbol_window,
                "minimum_setup_trades": self.net_guard.minimum_setup_trades,
                "minimum_symbol_trades": self.net_guard.minimum_symbol_trades,
                "full_setup_profit_factor": self.net_guard.full_setup_profit_factor,
                "full_symbol_profit_factor": self.net_guard.full_symbol_profit_factor,
                "maximum_all_in_cost_fraction_of_stop": (
                    self.net_guard.maximum_all_in_cost_fraction_of_stop
                ),
                "maximum_all_in_cost_fraction_of_target": (
                    self.net_guard.maximum_all_in_cost_fraction_of_target
                ),
            },
            "setup_performance": {
                key: {
                    **rolling_performance(values, self.net_guard.setup_window).__dict__,
                    "tier": net_positive_tier(
                        values,
                        symbol_stats.get(key.split("/", 1)[0], []),
                        self.net_guard,
                    ),
                }
                for key, values in sorted(setup_stats.items())
            },
            "symbol_performance": {
                key: rolling_performance(values, self.net_guard.symbol_window).__dict__
                for key, values in sorted(symbol_stats.items())
            },
        }

    def profit_guard_snapshot(self) -> dict[str, Any]:
        payload = super().profit_guard_snapshot()
        payload["v14_12_net_positive"] = self.net_positive_snapshot()
        return payload
