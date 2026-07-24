"""Profit-preserving risk profile for the V12 + V14.3 combined bot.

The entry logic is unchanged. This module assigns pre-entry risk from the signal's
symbol/setup and applies continuous drawdown scaling plus symbol-specific loss
controls. Values are frozen for demo forward testing and chronological replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SetupRiskRegistry(dict[tuple[str, str], float]):
    """GBP setup-risk table with safe fallback semantics for satellite symbols.

    GBPUSD and GBPJPY have frozen setup-specific tiers. EURUSD, AUDUSD and
    USDJPY receive their risk from the satellite signal adapters. Research
    utilities may temporarily register satellite keys for direct indexing, but
    callers using ``get(key, signal_risk)`` must retain the signal-supplied tier
    instead of inheriting mutable cross-run research state.
    """

    _FROZEN_SETUP_SYMBOLS = frozenset({"GBPUSD", "GBPJPY"})

    def get(self, key: Any, default: Any = None) -> Any:
        if isinstance(key, tuple) and len(key) == 2:
            symbol = str(key[0]).upper()
            if symbol not in self._FROZEN_SETUP_SYMBOLS:
                return default
        return super().get(key, default)


SETUP_RISK_PERCENT: SetupRiskRegistry = SetupRiskRegistry({
    ("GBPUSD", "breakout_60_fade"): 0.731,
    ("GBPUSD", "breakout_15_fade"): 0.455,
    ("GBPUSD", "breakout_30_fade"): 0.320,
    ("GBPUSD", "sweep_reclaim_30"): 0.606,
    ("GBPUSD", "sweep_reclaim_60"): 0.450,
    ("GBPJPY", "sweep_reclaim_15"): 0.735,
    ("GBPJPY", "sweep_reclaim_60"): 0.330,
    ("GBPJPY", "sweep_reclaim_30"): 0.058,
})


@dataclass(frozen=True)
class SymbolGuard:
    post_loss_multiplier: float
    max_open_positions: int
    max_entries_per_hour: int
    daily_loss_cap_percent: float
    stop_after_daily_losses: int
    block_after_consecutive_losses: int
    rolling_loss_count: int
    rolling_loss_hours: float
    win_pressure_recovery: float
    session_start_hour_utc: int = 0
    session_end_hour_utc: int = 24


SYMBOL_GUARDS: dict[str, SymbolGuard] = {
    "GBPUSD": SymbolGuard(
        post_loss_multiplier=0.82,
        max_open_positions=4,
        max_entries_per_hour=3,
        daily_loss_cap_percent=2.50,
        stop_after_daily_losses=6,
        block_after_consecutive_losses=5,
        rolling_loss_count=5,
        rolling_loss_hours=4.0,
        win_pressure_recovery=1.0,
    ),
    "GBPJPY": SymbolGuard(
        post_loss_multiplier=0.70,
        max_open_positions=1,
        max_entries_per_hour=2,
        daily_loss_cap_percent=1.00,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=4.0,
        win_pressure_recovery=0.75,
        session_start_hour_utc=7,
        session_end_hour_utc=20,
    ),
}


@dataclass(frozen=True)
class PortfolioGuard:
    starting_balance: float = 5000.0
    drawdown_scale_start_percent: float = 6.77
    drawdown_scale_end_percent: float = 9.47
    drawdown_risk_floor_percent: float = 0.10
    hard_drawdown_stop_percent: float = 9.90
    max_ict_open_risk_percent: float = 1.75
    max_combined_open_risk_percent: float = 3.25
    max_simultaneous_ict_positions: int = 6
    max_total_entries_per_hour: int = 8
    global_pause_after_consecutive_losses: int = 6
    global_pause_hours: float = 1.0
    global_stop_after_daily_losses: int = 12


PORTFOLIO_GUARD = PortfolioGuard()


def base_risk_percent(symbol: str, setup: str) -> float:
    try:
        return SETUP_RISK_PERCENT[(symbol.upper(), setup)]
    except KeyError as exc:
        raise ValueError(f"No approved V14.3 risk tier for {symbol}/{setup}") from exc


def scaled_risk_percent(
    symbol: str,
    setup: str,
    pre_entry_drawdown_percent: float,
    under_loss_pressure: bool,
) -> float:
    """Return risk using only information available before the entry."""
    guard = SYMBOL_GUARDS[symbol.upper()]
    portfolio = PORTFOLIO_GUARD
    risk = base_risk_percent(symbol, setup)
    if under_loss_pressure:
        risk *= guard.post_loss_multiplier

    drawdown = max(0.0, float(pre_entry_drawdown_percent))
    if drawdown <= portfolio.drawdown_scale_start_percent:
        return risk
    if drawdown >= portfolio.drawdown_scale_end_percent:
        return min(risk, portfolio.drawdown_risk_floor_percent)

    span = portfolio.drawdown_scale_end_percent - portfolio.drawdown_scale_start_percent
    fraction = (drawdown - portfolio.drawdown_scale_start_percent) / span
    floor = min(risk, portfolio.drawdown_risk_floor_percent)
    return risk * (1.0 - fraction) + floor * fraction


def validate_profile() -> None:
    expected = {
        ("GBPUSD", "breakout_60_fade"),
        ("GBPUSD", "breakout_15_fade"),
        ("GBPUSD", "breakout_30_fade"),
        ("GBPUSD", "sweep_reclaim_30"),
        ("GBPUSD", "sweep_reclaim_60"),
        ("GBPJPY", "sweep_reclaim_15"),
        ("GBPJPY", "sweep_reclaim_30"),
        ("GBPJPY", "sweep_reclaim_60"),
    }
    if set(SETUP_RISK_PERCENT) != expected:
        raise RuntimeError("V14.3 setup risk registry is incomplete")
    if SYMBOL_GUARDS["GBPJPY"].max_open_positions != 1:
        raise RuntimeError("GBPJPY must retain the one-position guard")
    if PORTFOLIO_GUARD.hard_drawdown_stop_percent >= 10.0:
        raise RuntimeError("Hard drawdown stop must remain below 10%")


validate_profile()
