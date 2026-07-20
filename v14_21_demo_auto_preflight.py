"""Fail-closed preflight for the V14.21 demo automatic runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client as create_raw_client
from mt5_ai_bridge.v14_3_live_signals import resolve_all_symbols
from mt5_ai_bridge.v14_3_mt5_broker_compat import MT5BrokerCompatibilityClient
from mt5_ai_bridge.v14_21_demo_auto_execution import (
    V1421DemoAutoConfig,
    V1421DemoAutoExecutor,
)


def _bar_ready(
    client: Any,
    broker_symbol: str,
    timeframe: str,
) -> bool:
    rates = client.copy_rates_from_pos(
        broker_symbol,
        timeframe,
        1,
        2,
    )
    return rates is not None and len(rates) >= 1


def build_preflight_snapshot(
    client: Any,
    config: V1421DemoAutoConfig,
    *,
    require_auto: bool,
) -> dict[str, Any]:
    executor = V1421DemoAutoExecutor(client, config)
    runtime = executor.runtime_snapshot()
    broker_map = resolve_all_symbols(client)
    bars = {
        symbol: {
            "H1_completed": _bar_ready(
                client,
                broker_symbol,
                "H1",
            ),
            "M1_completed": (
                _bar_ready(client, broker_symbol, "M1")
                if symbol in {"GBPUSD", "GBPJPY"}
                else True
            ),
        }
        for symbol, broker_symbol in broker_map.items()
    }
    checks = {
        "runtime_allowed": bool(runtime["allowed"]),
        "kill_switch_clear": not Path(config.kill_switch_path).exists(),
        "all_symbols_resolved": set(broker_map)
        == {"GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"},
        "completed_market_data": all(
            item["H1_completed"] and item["M1_completed"]
            for item in bars.values()
        ),
        "automatic_mode_selected": (
            config.execution_mode == "AUTO"
            if require_auto
            else True
        ),
        "forward_gate": (
            config.forward_gate_passed
            if require_auto
            else True
        ),
        "demo_auto_gate": (
            config.allow_demo_auto
            if require_auto
            else True
        ),
    }
    return {
        "runner": "V14.21_DEMO_AUTO",
        "requested_mode": config.requested_mode,
        "passed": all(checks.values()),
        "checks": checks,
        "runtime": runtime,
        "broker_symbols": broker_map,
        "completed_bars": bars,
        "state_path": config.state_path,
        "audit_log_path": config.audit_log_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-read-only",
        action="store_true",
        help="Permit a READ_ONLY preflight instead of requiring DEMO_AUTO.",
    )
    args = parser.parse_args()

    config = V1421DemoAutoConfig.from_env()
    settings = load_settings()
    client = MT5BrokerCompatibilityClient(create_raw_client())
    try:
        connect(client, settings)
        payload = build_preflight_snapshot(
            client,
            config,
            require_auto=not args.allow_read_only,
        )
        print(json.dumps(payload, indent=2, default=str))
        if not payload["passed"]:
            raise SystemExit(1)
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
