from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GuardConfig:
    rolling: int = 24
    minimum: int = 20
    full_pf: float = 1.05
    full_net_r: float = -1.0
    reduced_pf: float = 0.90
    reduced_net_r: float = -3.0
    reduced_multiplier: float = 0.50
    cooldown_days: int = 30
    probe_multiplier: float = 0.50


@dataclass(frozen=True)
class GuardDecision:
    multiplier: float
    reason: str
    is_probe: bool = False


def profit_factor(values):
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    return gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)


def risk_multiplier(engine, history, now, disabled_until, config=GuardConfig()):
    """Legacy V17 guard retained for exact before/after comparison.

    The original implementation does not issue a recovery probe for a mature
    engine after cooldown. It remains available only so research can reproduce
    the historical V17 result exactly.
    """
    if engine == "GBPUSD_V10_PRECISION":
        return 1.0
    until = disabled_until.get(engine)
    if until is not None and now < until:
        return 0.0
    values = history.get(engine, [])[-config.rolling:]
    if len(values) < config.minimum:
        if until is not None and now >= until:
            disabled_until.pop(engine, None)
            return config.probe_multiplier
        return 1.0
    pf = profit_factor(values)
    net_r = sum(values)
    if pf >= config.full_pf and net_r > config.full_net_r:
        return 1.0
    if pf >= config.reduced_pf and net_r > config.reduced_net_r:
        return config.reduced_multiplier
    disabled_until[engine] = now + __import__("pandas").Timedelta(days=config.cooldown_days)
    return 0.0


def recovery_decision(
    engine,
    history,
    now,
    disabled_until,
    probe_active_until,
    config=GuardConfig(),
):
    """Return a stateful guard decision with an attainable recovery probe.

    A mature engine that completes its cooldown receives exactly one reduced-
    risk probe. While that probe is open, additional signals from the engine
    are blocked. If the probe cannot be admitted by the portfolio gates, the
    expired cooldown remains in place so a later signal can try again.
    """
    if engine == "GBPUSD_V10_PRECISION":
        return GuardDecision(1.0, "precision_passthrough")

    probe_until = probe_active_until.get(engine)
    if probe_until is not None:
        if now < probe_until:
            return GuardDecision(0.0, "probe_in_flight")
        probe_active_until.pop(engine, None)

    until = disabled_until.get(engine)
    if until is not None:
        if now < until:
            return GuardDecision(0.0, "cooldown")
        return GuardDecision(config.probe_multiplier, "recovery_probe", True)

    values = history.get(engine, [])[-config.rolling:]
    if len(values) < config.minimum:
        return GuardDecision(1.0, "warmup")

    pf = profit_factor(values)
    net_r = sum(values)
    if pf >= config.full_pf and net_r > config.full_net_r:
        return GuardDecision(1.0, "full_performance")
    if pf >= config.reduced_pf and net_r > config.reduced_net_r:
        return GuardDecision(config.reduced_multiplier, "reduced_performance")

    disabled_until[engine] = now + __import__("pandas").Timedelta(days=config.cooldown_days)
    return GuardDecision(0.0, "new_cooldown")
