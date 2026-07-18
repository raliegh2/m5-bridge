"""Windows-safe launcher for V14.15 unified dual-engine reasoning.

Every symbol keeps V12 and ICT generation active.  V14.15 evaluates the
existing transaction-cost decision, broker-net rolling evidence, and same-symbol
cross-engine alignment before funding a proposal.  It can preserve, reduce,
probation-size, or shadow a trade; it cannot exceed the frozen strategy risk.
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
from mt5_ai_bridge.v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from mt5_ai_bridge.v14_15_unified_reasoning import DUAL_ENGINE_REGISTRY
from mt5_ai_bridge.v14_15_unified_reasoning_execution import UnifiedReasoningLiveExecutor
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _reasoning_banner(
    config: ResearchParityLiveRunnerConfig,
    dashboard_url: str,
) -> None:
    profit_guard = ProfitGuardConfig.from_env()
    cost = ExtendedCostRegimeConfig.from_env()
    print("=" * 76)
    print(" V14.15 SATELLITE BOT — UNIFIED V12 + ICT REASONING")
    print("=" * 76)
    print(f" Mode                 : {config.execution_mode}")
    print(f" Symbols              : {', '.join(bot.SYMBOLS)}")
    print(f" Dual-engine coverage : {len(DUAL_ENGINE_REGISTRY)}/5 symbols with V12 + ICT")
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
    print(" Reasoning evidence   : broker-net engine + symbol/mode rolling R")
    print(" Cross-engine logic   : aligned exposure recognized; conflicts shadowed")
    print(" Probation profiles   : bounded recovery opportunities for shadowed modes")
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
    print(" Risk authority       : never above the frozen strategy allocation")
    print(" Transmission         : confirmed MT5 demo account only")
    print(f" Dashboard            : {dashboard_url}")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


bot.LiveRunnerConfig = ResearchParityLiveRunnerConfig
bot.SatelliteLiveExecutor = UnifiedReasoningLiveExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot._startup_banner = _reasoning_banner


if __name__ == "__main__":
    bot.main()
