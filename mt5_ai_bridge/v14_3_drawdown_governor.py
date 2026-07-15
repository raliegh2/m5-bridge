"""Smooth pre-entry drawdown governor for the V12 + V14.3 research portfolio."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DrawdownGovernor:
    soft_start_percent: float = 5.5
    medium_start_percent: float = 7.0
    defensive_start_percent: float = 8.0
    hard_stop_percent: float = 9.25
    soft_multiplier: float = 0.97
    medium_multiplier: float = 0.88
    defensive_multiplier: float = 0.65
    minimum_risk_percent: float = 0.025

    def multiplier(self, drawdown_percent: float) -> float:
        dd = max(0.0, float(drawdown_percent))
        if dd >= self.hard_stop_percent:
            return 0.0
        if dd >= self.defensive_start_percent:
            return self.defensive_multiplier
        if dd >= self.medium_start_percent:
            return self.medium_multiplier
        if dd >= self.soft_start_percent:
            return self.soft_multiplier
        return 1.0

    def apply(self, risk_percent: float, drawdown_percent: float) -> float:
        base = max(0.0, float(risk_percent))
        multiplier = self.multiplier(drawdown_percent)
        if multiplier <= 0.0 or base <= 0.0:
            return 0.0
        return min(base, max(self.minimum_risk_percent, base * multiplier))


def validate_governor(governor: DrawdownGovernor) -> None:
    assert 0.0 <= governor.soft_start_percent < governor.medium_start_percent
    assert governor.medium_start_percent < governor.defensive_start_percent
    assert governor.defensive_start_percent < governor.hard_stop_percent < 10.0
    assert 0.0 < governor.defensive_multiplier < governor.medium_multiplier < governor.soft_multiplier <= 1.0
    assert governor.minimum_risk_percent >= 0.0


validate_governor(DrawdownGovernor())
