"""Pre-execution order-flow shadow decisions for forward validation.

The connected MT5 broker exposes quote ticks and, for some symbols, depth of
market.  Spot FX has no centralized order book, so this module deliberately
does not block orders.  It records what an order-flow gate *would* have done so
that filled and rejected candidates can be evaluated without look-ahead.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v14_21_order_flow import measure_order_flow
from .v14_3_live_execution import ExecutionResult, LiveSignal

ORDER_FLOW_SHADOW_MODE = "SHADOW_ONLY"


def evaluate_order_flow_shadow(
    client: Any,
    signal: LiveSignal,
    *,
    centralized_provider: Any | None = None,
    now: datetime | None = None,
    directional_threshold: float = 0.15,
    minimum_ticks: int = 30,
) -> dict[str, Any]:
    """Return a side-aware, non-blocking order-flow decision."""
    measured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reading = measure_order_flow(
        client,
        canonical_symbol=signal.symbol,
        broker_symbol=signal.broker_symbol,
        now=measured_at,
    )
    centralized = (
        centralized_provider.reading(signal.symbol)
        if centralized_provider is not None
        else None
    )
    use_centralized = bool(
        isinstance(centralized, dict)
        and centralized.get("state") == "READY"
        and centralized.get("imbalance") is not None
    )
    state = str(
        "CENTRALIZED_READY"
        if use_centralized
        else reading.get("state", "UNAVAILABLE")
    )
    imbalance = (
        centralized.get("imbalance")
        if use_centralized
        else reading.get("imbalance")
    )
    tick_count = int(
        (
            centralized.get("event_count", 0)
            if use_centralized
            else reading.get("tick_count", 0)
        )
        or 0
    )
    side_multiplier = 1.0 if str(signal.side).upper() == "BUY" else -1.0
    directional_imbalance = (
        float(imbalance) * side_multiplier if imbalance is not None else None
    )

    if state in {"UNAVAILABLE", "NO_TICKS", "ERROR"} or imbalance is None:
        verdict = "UNAVAILABLE"
        reason = str(
            reading.get("reason")
            or "A usable broker order-flow reading was not available."
        )
    elif tick_count < int(minimum_ticks):
        verdict = "INSUFFICIENT_TICKS"
        reason = (
            f"{tick_count} ticks were available; at least {minimum_ticks} "
            "are required for a shadow decision."
        )
    elif directional_imbalance >= float(directional_threshold):
        verdict = "ALIGNED"
        reason = "Broker tick pressure agrees with the candidate direction."
    elif directional_imbalance <= -float(directional_threshold):
        verdict = "CONFLICT"
        reason = "Broker tick pressure opposes the candidate direction."
    else:
        verdict = "NEUTRAL"
        reason = "Broker tick pressure is inside the neutral band."

    depth = (
        {
            "available": True,
            "imbalance": centralized.get("imbalance"),
            "levels": centralized.get("levels", 0),
        }
        if use_centralized
        else reading.get("market_depth")
    )
    depth_imbalance = (
        depth.get("imbalance") if isinstance(depth, dict) else None
    )
    directional_depth_imbalance = (
        float(depth_imbalance) * side_multiplier
        if depth_imbalance is not None
        else None
    )
    return {
        "mode": ORDER_FLOW_SHADOW_MODE,
        "scope": "ALL_ENGINE_CANDIDATES",
        "execution_policy": "PRESERVE_ENGINE_SIGNAL",
        "verdict_source": (
            "CENTRALIZED_CME_FUTURES_MBP10"
            if use_centralized
            else "BROKER_SPOT_TICKS"
        ),
        "evaluated_at": measured_at.isoformat(),
        "symbol": signal.symbol,
        "broker_symbol": signal.broker_symbol,
        "engine": signal.engine,
        "setup": signal.setup,
        "side": str(signal.side).upper(),
        "verdict": verdict,
        "side_confirmation": (
            f"CONFIRMED_{str(signal.side).upper()}"
            if verdict == "ALIGNED"
            else (
                f"CONFLICT_WITH_{str(signal.side).upper()}"
                if verdict == "CONFLICT"
                else verdict
            )
        ),
        "reason": reason,
        "hypothetical_block": verdict == "CONFLICT",
        "directional_threshold": float(directional_threshold),
        "minimum_ticks": int(minimum_ticks),
        "tick_count": tick_count,
        "market_depth_available": (
            bool(depth.get("available"))
            if isinstance(depth, dict)
            else False
        ),
        "directional_imbalance": (
            round(directional_imbalance, 4)
            if directional_imbalance is not None
            else None
        ),
        "directional_depth_imbalance": (
            round(directional_depth_imbalance, 4)
            if directional_depth_imbalance is not None
            else None
        ),
        "reading": reading,
        "centralized_order_flow": centralized,
    }


def append_order_flow_shadow(
    path: str | Path,
    *,
    signal: LiveSignal,
    result: ExecutionResult,
    shadow: dict[str, Any],
) -> None:
    """Persist a candidate, its hypothetical flow decision, and actual result."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "signal_key": signal.key,
        "signal": asdict(signal),
        "order_flow_shadow": shadow,
        "actual_execution_result": asdict(result),
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
        handle.flush()


__all__ = [
    "ORDER_FLOW_SHADOW_MODE",
    "append_order_flow_shadow",
    "evaluate_order_flow_shadow",
]
