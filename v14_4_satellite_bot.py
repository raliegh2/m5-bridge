"""Windows-safe launcher for V14.16 cost-efficient quality allocation.

Every symbol keeps V12 and ICT generation active. V14.16 retains the V14.15
cost, evidence and cross-engine reasoning path, then permits selected
full-strength profiles to use the existing 0.80% trade-risk ceiling only after
mature broker-net evidence confirms both the engine and its symbol/mode sleeve.
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
    PARITY_MAX_TRADE_RISK_PERCENT,
    ResearchParityLiveRunnerConfig,
)
from mt5_ai_bridge.v14_4_profit_guard import ProfitGuardConfig
from mt5_ai_bridge.v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from mt5_ai_bridge.v14_15_unified_reasoning import DUAL_ENGINE_REGISTRY
from mt5_ai_bridge.v14_16_quality_allocation_live import (
    QualityAllocationLiveExecutor,
)
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _quality_banner(
    config: ResearchParityLiveRunnerConfig,
    dashboard_url: str,
) -> None:
    profit_guard = ProfitGuardConfig.from_env()
    cost = ExtendedCostRegimeConfig.from_env()
    print("=" * 76)
    print(" V14.16 SATELLITE BOT — COST-EFFICIENT V12 + ICT ALLOCATION")
    print("=" * 76)
    print(f" Mode                 : {config.execution_mode}")
    print(f" Symbols              : {', '.join(bot.SYMBOLS)}")
    print(f" Dual-engine coverage : {len(DUAL_ENGINE_REGISTRY)}/5 symbols with V12 + ICT")
    print(f" Single-trade ceiling : {PARITY_MAX_TRADE_RISK_PERCENT:.2f}%")
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
    print(" Quality profiles     : GBPUSD/EURUSD/AUDUSD cost-resilient sleeves")
    print(" Live uplift evidence : >=12 engine and >=16 symbol/mode broker-net trades")
    print(" Reduction authority  : frozen nominal/pressure/expectancy/DD reductions hold")
    print(" Cross-engine logic   : aligned exposure recognized; conflicts shadowed")
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
    print(" Transmission         : confirmed MT5 demo account only")
    print(f" Dashboard            : {dashboard_url}")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


bot.LiveRunnerConfig = ResearchParityLiveRunnerConfig
bot.SatelliteLiveExecutor = QualityAllocationLiveExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot._startup_banner = _quality_banner


if __name__ == "__main__":
    bot.main()
