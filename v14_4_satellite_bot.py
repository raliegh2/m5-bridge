"""Windows-safe launcher for the V14.12 net-positive live model.

This remains the existing V14.4 startup surface so current operators do not
need a new command. V14.12 adds the V14.5.2 cost-robust allocation, all-in
spread/commission/slippage/swap admission, and broker-net setup/symbol
promotion gates while retaining every V14.4 safety control.
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
from mt5_ai_bridge.v14_5_cost_robust_profile import (
    PROMOTED_V12_ENGINES,
    V14_5_OBSERVATION_RISK_PERCENT,
)
from mt5_ai_bridge.v14_12_live_execution import NetPositiveLiveExecutor
from mt5_ai_bridge.v14_12_net_positive_guard import NetPositiveGuardConfig
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _profit_guard_banner(
    config: ResearchParityLiveRunnerConfig,
    dashboard_url: str,
) -> None:
    guard = ProfitGuardConfig.from_env()
    net = NetPositiveGuardConfig.from_env()
    print("=" * 76)
    print(" V14.12 SATELLITE BOT — NET-POSITIVE AFTER-COST LIVE MODEL")
    print("=" * 76)
    print(f" Mode                 : {config.execution_mode}")
    print(f" Symbols              : {', '.join(bot.SYMBOLS)}")
    print(
        " Promoted V12 engines : "
        + ", ".join(sorted(PROMOTED_V12_ENGINES))
    )
    print(f" Observation risk     : {V14_5_OBSERVATION_RISK_PERCENT:.3f}%")
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
        " All-in cost gate     : total cost <= "
        f"{net.maximum_all_in_cost_fraction_of_stop * 100.0:.0f}% stop / "
        f"{net.maximum_all_in_cost_fraction_of_target * 100.0:.0f}% target"
    )
    print(
        " Cost reserve         : commission "
        f"{net.commission_equivalent_pips:.2f} + slippage "
        f"{net.slippage_buffer_pips:.2f} + swap "
        f"{net.swap_reserve_pips:.2f} pips"
    )
    print(
        " Net-positive gate    : setup/symbol require "
        f"{net.minimum_setup_trades}/{net.minimum_symbol_trades} broker-net trades"
    )
    print(
        " Full-risk evidence   : setup PF >= "
        f"{net.full_setup_profit_factor:.2f}, symbol PF >= "
        f"{net.full_symbol_profit_factor:.2f}"
    )
    print(
        f" M1 staleness limit   : {guard.max_m1_signal_age_minutes:.0f} minutes"
    )
    print(
        f" Daily loss stop      : {guard.daily_loss_stop_percent:.2f}% of day-start equity"
    )
    print(" Peak-equity seeding  : reconstructed from broker deal history")
    print(" Transmission         : confirmed MT5 demo account only")
    print(f" Dashboard            : {dashboard_url}")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


bot.LiveRunnerConfig = ResearchParityLiveRunnerConfig
bot.SatelliteLiveExecutor = NetPositiveLiveExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot._startup_banner = _profit_guard_banner


if __name__ == "__main__":
    bot.main()
