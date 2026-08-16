"""Portfolio risk structure, diversification maths, and trial bookkeeping."""

import numpy as np
import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.candidate_v15 import LOCKED
from mt5_ai_bridge.costs import RETAIL_TYPICAL, ZERO_COST
from mt5_ai_bridge.portfolio_v15 import (PortfolioConfig, correlation_matrix,
                                         currency_exposure,
                                         diversification_report,
                                         effective_bets, replay_portfolio)
from mt5_ai_bridge.validation import TrialRegistry

H4 = 4 * 3600
START = 1_700_000_000


def _trend(n=200, base=1.2000, step=0.0020, seed=None):
    if seed is None:
        closes = [base + i * step for i in range(n)]
    else:
        rng = np.random.default_rng(seed)
        closes = list(base + np.cumsum(rng.normal(step, abs(step) * 2, n)))
    return pd.DataFrame({
        "time": [START + i * H4 for i in range(n)],
        "open": closes,
        "high": [c + 0.0010 for c in closes],
        "low": [c - 0.0010 for c in closes],
        "close": closes,
    })


# --- currency parsing -------------------------------------------------------


def test_currency_exposure_splits_symbols():
    assert currency_exposure("EURUSD") == ("EUR", "USD")
    assert currency_exposure("XAUUSD") == ("XAU", "USD")
    assert currency_exposure("gbpusd") == ("GBP", "USD")


def test_currency_exposure_rejects_short_symbols():
    with pytest.raises(ValueError):
        currency_exposure("EUR")


# --- config validation ------------------------------------------------------


def test_portfolio_config_rejects_incoherent_limits():
    with pytest.raises(ValueError):
        PortfolioConfig(risk_percent_per_trade=0).validate()
    with pytest.raises(ValueError):
        PortfolioConfig(risk_percent_per_trade=2.0,
                        max_total_risk_percent=1.0).validate()
    with pytest.raises(ValueError):
        PortfolioConfig(max_concurrent_positions=0).validate()


def test_default_config_is_valid():
    PortfolioConfig().validate()


# --- risk gates -------------------------------------------------------------


def test_concurrent_position_cap_is_enforced():
    """Four USD-quoted symbols must not all open at once under a cap of 2."""
    bars = {s: _trend() for s in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD")}
    cfg = PortfolioConfig(max_concurrent_positions=2,
                          max_currency_risk_percent=99.0,
                          max_total_risk_percent=99.0)
    result = replay_portfolio(bars, LOCKED, cfg, ZERO_COST)

    # Reconstruct concurrency from the trade ledger.
    events = []
    for t in result.trades:
        events.append((t.entry_time, 1))
        events.append((t.exit_time, -1))
    events.sort()
    live = peak = 0
    for _, delta in events:
        live += delta
        peak = max(peak, live)
    assert peak <= 2


def test_currency_cap_limits_correlated_usd_exposure():
    """The real constraint on this symbol set: they all quote USD."""
    bars = {s: _trend() for s in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD")}
    tight = PortfolioConfig(risk_percent_per_trade=0.5,
                            max_currency_risk_percent=1.0,
                            max_total_risk_percent=99.0,
                            max_concurrent_positions=99)
    loose = PortfolioConfig(risk_percent_per_trade=0.5,
                            max_currency_risk_percent=99.0,
                            max_total_risk_percent=99.0,
                            max_concurrent_positions=99)
    strict = replay_portfolio(bars, LOCKED, tight, ZERO_COST)
    open_ = replay_portfolio(bars, LOCKED, loose, ZERO_COST)
    assert strict.rejected_for_risk > 0
    assert len(strict.trades) < len(open_.trades)


def test_total_risk_ceiling_rejects_entries():
    bars = {s: _trend() for s in ("EURUSD", "GBPUSD", "AUDUSD")}
    cfg = PortfolioConfig(risk_percent_per_trade=0.5,
                          max_total_risk_percent=0.5,
                          max_currency_risk_percent=99.0,
                          max_concurrent_positions=99)
    result = replay_portfolio(bars, LOCKED, cfg, ZERO_COST)
    assert result.rejected_for_risk > 0


def test_admitted_subset_restricts_trading():
    bars = {s: _trend() for s in ("EURUSD", "GBPUSD", "AUDUSD")}
    result = replay_portfolio(bars, LOCKED, PortfolioConfig(), ZERO_COST,
                              admitted=["EURUSD"])
    assert {t.symbol for t in result.trades} <= {"EURUSD"}


def test_empty_admission_trades_nothing():
    bars = {s: _trend() for s in ("EURUSD", "GBPUSD")}
    result = replay_portfolio(bars, LOCKED, PortfolioConfig(), ZERO_COST,
                              admitted=[])
    assert result.trades == []
    assert result.net_profit == 0.0


# --- accounting -------------------------------------------------------------


def test_costs_reduce_portfolio_profit():
    bars = {s: _trend() for s in ("EURUSD", "GBPUSD")}
    free = replay_portfolio(bars, LOCKED, PortfolioConfig(), ZERO_COST)
    paid = replay_portfolio(bars, LOCKED, PortfolioConfig(), RETAIL_TYPICAL)
    assert paid.net_profit < free.net_profit
    assert sum(t.cost for t in paid.trades) > 0


def test_net_profit_matches_trade_ledger():
    bars = {s: _trend(seed=i) for i, s in enumerate(("EURUSD", "GBPUSD"))}
    r = replay_portfolio(bars, LOCKED, PortfolioConfig(), RETAIL_TYPICAL)
    assert r.net_profit == approx(sum(t.profit for t in r.trades), abs=0.05)


def test_open_positions_are_closed_at_end_of_data():
    bars = {"EURUSD": _trend()}
    r = replay_portfolio(bars, LOCKED, PortfolioConfig(), ZERO_COST)
    assert r.trades
    assert r.trades[-1].reason == "EOD"


def test_by_symbol_attribution_sums_to_total():
    bars = {s: _trend(seed=i) for i, s in enumerate(("EURUSD", "GBPUSD"))}
    r = replay_portfolio(bars, LOCKED, PortfolioConfig(), ZERO_COST)
    total = sum(v["profit"] for v in r.by_symbol().values())
    assert total == approx(sum(t.profit for t in r.trades), abs=0.05)


def test_summary_and_drawdown_are_sane():
    bars = {s: _trend(seed=i) for i, s in enumerate(("EURUSD", "GBPUSD"))}
    r = replay_portfolio(bars, LOCKED, PortfolioConfig(), ZERO_COST)
    s = r.summary()
    assert s["trades"] == len(r.trades)
    assert 0.0 <= s["max_drawdown_percent"] <= 100.0
    assert len(r.equity_curve) > 0


def test_unpriceable_symbol_is_refused_not_guessed():
    with pytest.raises(ValueError, match="not priceable"):
        replay_portfolio({"USDJPY": _trend()}, LOCKED, PortfolioConfig(),
                         ZERO_COST)


# --- diversification maths --------------------------------------------------


def test_effective_bets_of_identical_assets_is_one():
    corr = np.ones((4, 4))
    assert effective_bets(corr) == approx(1.0, abs=0.01)


def test_effective_bets_of_independent_assets_is_the_count():
    assert effective_bets(np.eye(5)) == approx(5.0, abs=0.01)


def test_effective_bets_matches_the_average_correlation_formula():
    n, rho = 4, 0.5
    corr = np.full((n, n), rho)
    np.fill_diagonal(corr, 1.0)
    expected = n / (1 + (n - 1) * rho)
    assert effective_bets(corr) == approx(expected, rel=1e-6)


def test_effective_bets_edge_cases():
    assert effective_bets(np.eye(1)) == 1.0
    assert effective_bets(np.zeros((0, 0))) == 0.0


def test_correlated_symbols_report_fewer_effective_bets():
    """Two copies of one series must not count as two bets."""
    base = _trend(seed=1)
    bars = {"EURUSD": base, "GBPUSD": base.copy()}
    report = diversification_report(bars)
    assert report["mean_abs_correlation"] == approx(1.0, abs=0.01)
    assert report["effective_bets"] == approx(1.0, abs=0.05)
    assert report["sharpe_multiplier"] == approx(1.0, abs=0.05)


def test_independent_symbols_report_full_diversification():
    bars = {"EURUSD": _trend(seed=1), "GBPUSD": _trend(seed=99)}
    report = diversification_report(bars)
    assert report["effective_bets"] > 1.5
    assert report["diversification_ratio"] > 0.7


def test_report_counts_shared_quote_currencies():
    bars = {s: _trend(seed=i)
            for i, s in enumerate(("EURUSD", "GBPUSD", "XAUUSD"))}
    report = diversification_report(bars)
    assert report["shared_quote_currencies"] == {"USD": 3}


def test_correlation_matrix_is_symmetric_with_unit_diagonal():
    bars = {s: _trend(seed=i) for i, s in enumerate(("EURUSD", "GBPUSD"))}
    symbols, corr = correlation_matrix(bars)
    assert len(symbols) == 2
    assert corr[0][0] == approx(1.0)
    assert corr[0][1] == approx(corr[1][0])


# --- trial registry ---------------------------------------------------------


def test_registry_counts_distinct_specifications(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.json")
    reg.record({"symbol": "EURUSD", "lookback": 20})
    reg.record({"symbol": "GBPUSD", "lookback": 20})
    assert reg.count == 2


def test_reevaluating_the_same_spec_does_not_inflate_the_count(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.json")
    for _ in range(5):
        reg.record({"symbol": "EURUSD", "lookback": 20})
    assert reg.count == 1
    assert len(reg) == 1


def test_key_order_does_not_create_a_new_trial(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.json")
    reg.record({"a": 1, "b": 2})
    reg.record({"b": 2, "a": 1})
    assert reg.count == 1


def test_registry_survives_a_new_session(tmp_path):
    """The whole point: n_trials cannot quietly reset to 1 tomorrow."""
    path = tmp_path / "trials.json"
    first = TrialRegistry(path)
    first.record_many([{"symbol": s} for s in ("EURUSD", "GBPUSD", "XAUUSD")])
    assert first.count == 3

    second = TrialRegistry(path)          # a fresh "session"
    assert second.count == 3
    second.record({"symbol": "AUDUSD"})
    assert second.count == 4


def test_registry_tracks_scores(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.json")
    reg.record({"symbol": "EURUSD"}, score=0.4)
    reg.record({"symbol": "GBPUSD"}, score=-0.2)
    reg.record({"symbol": "XAUUSD"})
    assert sorted(reg.scores) == [-0.2, 0.4]
    assert reg.summary()["n_trials"] == 3
    assert reg.summary()["with_scores"] == 2


def test_registry_works_without_a_path():
    reg = TrialRegistry(None)
    reg.record({"symbol": "EURUSD"})
    assert reg.count == 1
    reg.save()        # must not raise
