"""Does this market trend at all? Measured directly, not inferred from P&L.

A momentum strategy is a bet that returns are positively autocorrelated over
its holding period. That property is measurable *before* any backtest, and
measuring it separates two very different failures:

* the market does not trend, so no parameterisation will help; versus
* the market trends but the entry/exit rules are wrong.

The V15 results looked like the first: gold consistent, FX majors not. This
module tests that directly instead of guessing from returns.

Variance ratio
--------------
Under a random walk, the variance of q-period returns is q times the variance
of 1-period returns. So

    VR(q) = Var(r_q) / (q * Var(r_1))

* VR > 1 -> trending (a move tends to be followed by more of the same)
* VR = 1 -> random walk, no edge available to either trend or fade
* VR < 1 -> mean-reverting (moves get retraced)

Lo & MacKinlay (1988) give the heteroskedasticity-robust z-statistic used here,
so a VR can be judged against sampling noise rather than eyeballed. This is the
standard test, not a bespoke one.

Everything is pure and depends only on numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Sequence

import numpy as np

__all__ = [
    "VarianceRatio",
    "variance_ratio",
    "variance_ratio_profile",
    "hurst_exponent",
    "log_returns",
    "classify",
]

_NORMAL = NormalDist()


def log_returns(prices: Sequence[float]) -> np.ndarray:
    """Log returns of a price series, dropping non-finite values."""
    arr = np.asarray(list(prices), dtype=float)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size < 2:
        return np.empty(0, dtype=float)
    return np.diff(np.log(arr))


@dataclass(frozen=True)
class VarianceRatio:
    """One variance-ratio test result."""

    q: int
    ratio: float
    z_score: float
    p_value: float
    n_obs: int

    @property
    def trending(self) -> bool:
        """Significantly above 1 at the 5% level."""
        return self.ratio > 1.0 and self.p_value < 0.05

    @property
    def mean_reverting(self) -> bool:
        return self.ratio < 1.0 and self.p_value < 0.05

    @property
    def verdict(self) -> str:
        if self.trending:
            return "TRENDING"
        if self.mean_reverting:
            return "MEAN-REVERTING"
        return "RANDOM WALK"


def variance_ratio(returns: Sequence[float], q: int) -> VarianceRatio:
    """Lo-MacKinlay variance ratio at horizon ``q``, robust to heteroskedasticity.

    ``returns`` should be single-period log returns. ``q`` is the aggregation
    horizon in periods -- for an H4 strategy holding a few days, q of 6 (one
    day) to 30 (a week) is the range that matters.
    """
    if q < 2:
        raise ValueError("q must be at least 2")
    r = np.asarray(list(returns), dtype=float)
    r = r[np.isfinite(r)]
    n = r.size
    if n < q * 2:
        raise ValueError(f"need at least {q * 2} returns for q={q}, got {n}")

    mu = r.mean()
    # Variance of 1-period returns (unbiased).
    var_1 = np.sum((r - mu) ** 2) / (n - 1)
    if var_1 <= 0:
        raise ValueError("returns have no variance")

    # Variance of overlapping q-period returns, with the Lo-MacKinlay
    # small-sample correction.
    rolled = np.convolve(r, np.ones(q), mode="valid")      # q-period sums
    m = q * (n - q + 1) * (1 - q / n)
    var_q = np.sum((rolled - q * mu) ** 2) / m
    ratio = var_q / var_1

    # Heteroskedasticity-robust variance of the VR statistic (Lo & MacKinlay
    # 1988, eq. 't'):
    #     delta_j = sum_t (r_t-mu)^2 (r_{t-j}-mu)^2 / [sum_t (r_t-mu)^2]^2
    #     theta   = sum_{j=1}^{q-1} [2(q-j)/q]^2 * delta_j
    # delta_j is O(1/n), which is what makes theta the variance of VR rather
    # than n times it. Scaling it by n here collapses every z toward zero.
    theta = 0.0
    centred_sq = (r - mu) ** 2
    denom = np.sum(centred_sq) ** 2
    for j in range(1, q):
        num = np.sum(centred_sq[j:] * centred_sq[:-j])
        delta_j = num / denom if denom > 0 else 0.0
        theta += ((2.0 * (q - j) / q) ** 2) * delta_j

    z = (ratio - 1.0) / math.sqrt(theta) if theta > 0 else 0.0
    p = 2.0 * (1.0 - _NORMAL.cdf(abs(z)))
    return VarianceRatio(q=q, ratio=float(ratio), z_score=float(z),
                         p_value=float(p), n_obs=n)


def variance_ratio_profile(returns: Sequence[float],
                           horizons: Sequence[int] = (2, 4, 6, 12, 30)
                           ) -> list[VarianceRatio]:
    """Variance ratios across several horizons, skipping any that lack data."""
    out = []
    for q in horizons:
        try:
            out.append(variance_ratio(returns, q))
        except ValueError:
            continue
    return out


def hurst_exponent(returns: Sequence[float],
                   max_lag: int = 64) -> float:
    """Hurst exponent, estimated from the scaling of dispersion with lag.

    H > 0.5 trending, H = 0.5 random walk, H < 0.5 mean-reverting. Reported as
    a cross-check on the variance ratio; the VR carries the significance test,
    so prefer it when the two disagree.
    """
    r = np.asarray(list(returns), dtype=float)
    r = r[np.isfinite(r)]
    if r.size < max_lag * 2:
        max_lag = max(4, r.size // 4)
    if r.size < 8:
        raise ValueError("not enough returns for a Hurst estimate")

    series = np.cumsum(r)
    lags = np.unique(np.logspace(0, math.log10(max_lag), 12).astype(int))
    lags = lags[lags >= 2]
    tau = []
    kept = []
    for lag in lags:
        diff = series[lag:] - series[:-lag]
        sd = diff.std(ddof=1) if diff.size > 1 else 0.0
        if sd > 0:
            tau.append(sd)
            kept.append(lag)
    if len(kept) < 3:
        raise ValueError("could not estimate Hurst: degenerate series")
    slope = np.polyfit(np.log(kept), np.log(tau), 1)[0]
    return float(slope)


def classify(returns: Sequence[float], q: int = 6) -> dict:
    """Summarise whether a series suits momentum, with the numbers behind it."""
    vr = variance_ratio(returns, q)
    try:
        hurst = hurst_exponent(returns)
    except ValueError:
        hurst = float("nan")
    return {
        "q": q,
        "variance_ratio": round(vr.ratio, 4),
        "z_score": round(vr.z_score, 3),
        "p_value": round(vr.p_value, 4),
        "hurst": round(hurst, 4) if hurst == hurst else None,
        "verdict": vr.verdict,
        "suits_momentum": vr.trending,
        "n_obs": vr.n_obs,
    }
