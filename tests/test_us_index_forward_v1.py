from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from mt5_ai_bridge.us_index_forward_v1 import (
    LOCKED_CONFIG,
    USIndexForwardConfig,
    fit_model,
    size_for_risk,
)


def _daily(start="2012-01-03", periods=2400, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=periods, tz="UTC")
    shocks = rng.normal(0.00035, 0.009, periods)
    close = 1400.0 * np.exp(np.cumsum(shocks))
    open_ = close * (1.0 + rng.normal(0, 0.0015, periods))
    span = close * np.abs(rng.normal(0.006, 0.002, periods))
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    return pd.DataFrame({
        "time": np.array([int(ts.timestamp()) for ts in dates], dtype="int64"),
        "open": open_, "high": high, "low": low, "close": close,
    })


def test_training_requires_and_records_five_plus_years():
    bars = _daily(periods=2400)
    artifact = fit_model(
        bars, LOCKED_CONFIG,
        trained_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert artifact.training_years >= 5.0
    assert artifact.training_end <= int(pd.Timestamp("2020-12-31", tz="UTC").timestamp())
    assert artifact.training_rows > 1000
    assert artifact.signal_threshold > 0


def test_post_cutoff_prices_cannot_change_trained_artifact():
    bars = _daily(periods=3000)
    cutoff = int(pd.Timestamp("2020-12-31", tz="UTC").timestamp())
    pre = bars[bars["time"] <= cutoff].copy()
    changed = bars.copy()
    changed.loc[changed["time"] > cutoff, ["open", "high", "low", "close"]] *= 50.0

    a = fit_model(pre, LOCKED_CONFIG)
    b = fit_model(changed, LOCKED_CONFIG)
    assert a.training_start == b.training_start
    assert a.training_end == b.training_end
    assert np.allclose(a.feature_mean, b.feature_mean)
    assert np.allclose(a.feature_std, b.feature_std)
    assert np.allclose(a.coefficients, b.coefficients)
    assert a.intercept == pytest.approx(b.intercept)
    assert a.signal_threshold == pytest.approx(b.signal_threshold)


def test_short_history_is_rejected():
    cfg = USIndexForwardConfig(training_cutoff="2016-12-31")
    with pytest.raises(ValueError, match="training span"):
        fit_model(_daily(start="2014-01-02", periods=700), cfg)


def test_risk_size_respects_stop_and_70pct_cap():
    assert size_for_risk(10_000, 1_000, 25, 0.5, 0.70, 1.0, 0.1, 0.1) == 2.0
    # Exact-step floating-point flooring is deliberately conservative here.
    assert size_for_risk(10_000, 5_000, 25, 0.5, 0.70, 1.0, 0.1, 0.1) == 1.3
    assert size_for_risk(10_000, 1_000, 25, 0.5, 0.70, 0.5, 0.1, 0.1) == 1.0
