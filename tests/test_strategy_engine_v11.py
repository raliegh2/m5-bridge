import pytest

from mt5_ai_bridge.strategy_engine_v11 import (
    SetupDiagnostics,
    V11_PROFILE,
    compute_quality_score,
)


def test_v11_profile_defaults_are_intraday_research_safe():
    V11_PROFILE.validate()
    assert V11_PROFILE.mode == "READ_ONLY"
    assert V11_PROFILE.intraday_only is True
    assert V11_PROFILE.allow_overnight_positions is False
    assert V11_PROFILE.force_flat_hour_utc == 20
    assert V11_PROFILE.max_positions == 3
    assert V11_PROFILE.max_open_risk_percent == 0.90
    assert V11_PROFILE.aligned_gbp_cap_percent == 0.60
    assert V11_PROFILE.mixed_gbp_cap_percent == 0.45
    assert V11_PROFILE.daily_new_risk_percent == 0.75
    assert V11_PROFILE.target_weekly_profit_dollars == 50.0


def test_v11_engine_aliases_and_risk_tiers():
    assert V11_PROFILE.risk_for("GBPUSD_SATELLITE_V2") == 0.30
    assert V11_PROFILE.risk_for("GBPUSD_SATELLITE_V3", quality_score=0.75) == 0.35
    assert V11_PROFILE.risk_for("GBPUSD_SATELLITE_V3", quality_score=0.90) == 0.40
    assert V11_PROFILE.risk_for("EURUSD_SATELLITE_V7", quality_score=0.85) == 0.40
    assert V11_PROFILE.risk_for("GBPJPY_SATELLITE_V7", quality_score=0.70) == 0.25


def test_v11_rejects_swing_engines():
    with pytest.raises(KeyError):
        V11_PROFILE.risk_for("GBPUSD_SWING_V6")
    assert all("SWING" not in policy.engine for policy in V11_PROFILE.risk_policies)


def test_v11_quality_gate_uses_engine_specific_thresholds():
    assert V11_PROFILE.admit_quality("GBPUSD_SATELLITE_V3", 0.63)
    assert not V11_PROFILE.admit_quality("GBPJPY_SATELLITE_V7", 0.63)
    assert V11_PROFILE.admit_quality("GBPJPY_SATELLITE_V7", 0.67)


def test_compute_quality_score_rewards_structure_and_penalizes_cost():
    strong = SetupDiagnostics(
        trend_strength=0.90,
        ema_separation=0.80,
        body_quality=0.85,
        volume_confirmation=0.80,
        pullback_quality=0.82,
        session_range_quality=0.75,
        spread_pips=0.8,
        atr_pips=16.0,
        overextension=0.10,
    )
    weak = SetupDiagnostics(
        trend_strength=0.40,
        ema_separation=0.30,
        body_quality=0.25,
        volume_confirmation=0.20,
        pullback_quality=0.20,
        session_range_quality=0.30,
        spread_pips=2.0,
        atr_pips=8.0,
        overextension=0.80,
    )
    assert compute_quality_score(strong) > V11_PROFILE.strong_quality_threshold
    assert compute_quality_score(weak) < V11_PROFILE.quality_threshold
