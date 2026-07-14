from mt5_ai_bridge.strategy_engine_v8 import (
    DEFAULT_RULES,
    GBPUSD_SATELLITE_V2,
    GBPUSD_SWING_V6,
    OpenRisk,
    engine_spec,
    evaluate_candidate,
)


def test_gbpusd_swing_profile_is_frozen_to_tested_v6_contract():
    spec = engine_spec(GBPUSD_SWING_V6)
    assert spec.symbol == "GBPUSD"
    assert spec.risk_percent == 0.50
    assert spec.entry_timeframe == "M30"
    assert spec.observation_timeframes == ("M1", "M5")
    assert spec.anchor_timeframes == ("H4", "D1")
    assert spec.max_positions == 1
    assert spec.exit_profile.partial_at_r == 1.0
    assert spec.exit_profile.final_target_r == 3.0
    assert spec.exit_profile.maximum_hold_bars == 72


def test_aligned_swing_and_satellite_fit_shared_open_risk_cap():
    swing = OpenRisk(GBPUSD_SWING_V6, "GBPUSD", 1, 0.50)
    satellite = OpenRisk(GBPUSD_SATELLITE_V2, "GBPUSD", 1, 0.25)
    decision = evaluate_candidate([swing], satellite)
    assert decision.allowed
    assert decision.projected_open_risk_percent == DEFAULT_RULES.max_open_risk_percent


def test_mixed_gbp_direction_uses_lower_currency_cap():
    swing = OpenRisk(GBPUSD_SWING_V6, "GBPUSD", 1, 0.50)
    satellite = OpenRisk(GBPUSD_SATELLITE_V2, "GBPUSD", -1, 0.25)
    decision = evaluate_candidate([swing], satellite)
    assert not decision.allowed
    assert decision.reason == "gbp_currency_risk_cap"


def test_second_swing_is_blocked_by_engine_position_limit():
    swing = OpenRisk(GBPUSD_SWING_V6, "GBPUSD", 1, 0.50)
    second = OpenRisk(GBPUSD_SWING_V6, "GBPUSD", 1, 0.50)
    decision = evaluate_candidate([swing], second)
    assert not decision.allowed
    assert decision.reason == "engine_position_limit"
