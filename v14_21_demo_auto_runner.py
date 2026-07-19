"""V14.21 demo-only automatic MT5 runner.

The scheduler, signals, dashboard, reconciliation and broker order path are the
existing validated V14.3/V14.4 surfaces. V14.21 swaps in only the stricter
demo-AUTO configuration and execution boundary.
"""
from __future__ import annotations

import v14_3_satellite_bot_m1 as bot
from mt5_ai_bridge.mt5_client import create_client as create_raw_client
from mt5_ai_bridge.v14_3_mt5_broker_compat import MT5BrokerCompatibilityClient
from mt5_ai_bridge.v14_3_research_parity_execution import (
    PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
    PARITY_MAX_ICT_OPEN_RISK_PERCENT,
    PARITY_MAX_TRADE_RISK_PERCENT,
)
from mt5_ai_bridge.v14_21_demo_auto_execution import (
    V1421DemoAutoConfig,
    V1421DemoAutoExecutor,
)
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _startup_banner(
    config: V1421DemoAutoConfig,
    dashboard_url: str,
) -> None:
    print("=" * 78)
    print(" V14.21 TRADING BOT — V14.20 MODEL + DEMO-ONLY AUTOMATIC EXECUTION")
    print("=" * 78)
    print(f" Mode                    : {config.requested_mode}")
    print(f" Expected demo login     : {config.expected_login or 'not pinned'}")
    print(f" Expected demo server    : {config.expected_server or 'not pinned'}")
    print(f" Symbols                 : {', '.join(bot.SYMBOLS)}")
    print(f" Per-trade risk ceiling  : {PARITY_MAX_TRADE_RISK_PERCENT:.2f}%")
    print(f" ICT open-risk ceiling   : {PARITY_MAX_ICT_OPEN_RISK_PERCENT:.2f}%")
    print(f" Combined risk ceiling   : {PARITY_MAX_COMBINED_OPEN_RISK_PERCENT:.2f}%")
    print(
        " Dollar loss stops       : "
        f"${config.daily_loss_limit_dollars:.2f} daily / "
        f"${config.overall_loss_limit_dollars:.2f} total"
    )
    print(
        " Consecutive-loss stop   : "
        f"{config.maximum_consecutive_losses} closed losses per UTC day"
    )
    print(f" Kill switch             : {config.kill_switch_path}")
    print(" Range engine            : shadow-only; V14.20 conflict filter active")
    print(" Order path              : size -> order_check -> demo order_send")
    print(" Account boundary        : pinned MT5 demo account only")
    print(" H1 strategies           : each new completed H1 candle")
    print(" GBP ICT                 : each new completed M1 candle")
    print(f" Dashboard               : {dashboard_url}")
    print(" Create the kill-switch file or press Ctrl+C to stop new execution.")
    print("-" * 78)


bot.LiveRunnerConfig = V1421DemoAutoConfig
bot.SatelliteLiveExecutor = V1421DemoAutoExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot._startup_banner = _startup_banner


if __name__ == "__main__":
    bot.main()
