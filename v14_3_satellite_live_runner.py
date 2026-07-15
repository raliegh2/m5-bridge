"""Local MT5 runner for the enhanced V12 + V14.3 satellite portfolio.

The runner defaults to READ_ONLY and produces validated proposals without
sending orders. Use APPROVAL only on a demo account after reviewing every order.
AUTO remains locked unless the explicit demo and forward-validation gates are set.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.v14_3_live_execution import LiveRunnerConfig, SatelliteLiveExecutor
from mt5_ai_bridge.v14_3_live_signals import build_all_live_signals

ROOT = Path(__file__).resolve().parent
DIAGNOSTICS = ROOT / "v14_3_satellite_live_diagnostics.json"
EXECUTION_LOG = ROOT / "v14_3_satellite_live_executions.jsonl"


def _append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def scan_once(client, executor: SatelliteLiveExecutor, lookback_hours: int = 8) -> dict:
    now = datetime.now(timezone.utc)
    signals, generation = build_all_live_signals(client, lookback_hours=lookback_hours)
    results: list[dict] = []
    for signal in signals:
        result = executor.place(signal, now=now)
        payload = {
            "created_at": now.isoformat(),
            "signal_key": signal.key,
            "signal": asdict(signal),
            "result": asdict(result),
        }
        results.append(payload)
        _append_jsonl(EXECUTION_LOG, payload)
        print(json.dumps(payload, indent=2, default=str))

    per_symbol = {}
    for symbol in ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"):
        symbol_signals = [item for item in signals if item.symbol == symbol]
        symbol_results = [item for item in results if item["signal"]["symbol"] == symbol]
        per_symbol[symbol] = {
            "v12_candidates": sum(item.mode == "V12" for item in symbol_signals),
            "ict_candidates": sum(item.mode == "ICT" for item in symbol_signals),
            "results": [
                {"engine": item["signal"]["engine"], "code": item["result"]["code"], "message": item["result"]["message"]}
                for item in symbol_results
            ],
        }
    account = client.account_info()
    diagnostics = {
        "generated_at": now.isoformat(),
        "execution_mode": executor.config.execution_mode,
        "account": {
            "login": getattr(account, "login", None),
            "server": str(getattr(account, "server", "")),
            "balance": float(getattr(account, "balance", 0.0) or 0.0),
            "equity": float(getattr(account, "equity", 0.0) or 0.0),
            "trade_mode": getattr(account, "trade_mode", None),
            "demo_confirmed": executor._is_demo(account) if account is not None else False,
        },
        "generation": generation,
        "candidate_count": len(signals),
        "result_count": len(results),
        "orders_filled": sum(item["result"]["code"] == "ORDER_FILLED" for item in results),
        "read_only_proposals": sum(item["result"]["code"] == "READ_ONLY_PROPOSAL" for item in results),
        "symbols": per_symbol,
        "state_path": executor.config.state_path,
        "next_scan_seconds": None,
    }
    DIAGNOSTICS.write_text(json.dumps(diagnostics, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"diagnostics": diagnostics}, indent=2, default=str))
    return diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the enhanced V14.3 satellite portfolio locally")
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between scans")
    parser.add_argument("--lookback-hours", type=int, default=8)
    args = parser.parse_args()

    config = LiveRunnerConfig.from_env()
    settings = load_settings()
    client = create_client()
    connect(client, settings)
    executor = SatelliteLiveExecutor(client, config)
    try:
        while True:
            try:
                diagnostics = scan_once(client, executor, lookback_hours=args.lookback_hours)
                diagnostics["next_scan_seconds"] = max(15, args.interval)
                DIAGNOSTICS.write_text(json.dumps(diagnostics, indent=2, default=str), encoding="utf-8")
                if diagnostics["candidate_count"] == 0:
                    print(f"{datetime.now(timezone.utc).isoformat()} no new completed-candle signals")
            except Exception as exc:  # noqa: BLE001
                error = {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "code": "RUNNER_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                }
                _append_jsonl(EXECUTION_LOG, error)
                print(json.dumps(error, indent=2))
            if args.once:
                break
            time.sleep(max(15, args.interval))
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
