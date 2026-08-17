"""Does trading activity carry information beyond price?

Spot FX has no centralised tape, so true order flow is unavailable historically
-- ``v14_21_order_flow.py`` says as much and works only on live broker ticks.
What every MT5 export *does* carry is ``tick_volume``: the number of quote
changes in a bar. It is a proxy for activity rather than traded size, but a
well-documented one, and it is the only flow-like information in this dataset.

The hypothesis being tested is not invented here. Campbell, Grossman & Wang
(1993) predict that price moves accompanied by **high** volume are driven by
information and tend to persist, while moves on **low** volume are driven by
liquidity demand and tend to revert. If that holds, return autocorrelation
should be more negative after low-volume bars than after high-volume ones.

That is a falsifiable statement about the data, testable *before* any strategy
is built on it -- the same discipline that let the variance ratio predict V15's
failure in advance. :func:`conditional_autocorrelation` measures it with a
significance test attached.

Everything here is pure and depends only on numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Optional, Sequence

import numpy as np

__all__ = [
    "relative_volume",
    "ConditionalAutocorr",
    "conditional_autocorrelation",
    "volume_conditioned_profile",
]

_NORMAL = NormalDist()


def relative_volume(volume: Sequence[float], lookback: int = 20
                    ) -> np.ndarray:
    """Volume divided by its own trailing mean, shifted to avoid look-ahead.

    Raw tick counts trend over decades as markets get busier, so an absolute
    threshold would silently become a date filter. Normalising against a
    trailing mean keeps "high volume" meaning the same thing in 1995 and 2026.

    Element ``i`` uses bars strictly before ``i``; the first ``lookback``
    entries are NaN.
    """
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    v = np.asarray(list(volume), dtype=float)
    out = np.full(v.size, np.nan)
    if v.size <= lookback:
        return out
    # Trailing mean of the lookback bars ending at i-1.
    csum = np.cumsum(np.insert(v, 0, 0.0))
    window_sum = csum[lookback:-1] - csum[:-lookback - 1]
    mean = window_sum / lookback
    with np.errstate(divide="ignore", invalid="ignore"):
        out[lookback:] = np.where(mean > 0, v[lookback:] / mean, np.nan)
    return out


@dataclass(frozen=True)
class ConditionalAutocorr:
    """First-order return autocorrelation within one condition."""

    label: str
    rho: float
    n: int
    t_stat: float
    p_value: float

    @property
    def reverting(self) -> bool:
        return self.rho < 0 and self.p_value < 0.05

    @property
    def trending(self) -> bool:
        return self.rho > 0 and self.p_value < 0.05

    @property
    def verdict(self) -> str:
        if self.reverting:
            return "REVERTS"
        if self.trending:
            return "PERSISTS"
        return "no signal"


def _autocorr(x: np.ndarray, y: np.ndarray, label: str) -> ConditionalAutocorr:
    n = x.size
    if n < 30:
        return ConditionalAutocorr(label, 0.0, n, 0.0, 1.0)
    sx, sy = x.std(ddof=1), y.std(ddof=1)
    if sx <= 0 or sy <= 0:
        return ConditionalAutocorr(label, 0.0, n, 0.0, 1.0)
    rho = float(np.corrcoef(x, y)[0, 1])
    if not np.isfinite(rho) or abs(rho) >= 1.0:
        return ConditionalAutocorr(label, 0.0, n, 0.0, 1.0)
    # Standard t-test for a correlation coefficient.
    t = rho * math.sqrt((n - 2) / (1.0 - rho ** 2))
    p = 2.0 * (1.0 - _NORMAL.cdf(abs(t)))
    return ConditionalAutocorr(label, rho, n, t, p)


def conditional_autocorrelation(returns: Sequence[float],
                                condition: Sequence[float],
                                low_quantile: float = 0.3,
                                high_quantile: float = 0.7
                                ) -> dict:
    """Return autocorrelation split by a conditioning variable.

    ``condition[i]`` must describe bar ``i`` using only information available
    before it -- :func:`relative_volume` already satisfies that. Pairs
    ``(r[i], r[i+1])`` are bucketed by ``condition[i]``.

    Returns the low bucket, high bucket, and the unconditional baseline, so the
    question "does volume tell us anything price does not?" is answered by the
    *difference* rather than by either number alone.
    """
    r = np.asarray(list(returns), dtype=float)
    c = np.asarray(list(condition), dtype=float)
    if r.size != c.size:
        raise ValueError("returns and condition must be the same length")
    if not 0 < low_quantile < high_quantile < 1:
        raise ValueError("need 0 < low_quantile < high_quantile < 1")

    x, y, cond = r[:-1], r[1:], c[:-1]
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(cond)
    x, y, cond = x[ok], y[ok], cond[ok]
    if x.size < 60:
        raise ValueError(f"need at least 60 usable pairs, got {x.size}")

    lo_cut = float(np.quantile(cond, low_quantile))
    hi_cut = float(np.quantile(cond, high_quantile))
    low_mask = cond <= lo_cut
    high_mask = cond >= hi_cut

    low = _autocorr(x[low_mask], y[low_mask], "low volume")
    high = _autocorr(x[high_mask], y[high_mask], "high volume")
    overall = _autocorr(x, y, "all bars")

    # Campbell-Grossman-Wang: reversion concentrated in low volume, so the
    # low bucket's rho should sit BELOW the high bucket's.
    spread = low.rho - high.rho
    return {
        "low": low, "high": high, "overall": overall,
        "low_cut": round(lo_cut, 4), "high_cut": round(hi_cut, 4),
        "rho_spread": round(spread, 5),
        "supports_cgw": bool(spread < 0 and low.reverting),
    }


def volume_conditioned_profile(returns: Sequence[float],
                               volume: Sequence[float],
                               lookback: int = 20,
                               buckets: int = 5) -> list[dict]:
    """Autocorrelation by volume quintile, to see whether the effect is graded.

    A real microstructure effect should vary smoothly across buckets. One
    extreme bucket behaving differently from the rest is usually an outlier
    rather than a mechanism.
    """
    r = np.asarray(list(returns), dtype=float)
    rv = relative_volume(volume, lookback)
    n = min(r.size, rv.size)
    r, rv = r[:n], rv[:n]

    x, y, cond = r[:-1], r[1:], rv[:-1]
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(cond)
    x, y, cond = x[ok], y[ok], cond[ok]
    if x.size < buckets * 60:
        raise ValueError("not enough data for the requested bucket count")

    edges = np.quantile(cond, np.linspace(0, 1, buckets + 1))
    out = []
    for i in range(buckets):
        lo, hi = edges[i], edges[i + 1]
        mask = (cond >= lo) & (cond <= hi if i == buckets - 1 else cond < hi)
        stat = _autocorr(x[mask], y[mask], f"q{i + 1}")
        out.append({
            "bucket": i + 1,
            "relative_volume_range": (round(float(lo), 3), round(float(hi), 3)),
            "rho": round(stat.rho, 5),
            "n": stat.n,
            "p_value": round(stat.p_value, 5),
            "verdict": stat.verdict,
        })
    return out
