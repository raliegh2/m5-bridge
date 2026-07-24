"""Forward-only validation for per-engine order-flow risk controls.

The gate deliberately uses closed live outcomes only.  Each engine/timeframe
bucket is split chronologically into calibration and confirmation halves.  A
risk reduction is eligible only when it improves net R, profit factor and
maximum drawdown in both halves.
"""
from __future__ import annotations

from math import inf
from typing import Any, Iterable


def _metrics(values: Iterable[float]) -> dict[str, float]:
    results = [float(value) for value in values]
    gross_profit = sum(value for value in results if value > 0)
    gross_loss = -sum(value for value in results if value < 0)
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else (inf if gross_profit > 0 else 0.0)
    )
    equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for value in results:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    return {
        "trades": float(len(results)),
        "net_r": round(sum(results), 6),
        "profit_factor": round(profit_factor, 6),
        "max_drawdown_r": round(maximum_drawdown, 6),
    }


def _partition_assessment(
    records: list[dict[str, Any]],
    conflict_multiplier: float,
    minimum_conflicts: int,
) -> dict[str, Any]:
    baseline = [float(item["r_multiple"]) for item in records]
    adjusted = [
        value * conflict_multiplier
        if str(item.get("verdict", "")).upper() == "CONFLICT"
        else value
        for item, value in zip(records, baseline)
    ]
    conflict_count = sum(
        str(item.get("verdict", "")).upper() == "CONFLICT"
        for item in records
    )
    base_metrics = _metrics(baseline)
    adjusted_metrics = _metrics(adjusted)
    passed = (
        conflict_count >= minimum_conflicts
        and adjusted_metrics["net_r"] > base_metrics["net_r"]
        and adjusted_metrics["profit_factor"] > base_metrics["profit_factor"]
        and adjusted_metrics["max_drawdown_r"]
        < base_metrics["max_drawdown_r"]
    )
    return {
        "passed": passed,
        "conflicts": conflict_count,
        "baseline": base_metrics,
        "adjusted": adjusted_metrics,
    }


def assess_forward_order_flow(
    records: Iterable[dict[str, Any]],
    *,
    minimum_closed_candidates: int = 200,
    minimum_conflicts_per_partition: int = 10,
    conflict_multiplier: float = 0.50,
) -> dict[str, Any]:
    """Assess one engine/timeframe bucket without using future observations."""
    ordered = sorted(
        (
            dict(item)
            for item in records
            if item.get("r_multiple") is not None
        ),
        key=lambda item: str(item.get("closed_at", "")),
    )
    required = max(200, int(minimum_closed_candidates))
    if len(ordered) < required:
        return {
            "status": "COLLECTING",
            "eligible": False,
            "closed_candidates": len(ordered),
            "required_candidates": required,
            "conflict_multiplier": float(conflict_multiplier),
        }

    split = len(ordered) // 2
    calibration = _partition_assessment(
        ordered[:split],
        float(conflict_multiplier),
        int(minimum_conflicts_per_partition),
    )
    confirmation = _partition_assessment(
        ordered[split:],
        float(conflict_multiplier),
        int(minimum_conflicts_per_partition),
    )
    eligible = bool(calibration["passed"] and confirmation["passed"])
    return {
        "status": "PASSED" if eligible else "FAILED",
        "eligible": eligible,
        "closed_candidates": len(ordered),
        "required_candidates": required,
        "conflict_multiplier": float(conflict_multiplier),
        "calibration": calibration,
        "confirmation": confirmation,
    }


def order_flow_bucket(engine: str, timeframe: str) -> str:
    return f"{str(engine).upper()}::{str(timeframe).upper()}"


__all__ = ["assess_forward_order_flow", "order_flow_bucket"]
