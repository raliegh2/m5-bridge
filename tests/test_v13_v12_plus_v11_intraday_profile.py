from mt5_ai_bridge.v13_v12_plus_v11_intraday_profile import (
    V11_INTRADAY_ENGINE_RULES,
    V13_COMBINED_PROFILE,
)


def test_v13_profile_is_research_safe():
    V13_COMBINED_PROFILE.validate()
    assert V13_COMBINED_PROFILE.mode == "READ_ONLY"
    assert V13_COMBINED_PROFILE.allow_broker_order_api is False
    assert V13_COMBINED_PROFILE.require_human_review is True
    assert V13_COMBINED_PROFILE.v12_final_controls_imported is True
    assert V13_COMBINED_PROFILE.v11_intraday_added is True


def test_v13_v11_side_remains_intraday_only():
    assert V13_COMBINED_PROFILE.intraday_only_for_v11 is True
    assert V13_COMBINED_PROFILE.allow_v11_overnight_positions is False
    assert V13_COMBINED_PROFILE.force_v11_flat_hour_utc == 20
    assert all("SWING" not in engine.upper() for engine in V11_INTRADAY_ENGINE_RULES)


def test_v13_adds_expected_v11_intraday_engines():
    assert set(V11_INTRADAY_ENGINE_RULES) == {
        "GBPUSD_V11_INTRADAY",
        "EURUSD_V11_INTRADAY",
        "GBPJPY_V11_INTRADAY",
    }
    assert V11_INTRADAY_ENGINE_RULES["GBPUSD_V11_INTRADAY"].symbol == "GBPUSD"
    assert V11_INTRADAY_ENGINE_RULES["EURUSD_V11_INTRADAY"].symbol == "EURUSD"
    assert V11_INTRADAY_ENGINE_RULES["GBPJPY_V11_INTRADAY"].symbol == "GBPJPY"


def test_gbpjpy_profile_uses_corrected_risk_and_loss_controls():
    rule = V11_INTRADAY_ENGINE_RULES["GBPJPY_V11_INTRADAY"]
    guard = V13_COMBINED_PROFILE.gbpjpy_guard

    assert rule.adaptive is True
    assert set(rule.allowed_risk_percent) == {0.10, 0.20}
    assert guard.max_open_positions == 1
    assert guard.max_daily_losses == 2
    assert guard.normal_risk_cap_percent == 0.20
    assert guard.post_loss_risk_cap_percent == 0.10
    assert guard.cooldown_hours == 4.0
