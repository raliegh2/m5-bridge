from mt5_ai_bridge.strategy_engine_v10 import V10_PROFILE


def test_v10_profile_defaults():
    V10_PROFILE.validate()
    assert V10_PROFILE.mode == "READ_ONLY"
    assert V10_PROFILE.max_open_risk_percent == 0.75
    assert V10_PROFILE.risk_for("EURUSD_SATELLITE_V7") == 0.35
    assert V10_PROFILE.risk_for("GBPJPY_SATELLITE_V7") == 0.35
    assert V10_PROFILE.risk_for("GBPUSD_SATELLITE_V2") == 0.30
    assert V10_PROFILE.risk_for("GBPUSD_SWING_V6") == 0.40
