from __future__ import annotations

import pandas as pd

from v12_audusd_quality_upgrade import apply_rule


def test_quality_rule_filters_all_required_dimensions() -> None:
    frame = pd.DataFrame([
        {
            "signal_hour": 8,
            "signal_weekday": 0,
            "adx14": 22.0,
            "body_ratio": 0.50,
            "volume_ratio": 1.20,
            "atr_ratio": 1.10,
            "ema_gap_atr": 0.70,
            "close_extension_atr": 0.40,
        },
        {
            "signal_hour": 4,
            "signal_weekday": 2,
            "adx14": 16.0,
            "body_ratio": 0.20,
            "volume_ratio": 0.60,
            "atr_ratio": 0.70,
            "ema_gap_atr": 1.80,
            "close_extension_atr": 1.40,
        },
    ])
    rule = {
        "hours": (8,),
        "weekdays": (0, 3),
        "min_adx": 18.0,
        "min_body": 0.35,
        "min_volume": 1.0,
        "min_atr_ratio": 1.0,
        "max_ema_gap": 1.2,
        "max_extension": 0.8,
    }
    result = apply_rule(frame, rule)
    assert len(result) == 1
    assert int(result.iloc[0]["signal_hour"]) == 8


def test_quality_rule_does_not_mutate_source_frame() -> None:
    frame = pd.DataFrame([
        {
            "signal_hour": 8,
            "signal_weekday": 0,
            "adx14": 22.0,
            "body_ratio": 0.50,
            "volume_ratio": 1.20,
            "atr_ratio": 1.10,
            "ema_gap_atr": 0.70,
            "close_extension_atr": 0.40,
        }
    ])
    original = frame.copy(deep=True)
    rule = {
        "hours": (8,), "weekdays": (0,), "min_adx": 15.0,
        "min_body": 0.25, "min_volume": 0.0, "min_atr_ratio": 0.0,
        "max_ema_gap": 2.0, "max_extension": 2.0,
    }
    apply_rule(frame, rule)
    pd.testing.assert_frame_equal(frame, original)
