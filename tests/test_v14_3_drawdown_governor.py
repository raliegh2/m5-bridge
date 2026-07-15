from __future__ import annotations

import pandas as pd
import pytest

from mt5_ai_bridge.v14_3_drawdown_governor import (
    CONFIG,
    DrawdownGovernorState,
    adjusted_ict_risk_percent,
    adjusted_v12_risk_percent,
)


def test_quality_v12_allocation_is_bounded() -> None:
    risk, tier = adjusted_v12_risk_percent("GBPUSD_V10_PRECISION", 0.50)
    assert risk == pytest.approx(0.75)
    assert tier == "V12_QUALITY_150"
    capped, _ = adjusted_v12_risk_percent("GBPUSD_V10_PRECISION", 0.60)
    assert capped == pytest.approx(0.75)


def test_unselected_v12_engine_is_unchanged() -> None:
    risk, tier = adjusted_v12_risk_percent("GBPUSD_SWING_RETEST", 0.15)
    assert risk == pytest.approx(0.15)
    assert tier == "V12_UNCHANGED"


def test_ict_normal_and_recovery_allocations() -> None:
    normal = adjusted_ict_risk_percent(
        "GBPUSD", "breakout_15_fade", 0.0, False, 1.0
    )
    recovery = adjusted_ict_risk_percent(
        "GBPUSD", "breakout_15_fade", 0.0, False, CONFIG.recovery_risk_multiplier
    )
    assert normal == pytest.approx(0.455 * 1.05)
    assert recovery == pytest.approx(normal * 0.30)
    assert normal < 0.80


def test_governor_pause_recovery_and_rearm() -> None:
    state = DrawdownGovernorState()
    start = pd.Timestamp("2026-01-05T10:00:00Z")
    state.observe(start, 5.99)
    assert state.armed

    state.observe(start, 6.0)
    assert not state.armed
    assert state.in_pause(start + pd.Timedelta(hours=71))
    assert not state.in_pause(start + pd.Timedelta(hours=72))
    assert state.recovery_multiplier(start + pd.Timedelta(hours=72)) == pytest.approx(0.30)
    assert state.trigger_count == 1

    state.observe(start + pd.Timedelta(hours=80), 4.01)
    assert not state.armed
    state.observe(start + pd.Timedelta(hours=80), 4.0)
    assert state.armed
    assert state.pause_until is None


def test_governor_does_not_retrigger_before_release() -> None:
    state = DrawdownGovernorState()
    start = pd.Timestamp("2026-02-02T09:00:00Z")
    state.observe(start, 6.5)
    state.observe(start + pd.Timedelta(days=4), 7.0)
    assert state.trigger_count == 1
