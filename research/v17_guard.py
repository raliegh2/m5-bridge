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


def profit_factor(values):
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    return gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)


def risk_multiplier(engine, history, now, disabled_until, config=GuardConfig()):
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
