from __future__ import annotations

import pytest

from mt5_ai_bridge.v14_16_quality_allocation_live import (
    projected_quality_capacity_percent,
)


def test_projected_capacity_preserves_940_percent_floor() -> None:
    capacity = projected_quality_capacity_percent(
        balance=10_000.0,
        peak_equity=10_000.0,
        open_stop_risk_dollars=500.0,
    )
    # 9.40% budget = $940. Existing open stop risk is $500, leaving $440.
    assert capacity == pytest.approx(4.40)


def test_projected_capacity_is_zero_when_budget_is_consumed() -> None:
    capacity = projected_quality_capacity_percent(
        balance=9_200.0,
        peak_equity=10_000.0,
        open_stop_risk_dollars=200.0,
    )
    assert capacity == pytest.approx(0.0)


def test_projected_capacity_uses_peak_not_only_current_balance() -> None:
    capacity = projected_quality_capacity_percent(
        balance=9_700.0,
        peak_equity=10_000.0,
        open_stop_risk_dollars=100.0,
    )
    # Floor is $9,060, so $540 remains against the $9,700 balance.
    assert capacity == pytest.approx(540.0 / 9700.0 * 100.0)
