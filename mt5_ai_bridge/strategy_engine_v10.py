"""Strategy Engine V10 profitability research profile.

V10 preserves the V9 GBPUSD hour gate and shared portfolio safeguards while
reallocating risk toward the stronger synchronized engines. This module is a
configuration/validation layer; it does not silently enable live trading.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineAllocation:
    engine: str
    risk_percent: float
    rationale: str


@dataclass(frozen=True)
class StrategyEngineV10Profile:
    initial_balance: float = 5_000.0
    max_positions: int = 3
    max_open_risk_percent: float = 0.75
    aligned_gbp_cap_percent: float = 0.75
    mixed_gbp_cap_percent: float = 0.50
    realized_drawdown_limit_percent: float = 6.0
    pause_drawdown_percent: float = 10.0
    mode: str = "READ_ONLY"
    allocations: tuple[EngineAllocation, ...] = (
        EngineAllocation(
            "EURUSD_SATELLITE_V7",
            0.35,
            "Higher synchronized profit contribution with no direct GBP exposure.",
        ),
        EngineAllocation(
            "GBPJPY_SATELLITE_V7",
            0.35,
            "Strong synchronized profit factor; GBP caps remain authoritative.",
        ),
        EngineAllocation(
            "GBPUSD_SATELLITE_V3",
            0.30,
            "V9 hour-filtered satellite receives a modest increase.",
        ),
        EngineAllocation(
            "GBPUSD_SWING_V6",
            0.40,
            "Lower reservation frees capacity while retaining swing exposure.",
        ),
    )

    def risk_for(self, engine: str) -> float:
        normalized = engine.upper()
        aliases = {"GBPUSD_SATELLITE_V2": "GBPUSD_SATELLITE_V3"}
        normalized = aliases.get(normalized, normalized)
        for allocation in self.allocations:
            if allocation.engine == normalized:
                return allocation.risk_percent
        raise KeyError(f"Unknown V10 engine: {engine}")

    def validate(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least one")
        if not 0 < self.max_open_risk_percent <= 1.0:
            raise ValueError("max_open_risk_percent must be within (0, 1]")
        if self.mixed_gbp_cap_percent > self.aligned_gbp_cap_percent:
            raise ValueError("mixed GBP cap cannot exceed aligned GBP cap")
        if self.aligned_gbp_cap_percent > self.max_open_risk_percent:
            raise ValueError("aligned GBP cap cannot exceed total open-risk cap")
        names = [allocation.engine for allocation in self.allocations]
        if len(names) != len(set(names)):
            raise ValueError("engine allocations must be unique")
        for allocation in self.allocations:
            if not 0 < allocation.risk_percent <= self.max_open_risk_percent:
                raise ValueError(f"Invalid risk for {allocation.engine}")
        if self.mode != "READ_ONLY":
            raise ValueError("Research profile must default to READ_ONLY")


V10_PROFILE = StrategyEngineV10Profile()
V10_PROFILE.validate()
