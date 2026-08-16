"""Honest out-of-sample validation with a multiple-testing correction.

Why this module exists
----------------------
``research/`` holds twenty-five profitable-looking reports and the account has
no edge. That is the signature of selection bias, not bad luck: when you try
enough profiles against the same history, the best one looks good *because* you
tried enough, and its in-sample Sharpe tells you nothing about tomorrow.
``research/V14_4_TRAIN_CONFIRM_EDGE_REBUILD_REPORT.md`` reached the same
conclusion the hard way -- all five selection protocols failed their locked test.

The fix is not a better strategy. It is refusing to call anything an edge until
it survives:

1. **Walk-forward evaluation** -- parameters chosen on a train slice are scored
   only on the slice that follows, repeatedly, so every score is out of sample.
2. **A deflation for how many things you tried** -- the Deflated Sharpe Ratio
   (Bailey & Lopez de Prado, 2014) asks whether the winner beats what the *best
   of N random trials* would have produced anyway.
3. **Explicit gates** -- a verdict function that says PASS or FAIL and names the
   gate that failed, so a result cannot be quietly reinterpreted.

Everything here is pure and depends only on the standard library plus numpy, so
it can be unit-tested without market data or a broker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Callable, Iterable, List, Optional, Sequence

import numpy as np

__all__ = [
    "Split",
    "walk_forward_splits",
    "FoldResult",
    "WalkForwardReport",
    "sharpe_ratio",
    "probabilistic_sharpe_ratio",
    "expected_max_sharpe",
    "deflated_sharpe_ratio",
    "benjamini_hochberg",
    "Gate",
    "Verdict",
    "DEFAULT_GATES",
    "evaluate",
    "run_walk_forward",
]

_NORMAL = NormalDist()
_EULER_MASCHERONI = 0.5772156649015329


# --- splitting -------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    """One walk-forward fold, as half-open index ranges into the bar series."""

    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    def __post_init__(self) -> None:
        if not (0 <= self.train_start < self.train_end
                <= self.test_start < self.test_end):
            raise ValueError(
                f"fold {self.index} has non-monotonic bounds: {self}")

    @property
    def train_len(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_len(self) -> int:
        return self.test_end - self.test_start

    def train_slice(self) -> slice:
        return slice(self.train_start, self.train_end)

    def test_slice(self) -> slice:
        return slice(self.test_start, self.test_end)


def walk_forward_splits(n_bars: int, n_folds: int = 5,
                        train_frac: float = 0.6,
                        anchored: bool = False,
                        embargo: int = 0) -> List[Split]:
    """Build ``n_folds`` consecutive train/test folds over ``n_bars``.

    ``train_frac`` is the share of each *window* used for training; the rest is
    the out-of-sample test. With ``anchored=True`` the training set always
    starts at bar 0 and grows (expanding window); otherwise it rolls.

    ``embargo`` drops that many bars between train and test, which matters when
    indicators look back far enough for the last training bar to leak into the
    first test bar.
    """
    if n_bars <= 0:
        raise ValueError("n_bars must be positive")
    if n_folds < 1:
        raise ValueError("n_folds must be at least 1")
    if not 0 < train_frac < 1:
        raise ValueError("train_frac must be in (0, 1)")
    if embargo < 0:
        raise ValueError("embargo must be non-negative")

    # Each fold advances by one test block; the first fold needs a full train
    # block ahead of it.
    window = n_bars // (1 + n_folds * (1 - train_frac) / train_frac)
    window = int(window)
    train_len = int(window * train_frac)
    test_len = max(1, (n_bars - train_len) // n_folds)
    if train_len <= embargo or test_len <= 0:
        raise ValueError(
            f"{n_bars} bars cannot be split into {n_folds} folds "
            f"(train={train_len}, test={test_len}, embargo={embargo})")

    splits: List[Split] = []
    for i in range(n_folds):
        test_start = train_len + i * test_len
        test_end = min(test_start + test_len, n_bars)
        if test_start >= n_bars or test_end - test_start < 1:
            break
        train_start = 0 if anchored else max(0, test_start - train_len)
        train_end = max(train_start + 1, test_start - embargo)
        splits.append(Split(index=i, train_start=train_start,
                            train_end=train_end, test_start=test_start,
                            test_end=test_end))
    if not splits:
        raise ValueError("no usable folds were produced")
    return splits


# --- risk-adjusted statistics ---------------------------------------------


def sharpe_ratio(returns: Sequence[float], periods_per_year: float = 252.0
                 ) -> float:
    """Annualised Sharpe of a per-trade or per-period return series.

    Returns 0.0 for a degenerate series (fewer than two points, or no
    variance) rather than raising -- a strategy with one trade has no
    measurable Sharpe, and that is the honest answer.
    """
    arr = np.asarray(list(returns), dtype=float)
    if arr.size < 2:
        return 0.0
    sd = arr.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return 0.0
    return float(arr.mean() / sd * math.sqrt(periods_per_year))


def _moments(returns: Sequence[float]) -> tuple[int, float, float]:
    """(n, skew, non-excess kurtosis) of a return series."""
    arr = np.asarray(list(returns), dtype=float)
    n = arr.size
    if n < 2:
        return n, 0.0, 3.0
    sd = arr.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return n, 0.0, 3.0
    z = (arr - arr.mean()) / sd
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())
    return n, skew, kurt


def probabilistic_sharpe_ratio(observed_sr: float, n_obs: int,
                               skew: float = 0.0, kurtosis: float = 3.0,
                               benchmark_sr: float = 0.0) -> float:
    """P(true Sharpe > ``benchmark_sr``) given the observed Sharpe.

    Sharpe ratios here are *per-observation* (not annualised) so the estimator
    matches the sample size; :func:`deflated_sharpe_ratio` handles the
    conversion for you.
    """
    if n_obs < 2:
        return 0.0
    denom_sq = (1.0
                - skew * observed_sr
                + 0.25 * (kurtosis - 1.0) * observed_sr ** 2)
    if denom_sq <= 0:
        return 0.0
    z = ((observed_sr - benchmark_sr) * math.sqrt(n_obs - 1)
         / math.sqrt(denom_sq))
    return float(_NORMAL.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe from ``n_trials`` strategies with no real edge.

    This is the bar a genuine edge has to clear. With enough trials it rises
    without limit, which is precisely why twenty-five profiles produced a
    winner that meant nothing.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be at least 1")
    if sr_variance < 0:
        raise ValueError("sr_variance must be non-negative")
    if n_trials == 1 or sr_variance == 0:
        return 0.0
    sd = math.sqrt(sr_variance)
    a = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    b = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(sd * ((1.0 - _EULER_MASCHERONI) * a + _EULER_MASCHERONI * b))


def deflated_sharpe_ratio(returns: Sequence[float], n_trials: int,
                          sr_variance: Optional[float] = None,
                          trial_sharpes: Optional[Sequence[float]] = None
                          ) -> float:
    """Probability the strategy's edge is real after deflating for ``n_trials``.

    Supply either ``sr_variance`` (the variance of the Sharpe ratios you tried)
    or ``trial_sharpes`` to have it computed. A result below ~0.95 means the
    winner is not distinguishable from the best of N coin flips.
    """
    n, skew, kurt = _moments(returns)
    if n < 2:
        return 0.0
    if sr_variance is None:
        if trial_sharpes is None:
            raise ValueError(
                "provide sr_variance or trial_sharpes to deflate against")
        trials = np.asarray(list(trial_sharpes), dtype=float)
        sr_variance = float(trials.var(ddof=1)) if trials.size > 1 else 0.0

    # Per-observation Sharpe keeps the estimator consistent with n.
    observed = sharpe_ratio(returns, periods_per_year=1.0)
    benchmark = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(observed, n, skew, kurt, benchmark)


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05
                       ) -> List[bool]:
    """Benjamini-Hochberg FDR control; True where the hypothesis is rejected.

    Use when scoring many candidate profiles at once and you want to control
    the share of false discoveries rather than deflate a single winner.
    """
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    ps = list(p_values)
    m = len(ps)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: ps[i])
    keep = [False] * m
    cutoff_rank = -1
    for rank, idx in enumerate(order, start=1):
        if ps[idx] <= alpha * rank / m:
            cutoff_rank = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= cutoff_rank:
            keep[idx] = True
    return keep


# --- verdict ---------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    """One named pass/fail condition applied to a metrics dict."""

    name: str
    test: Callable[[dict], bool]
    describe: str

    def check(self, metrics: dict) -> bool:
        return bool(self.test(metrics))


DEFAULT_GATES: tuple[Gate, ...] = (
    Gate("net_profitable",
         lambda m: m.get("net_profit", 0.0) > 0,
         "net profit after costs must be positive"),
    Gate("profit_factor",
         lambda m: m.get("profit_factor", 0.0) >= 1.10,
         "net profit factor >= 1.10"),
    Gate("min_trades",
         lambda m: m.get("trades", 0) >= 200,
         "at least 200 out-of-sample trades"),
    Gate("majority_of_folds_positive",
         lambda m: m.get("positive_fold_fraction", 0.0) > 0.5,
         "more than half the walk-forward folds profitable"),
    Gate("deflated_sharpe",
         lambda m: m.get("deflated_sharpe", 0.0) >= 0.95,
         "deflated Sharpe probability >= 0.95 after multiple-testing"),
)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    failed_gates: tuple[str, ...]
    metrics: dict
    gate_results: dict = field(default_factory=dict)

    def explain(self) -> str:
        if self.passed:
            return "PASS: every gate satisfied."
        lines = ["FAIL: " + ", ".join(self.failed_gates)]
        for name, ok in self.gate_results.items():
            lines.append(f"  [{'ok' if ok else 'FAIL'}] {name}")
        return "\n".join(lines)


def evaluate(metrics: dict, gates: Iterable[Gate] = DEFAULT_GATES) -> Verdict:
    """Apply ``gates`` to a metrics dict and return a PASS/FAIL verdict."""
    results = {g.name: g.check(metrics) for g in gates}
    failed = tuple(name for name, ok in results.items() if not ok)
    return Verdict(passed=not failed, failed_gates=failed,
                   metrics=metrics, gate_results=results)


# --- walk-forward driver ---------------------------------------------------


@dataclass
class FoldResult:
    split: Split
    net_profit: float
    trades: int
    returns: List[float] = field(default_factory=list)
    chosen_params: dict = field(default_factory=dict)

    @property
    def profitable(self) -> bool:
        return self.net_profit > 0


@dataclass
class WalkForwardReport:
    folds: List[FoldResult]
    n_trials: int
    trial_sharpes: List[float] = field(default_factory=list)

    @property
    def all_returns(self) -> List[float]:
        out: List[float] = []
        for f in self.folds:
            out.extend(f.returns)
        return out

    @property
    def net_profit(self) -> float:
        return round(sum(f.net_profit for f in self.folds), 2)

    @property
    def trades(self) -> int:
        return sum(f.trades for f in self.folds)

    @property
    def positive_fold_fraction(self) -> float:
        if not self.folds:
            return 0.0
        return sum(1 for f in self.folds if f.profitable) / len(self.folds)

    @property
    def profit_factor(self) -> float:
        rets = self.all_returns
        gross_win = sum(r for r in rets if r > 0)
        gross_loss = -sum(r for r in rets if r < 0)
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return round(gross_win / gross_loss, 3)

    def metrics(self, periods_per_year: float = 252.0) -> dict:
        rets = self.all_returns
        dsr = (deflated_sharpe_ratio(rets, self.n_trials,
                                     trial_sharpes=self.trial_sharpes)
               if self.trial_sharpes else 0.0)
        return {
            "folds": len(self.folds),
            "trades": self.trades,
            "net_profit": self.net_profit,
            "profit_factor": self.profit_factor,
            "positive_fold_fraction": round(self.positive_fold_fraction, 3),
            "sharpe": round(sharpe_ratio(rets, periods_per_year), 3),
            "deflated_sharpe": round(dsr, 4),
            "n_trials": self.n_trials,
        }

    def verdict(self, gates: Iterable[Gate] = DEFAULT_GATES) -> Verdict:
        return evaluate(self.metrics(), gates)


def run_walk_forward(n_bars: int,
                     select_fn: Callable[[Split], dict],
                     score_fn: Callable[[Split, dict], FoldResult],
                     n_folds: int = 5, train_frac: float = 0.6,
                     anchored: bool = False, embargo: int = 0,
                     n_trials: int = 1,
                     trial_sharpes: Optional[Sequence[float]] = None
                     ) -> WalkForwardReport:
    """Drive a full walk-forward run.

    ``select_fn`` sees only a fold's *training* slice and returns the chosen
    parameters. ``score_fn`` applies them to the *test* slice and returns a
    :class:`FoldResult`. Keeping the two apart is what makes the score honest;
    a ``select_fn`` that peeks at the test slice defeats the entire module.
    """
    splits = walk_forward_splits(n_bars, n_folds, train_frac, anchored, embargo)
    folds = []
    for split in splits:
        params = select_fn(split)
        result = score_fn(split, params)
        result.chosen_params = params
        folds.append(result)
    return WalkForwardReport(folds=folds, n_trials=n_trials,
                             trial_sharpes=list(trial_sharpes or []))
