"""V14.4 profit-guard primitives for the live V14.3 satellite bot.

The 10-year benchmark assumed zero spread, commission, swap and slippage
while the portfolio profit factor was only 1.18 (average trade +$7 on the
research account). The live GBP ICT engine trades 5.0-7.5 pip stops with
1.25R targets, so even 0.5-1.1 pips of spread consumes 10-22% of every
trade and can turn the whole edge negative. This module adds the controls
the research replay never needed:

* a cost gate that rejects entries when the live spread is too large a
  fraction of the stop distance;
* a per-setup live expectancy tracker that reduces risk on setups that
  bleed in forward testing and demotes persistently negative setups to
  micro observation risk;
* a portfolio-level daily loss stop measured in percent of day-start
  equity (the parity profile only has per-symbol caps and loss counts);
* a tight staleness limit for M1 scalp signals (the shared 90-minute
  limit was designed for H1 engines; a sweep/reclaim entry taken almost
  an hour late is a different trade from the researched one);
* peak-equity reconstruction from broker deal history, so a deleted or
  reset state file cannot blind the drawdown governor.

Everything is additive: no V14.3 frozen profile, signal or admission value
is modified. All thresholds are environment-tunable with safe defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

OBSERVATION_RISK_PERCENT = 0.025
"""Micro allocation used when a setup is demoted for negative live results."""


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
class ProfitGuardConfig:
    """Tunable live-profitability guard thresholds."""

    # Reject an entry when spread exceeds this fraction of the stop distance.
    # 0.10 on a 5-pip stop allows at most 0.5 pips of spread.
    max_spread_fraction_of_stop: float = 0.10
    # M1 scalp signals must be executed almost immediately to match research.
    max_m1_signal_age_minutes: float = 5.0
    # Stop opening new positions once the account has lost this percent of
    # day-start equity (closed + floating), for the rest of the UTC day.
    daily_loss_stop_percent: float = 1.50
    # Rolling live-expectancy window per (symbol, setup).
    expectancy_window: int = 20
    # Below this cumulative R over the window, risk is multiplied down.
    reduce_threshold_r: float = -4.0
    reduced_risk_multiplier: float = 0.50
    # Below this cumulative R, the setup trades at observation risk only.
    observe_threshold_r: float = -8.0
    # Require this many closed trades before any reduction can trigger.
    min_trades_before_reduction: int = 8
    # How far back to rebuild the balance curve for peak-equity seeding.
    peak_reconstruction_days: int = 120

    @classmethod
    def from_env(cls) -> "ProfitGuardConfig":
        config = cls(
            max_spread_fraction_of_stop=_env_float(
                "V14_4_MAX_SPREAD_FRACTION_OF_STOP", cls.max_spread_fraction_of_stop
            ),
            max_m1_signal_age_minutes=_env_float(
                "V14_4_MAX_M1_SIGNAL_AGE_MINUTES", cls.max_m1_signal_age_minutes
            ),
            daily_loss_stop_percent=_env_float(
                "V14_4_DAILY_LOSS_STOP_PERCENT", cls.daily_loss_stop_percent
            ),
            expectancy_window=_env_int(
                "V14_4_EXPECTANCY_WINDOW", cls.expectancy_window
            ),
            reduce_threshold_r=_env_float(
                "V14_4_REDUCE_THRESHOLD_R", cls.reduce_threshold_r
            ),
            reduced_risk_multiplier=_env_float(
                "V14_4_REDUCED_RISK_MULTIPLIER", cls.reduced_risk_multiplier
            ),
            observe_threshold_r=_env_float(
                "V14_4_OBSERVE_THRESHOLD_R", cls.observe_threshold_r
            ),
            min_trades_before_reduction=_env_int(
                "V14_4_MIN_TRADES_BEFORE_REDUCTION", cls.min_trades_before_reduction
            ),
            peak_reconstruction_days=_env_int(
                "V14_4_PEAK_RECONSTRUCTION_DAYS", cls.peak_reconstruction_days
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not 0 < self.max_spread_fraction_of_stop <= 1:
            raise ValueError("V14_4_MAX_SPREAD_FRACTION_OF_STOP must be in (0, 1]")
        if self.max_m1_signal_age_minutes <= 0:
            raise ValueError("V14_4_MAX_M1_SIGNAL_AGE_MINUTES must be positive")
        if not 0 < self.daily_loss_stop_percent < 9.60:
            raise ValueError(
                "V14_4_DAILY_LOSS_STOP_PERCENT must be positive and below the"
                " 9.60% parity hard stop"
            )
        if self.expectancy_window < 5:
            raise ValueError("V14_4_EXPECTANCY_WINDOW must be at least 5")
        if self.observe_threshold_r > self.reduce_threshold_r:
            raise ValueError(
                "V14_4_OBSERVE_THRESHOLD_R must be at or below V14_4_REDUCE_THRESHOLD_R"
            )
        if not 0 < self.reduced_risk_multiplier <= 1:
            raise ValueError("V14_4_REDUCED_RISK_MULTIPLIER must be in (0, 1]")
        if self.min_trades_before_reduction < 1:
            raise ValueError("V14_4_MIN_TRADES_BEFORE_REDUCTION must be positive")
        if self.peak_reconstruction_days < 1:
            raise ValueError("V14_4_PEAK_RECONSTRUCTION_DAYS must be positive")


def setup_key(symbol: str, setup: str) -> str:
    return f"{symbol.upper()}/{setup}"


def rolling_r_sum(results: list[float], window: int) -> float:
    return float(sum(results[-window:]))


def expectancy_tier(
    results: list[float],
    config: ProfitGuardConfig,
) -> str:
    """Return FULL, REDUCED or OBSERVE for a setup's recent live results."""
    if len(results) < config.min_trades_before_reduction:
        return "FULL"
    recent = rolling_r_sum(results, config.expectancy_window)
    if recent <= config.observe_threshold_r:
        return "OBSERVE"
    if recent <= config.reduce_threshold_r:
        return "REDUCED"
    return "FULL"


def apply_expectancy_tier(
    base_risk_percent: float,
    tier: str,
    config: ProfitGuardConfig,
) -> float:
    if tier == "OBSERVE":
        return min(base_risk_percent, OBSERVATION_RISK_PERCENT)
    if tier == "REDUCED":
        return base_risk_percent * config.reduced_risk_multiplier
    return base_risk_percent


def spread_cost_reason(
    spread_pips: float,
    stop_pips: float,
    config: ProfitGuardConfig,
) -> str | None:
    """Return a rejection message when spread is too large for the stop."""
    if stop_pips <= 0:
        return None
    fraction = spread_pips / stop_pips
    if fraction > config.max_spread_fraction_of_stop + 1e-12:
        return (
            f"Spread {spread_pips:.2f} pips is {fraction * 100.0:.1f}% of the"
            f" {stop_pips:.1f}-pip stop; cap is"
            f" {config.max_spread_fraction_of_stop * 100.0:.1f}%"
        )
    return None


def _deal_cash_delta(deal: Any) -> float:
    return (
        float(getattr(deal, "profit", 0.0) or 0.0)
        + float(getattr(deal, "commission", 0.0) or 0.0)
        + float(getattr(deal, "swap", 0.0) or 0.0)
        + float(getattr(deal, "fee", 0.0) or 0.0)
    )


def reconstruct_peak_balance(
    client: Any,
    current_balance: float,
    lookback_days: int,
    now: datetime | None = None,
) -> float:
    """Rebuild the historical balance peak from broker deal history.

    Walks the deal ledger backwards from the current balance so a reset or
    deleted state file cannot make the drawdown governor believe the account
    is at its peak while it is actually in drawdown.
    """
    now = now or datetime.now(timezone.utc)
    peak = float(current_balance)
    if not hasattr(client, "history_deals_get"):
        return peak
    try:
        deals = client.history_deals_get(
            now - timedelta(days=lookback_days),
            now + timedelta(days=1),
        ) or []
    except Exception:  # noqa: BLE001 - broker call must never break admission
        return peak
    ordered = sorted(
        deals,
        key=lambda deal: int(getattr(deal, "time", 0) or 0),
        reverse=True,
    )
    balance = float(current_balance)
    for deal in ordered:
        balance -= _deal_cash_delta(deal)
        peak = max(peak, balance)
    return peak
