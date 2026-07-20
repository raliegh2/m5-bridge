from __future__ import annotations

import pandas as pd

from mt5_ai_bridge.v14_22_range_breakout_retest_shadow import (
    PROFILES,
    _find_breakout,
    _find_retest,
    apply_scenario_reserve,
)


def test_profiles_are_pre_registered_and_unique() -> None:
    assert [item.name for item in PROFILES] == [
        "BALANCED_2R",
        "CONSERVATIVE_2_5R",
        "FAST_1_5R",
    ]
    assert all(item.target_r > 1.0 for item in PROFILES)
    assert all(item.maximum_stop_atr > item.minimum_stop_atr for item in PROFILES)


def test_breakout_then_retest_is_detected_without_same_bar_entry() -> None:
    profile = PROFILES[0]
    frame = pd.DataFrame(
        [
            {
                "mid_close": 1.0990,
                "mid_open": 1.0985,
                "mid_high": 1.0995,
                "mid_low": 1.0980,
                "h4_atr14": 0.0010,
                "body_h4_atr": 0.50,
                "close_location": 0.67,
            },
            {
                "mid_close": 1.1010,
                "mid_open": 1.0998,
                "mid_high": 1.1012,
                "mid_low": 1.0997,
                "h4_atr14": 0.0010,
                "body_h4_atr": 1.20,
                "close_location": 0.87,
            },
            {
                "mid_close": 1.1002,
                "mid_open": 1.1007,
                "mid_high": 1.1008,
                "mid_low": 1.0999,
                "h4_atr14": 0.0010,
                "body_h4_atr": 0.50,
                "close_location": 0.33,
            },
            {
                "mid_close": 1.1006,
                "mid_open": 1.1001,
                "mid_high": 1.1008,
                "mid_low": 1.0999,
                "h4_atr14": 0.0010,
                "body_h4_atr": 0.50,
                "close_location": 0.78,
            },
        ]
    )
    breakout = _find_breakout(
        frame,
        start_index=0,
        range_high=1.1000,
        range_low=1.0900,
        daily_atr=0.0050,
        profile=profile,
    )
    assert breakout == (1, "BUY", 1.1000)
    retest = _find_retest(
        frame,
        breakout_index=1,
        side="BUY",
        level=1.1000,
        daily_atr=0.0050,
        profile=profile,
    )
    assert retest == 3


def test_scenario_reserve_is_deducted_after_base_reserve() -> None:
    source = pd.DataFrame({"base_net_r_multiple": [1.0, -0.5]})
    ledger = apply_scenario_reserve(
        source,
        scenario="retail_cost",
        additional_cost_r=0.03,
    )
    assert ledger["scenario"].eq("retail_cost").all()
    assert ledger["r_multiple"].round(8).tolist() == [0.97, -0.53]
