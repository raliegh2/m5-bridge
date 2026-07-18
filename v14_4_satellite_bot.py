"""Windows-safe launcher for V14.14 extended cost-regime research parity.

V14.14 retains the V14.3 completed-candle signals, setup-specific risk,
portfolio controls and V14.4 live guards. It extends the all-in cost policy
above 0.18R only for engine groups with supporting cost-adjusted evidence.
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
from mt5_ai_bridge.v14_14_extended_cost_execution import ExtendedCostRegimeLiveExecutor
from mt5_ai_bridge.v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _cost_regime_banner(
    config: ResearchParityLiveRunnerConfig,
    dashboard_url: str,
) -> None:
    profit_guard = ProfitGuardConfig.from_env()
    cost = ExtendedCostRegimeConfig.from_env()
    print("=" * 76)
    print(" V14.14 SATELLITE BOT — EXTENDED ENGINE-SPECIFIC COST REGIME")
    print("=" * 76)
    print(f" Mode                 : {config.execution_mode}")
    print(f" Symbols              : {', '.join(bot.SYMBOLS)}")
    print(f" ICT open-risk cap    : {PARITY_MAX_ICT_OPEN_RISK_PERCENT:.2f}%")
    print(f" Combined-risk cap    : {PARITY_MAX_COMBINED_OPEN_RISK_PERCENT:.2f}%")
    print(f" Max ICT positions    : {PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS}")
    print(f" Max ICT entries/hour : {PARITY_MAX_TOTAL_ENTRIES_PER_HOUR}")
    print(" Drawdown governor    : 7.50 / 8.50 / 9.00 / 9.60% hard stop")
    print(
        " Cost tiers           : parity <= "
        f"{cost.parity_cost_r:.2f}R, medium <= {cost.medium_cost_r:.2f}R, "
        f"standard <= {cost.standard_cost_r:.2f}R"
    )
    print(
        " Extended ceilings    : V12 "
        f"{cost.maximum_v12_cost_r:.2f}R, satellite ICT "
        f"{cost.maximum_satellite_ict_cost_r:.2f}R, strict GBP ICT "
        f"{cost.maximum_strict_gbp_cost_r:.2f}R"
    )
    print(
        " Cost reserves        : commission "
        f"{cost.commission_reserve_pips:.2f}p, slippage "
        f"{cost.slippage_reserve_pips:.2f}p, non-M1 swap "
        f"{cost.non_m1_swap_reserve_pips:.2f}p, latency "
        f"{cost.latency_reserve_r:.3f}R"
    )
    print(
        " Spread guard         : spread <= "
        f"{profit_guard.max_spread_fraction_of_stop * 100.0:.0f}% of stop"
    )
    print(
        f" M1 staleness limit   : {profit_guard.max_m1_signal_age_minutes:.0f} minutes"
    )
    print(
        f" Daily loss stop      : {profit_guard.daily_loss_stop_percent:.2f}%"
        " of day-start equity"
    )
    print(" Shadow mode          : cost-negative candidates logged, no order funded")
    print(" Peak-equity seeding  : reconstructed from broker deal history")
    print(" Transmission         : confirmed MT5 demo account only")
    print(f" Dashboard            : {dashboard_url}")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


bot.LiveRunnerConfig = ResearchParityLiveRunnerConfig
bot.SatelliteLiveExecutor = ExtendedCostRegimeLiveExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot._startup_banner = _cost_regime_banner


if __name__ == "__main__":
    bot.main()
