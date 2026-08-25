from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from mt5_ai_bridge.us_index_forward_v2 import (
    FEATURES,
    LOCKED_CONFIG,
    fit_model,
    feature_frame,
)


def _daily(start="2012-01-03", periods=3000, seed=19):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=periods, tz="UTC")
    # Regime-switching synthetic equity series: persistent drift plus periodic
    # corrections so both trend and mean-reversion features have something to learn.
    regime = np.where((np.arange(periods) // 180) % 3 == 2, -0.00020, 0.00042)
    shocks = regime + rng.normal(0.0, 0.010, periods)
    close = 1400.0 * np.exp(np.cumsum(shocks))
    open_ = close * (1.0 + rng.normal(0, 0.0015, periods))
    span = close * np.abs(rng.normal(0.006, 0.002, periods))
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    return pd.DataFrame({
        "time": np.array([int(ts.timestamp()) for ts in dates], dtype="int64"),
        "open": open_, "high": high, "low": low, "close": close,
    })


def test_feature_schema_is_backward_looking():
    bars = _daily(periods=500)
    frame = feature_frame(bars)
    assert set(FEATURES).issubset(frame.columns)
    # Future targets exist for training but features at row i are unchanged when
    # only later prices are altered outside the feature lookback.
    i = 300
    before = frame.loc[i, list(FEATURES)].to_numpy(float)
    changed = bars.copy()
    changed.loc[changed.index > i, ["open", "high", "low", "close"]] *= 20.0
    after = feature_frame(changed).loc[i, list(FEATURES)].to_numpy(float)
    assert np.allclose(before, after, equal_nan=True)


def test_v2_training_is_five_plus_years_and_records_selection():
    artifact = fit_model(
        _daily(periods=3000), LOCKED_CONFIG,
        trained_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert artifact.training_years >= 5.0
    assert artifact.training_rows > 1000
    assert artifact.score_threshold > 0
    assert artifact.selection_report["selected"] == artifact.candidate["name"]
    assert set(artifact.selection_report) >= {"balanced", "selective", "trend_guard", "long_bias", "long_only", "selected"}


def test_post_2020_mutation_cannot_change_selected_model_or_coefficients():
    bars = _daily(periods=3300)
    cutoff = int(pd.Timestamp("2020-12-31", tz="UTC").timestamp())
    changed = bars.copy()
    mask = changed["time"] > cutoff
    changed.loc[mask, ["open", "high", "low", "close"]] *= 50.0

    a = fit_model(bars, LOCKED_CONFIG)
    b = fit_model(changed, LOCKED_CONFIG)
    assert a.candidate["name"] == b.candidate["name"]
    assert a.training_start == b.training_start
    assert a.training_end == b.training_end
    assert np.allclose(a.feature_mean, b.feature_mean)
    assert np.allclose(a.feature_std, b.feature_std)
    assert np.allclose(a.fast_coefficients, b.fast_coefficients)
    assert np.allclose(a.slow_coefficients, b.slow_coefficients)
    assert a.fast_intercept == pytest.approx(b.fast_intercept)
    assert a.slow_intercept == pytest.approx(b.slow_intercept)
    assert a.score_threshold == pytest.approx(b.score_threshold)
