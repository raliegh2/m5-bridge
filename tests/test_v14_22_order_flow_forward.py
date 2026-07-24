from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mt5_ai_bridge.v14_22_order_flow_forward import (
    assess_forward_order_flow,
    order_flow_bucket,
)


def records(conflict_result: float, aligned_result: float) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = []
    for index in range(200):
        conflict = index % 2 == 0
        values.append({
            "closed_at": (start + timedelta(hours=index)).isoformat(),
            "verdict": "CONFLICT" if conflict else "ALIGNED",
            "r_multiple": conflict_result if conflict else aligned_result,
        })
    return values


def test_forward_gate_requires_two_hundred_closed_candidates() -> None:
    result = assess_forward_order_flow(records(-1.0, 1.0)[:199])
    assert result["status"] == "COLLECTING"
    assert result["eligible"] is False
    assert result["required_candidates"] == 200


def test_graduated_risk_must_improve_both_chronological_halves() -> None:
    result = assess_forward_order_flow(records(-1.0, 1.0))
    assert result["status"] == "PASSED"
    assert result["eligible"] is True
    assert result["calibration"]["adjusted"]["profit_factor"] > 1.0
    assert (
        result["confirmation"]["adjusted"]["max_drawdown_r"]
        < result["confirmation"]["baseline"]["max_drawdown_r"]
    )


def test_gate_rejects_reducing_profitable_conflicts() -> None:
    result = assess_forward_order_flow(records(1.0, -0.5))
    assert result["status"] == "FAILED"
    assert result["eligible"] is False


def test_bucket_is_separate_by_engine_and_timeframe() -> None:
    assert order_flow_bucket("engine_a", "h4") == "ENGINE_A::H4"
    assert order_flow_bucket("engine_a", "m30") != "ENGINE_A::H4"
