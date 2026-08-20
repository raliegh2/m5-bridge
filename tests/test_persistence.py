"""Variance-ratio and Hurst estimators, checked against known processes.

These tests are the reason the module can be trusted: each one builds a series
whose behaviour is known by construction and asserts the statistic recovers it.
"""

import math

import numpy as np
import pytest
from pytest import approx

from mt5_ai_bridge.persistence import (classify, hurst_exponent, log_returns,
                                       variance_ratio, variance_ratio_profile)


def _random_walk(n=20_000, seed=0, sigma=0.01):
    return np.random.default_rng(seed).normal(0.0, sigma, size=n)


def _trending(n=20_000, seed=0, phi=0.25, sigma=0.01):
    """AR(1) with positive phi: today's return partly repeats yesterday's."""
    rng = np.random.default_rng(seed)
    eps = rng.normal(0.0, sigma, size=n)
    r = np.empty(n)
    r[0] = eps[0]
    for i in range(1, n):
        r[i] = phi * r[i - 1] + eps[i]
    return r


def _mean_reverting(n=20_000, seed=0, phi=-0.25, sigma=0.01):
    return _trending(n, seed, phi, sigma)


# --- calibration against known processes ------------------------------------


def test_random_walk_has_variance_ratio_near_one():
    vr = variance_ratio(_random_walk(), q=2)
    assert vr.ratio == approx(1.0, abs=0.05)
    assert vr.verdict == "RANDOM WALK"
    assert not vr.trending and not vr.mean_reverting


def test_random_walk_z_score_is_a_standard_normal():
    """The estimator must not collapse z toward zero -- the bug this caught."""
    zs = [variance_ratio(_random_walk(seed=s), q=2).z_score for s in range(25)]
    zs = np.array(zs)
    assert abs(zs.mean()) < 0.5, "z should be centred on 0"
    assert 0.5 < zs.std() < 2.0, f"z should have unit-ish spread, got {zs.std()}"
    # A genuinely standard-normal z exceeds 1 sometimes; an all-but-zero z never
    # does. This is exactly what the n-scaling bug destroyed.
    assert np.abs(zs).max() > 1.0


def test_positive_autocorrelation_is_detected_as_trending():
    vr = variance_ratio(_trending(phi=0.25), q=6)
    assert vr.ratio > 1.1
    assert vr.z_score > 2.0
    assert vr.p_value < 0.05
    assert vr.trending
    assert vr.verdict == "TRENDING"


def test_negative_autocorrelation_is_detected_as_mean_reverting():
    vr = variance_ratio(_mean_reverting(phi=-0.25), q=6)
    assert vr.ratio < 0.9
    assert vr.z_score < -2.0
    assert vr.mean_reverting
    assert vr.verdict == "MEAN-REVERTING"


def test_stronger_trend_gives_a_larger_ratio():
    weak = variance_ratio(_trending(phi=0.10, seed=3), q=6).ratio
    strong = variance_ratio(_trending(phi=0.40, seed=3), q=6).ratio
    assert strong > weak > 1.0


def test_ar1_variance_ratio_matches_theory():
    """For AR(1), VR(2) = 1 + phi (approximately, for large n)."""
    phi = 0.30
    vr = variance_ratio(_trending(n=60_000, phi=phi, seed=5), q=2)
    assert vr.ratio == approx(1.0 + phi, abs=0.05)


# --- Hurst ------------------------------------------------------------------


def test_hurst_near_half_for_a_random_walk():
    assert hurst_exponent(_random_walk()) == approx(0.5, abs=0.06)


def test_hurst_above_half_when_trending():
    assert hurst_exponent(_trending(phi=0.35)) > 0.55


def test_hurst_orders_the_three_regimes_correctly():
    """Hurst is a weak estimator on AR(1); its ordering is what's meaningful.

    The dispersion-scaling estimate is dominated by long lags, where an AR(1)
    effect has washed out, so the absolute level compresses toward 0.5. The
    variance ratio carries the significance test -- this is a cross-check.
    """
    reverting = hurst_exponent(_mean_reverting(phi=-0.35))
    walk = hurst_exponent(_random_walk())
    trending = hurst_exponent(_trending(phi=0.35))
    assert reverting < walk < trending
    assert reverting < 0.5 < trending


def test_hurst_rejects_a_degenerate_series():
    with pytest.raises(ValueError):
        hurst_exponent([0.0] * 100)
    with pytest.raises(ValueError):
        hurst_exponent([0.01, 0.02])


# --- input handling ---------------------------------------------------------


def test_log_returns_basic_maths():
    r = log_returns([100.0, 110.0, 121.0])
    assert len(r) == 2
    assert r[0] == approx(math.log(1.1))
    assert r[1] == approx(math.log(1.1))


def test_log_returns_drops_non_positive_and_nan():
    assert len(log_returns([100.0, float("nan"), 110.0, -5.0, 0.0, 120.0])) == 2
    assert len(log_returns([100.0])) == 0
    assert len(log_returns([])) == 0


def test_q_must_be_at_least_two():
    with pytest.raises(ValueError):
        variance_ratio(_random_walk(n=1000), q=1)


def test_short_series_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="need at least"):
        variance_ratio(_random_walk(n=10), q=30)


def test_zero_variance_series_is_refused():
    with pytest.raises(ValueError, match="no variance"):
        variance_ratio([0.01] * 500, q=2)


def test_profile_skips_horizons_without_enough_data():
    profile = variance_ratio_profile(_random_walk(n=100), (2, 6, 30, 120))
    qs = [vr.q for vr in profile]
    assert 2 in qs and 6 in qs
    assert 120 not in qs      # needs 240 observations


def test_classify_summarises_with_the_numbers_behind_it():
    out = classify(_trending(phi=0.3), q=6)
    assert out["suits_momentum"] is True
    assert out["verdict"] == "TRENDING"
    assert out["variance_ratio"] > 1.0
    assert out["p_value"] < 0.05
    assert out["n_obs"] > 0
    assert out["hurst"] is not None
