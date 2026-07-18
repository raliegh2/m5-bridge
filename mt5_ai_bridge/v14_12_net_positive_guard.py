"""V14.12 net-positive live profitability controls.

This module turns the V14.5.2 cost-robust historical allocation into a live,
fee-aware promotion policy. Broker reconciliation supplies net cash P/L after
commission, swap and fees. Setups and symbols begin at probation/observation
risk and receive full historical allocation only while their recent net broker
results remain profitable.

The policy cannot guarantee future profit. It is deliberately one-way: it may
reduce risk or reject an expensive entry, but it never increases risk above the
validated V14.5.2 allocation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from .v14_5_cost_robust_profile import V14_5_OBSERVATION_RISK_PERCENT

TIERS = {"PROBATION", "OBSERVE", "REDUCED", "FULL"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class NetPositiveGuardConfig:
    setup_window: int = 30
    symbol_window: int = 60
    minimum_setup_trades: int = 12
    minimum_symbol_trades: int = 20
    probation_risk_multiplier: float = 0.25
    reduced_risk_multiplier: float = 0.50
    full_setup_profit_factor: float = 1.15
    full_symbol_profit_factor: float = 1.08
    full_setup_net_r: float = 1.00
    full_symbol_net_r: float = 1.00
    minimum_positive_profit_factor: float = 1.00
    minimum_positive_net_r: float = 0.00
    commission_equivalent_pips: float = 0.10
    slippage_buffer_pips: float = 0.10
    swap_reserve_pips: float = 0.05
    maximum_all_in_cost_fraction_of_stop: float = 0.12
    maximum_all_in_cost_fraction_of_target: float = 0.08

    @classmethod
    def from_env(cls) -> "NetPositiveGuardConfig":
        config = cls(
            setup_window=_env_int("V14_12_SETUP_WINDOW", cls.setup_window),
            symbol_window=_env_int("V14_12_SYMBOL_WINDOW", cls.symbol_window),
            minimum_setup_trades=_env_int(
                "V14_12_MINIMUM_SETUP_TRADES", cls.minimum_setup_trades
            ),
            minimum_symbol_trades=_env_int(
                "V14_12_MINIMUM_SYMBOL_TRADES", cls.minimum_symbol_trades
            ),
            probation_risk_multiplier=_env_float(
                "V14_12_PROBATION_RISK_MULTIPLIER", cls.probation_risk_multiplier
            ),
            reduced_risk_multiplier=_env_float(
                "V14_12_REDUCED_RISK_MULTIPLIER", cls.reduced_risk_multiplier
            ),
            full_setup_profit_factor=_env_float(
                "V14_12_FULL_SETUP_PROFIT_FACTOR", cls.full_setup_profit_factor
            ),
            full_symbol_profit_factor=_env_float(
                "V14_12_FULL_SYMBOL_PROFIT_FACTOR", cls.full_symbol_profit_factor
            ),
            full_setup_net_r=_env_float(
                "V14_12_FULL_SETUP_NET_R", cls.full_setup_net_r
            ),
            full_symbol_net_r=_env_float(
                "V14_12_FULL_SYMBOL_NET_R", cls.full_symbol_net_r
            ),
            minimum_positive_profit_factor=_env_float(
                "V14_12_MINIMUM_POSITIVE_PROFIT_FACTOR",
                cls.minimum_positive_profit_factor,
            ),
            minimum_positive_net_r=_env_float(
                "V14_12_MINIMUM_POSITIVE_NET_R", cls.minimum_positive_net_r
            ),
            commission_equivalent_pips=_env_float(
                "V14_12_COMMISSION_EQUIVALENT_PIPS",
                cls.commission_equivalent_pips,
            ),
            slippage_buffer_pips=_env_float(
                "V14_12_SLIPPAGE_BUFFER_PIPS", cls.slippage_buffer_pips
            ),
            swap_reserve_pips=_env_float(
                "V14_12_SWAP_RESERVE_PIPS", cls.swap_reserve_pips
            ),
            maximum_all_in_cost_fraction_of_stop=_env_float(
                "V14_12_MAX_ALL_IN_COST_FRACTION_OF_STOP",
                cls.maximum_all_in_cost_fraction_of_stop,
            ),
            maximum_all_in_cost_fraction_of_target=_env_float(
                "V14_12_MAX_ALL_IN_COST_FRACTION_OF_TARGET",
                cls.maximum_all_in_cost_fraction_of_target,
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.setup_window < 5 or self.symbol_window < 5:
            raise ValueError("V14.12 rolling windows must be at least 5 trades")
        if self.minimum_setup_trades < 1 or self.minimum_symbol_trades < 1:
            raise ValueError("V14.12 minimum trade counts must be positive")
        if not 0 < self.probation_risk_multiplier <= 1:
            raise ValueError("V14.12 probation multiplier must be in (0, 1]")
        if not 0 < self.reduced_risk_multiplier <= 1:
            raise ValueError("V14.12 reduced multiplier must be in (0, 1]")
        if self.probation_risk_multiplier > self.reduced_risk_multiplier:
            raise ValueError("Probation risk cannot exceed reduced risk")
        if self.full_setup_profit_factor < self.minimum_positive_profit_factor:
            raise ValueError("Full setup PF must exceed the positive PF floor")
        if self.full_symbol_profit_factor < self.minimum_positive_profit_factor:
            raise ValueError("Full symbol PF must exceed the positive PF floor")
        for value in (
            self.commission_equivalent_pips,
            self.slippage_buffer_pips,
            self.swap_reserve_pips,
        ):
            if value < 0:
                raise ValueError("V14.12 modeled pip reserves cannot be negative")
        if not 0 < self.maximum_all_in_cost_fraction_of_stop <= 1:
            raise ValueError("V14.12 stop-cost fraction must be in (0, 1]")
        if not 0 < self.maximum_all_in_cost_fraction_of_target <= 1:
            raise ValueError("V14.12 target-cost fraction must be in (0, 1]")


@dataclass(frozen=True)
class RollingPerformance:
    trades: int
    net_r: float
    expectancy_r: float
    profit_factor: float


def rolling_performance(results: Iterable[float], window: int) -> RollingPerformance:
    values = [float(value) for value in results][-int(window):]
    if not values:
        return RollingPerformance(0, 0.0, 0.0, 0.0)
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.0
    return RollingPerformance(
        trades=len(values),
        net_r=float(sum(values)),
        expectancy_r=float(sum(values) / len(values)),
        profit_factor=float(profit_factor),
    )


def net_positive_tier(
    setup_results: Iterable[float],
    symbol_results: Iterable[float],
    config: NetPositiveGuardConfig,
) -> str:
    setup = rolling_performance(setup_results, config.setup_window)
    symbol = rolling_performance(symbol_results, config.symbol_window)

    if (
        setup.trades < config.minimum_setup_trades
        or symbol.trades < config.minimum_symbol_trades
    ):
        return "PROBATION"

    setup_positive = (
        setup.net_r > config.minimum_positive_net_r
        and setup.expectancy_r > 0.0
        and setup.profit_factor >= config.minimum_positive_profit_factor
    )
    symbol_positive = (
        symbol.net_r > config.minimum_positive_net_r
        and symbol.expectancy_r > 0.0
        and symbol.profit_factor >= config.minimum_positive_profit_factor
    )
    if not setup_positive or not symbol_positive:
        return "OBSERVE"

    full = (
        setup.net_r >= config.full_setup_net_r
        and setup.profit_factor >= config.full_setup_profit_factor
        and symbol.net_r >= config.full_symbol_net_r
        and symbol.profit_factor >= config.full_symbol_profit_factor
    )
    return "FULL" if full else "REDUCED"


def apply_net_positive_tier(
    base_risk_percent: float,
    tier: str,
    config: NetPositiveGuardConfig,
) -> float:
    if tier not in TIERS:
        raise ValueError(f"Unsupported V14.12 tier: {tier}")
    base = max(0.0, float(base_risk_percent))
    observation = min(base, V14_5_OBSERVATION_RISK_PERCENT)
    if tier == "OBSERVE":
        return observation
    if tier == "PROBATION":
        return max(observation, base * config.probation_risk_multiplier)
    if tier == "REDUCED":
        return max(observation, base * config.reduced_risk_multiplier)
    return base


def all_in_cost_reason(
    spread_pips: float,
    stop_pips: float,
    target_pips: float,
    config: NetPositiveGuardConfig,
) -> str | None:
    if stop_pips <= 0 or target_pips <= 0:
        return None
    reserve = (
        float(config.commission_equivalent_pips)
        + float(config.slippage_buffer_pips)
        + float(config.swap_reserve_pips)
    )
    total_cost = max(0.0, float(spread_pips)) + reserve
    stop_fraction = total_cost / float(stop_pips)
    target_fraction = total_cost / float(target_pips)
    if (
        stop_fraction > config.maximum_all_in_cost_fraction_of_stop + 1e-12
        or target_fraction > config.maximum_all_in_cost_fraction_of_target + 1e-12
    ):
        return (
            f"All-in entry cost {total_cost:.2f} pips (spread {spread_pips:.2f} + "
            f"reserve {reserve:.2f}) is {stop_fraction * 100.0:.1f}% of the "
            f"{stop_pips:.1f}-pip stop and {target_fraction * 100.0:.1f}% of the "
            f"{target_pips:.1f}-pip target; caps are "
            f"{config.maximum_all_in_cost_fraction_of_stop * 100.0:.1f}% and "
            f"{config.maximum_all_in_cost_fraction_of_target * 100.0:.1f}%"
        )
    return None
