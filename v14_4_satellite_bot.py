"""Windows-safe launcher for the V14.4 profit-guarded research-parity bot.

Identical to the V14.3 research-risk parity launcher, plus the V14.4 live
profit guards: spread-cost admission gate, M1 staleness limit, portfolio
daily loss stop, per-setup live expectancy tiers, and peak-equity
reconstruction from broker history.
"""
from __future__ import annotations

import v14_3_satellite_bot_m1 as bot
from mt5_ai_bridge.mt5_client import create_client as create_raw_client
from mt5_ai_bridge.v14_3_mt5_broker_compat import MT5BrokerCompatibilityClient
from mt5_ai_bridge.v14_3_research_parity_execution import (
    PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
    PARITY_MAX_ICT_OPEN_RISK_PERCENT,
    PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS,
    PARITY_MAX_TOTAL_ENTRIES_PER_HOUR,
    ResearchParityLiveRunnerConfig,
)
from mt5_ai_bridge.v14_4_profit_guard import ProfitGuardConfig
from mt5_ai_bridge.v14_4_profit_guard_execution import ProfitGuardedLiveExecutor
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _profit_guard_banner(
    config: ResearchParityLiveRunnerConfig,
    dashboard_url: str,
) -> None:
    guard = ProfitGuardConfig.from_env()
    print("=" * 76)
    print(" V14.4 SATELLITE BOT — RESEARCH-RISK PARITY + LIVE PROFIT GUARD")
    print("=" * 76)
    print(f" Mode                 : {config.execution_mode}")
    print(f" Symbols              : {', '.join(bot.SYMBOLS)}")
    print(f" ICT open-risk cap    : {PARITY_MAX_ICT_OPEN_RISK_PERCENT:.2f}%")
    print(f" Combined-risk cap    : {PARITY_MAX_COMBINED_OPEN_RISK_PERCENT:.2f}%")
    print(f" Max ICT positions    : {PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS}")
    print(f" Max ICT entries/hour : {PARITY_MAX_TOTAL_ENTRIES_PER_HOUR}")
    print(" Drawdown governor    : 7.50 / 8.50 / 9.00 / 9.60% hard stop")
    print(
        " Spread cost gate     : spread <= "
        f"{guard.max_spread_fraction_of_stop * 100.0:.0f}% of stop distance"
    )
    print(
        f" M1 staleness limit   : {guard.max_m1_signal_age_minutes:.0f} minutes"
    )
    print(
        f" Daily loss stop      : {guard.daily_loss_stop_percent:.2f}% of day-start equity"
    )
    print(
        " Expectancy tiers     : reduce at "
        f"{guard.reduce_threshold_r:+.1f}R, observe at "
        f"{guard.observe_threshold_r:+.1f}R over last {guard.expectancy_window}"
    )
    print(" Peak-equity seeding  : reconstructed from broker deal history")
    print(" Transmission         : confirmed MT5 demo account only")
    print(f" Dashboard            : {dashboard_url}")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


bot.LiveRunnerConfig = ResearchParityLiveRunnerConfig
bot.SatelliteLiveExecutor = ProfitGuardedLiveExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot._startup_banner = _profit_guard_banner


if __name__ == "__main__":
    bot.main()
