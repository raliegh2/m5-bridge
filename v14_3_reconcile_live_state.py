"""Reconcile persisted V14.3 positions against MT5 deal history once."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client as create_raw_client
from mt5_ai_bridge.v14_3_mt5_broker_compat import MT5BrokerCompatibilityClient
from mt5_ai_bridge.v14_3_position_reconciliation import (
    ReconciledResearchParityLiveExecutor,
)
from mt5_ai_bridge.v14_3_research_parity_execution import (
    ResearchParityLiveRunnerConfig,
)


def main() -> None:
    os.environ.setdefault(
        "V14_3_LIVE_STATE_PATH",
        "state/v14_3_research_parity_live_state.json",
    )
    config = ResearchParityLiveRunnerConfig.from_env()
    settings = load_settings()
    client = MT5BrokerCompatibilityClient(create_raw_client())
    try:
        connect(client, settings)
        executor = ReconciledResearchParityLiveExecutor(client, config)
        executor.reconcile(datetime.now(timezone.utc))
        day = executor.state.data.get("day", {})
        summary = {
            "state_path": config.state_path,
            "tracked_open_positions": len(executor.state.data.get("positions", {})),
            "processed_closed_positions": len(
                executor.state.data.get("processed_closed_positions", {})
            ),
            "global_consecutive_losses": day.get("global_consecutive_losses", 0),
            "global_daily_losses": day.get("global_daily_losses", 0),
            "symbol_losses": day.get("symbol_losses", {}),
            "symbol_consecutive_losses": day.get(
                "symbol_consecutive_losses", {}
            ),
            "symbol_loss_pressure": day.get("symbol_loss_pressure", {}),
            "symbol_pnl": day.get("symbol_pnl", {}),
            "symbol_blocked": day.get("symbol_blocked", {}),
            "pause_until": day.get("pause_until"),
        }
        print(json.dumps(summary, indent=2, default=str))
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
