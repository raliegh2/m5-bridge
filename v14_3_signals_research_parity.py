"""Research-risk parity wrapper for the recovered GBP V14.3 live provider."""
from __future__ import annotations

from typing import Any

from mt5_ai_bridge.v14_3_profit_preserving_profile import SETUP_RISK_PERCENT
from v14_3_signals import build_live_signals as build_detected_signals


def build_live_signals(client: Any) -> list[dict[str, Any]]:
    """Return the recovered signals with the frozen setup-specific risk tier."""
    values = build_detected_signals(client)
    outputs: list[dict[str, Any]] = []
    for value in values:
        payload = dict(value)
        symbol = str(payload["symbol"]).upper()
        setup = str(payload["setup"])
        key = (symbol, setup)
        if key not in SETUP_RISK_PERCENT:
            # The locked provider filters should make this unreachable. Fail closed
            # rather than silently substituting a generic risk percentage.
            continue
        payload["risk_percent"] = float(SETUP_RISK_PERCENT[key])
        payload["metadata"] = {
            **dict(payload.get("metadata", {})),
            "risk_profile": "V14_3_ENHANCED_RESEARCH_RISK_PARITY",
            "setup_risk_percent": float(SETUP_RISK_PERCENT[key]),
        }
        outputs.append(payload)
    return outputs
