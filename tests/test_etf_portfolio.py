"""ETF portfolio: factor caps, whole-share sizing, one shared account."""

import pandas as pd
import pytest

from mt5_ai_bridge.candidate_v16 import LOCKED_V16
from mt5_ai_bridge.etf_portfolio import (EtfPortfolioConfig, factor_of,
                                         replay_etf_portfolio)


def _oscillating(n=400, level=100.0, swing=6.0, start=1_000_000_000):
    """Bars that stretch far enough from the mean to trigger V16 repeatedly."""
    rows = []
    for i in range(n):
        offset = swing if (i // 25) % 2 else -swing
        close = level + offset + (i % 5) * 0.1
        rows.append({"time": start + i * 86_400, "open": close,
                     "high": close + 1.0, "low": close - 1.0, "close": close})
    return pd.DataFrame(rows)


def test_factor_of_groups_the_equity_etfs_and_weights_the_leveraged_one():
    assert factor_of("IVV") == ("US_EQUITY", 1.0)
    assert factor_of("VTI") == ("US_EQUITY", 1.0)
    # A 3x fund is three units of the same bet, not one.
    assert factor_of("TQQQ") == ("US_EQUITY", 3.0)
    # EEM correlates 0.77 with IVV, not 0.99, so it is its own factor.
    assert factor_of("EEM") == ("EM_EQUITY", 1.0)
    # An unknown ticker is isolated rather than silently pooled.
    assert factor_of("XYZ") == ("XYZ", 1.0)


def test_config_rejects_incoherent_risk_structure():
    with pytest.raises(ValueError):
        EtfPortfolioConfig(risk_percent_per_trade=0.0).validate()
    with pytest.raises(ValueError):
        EtfPortfolioConfig(risk_percent_per_trade=2.0,
                           max_total_risk_percent=1.0).validate()
    with pytest.raises(ValueError):
        EtfPortfolioConfig(max_concurrent_positions=0).validate()
    with pytest.raises(ValueError):
        EtfPortfolioConfig(min_shares=0).validate()


def test_correlated_etfs_cannot_all_open_at_once():
    # IVV and VTI correlate 0.989 and share a factor. A cap that admits one
    # 0.5% position per factor must never hold both simultaneously.
    bars = {"IVV": _oscillating(), "VTI": _oscillating()}
    cfg = EtfPortfolioConfig(risk_percent_per_trade=0.5,
                             max_factor_risk_percent=0.5,
                             max_concurrent_positions=4)

    result = replay_etf_portfolio(bars, portfolio=cfg, starting_balance=50_000)

    open_windows = [(t.entry_time, t.exit_time) for t in result.trades
                    if t.symbol == "IVV"]
    for trade in (t for t in result.trades if t.symbol == "VTI"):
        for start, end in open_windows:
            assert not (trade.entry_time < end and start < trade.exit_time), (
                "two positions in one factor were open at the same time")


def test_separate_factors_are_not_capped_against_each_other():
    bars = {"IVV": _oscillating(), "EEM": _oscillating()}
    cfg = EtfPortfolioConfig(risk_percent_per_trade=0.5,
                             max_factor_risk_percent=0.5,
                             max_total_risk_percent=1.5,
                             max_concurrent_positions=4)

    result = replay_etf_portfolio(bars, portfolio=cfg, starting_balance=50_000)
    traded = {t.symbol for t in result.trades}

    assert traded == {"IVV", "EEM"}
    assert "factor_risk_US_EQUITY" not in result.rejected


def test_whole_shares_only_and_never_rounded_up_past_the_budget():
    bars = {"IVV": _oscillating()}
    result = replay_etf_portfolio(bars, starting_balance=50_000)

    assert result.trades
    for trade in result.trades:
        assert trade.shares == int(trade.shares)
        assert trade.shares >= 1
        # Sizing floors, so realised risk never exceeds the per-trade budget.
        assert trade.risk_percent <= EtfPortfolioConfig().risk_percent_per_trade + 1e-9


def test_an_account_too_small_for_one_share_takes_no_trade():
    # One share of a $100 ETF with a wide stop risks more than 0.5% of $200.
    bars = {"IVV": _oscillating()}

    result = replay_etf_portfolio(bars, starting_balance=200.0)

    assert result.trades == []
    assert result.rejected.get("below_min_lot", 0) > 0


def test_beta_scaled_risk_lets_the_leveraged_fund_trade_at_all():
    bars = {"TQQQ": _oscillating()}
    capped = EtfPortfolioConfig(risk_percent_per_trade=0.5,
                                max_factor_risk_percent=1.0)

    scaled = replay_etf_portfolio(bars, portfolio=capped,
                                  starting_balance=50_000)
    flat = replay_etf_portfolio(
        bars, portfolio=EtfPortfolioConfig(risk_percent_per_trade=0.5,
                                           max_factor_risk_percent=1.0,
                                           beta_scaled_risk=False),
        starting_balance=50_000)

    # Flat sizing asks for 3 factor units against a 1-unit cap: never admitted.
    assert flat.trades == []
    assert flat.rejected.get("factor_risk_US_EQUITY", 0) > 0
    assert scaled.trades


def test_one_shared_balance_compounds_across_symbols():
    bars = {"IVV": _oscillating(), "EEM": _oscillating()}
    result = replay_etf_portfolio(bars, starting_balance=50_000)

    replayed = 50_000 + sum(t.profit for t in result.trades)

    assert round(replayed, 2) == result.final_balance
    assert len(result.equity_curve) > 0


def test_time_stop_is_honoured_so_a_reversion_trade_cannot_become_a_hold():
    # A one-way ramp never reverts; without the time stop the position would
    # run to the end of the series.
    n = 300
    rows = [{"time": 1_000_000_000 + i * 86_400, "open": 100.0 + i,
             "high": 101.0 + i, "low": 99.0 + i, "close": 100.0 + i}
            for i in range(n)]
    result = replay_etf_portfolio({"IVV": pd.DataFrame(rows)},
                                  starting_balance=50_000)

    for trade in result.trades:
        held_bars = (trade.exit_time - trade.entry_time) / 86_400
        assert held_bars <= LOCKED_V16.max_holding_bars + 1
