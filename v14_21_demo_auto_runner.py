"""V14.21 demo-only automatic MT5 runner.

The scheduler, signals, dashboard, reconciliation and broker order path are the
existing validated V14.3/V14.4 surfaces. V14.21 swaps in only the stricter
demo-AUTO configuration and execution boundary.
"""
from __future__ import annotations

import os

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
from mt5_ai_bridge.v14_21_signal_freshness import (
    load_current_m1_gbp_ict_signals,
)
from v14_3_satellite_bot_windows import WindowsSafeLiveDashboard


def _create_compatible_client() -> MT5BrokerCompatibilityClient:
    return MT5BrokerCompatibilityClient(create_raw_client())


def _startup_banner(
    config: V1421DemoAutoConfig,
    dashboard_url: str,
) -> None:
    print("=" * 78)
    print(
        " V14.21 TRADING BOT — FOUR-FX + GOLD + V14.22 ORDER-FLOW "
        "TELEMETRY"
    )
    print("=" * 78)
    print(f" Mode                    : {config.requested_mode}")
    print(f" Expected demo login     : {config.expected_login or 'not pinned'}")
    print(f" Expected demo server    : {config.expected_server or 'not pinned'}")
    print(f" Symbols (FX engines)    : {', '.join(bot.SYMBOLS)}")
    _gold_on = os.getenv("GOLD_ENGINE", "").strip().lower() in {
        "1", "true", "yes", "on"}
    _gold_risk = os.getenv("GOLD_RISK_PERCENT", "0.25")
    print(
        " Gold metals engine      : "
        + (f"ON  (XAUUSD M30 breakout, H4 trend, risk {_gold_risk}%, "
           "AUTO CONNECTED)"
           if _gold_on else "OFF  (set GOLD_ENGINE=on in .env to enable)")
    )
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
    print(" FX portfolio trigger    : H1; engines read H1/H4/D1 as configured")
    print(" GBP ICT trigger         : M1; current-bar freshness checks")
    print(
        " Gold trigger/context    : "
        + (
            "ACTIVE; M30 breakout AUTO + H4/M30/M15 pullback SHADOW_ONLY"
            if _gold_on
            else "DISABLED; no XAUUSD scans or candidates"
        )
    )
    print(
        " Order-flow integration  : ALL engine candidates; "
        "engine/timeframe forward tracking"
    )
    print(
        " Order-flow enforcement  : "
        f"{config.order_flow_enforcement_mode}; "
        f"{config.order_flow_minimum_closed_candidates} closed outcomes "
        "required per bucket"
    )
    print(
        " Tick/DOM pressure       : live broker ticks + DOM when available; "
        + (
            "collecting evidence only"
            if config.order_flow_enforcement_mode == "SHADOW_ONLY"
            else "forward-gated execution control"
        )
    )
    _futures_on = os.getenv(
        "V14_25_FUTURES_ORDER_FLOW", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    _futures_key = bool(os.getenv("DATABENTO_API_KEY", "").strip())
    if not _futures_on:
        _futures_status = "DISABLED"
    elif not _futures_key:
        _futures_status = "API_KEY_REQUIRED"
    else:
        _futures_status = "STARTING (CME Globex MBP-10)"
    print(f" Centralized futures flow: {_futures_status}; shadow/forward-gated")
    print(" Automatic runner wiring : every candidate uses this demo executor")
    print(f" Dashboard               : {dashboard_url}")
    print(" Create the kill-switch file or press Ctrl+C to stop new execution.")
    print("-" * 78)


bot.LiveRunnerConfig = V1421DemoAutoConfig
bot.SatelliteLiveExecutor = V1421DemoAutoExecutor
bot.LiveDashboard = WindowsSafeLiveDashboard
bot.create_client = _create_compatible_client
bot.load_legacy_gbp_ict_signals = load_current_m1_gbp_ict_signals
bot._startup_banner = _startup_banner


if __name__ == "__main__":
    bot.main()
