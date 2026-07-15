"""Preflight checks for the enhanced V14.3 local runner."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.v14_3_live_execution import LiveRunnerConfig
from mt5_ai_bridge.v14_3_live_signals import resolve_all_symbols


def main() -> None:
    config = LiveRunnerConfig.from_env()
    settings = load_settings()
    client = create_client()
    checks: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": config.execution_mode,
        "safe_default": config.execution_mode == "READ_ONLY",
    }
    try:
        connect(client, settings)
        account = client.account_info()
        terminal = client.terminal_info()
        checks.update({
            "connected": account is not None,
            "login": getattr(account, "login", None),
            "server": str(getattr(account, "server", "")),
            "balance": float(getattr(account, "balance", 0.0) or 0.0),
            "equity": float(getattr(account, "equity", 0.0) or 0.0),
            "demo_account": getattr(account, "trade_mode", None) == getattr(client, "ACCOUNT_TRADE_MODE_DEMO", 0),
            "terminal_trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
            "terminal_connected": bool(getattr(terminal, "connected", False)),
            "broker_symbols": resolve_all_symbols(client),
            "risk_cap_percent": config.max_live_risk_percent,
            "open_risk_cap_percent": config.max_open_risk_percent,
            "daily_loss_stop_percent": config.daily_account_loss_limit_percent,
            "live_drawdown_stop_percent": config.live_hard_drawdown_percent,
            "forward_gate_passed": config.forward_gate_passed,
            "demo_auto_gate": config.allow_demo_auto,
        })
        checks["transmission_allowed"] = bool(
            checks["demo_account"]
            and checks["terminal_trade_allowed"]
            and (
                config.execution_mode == "APPROVAL"
                or (
                    config.execution_mode == "AUTO"
                    and config.forward_gate_passed
                    and config.allow_demo_auto
                )
            )
        )
        checks["status"] = "PASS" if checks["connected"] and len(checks["broker_symbols"]) == 5 else "FAIL"
    except Exception as exc:  # noqa: BLE001
        checks["status"] = "FAIL"
        checks["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            client.shutdown()
        except Exception:
            pass
    print(json.dumps(checks, indent=2, default=str))
    if checks.get("status") != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
