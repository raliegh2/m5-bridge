import pandas as pd

from mt5_ai_bridge.v14_19_range_mean_reversion_shadow import (
    apply_scenario_reserve,
    first_permitted_entry_time,
    resolve_bar_exit,
    shadow_configuration,
    signal_available_at,
)


def test_daily_signal_is_not_available_until_next_day():
    start = pd.Timestamp("2026-07-01T00:00:00Z")
    assert signal_available_at(start) == pd.Timestamp(
        "2026-07-02T00:00:00Z"
    )


def test_entry_is_later_and_uses_permitted_h4_session():
    times = pd.Series(
        pd.to_datetime(
            [
                "2026-07-02T00:00:00Z",
                "2026-07-02T04:00:00Z",
                "2026-07-02T08:00:00Z",
                "2026-07-02T12:00:00Z",
            ],
            utc=True,
        )
    )
    entry = first_permitted_entry_time(
        pd.Timestamp("2026-07-02T00:00:00Z"),
        times,
    )
    assert entry == pd.Timestamp("2026-07-02T08:00:00Z")


def test_stop_first_when_stop_and_target_are_inside_same_bar():
    assert resolve_bar_exit(
        side="BUY",
        high=105.0,
        low=95.0,
        stop=97.0,
        target=103.0,
    ) == "STOP"
    assert resolve_bar_exit(
        side="SELL",
        high=105.0,
        low=95.0,
        stop=103.0,
        target=97.0,
    ) == "STOP"


def test_shadow_configuration_cannot_request_or_execute_risk():
    config = shadow_configuration()
    assert config["shadow_only"] is True
    assert config["requested_risk_percent"] == 0.0
    assert config["executed_risk_percent"] == 0.0
    assert config["broker_transmission"] is False


def test_scenario_reserve_only_reduces_shadow_result():
    frame = pd.DataFrame(
        {
            "base_net_r_multiple": [1.0, -0.5],
            "shadow_only": [True, True],
            "requested_risk_percent": [0.0, 0.0],
            "executed_risk_percent": [0.0, 0.0],
            "transmitted": [False, False],
        }
    )
    demo = apply_scenario_reserve(
        frame,
        scenario="demo_cost",
        additional_cost_r=0.02,
    )
    stress = apply_scenario_reserve(
        frame,
        scenario="stress_cost",
        additional_cost_r=0.05,
    )
    assert stress["r_multiple"].sum() < demo["r_multiple"].sum()
    assert (stress["requested_risk_percent"] == 0.0).all()
    assert (~stress["transmitted"]).all()
