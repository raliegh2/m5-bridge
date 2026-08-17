import math
import random

import numpy as np
import pytest
from pytest import approx

from mt5_ai_bridge.validation import (DEFAULT_GATES, FoldResult, Gate, Split,
                                      WalkForwardReport, benjamini_hochberg,
                                      deflated_sharpe_ratio, evaluate,
                                      expected_max_sharpe,
                                      probabilistic_sharpe_ratio,
                                      run_walk_forward, sharpe_ratio,
                                      walk_forward_splits)


# --- splits ----------------------------------------------------------------


def test_splits_are_ordered_and_out_of_sample():
    splits = walk_forward_splits(10_000, n_folds=5, train_frac=0.6)
    assert len(splits) == 5
    for s in splits:
        assert s.train_end <= s.test_start      # never trains on the test slice
        assert s.train_len > 0 and s.test_len > 0
        assert s.test_end <= 10_000


def test_test_slices_do_not_overlap_and_move_forward():
    splits = walk_forward_splits(10_000, n_folds=4, train_frac=0.5)
    for a, b in zip(splits, splits[1:]):
        assert b.test_start >= a.test_end
        assert b.test_start > a.test_start


def test_rolling_window_moves_but_anchored_window_grows():
    rolling = walk_forward_splits(10_000, n_folds=4, train_frac=0.5)
    anchored = walk_forward_splits(10_000, n_folds=4, train_frac=0.5,
                                   anchored=True)
    assert all(s.train_start == 0 for s in anchored)
    assert anchored[-1].train_len > anchored[0].train_len
    assert rolling[-1].train_start > rolling[0].train_start


def test_embargo_opens_a_gap_between_train_and_test():
    splits = walk_forward_splits(10_000, n_folds=3, train_frac=0.6, embargo=200)
    for s in splits:
        assert s.test_start - s.train_end >= 200


def test_split_rejects_non_monotonic_bounds():
    with pytest.raises(ValueError):
        Split(index=0, train_start=100, train_end=50, test_start=200,
              test_end=300)
    with pytest.raises(ValueError):
        Split(index=0, train_start=0, train_end=100, test_start=50,
              test_end=300)


def test_bad_split_arguments_are_rejected():
    with pytest.raises(ValueError):
        walk_forward_splits(0)
    with pytest.raises(ValueError):
        walk_forward_splits(1000, n_folds=0)
    with pytest.raises(ValueError):
        walk_forward_splits(1000, train_frac=1.5)
    with pytest.raises(ValueError):
        walk_forward_splits(1000, embargo=-1)
    with pytest.raises(ValueError):
        walk_forward_splits(10, n_folds=50)     # too few bars


# --- Sharpe ----------------------------------------------------------------


def test_sharpe_of_constant_series_is_zero_not_infinite():
    assert sharpe_ratio([0.01] * 50) == 0.0
    assert sharpe_ratio([]) == 0.0
    assert sharpe_ratio([0.5]) == 0.0


def test_sharpe_scales_with_annualisation():
    rets = [0.01, -0.005, 0.02, 0.0, -0.01, 0.015]
    daily = sharpe_ratio(rets, periods_per_year=1.0)
    annual = sharpe_ratio(rets, periods_per_year=252.0)
    assert annual == approx(daily * math.sqrt(252))


def test_sharpe_sign_follows_mean_return():
    assert sharpe_ratio([0.02, 0.01, 0.03, 0.015]) > 0
    assert sharpe_ratio([-0.02, -0.01, -0.03, -0.015]) < 0


# --- PSR / DSR -------------------------------------------------------------


def test_psr_is_half_when_observed_equals_benchmark():
    assert probabilistic_sharpe_ratio(0.5, 100, 0.0, 3.0,
                                      benchmark_sr=0.5) == approx(0.5)


def test_psr_rises_with_sample_size():
    """The same Sharpe is more believable from a longer track record."""
    small = probabilistic_sharpe_ratio(0.2, 20, 0.0, 3.0)
    large = probabilistic_sharpe_ratio(0.2, 2000, 0.0, 3.0)
    # A large sample saturates to 1.0 in float, so bound it inclusively.
    assert 0.5 < small < large <= 1.0


def test_psr_penalises_negative_skew_and_fat_tails():
    base = probabilistic_sharpe_ratio(0.3, 500, 0.0, 3.0)
    skewed = probabilistic_sharpe_ratio(0.3, 500, -1.5, 3.0)
    fat = probabilistic_sharpe_ratio(0.3, 500, 0.0, 12.0)
    assert skewed < base
    assert fat < base


def test_psr_of_tiny_sample_is_zero():
    assert probabilistic_sharpe_ratio(2.0, 1) == 0.0


def test_expected_max_sharpe_grows_with_trial_count():
    v = 0.25
    assert expected_max_sharpe(1, v) == 0.0
    e10 = expected_max_sharpe(10, v)
    e100 = expected_max_sharpe(100, v)
    e1000 = expected_max_sharpe(1000, v)
    assert 0 < e10 < e100 < e1000


def test_expected_max_sharpe_is_zero_without_trial_variance():
    assert expected_max_sharpe(500, 0.0) == 0.0


def test_expected_max_sharpe_rejects_bad_input():
    with pytest.raises(ValueError):
        expected_max_sharpe(0, 0.1)
    with pytest.raises(ValueError):
        expected_max_sharpe(10, -0.1)


def test_deflation_punishes_a_winner_picked_from_many_trials():
    """The core lesson: the same track record means less if you tried harder.

    One identical return series, evaluated as if it were the only idea tested,
    then as the best of 25, then as the best of 500. Only the first survives
    the 0.95 gate -- which is exactly what went wrong across v4..v14.25.
    """
    rng = random.Random(7)
    rets = [rng.gauss(0.006, 0.01) for _ in range(750)]
    trials = [rng.gauss(0.0, 0.30) for _ in range(40)]

    honest = deflated_sharpe_ratio(rets, n_trials=1, trial_sharpes=trials)
    searched = deflated_sharpe_ratio(rets, n_trials=25, trial_sharpes=trials)
    ransacked = deflated_sharpe_ratio(rets, n_trials=500, trial_sharpes=trials)

    assert honest > searched > ransacked
    assert honest >= 0.95           # a genuine, singly-tested edge
    assert ransacked < 0.05         # the same record, found by ransacking


def test_pure_noise_does_not_survive_deflation():
    """25 random profiles, best one selected -- must not read as an edge."""
    rng = np.random.default_rng(11)
    trials = []
    best, best_sr = None, -math.inf
    for _ in range(25):
        rets = rng.normal(0.0, 0.01, size=600)
        sr = sharpe_ratio(rets, periods_per_year=1.0)
        trials.append(sr)
        if sr > best_sr:
            best, best_sr = rets, sr

    dsr = deflated_sharpe_ratio(best, n_trials=25, trial_sharpes=trials)
    assert dsr < 0.95, f"noise passed deflation with DSR={dsr}"


def test_deflated_sharpe_needs_a_variance_source():
    with pytest.raises(ValueError, match="sr_variance or trial_sharpes"):
        deflated_sharpe_ratio([0.01, 0.02, -0.01], n_trials=5)


def test_deflated_sharpe_of_empty_series_is_zero():
    assert deflated_sharpe_ratio([], n_trials=5, sr_variance=0.1) == 0.0


# --- Benjamini-Hochberg ----------------------------------------------------


def test_bh_rejects_only_clearly_small_p_values():
    keep = benjamini_hochberg([0.001, 0.9, 0.8, 0.7, 0.6], alpha=0.05)
    assert keep == [True, False, False, False, False]


def test_bh_rejects_nothing_when_all_p_values_are_large():
    assert benjamini_hochberg([0.4, 0.5, 0.9], alpha=0.05) == [False] * 3


def test_bh_is_less_strict_than_bonferroni():
    ps = [0.001, 0.008, 0.02, 0.5, 0.7]
    bh = benjamini_hochberg(ps, alpha=0.05)
    bonferroni = [p <= 0.05 / len(ps) for p in ps]
    assert sum(bh) >= sum(bonferroni)


def test_bh_handles_empty_and_bad_alpha():
    assert benjamini_hochberg([]) == []
    with pytest.raises(ValueError):
        benjamini_hochberg([0.1], alpha=0.0)


# --- gates and verdicts ----------------------------------------------------


def _passing_metrics():
    return {"net_profit": 500.0, "profit_factor": 1.3, "trades": 400,
            "positive_fold_fraction": 0.8, "deflated_sharpe": 0.99}


def test_verdict_passes_when_every_gate_is_met():
    v = evaluate(_passing_metrics())
    assert v.passed
    assert v.failed_gates == ()
    assert "PASS" in v.explain()


@pytest.mark.parametrize("key,bad", [
    ("net_profit", -1.0),
    ("profit_factor", 1.0),
    ("trades", 199),
    ("positive_fold_fraction", 0.5),
    ("deflated_sharpe", 0.94),
])
def test_each_default_gate_can_fail_independently(key, bad):
    metrics = _passing_metrics()
    metrics[key] = bad
    v = evaluate(metrics)
    assert not v.passed
    assert len(v.failed_gates) == 1
    assert "FAIL" in v.explain()


def test_missing_metrics_fail_closed():
    v = evaluate({})
    assert not v.passed
    assert len(v.failed_gates) == len(DEFAULT_GATES)


def test_custom_gates_are_honoured():
    gate = Gate("max_dd", lambda m: m.get("dd", 1.0) <= 0.1, "drawdown <= 10%")
    assert evaluate({"dd": 0.05}, [gate]).passed
    assert not evaluate({"dd": 0.5}, [gate]).passed


# --- driver ----------------------------------------------------------------


def test_run_walk_forward_never_shows_the_test_slice_to_the_selector():
    seen = []

    def select(split):
        seen.append((split.train_start, split.train_end))
        return {"p": split.index}

    def score(split, params):
        assert params["p"] == split.index
        return FoldResult(split=split, net_profit=10.0, trades=25,
                          returns=[0.01] * 25)

    report = run_walk_forward(6000, select, score, n_folds=3)
    assert len(report.folds) == 3
    # Every training window ends at or before its own test window starts.
    for (t_start, t_end), fold in zip(seen, report.folds):
        assert t_end <= fold.split.test_start
    assert report.trades == 75
    assert report.net_profit == 30.0
    assert report.positive_fold_fraction == 1.0
    assert report.folds[0].chosen_params == {"p": 0}


def test_report_metrics_and_verdict_reject_a_thin_losing_run():
    splits = walk_forward_splits(6000, n_folds=3)
    folds = [FoldResult(split=s, net_profit=-5.0, trades=3,
                        returns=[-0.01, 0.005, -0.02]) for s in splits]
    report = WalkForwardReport(folds=folds, n_trials=25,
                               trial_sharpes=[0.1, -0.2, 0.3])
    m = report.metrics()
    assert m["net_profit"] < 0
    assert m["trades"] == 9
    assert m["positive_fold_fraction"] == 0.0
    verdict = report.verdict()
    assert not verdict.passed
    assert "net_profitable" in verdict.failed_gates
    assert "min_trades" in verdict.failed_gates


def test_profit_factor_handles_a_lossless_run():
    splits = walk_forward_splits(4000, n_folds=2)
    folds = [FoldResult(split=s, net_profit=1.0, trades=2, returns=[0.01, 0.02])
             for s in splits]
    assert WalkForwardReport(folds=folds, n_trials=1).profit_factor == float("inf")
