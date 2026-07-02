"""V15 per-engine risk policy for the five-symbol portfolio."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class V15GuardConfig:
    rolling_trades: int = 24
    minimum_trades: int = 20
    full_profit_factor: float = 1.15
    full_net_r: float = 0.0
    reduced_profit_factor: float = 0.95
    reduced_net_r: float = -2.0
    reduced_multiplier: float = 0.50
    cooldown_days: int = 60
    probe_multiplier: float = 0.50
    protected_engines: tuple[str, ...] = ("GBPUSD_V10_PRECISION",)


@dataclass(frozen=True)
class V15GuardDecision:
    multiplier: float
    status: str
    reason: str


def _profit_factor(values: list[float]) -> float:
    profit = sum(value for value in values if value > 0)
    loss = -sum(value for value in values if value < 0)
    return profit / loss if loss else (math.inf if profit else 0.0)


def decide_component_risk(
    engine: str,
    closed_r: list[float],
    state: dict,
    now: datetime | None = None,
    config: V15GuardConfig = V15GuardConfig(),
) -> V15GuardDecision:
    now = now or datetime.now(timezone.utc)
    if engine in config.protected_engines:
        return V15GuardDecision(1.0, "PROTECTED", "validated_anchor")

    disabled_until = state.get("disabled_until")
    if disabled_until:
        disabled_until = datetime.fromisoformat(str(disabled_until))
        if now < disabled_until:
            return V15GuardDecision(0.0, "DISABLED", "60_day_cooldown")
        if state.get("probe_in_flight"):
            return V15GuardDecision(0.0, "PROBE_BLOCKED", "probe_in_flight")
        return V15GuardDecision(config.probe_multiplier, "PROBE", "cooldown_complete")

    values = [float(value) for value in closed_r[-config.rolling_trades:]]
    if len(values) < config.minimum_trades:
        return V15GuardDecision(1.0, "WARMUP", "insufficient_closed_trades")
    pf = _profit_factor(values)
    net_r = sum(values)
    if pf >= config.full_profit_factor and net_r > config.full_net_r:
        return V15GuardDecision(1.0, "FULL", "rolling_profile_positive")
    if pf >= config.reduced_profit_factor and net_r > config.reduced_net_r:
        return V15GuardDecision(config.reduced_multiplier, "REDUCED", "rolling_profile_weak")

    state["disabled_until"] = (now + timedelta(days=config.cooldown_days)).isoformat()
    state["probe_in_flight"] = False
    return V15GuardDecision(0.0, "DISABLED", "rolling_profile_failed")


def normal_risk_percent(engine: str) -> float | None:
    return {
        "USDJPY_H4_VALIDATED": 0.25,
        "AUDUSD_TREND_PULLBACK_04_08UTC": 0.25,
        "EURUSD_H4_VALIDATED": 0.20,
        "GBPJPY_H4_VALIDATED": 0.20,
    }.get(engine)
