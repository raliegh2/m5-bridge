"""Projected-stress admission control for V14.8 research and demo validation.

The existing drawdown governor reacts to closed balance drawdown. This module
adds a causal pre-entry calculation that includes the full risk dollars of all
currently open positions plus the proposed position. It reduces or rejects the
new risk when the resulting stressed equity would cross the configured limit.

No broker or order API is imported here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectedStressGovernor:
    maximum_stress_drawdown_percent: float = 9.95
    minimum_trade_risk_percent: float = 0.025

    def maximum_new_risk_percent(
        self,
        *,
        balance: float,
        peak_balance: float,
        existing_open_risk_dollars: float,
    ) -> float:
        balance = max(0.0, float(balance))
        peak = max(balance, float(peak_balance))
        existing = max(0.0, float(existing_open_risk_dollars))
        if balance <= 0.0 or peak <= 0.0:
            return 0.0
        minimum_stressed_equity = peak * (
            1.0 - self.maximum_stress_drawdown_percent / 100.0
        )
        available_dollars = balance - existing - minimum_stressed_equity
        return max(0.0, available_dollars / balance * 100.0)

    def apply(
        self,
        proposed_risk_percent: float,
        *,
        balance: float,
        peak_balance: float,
        existing_open_risk_dollars: float,
    ) -> float:
        proposed = max(0.0, float(proposed_risk_percent))
        maximum = self.maximum_new_risk_percent(
            balance=balance,
            peak_balance=peak_balance,
            existing_open_risk_dollars=existing_open_risk_dollars,
        )
        approved = min(proposed, maximum)
        if approved + 1e-12 < self.minimum_trade_risk_percent:
            return 0.0
        return approved


def validate_projected_stress_governor(
    governor: ProjectedStressGovernor = ProjectedStressGovernor(),
) -> None:
    if not 0.0 < governor.maximum_stress_drawdown_percent < 10.0:
        raise RuntimeError("Projected stress limit must remain between 0% and 10%")
    if governor.minimum_trade_risk_percent < 0.0:
        raise RuntimeError("Minimum trade risk cannot be negative")


validate_projected_stress_governor()
