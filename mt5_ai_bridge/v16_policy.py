"""Selective component policy for V16."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .v15_loss_control import V15GuardConfig, V15GuardDecision, decide_component_risk

PROFIT_ENGINES = frozenset({
    "GBPUSD_V10_PRECISION",
    "AUDUSD_TREND_PULLBACK_04_08UTC",
    "USDJPY_H4_VALIDATED",
    "EURUSD_H4_VALIDATED",
})


@dataclass(frozen=True)
class V16Config:
    weak_engine: str = "GBPJPY_H4_VALIDATED"
    guard: V15GuardConfig = V15GuardConfig(
        rolling_trades=24,
        minimum_trades=20,
        full_profit_factor=1.15,
        full_net_r=0.0,
        reduced_profit_factor=0.95,
        reduced_net_r=-2.0,
        reduced_multiplier=0.50,
        cooldown_days=60,
        probe_multiplier=0.50,
        protected_engines=(),
    )


def decide_v16_risk(engine: str, closed_r: list[float], state: dict,
                    now: datetime | None = None,
                    config: V16Config = V16Config()) -> V15GuardDecision:
    if engine in PROFIT_ENGINES:
        return V15GuardDecision(1.0, "PROFIT_PRESERVED", "validated_profit_engine")
    if engine == config.weak_engine:
        return decide_component_risk(
            engine, closed_r, state, now=now, config=config.guard
        )
    return V15GuardDecision(0.0, "UNSUPPORTED", "engine_not_in_v16_policy")
