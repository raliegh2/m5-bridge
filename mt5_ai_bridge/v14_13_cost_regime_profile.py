"""V14.13 transaction-cost regime policy for the V14.3 research-parity model.

The policy preserves the documented V14.3 allocation when modeled transaction
cost is zero.  Once cost is non-zero, only engines and pre-entry subsets that
retained positive cost-adjusted evidence receive normal risk.  Other candidates
remain observable without forcing a broker order.

No decision may increase the frozen V14.3 risk tier.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

OBSERVATION_RISK_PERCENT = 0.025

WEAK_V12_ENGINES: frozenset[str] = frozenset(
    {"EURUSD_SWING_RETEST", "USDJPY_SAFE_HAVEN_BREAKOUT"}
)
ROBUST_V12_ENGINES: frozenset[str] = frozenset(
    {
        "GBPUSD_V10_PRECISION",
        "GBPUSD_SWING_RETEST",
        "EURUSD_SWING_CORE",
        "GBPJPY_SWING_CORE",
        "AUDUSD_TREND_PULLBACK",
    }
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class CostRegimeConfig:
    """Runtime cost reserves and admission boundaries."""

    commission_reserve_pips: float = 0.10
    slippage_reserve_pips: float = 0.10
    non_m1_swap_reserve_pips: float = 0.20
    latency_reserve_r: float = 0.01

    parity_cost_r: float = 0.04
    medium_cost_r: float = 0.09
    maximum_supported_cost_r: float = 0.18
    maximum_cost_fraction_of_target: float = 0.15
    observation_risk_percent: float = OBSERVATION_RISK_PERCENT

    @classmethod
    def from_env(cls) -> "CostRegimeConfig":
        config = cls(
            commission_reserve_pips=_env_float(
                "V14_13_COMMISSION_RESERVE_PIPS", cls.commission_reserve_pips
            ),
            slippage_reserve_pips=_env_float(
                "V14_13_SLIPPAGE_RESERVE_PIPS", cls.slippage_reserve_pips
            ),
            non_m1_swap_reserve_pips=_env_float(
                "V14_13_NON_M1_SWAP_RESERVE_PIPS", cls.non_m1_swap_reserve_pips
            ),
            latency_reserve_r=_env_float(
                "V14_13_LATENCY_RESERVE_R", cls.latency_reserve_r
            ),
            parity_cost_r=_env_float("V14_13_PARITY_COST_R", cls.parity_cost_r),
            medium_cost_r=_env_float("V14_13_MEDIUM_COST_R", cls.medium_cost_r),
            maximum_supported_cost_r=_env_float(
                "V14_13_MAXIMUM_SUPPORTED_COST_R", cls.maximum_supported_cost_r
            ),
            maximum_cost_fraction_of_target=_env_float(
                "V14_13_MAXIMUM_COST_FRACTION_OF_TARGET",
                cls.maximum_cost_fraction_of_target,
            ),
            observation_risk_percent=_env_float(
                "V14_13_OBSERVATION_RISK_PERCENT", cls.observation_risk_percent
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        for name in (
            "commission_reserve_pips",
            "slippage_reserve_pips",
            "non_m1_swap_reserve_pips",
            "latency_reserve_r",
        ):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")
        if not 0 < self.parity_cost_r < self.medium_cost_r:
            raise ValueError("cost regime thresholds must be strictly increasing")
        if not self.medium_cost_r < self.maximum_supported_cost_r < 1:
            raise ValueError("maximum supported cost R must exceed medium cost R")
        if not 0 < self.maximum_cost_fraction_of_target < 1:
            raise ValueError("maximum cost fraction of target must be in (0, 1)")
        if not 0 < self.observation_risk_percent <= 0.05:
            raise ValueError("observation risk must be in (0, 0.05]")


@dataclass(frozen=True)
class CostRegimeDecision:
    funded: bool
    regime: str
    risk_percent: float
    reason: str
    all_in_cost_r: float
    target_r: float

    @property
    def is_shadow(self) -> bool:
        return not self.funded or self.risk_percent <= 0


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    elif hasattr(value, "to_pydatetime"):
        stamp = value.to_pydatetime()
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def all_in_cost_r(
    spread_pips: float,
    stop_pips: float,
    timeframe: str,
    config: CostRegimeConfig,
) -> float:
    """Convert live spread and configured reserves into initial-risk units."""
    stop = float(stop_pips)
    if stop <= 0:
        raise ValueError("stop_pips must be positive")
    reserve_pips = (
        max(0.0, float(spread_pips))
        + config.commission_reserve_pips
        + config.slippage_reserve_pips
    )
    if str(timeframe).upper() != "M1":
        reserve_pips += config.non_m1_swap_reserve_pips
    return reserve_pips / stop + config.latency_reserve_r


def strict_retail_profile(
    symbol: str,
    setup: str,
    side: str,
    entry_time: Any,
) -> bool:
    """Frozen high-cost GBP subsets using only information known before entry."""
    symbol = str(symbol).upper()
    setup = str(setup)
    side = str(side).upper()
    if side in {"1", "1.0", "LONG"}:
        side = "BUY"
    elif side in {"-1", "-1.0", "SHORT"}:
        side = "SELL"
    stamp = _utc(entry_time)
    hour = stamp.hour

    if symbol == "GBPUSD":
        return bool(
            (setup == "sweep_reclaim_30" and side == "SELL" and 12 <= hour < 18)
            or (setup == "sweep_reclaim_30" and hour == 17)
            or (setup == "breakout_15_fade" and hour == 12)
        )
    if symbol == "GBPJPY":
        return bool(
            setup == "sweep_reclaim_15"
            and ((side == "BUY" and hour == 9) or hour == 12)
        )
    return False


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


def cost_regime_decision(
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
    config: CostRegimeConfig,
) -> CostRegimeDecision:
    """Return a fail-closed risk cap that never exceeds the frozen allocation."""
    base = max(0.0, float(base_risk_percent))
    cost = max(0.0, float(all_in_cost))
    target = max(0.0, float(target_r))
    symbol = str(symbol).upper()
    engine = str(engine)
    mode = str(mode).upper()

    if base <= 0 or target <= 0:
        return _decision(False, "SHADOW", 0.0, "INVALID_RISK_OR_TARGET", cost, target)

    # Exact zero-cost replay parity is retained for reproducibility.
    if cost <= 1e-12:
        return _decision(True, "ZERO_COST_PARITY", base, "EXACT_V14_3_PARITY", cost, target)

    if cost > config.maximum_supported_cost_r + 1e-12:
        return _decision(False, "SHADOW", 0.0, "COST_R_ABOVE_SUPPORTED_LIMIT", cost, target)
    if cost / target > config.maximum_cost_fraction_of_target + 1e-12:
        return _decision(False, "SHADOW", 0.0, "COST_CONSUMES_TOO_MUCH_TARGET", cost, target)

    if mode == "V12":
        if engine in WEAK_V12_ENGINES:
            return _decision(False, "SHADOW", 0.0, "WEAK_V12_AFTER_COSTS", cost, target)
        if engine in ROBUST_V12_ENGINES:
            return _decision(True, "COST_ROBUST_V12", base, "VALIDATED_V12_EDGE", cost, target)
        return _decision(False, "SHADOW", 0.0, "UNREGISTERED_V12_ENGINE", cost, target)

    if mode != "ICT":
        return _decision(False, "SHADOW", 0.0, "UNSUPPORTED_MODE", cost, target)

    # Very low-cost execution preserves the full documented ICT allocation.
    if cost <= config.parity_cost_r + 1e-12:
        return _decision(True, "LOW_COST_PARITY", base, "LOW_COST_V14_3_PARITY", cost, target)

    # EURUSD and AUDUSD H1 satellite sleeves retained positive cost evidence.
    if symbol in {"EURUSD", "AUDUSD"}:
        return _decision(True, "COST_ROBUST_SATELLITE", base, "SATELLITE_EDGE_AFTER_COSTS", cost, target)

    # USDJPY's selected ICT sleeve did not clear the retail-cost reserve.
    if symbol == "USDJPY":
        if cost <= 0.08 + 1e-12:
            return _decision(True, "MEDIUM_COST_USDJPY", base, "USDJPY_WITHIN_MEDIUM_COST", cost, target)
        return _decision(False, "SHADOW", 0.0, "USDJPY_EDGE_BELOW_RETAIL_COST", cost, target)

    # At medium cost the broad GBPJPY sweep-15 family remains fundable.
    if (
        symbol == "GBPJPY"
        and setup == "sweep_reclaim_15"
        and cost <= config.medium_cost_r + 1e-12
    ):
        return _decision(True, "MEDIUM_COST_GBPJPY", base, "GBPJPY_SWEEP15_MEDIUM_COST_EDGE", cost, target)

    # At retail/stressed cost only the frozen high-cost GBP subsets receive risk.
    if strict_retail_profile(symbol, setup, side, entry_time):
        return _decision(True, "STRICT_RETAIL_GBP", base, "PRE_ENTRY_HIGH_COST_PROFILE", cost, target)

    # Medium-cost non-core candidates may remain at micro observation risk.
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
    CostRegimeConfig().validate()
    assert WEAK_V12_ENGINES.isdisjoint(ROBUST_V12_ENGINES)
    assert strict_retail_profile(
        "GBPUSD", "breakout_15_fade", "SELL", "2026-07-17T12:00:00+00:00"
    )
    assert strict_retail_profile(
        "GBPJPY", "sweep_reclaim_15", "BUY", "2026-07-17T09:00:00+00:00"
    )


validate_profile()
