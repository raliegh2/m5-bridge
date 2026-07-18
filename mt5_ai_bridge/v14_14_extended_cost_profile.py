"""V14.14 extended transaction-cost policy.

Extends V14.13 above 0.18R only for strategy groups whose cost-adjusted
historical evidence can support the extra execution burden.  The policy is
fail-closed and never raises the frozen strategy risk percentage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .v14_13_cost_regime_profile import (
    CostRegimeDecision,
    ROBUST_V12_ENGINES,
    WEAK_V12_ENGINES,
    strict_retail_profile,
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class ExtendedCostRegimeConfig:
    commission_reserve_pips: float = 0.10
    slippage_reserve_pips: float = 0.10
    non_m1_swap_reserve_pips: float = 0.20
    latency_reserve_r: float = 0.01

    parity_cost_r: float = 0.04
    medium_cost_r: float = 0.09
    standard_cost_r: float = 0.18
    maximum_v12_cost_r: float = 0.10
    maximum_satellite_ict_cost_r: float = 0.23
    maximum_strict_gbp_cost_r: float = 0.28
    maximum_cost_fraction_of_target: float = 0.225
    observation_risk_percent: float = 0.025

    @classmethod
    def from_env(cls) -> "ExtendedCostRegimeConfig":
        config = cls(
            commission_reserve_pips=_env_float(
                "V14_14_COMMISSION_RESERVE_PIPS", cls.commission_reserve_pips
            ),
            slippage_reserve_pips=_env_float(
                "V14_14_SLIPPAGE_RESERVE_PIPS", cls.slippage_reserve_pips
            ),
            non_m1_swap_reserve_pips=_env_float(
                "V14_14_NON_M1_SWAP_RESERVE_PIPS", cls.non_m1_swap_reserve_pips
            ),
            latency_reserve_r=_env_float(
                "V14_14_LATENCY_RESERVE_R", cls.latency_reserve_r
            ),
            parity_cost_r=_env_float("V14_14_PARITY_COST_R", cls.parity_cost_r),
            medium_cost_r=_env_float("V14_14_MEDIUM_COST_R", cls.medium_cost_r),
            standard_cost_r=_env_float("V14_14_STANDARD_COST_R", cls.standard_cost_r),
            maximum_v12_cost_r=_env_float(
                "V14_14_MAXIMUM_V12_COST_R", cls.maximum_v12_cost_r
            ),
            maximum_satellite_ict_cost_r=_env_float(
                "V14_14_MAXIMUM_SATELLITE_ICT_COST_R",
                cls.maximum_satellite_ict_cost_r,
            ),
            maximum_strict_gbp_cost_r=_env_float(
                "V14_14_MAXIMUM_STRICT_GBP_COST_R",
                cls.maximum_strict_gbp_cost_r,
            ),
            maximum_cost_fraction_of_target=_env_float(
                "V14_14_MAXIMUM_COST_FRACTION_OF_TARGET",
                cls.maximum_cost_fraction_of_target,
            ),
            observation_risk_percent=_env_float(
                "V14_14_OBSERVATION_RISK_PERCENT",
                cls.observation_risk_percent,
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not 0 < self.parity_cost_r < self.medium_cost_r < self.standard_cost_r:
            raise ValueError("parity, medium and standard thresholds must increase")
        if not self.maximum_v12_cost_r <= self.standard_cost_r:
            raise ValueError("V12 cost ceiling must not exceed the standard tier")
        if not self.standard_cost_r < self.maximum_satellite_ict_cost_r:
            raise ValueError("satellite ceiling must exceed the standard tier")
        if not self.maximum_satellite_ict_cost_r < self.maximum_strict_gbp_cost_r < 1:
            raise ValueError("strict GBP ceiling must be the highest bounded tier")
        if not 0 < self.maximum_cost_fraction_of_target < 1:
            raise ValueError("target-cost fraction must be in (0, 1)")
        if not 0 < self.observation_risk_percent <= 0.05:
            raise ValueError("observation risk must be in (0, 0.05]")
        for name in (
            "commission_reserve_pips",
            "slippage_reserve_pips",
            "non_m1_swap_reserve_pips",
            "latency_reserve_r",
        ):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")


def _decision(
    funded: bool,
    regime: str,
    risk: float,
    reason: str,
    cost_r: float,
    target_r: float,
) -> CostRegimeDecision:
    return CostRegimeDecision(
        funded=bool(funded and risk > 0),
        regime=regime,
        risk_percent=max(0.0, float(risk)),
        reason=reason,
        all_in_cost_r=float(cost_r),
        target_r=float(target_r),
    )


def extended_cost_regime_decision(
    *,
    symbol: str,
    engine: str,
    setup: str,
    mode: str,
    side: str,
    entry_time: Any,
    base_risk_percent: float,
    all_in_cost: float,
    target_r: float,
    config: ExtendedCostRegimeConfig,
) -> CostRegimeDecision:
    base = max(0.0, float(base_risk_percent))
    cost = max(0.0, float(all_in_cost))
    target = max(0.0, float(target_r))
    symbol = str(symbol).upper()
    engine = str(engine)
    mode = str(mode).upper()

    if base <= 0 or target <= 0:
        return _decision(False, "SHADOW", 0.0, "INVALID_RISK_OR_TARGET", cost, target)
    if cost <= 1e-12:
        return _decision(True, "ZERO_COST_PARITY", base, "EXACT_V14_3_PARITY", cost, target)
    if cost / target > config.maximum_cost_fraction_of_target + 1e-12:
        return _decision(False, "SHADOW", 0.0, "COST_CONSUMES_TOO_MUCH_TARGET", cost, target)

    if mode == "V12":
        if engine in WEAK_V12_ENGINES:
            return _decision(False, "SHADOW", 0.0, "WEAK_V12_AFTER_COSTS", cost, target)
        if engine not in ROBUST_V12_ENGINES:
            return _decision(False, "SHADOW", 0.0, "UNREGISTERED_V12_ENGINE", cost, target)
        if cost <= config.maximum_v12_cost_r + 1e-12:
            return _decision(True, "EXTENDED_COST_V12", base, "V12_WITHIN_EXTENDED_COST", cost, target)
        return _decision(False, "SHADOW", 0.0, "V12_COST_ABOVE_VALIDATED_LIMIT", cost, target)

    if mode != "ICT":
        return _decision(False, "SHADOW", 0.0, "UNSUPPORTED_MODE", cost, target)

    if cost <= config.parity_cost_r + 1e-12:
        return _decision(True, "LOW_COST_PARITY", base, "LOW_COST_V14_3_PARITY", cost, target)

    if symbol == "USDJPY":
        if cost <= 0.08 + 1e-12:
            return _decision(True, "MEDIUM_COST_USDJPY", base, "USDJPY_WITHIN_MEDIUM_COST", cost, target)
        return _decision(False, "SHADOW", 0.0, "USDJPY_EDGE_BELOW_COST", cost, target)

    if symbol in {"EURUSD", "AUDUSD"}:
        if cost <= config.maximum_satellite_ict_cost_r + 1e-12:
            return _decision(
                True,
                "EXTENDED_COST_SATELLITE",
                base,
                "SATELLITE_WITHIN_EXTENDED_COST",
                cost,
                target,
            )
        return _decision(False, "SHADOW", 0.0, "SATELLITE_COST_ABOVE_VALIDATED_LIMIT", cost, target)

    if (
        symbol == "GBPJPY"
        and setup == "sweep_reclaim_15"
        and cost <= config.medium_cost_r + 1e-12
    ):
        return _decision(True, "MEDIUM_COST_GBPJPY", base, "GBPJPY_SWEEP15_MEDIUM_COST_EDGE", cost, target)

    if strict_retail_profile(symbol, setup, side, entry_time):
        if cost <= config.maximum_strict_gbp_cost_r + 1e-12:
            regime = "STRICT_RETAIL_GBP" if cost <= config.standard_cost_r else "EXTREME_COST_GBP"
            return _decision(True, regime, base, "STRICT_GBP_WITHIN_EXTENDED_COST", cost, target)
        return _decision(False, "SHADOW", 0.0, "STRICT_GBP_COST_ABOVE_VALIDATED_LIMIT", cost, target)

    if cost <= config.medium_cost_r + 1e-12:
        return _decision(
            True,
            "OBSERVATION",
            min(base, config.observation_risk_percent),
            "MEDIUM_COST_OBSERVATION",
            cost,
            target,
        )

    return _decision(False, "SHADOW", 0.0, "HIGH_COST_NONCORE_SHADOW", cost, target)


def validate_profile() -> None:
    ExtendedCostRegimeConfig().validate()
    decision = extended_cost_regime_decision(
        symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="breakout_15_fade",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-17T12:00:00+00:00",
        base_risk_percent=0.455,
        all_in_cost=0.28,
        target_r=1.25,
        config=ExtendedCostRegimeConfig(),
    )
    assert decision.funded and decision.regime == "EXTREME_COST_GBP"


validate_profile()
