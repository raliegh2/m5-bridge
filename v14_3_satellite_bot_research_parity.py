"""Windows-safe launcher for the exact V14.3 research-risk parity profile."""
from __future__ import annotations

import v14_3_satellite_bot_m1 as bot
from mt5_ai_bridge.mt5_client import create_client as create_raw_client
from mt5_ai_bridge.v14_3_mt5_broker_compat import MT5BrokerCompatibilityClient
from mt5_ai_bridge.v14_3_research_parity_execution import (
    PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
    PARITY_MAX_ICT_OPEN_RISK_PERCENT,
    PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS,
    PARITY_MAX_TOTAL_ENTRIES_PER_HOUR,
    ResearchParityLiveExecutor,
    ResearchParityLiveRunnerConfig,
)
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _parity_banner(
    config: ResearchParityLiveRunnerConfig,
    dashboard_url: str,
) -> None:
    print("=" * 76)
    print(" V14.3 SATELLITE BOT — ENHANCED RESEARCH-RISK PARITY")
    print("=" * 76)
    print(f" Mode                 : {config.execution_mode}")
    print(f" Symbols              : {', '.join(bot.SYMBOLS)}")
    print(f" Account refresh      : every {bot.HEARTBEAT_SECONDS:.0f} second")
    print(" H1 strategies        : on each new completed H1 candle")
    print(" GBP ICT scan         : on each new completed M1 candle")
    print(f" ICT open-risk cap    : {PARITY_MAX_ICT_OPEN_RISK_PERCENT:.2f}%")
    print(f" Combined-risk cap    : {PARITY_MAX_COMBINED_OPEN_RISK_PERCENT:.2f}%")
    print(f" Max ICT positions    : {PARITY_MAX_SIMULTANEOUS_ICT_POSITIONS}")
    print(f" Max ICT entries/hour : {PARITY_MAX_TOTAL_ENTRIES_PER_HOUR}")
    print(" Drawdown governor    : 7.50 / 8.50 / 9.00 / 9.60% hard stop")
    print(" MT5 compatibility    : broker fill policy + UTC bar normalization")
    print(" Transmission         : confirmed MT5 demo account only")
    print(f" Dashboard            : {dashboard_url}")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


# The split runner resolves these names from its module globals at runtime.
bot.LiveRunnerConfig = ResearchParityLiveRunnerConfig
bot.SatelliteLiveExecutor = ResearchParityLiveExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot._startup_banner = _parity_banner


if __name__ == "__main__":
    bot.main()
