from dataclasses import replace

import numpy as np
import pandas as pd

from mt5_ai_bridge.us_index_forward_v3 import (
    CANDIDATES,
    LOCKED_CONFIG,
    fit_model,
)


def _bars() -> pd.DataFrame:
    dates = pd.date_range("2012-01-02", "2026-08-17", freq="B", tz="UTC")
    n = len(dates)
    x = np.arange(n, dtype=float)
    close = 1400.0 * np.exp(0.00022 * x + 0.025 * np.sin(x / 17.0) + 0.012 * np.sin(x / 5.0))
    open_ = close * (1.0 + 0.0015 * np.sin(x / 3.0))
    high = np.maximum(open_, close) * 1.006
    low = np.minimum(open_, close) * 0.994
    epoch_seconds = np.asarray([int(ts.timestamp()) for ts in dates], dtype=np.int64)
    return pd.DataFrame({
        "time": epoch_seconds,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    })


def test_v3_risk_ceiling_and_active_family():
    assert LOCKED_CONFIG.risk_percent == 1.0
    assert max(c.signal_quantile for c in CANDIDATES) <= 0.66
    assert max(c.max_holding_bars for c in CANDIDATES) <= 5
    assert min(c.signal_quantile for c in CANDIDATES) <= 0.52


def test_v3_training_requires_five_years():
    bars = _bars()
    cfg = replace(LOCKED_CONFIG, training_cutoff="2016-12-31")
    try:
        fit_model(bars, cfg)
    except ValueError as exc:
        assert "5.00+" in str(exc) or "five" in str(exc).lower()
    else:
        raise AssertionError("V3 should reject less than five years of training")


def test_v3_post_cutoff_mutation_cannot_change_selection_or_fit():
    bars = _bars()
    a = fit_model(bars, LOCKED_CONFIG)

    mutated = bars.copy()
    cutoff = int(pd.Timestamp("2020-12-31", tz="UTC").timestamp())
    mask = mutated["time"] > cutoff
    factor = np.linspace(0.35, 2.40, int(mask.sum()))
    for col in ("open", "high", "low", "close"):
        mutated.loc[mask, col] = mutated.loc[mask, col].to_numpy(float) * factor

    b = fit_model(mutated, LOCKED_CONFIG)
    assert a.candidate == b.candidate
    assert a.selection_report == b.selection_report
    np.testing.assert_allclose(a.feature_mean, b.feature_mean, rtol=0, atol=1e-12)
    np.testing.assert_allclose(a.feature_std, b.feature_std, rtol=0, atol=1e-12)
    np.testing.assert_allclose(a.fast_coefficients, b.fast_coefficients, rtol=0, atol=1e-12)
    np.testing.assert_allclose(a.slow_coefficients, b.slow_coefficients, rtol=0, atol=1e-12)
    assert a.fast_intercept == b.fast_intercept
    assert a.slow_intercept == b.slow_intercept
    assert a.score_threshold == b.score_threshold
