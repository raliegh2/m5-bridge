from __future__ import annotations

import pandas as pd
import pytest

from v17_swing_risk_120_runner import (
    SWING_RISK_MULTIPLIER,
    apply_swing_risk_multiplier,
    is_swing_engine,
)


def sample_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "engine": "GBPUSD_SWING_BREAKOUT",
                "risk_percent": 0.25,
            },
            {
                "engine": "GBPJPY_SWING_PULLBACK",
                "risk_percent": 0.20,
            },
            {
                "engine": "GBPUSD_V10_PRECISION",
                "risk_percent": 0.50,
            },
        ]
    )


def test_only_swing_engines_are_classified() -> None:
    assert is_swing_engine("GBPUSD_SWING_BREAKOUT")
    assert is_swing_engine("GBPJPY_SWING_PULLBACK")
    assert not is_swing_engine("GBPUSD_V10_PRECISION")
    assert not is_swing_engine("EURUSD_H1_SATELLITE")


def test_twenty_percent_multiplier_preserves_satellite_risk() -> None:
    adjusted = apply_swing_risk_multiplier(sample_candidates())
    assert adjusted.loc[0, "risk_percent"] == pytest.approx(
        0.25 * SWING_RISK_MULTIPLIER
    )
    assert adjusted.loc[1, "risk_percent"] == pytest.approx(
        0.20 * SWING_RISK_MULTIPLIER
    )
    assert adjusted.loc[2, "risk_percent"] == pytest.approx(0.50)
    assert adjusted.loc[2, "original_risk_percent"] == pytest.approx(0.50)


def test_original_candidates_are_not_mutated() -> None:
    candidates = sample_candidates()
    original = candidates.copy(deep=True)
    apply_swing_risk_multiplier(candidates)
    pd.testing.assert_frame_equal(candidates, original)


def test_invalid_multiplier_is_rejected() -> None:
    with pytest.raises(ValueError):
        apply_swing_risk_multiplier(sample_candidates(), 0.0)
