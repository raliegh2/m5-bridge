from __future__ import annotations

import numpy as np
import pandas as pd

from mt5_ai_bridge.v14_3_live_signals import (
    AUDUSD_PARAMS,
    EURUSD_CORE_CHANNEL_BARS,
    _with_eurusd_core_channel,
)


def test_validated_frequency_parameters_are_frozen() -> None:
    assert EURUSD_CORE_CHANNEL_BARS == 34
    assert AUDUSD_PARAMS.adx_min == 15.0
    assert AUDUSD_PARAMS.touch_atr == 0.40
    assert AUDUSD_PARAMS.body_ratio_min == 0.25
    assert AUDUSD_PARAMS.allowed_hours == (4, 8)


def test_eurusd_34_bar_channel_does_not_mutate_other_engine_context() -> None:
    rows = 70
    frame = pd.DataFrame({
        "high": np.full(rows, 10.0),
        "low": np.full(rows, 8.0),
        "close": np.full(rows, 9.5),
        "ema20": np.full(rows, 9.0),
        "adx14": np.full(rows, 25.0),
        "dclose": np.full(rows, 12.0),
        "dema20": np.full(rows, 11.0),
        "dema50": np.full(rows, 10.0),
        "directional_di_gap_long": np.full(rows, 20.0),
        "directional_di_gap_short": np.full(rows, -20.0),
        "breakout_side": np.zeros(rows, dtype=int),
        "breakout_level": np.full(rows, np.nan),
    })
    # This old high remains inside the original 55-bar channel at row 60,
    # but is outside the independently validated 34-bar EURUSD core window.
    frame.loc[5, "high"] = 15.0
    frame.loc[60, "high"] = 12.5
    frame.loc[60, "close"] = 12.0

    adjusted = _with_eurusd_core_channel(frame)

    assert adjusted.loc[60, "breakout_side"] == 1
    assert adjusted.loc[60, "breakout_level"] == 10.0
    assert frame.loc[60, "breakout_side"] == 0
