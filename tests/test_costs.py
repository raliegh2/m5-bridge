import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.backtest import Backtester, _nights_between
from mt5_ai_bridge.costs import (RETAIL_TYPICAL, ZERO_COST, CostModel,
                                 breakeven_win_rate,
                                 cost_adjusted_expectancy_r, preset)
from mt5_ai_bridge.enums import OrderSide, Signal
from mt5_ai_bridge.strategy import Decision

from .test_backtest import _buy_once, _drift_df


# --- the cost model itself -------------------------------------------------


def test_round_trip_is_spread_plus_both_slippages():
    c = CostModel(spread_pips=0.9, slippage_pips=0.2)
    assert c.half_spread_pips == approx(0.45)
    assert c.per_side_pips == approx(0.65)
    assert c.round_trip_pips == approx(1.3)


def test_zero_cost_is_free():
    assert ZERO_COST.round_trip_pips == 0.0
    assert ZERO_COST.commission_cost(1.0) == 0.0
    assert ZERO_COST.swap_pips(Signal.BUY, 5) == 0.0


def test_entry_and_exit_prices_always_move_against_the_trader():
    c = CostModel(spread_pips=1.0, slippage_pips=0.5)  # 1.0 pip per side
    pip = 0.0001
    assert c.entry_price(Signal.BUY, 1.2000, pip) == approx(1.2001)
    assert c.exit_price(Signal.BUY, 1.2000, pip) == approx(1.1999)
    assert c.entry_price(Signal.SELL, 1.2000, pip) == approx(1.1999)
    assert c.exit_price(Signal.SELL, 1.2000, pip) == approx(1.2001)


def test_side_accepts_signal_orderside_and_string():
    c = CostModel(spread_pips=1.0)
    pip = 0.0001
    for side in (Signal.BUY, OrderSide.BUY, "BUY", "Signal.BUY"):
        assert c.entry_price(side, 1.2, pip) > 1.2
    for side in (Signal.SELL, OrderSide.SELL, "SELL"):
        assert c.entry_price(side, 1.2, pip) < 1.2
    with pytest.raises(ValueError):
        c.entry_price("WAIT", 1.2, pip)


def test_negative_costs_are_rejected():
    with pytest.raises(ValueError):
        CostModel(spread_pips=-0.1)
    with pytest.raises(ValueError):
        CostModel(slippage_pips=-0.1)
    with pytest.raises(ValueError):
        CostModel(commission_per_lot_round_turn=-1.0)


def test_swap_only_accrues_for_nights_held_and_is_side_dependent():
    c = CostModel(swap_pips_per_night_long=-0.5, swap_pips_per_night_short=-0.2)
    assert c.swap_pips(Signal.BUY, 0) == 0.0
    assert c.swap_pips(Signal.BUY, 3) == approx(-1.5)
    assert c.swap_pips(Signal.SELL, 3) == approx(-0.6)
    # negative swap pips become a positive dollar cost
    assert c.swap_cost(Signal.BUY, lots=1.0, nights=2,
                       pip_value_per_lot=10.0) == approx(10.0)


def test_commission_scales_with_lots():
    c = CostModel(commission_per_lot_round_turn=7.0)
    assert c.commission_cost(0.01) == approx(0.07)
    assert c.commission_cost(2.0) == approx(14.0)
    assert c.commission_pips(1.0, pip_value_per_lot=10.0) == approx(0.7)


def test_preset_lookup_rejects_typos():
    assert preset("zero") is ZERO_COST
    assert preset("TYPICAL") is RETAIL_TYPICAL
    with pytest.raises(ValueError, match="unknown cost preset"):
        preset("cheap")


# --- the analytic helpers --------------------------------------------------


def test_breakeven_win_rate_rises_with_cost():
    free = breakeven_win_rate(5.0, 1.25, ZERO_COST)
    costly = breakeven_win_rate(5.0, 1.25, CostModel(spread_pips=1.0))
    assert free == approx(1 / (1 + 1.25))          # 5 / (5 + 6.25)
    assert costly > free


def test_target_unreachable_after_costs_needs_certainty():
    # A 1-pip target with a 2-pip round trip can never pay.
    assert breakeven_win_rate(1.0, 1.0, CostModel(spread_pips=2.0)) == 1.0


def test_scalp_expectancy_is_destroyed_by_sub_pip_costs():
    """The V14.4 cost-stress finding, pinned as a test.

    A 5-pip stop at 1.25R with the backtest-implied 48.1% win rate is barely
    profitable gross and loses money once a realistic spread is charged.
    """
    win_rate = 1.158 / (1.158 + 1.25)
    gross = cost_adjusted_expectancy_r(5.0, 1.25, win_rate, ZERO_COST)
    net = cost_adjusted_expectancy_r(5.0, 1.25, win_rate,
                                     CostModel(spread_pips=0.9, slippage_pips=0.2))
    assert gross > 0
    assert net < 0


# --- integration with the backtester ---------------------------------------


def test_zero_cost_backtest_is_unchanged():
    df = _drift_df(40, overrides={15: {"high": 1.2070}})
    r = Backtester(stop_loss_pips=30, take_profit_pips=60,
                   strategy_fn=_buy_once(), cost=ZERO_COST).run(df)
    assert r.trades[0].profit == approx(6.0)
    assert r.trades[0].cost == 0.0
    assert r.total_costs == 0.0
    assert r.gross_profit == r.total_profit


def test_costs_are_deducted_from_a_winning_trade():
    df = _drift_df(40, overrides={15: {"high": 1.2070}})
    cost = CostModel(spread_pips=1.0, slippage_pips=0.5,
                     commission_per_lot_round_turn=7.0)
    r = Backtester(stop_loss_pips=30, take_profit_pips=60,
                   strategy_fn=_buy_once(), cost=cost).run(df)
    t = r.trades[0]
    # 2.0 pips round trip * 0.01 lot * $10/pip = $0.20, plus $0.07 commission
    assert t.cost == approx(0.27)
    assert t.profit == approx(6.0 - 0.27)
    assert t.gross_profit == approx(6.0)
    assert r.total_costs == approx(0.27)


def test_costs_make_a_losing_trade_worse():
    df = _drift_df(40, overrides={15: {"low": 1.1960}})
    r = Backtester(stop_loss_pips=30, take_profit_pips=60,
                   strategy_fn=_buy_once(),
                   cost=CostModel(spread_pips=1.0)).run(df)
    assert r.trades[0].profit == approx(-3.10)
    assert r.trades[0].gross_profit == approx(-3.0)


def test_summary_exposes_the_gross_net_gap():
    df = _drift_df(40, overrides={15: {"high": 1.2070}})
    s = Backtester(strategy_fn=_buy_once(),
                   cost=RETAIL_TYPICAL).run(df).summary()
    assert s["total_costs"] > 0
    assert s["gross_profit"] > s["total_profit"]


def _narrow_uptrend(n=400, base=1.2000, step=1e-5):
    """Bars too narrow to touch a 3-pip stop, drifting up into the target.

    Every trade therefore wins gross, which isolates the effect of costs.
    """
    rows = []
    for i in range(n):
        close = base + i * step
        rows.append({"time": i, "open": close, "high": close + 2e-5,
                     "low": close - 2e-5, "close": close})
    return pd.DataFrame(rows)


def test_marginal_edge_flips_negative_under_realistic_costs():
    """A strategy that wins by a hair gross should lose net -- the whole point."""
    def always_buy(market):
        return Decision(Signal.BUY, "t", 1.0)

    # A 1-pip target is worth $0.10 on 0.01 lots -- less than the $0.13
    # round trip charged by RETAIL_TYPICAL (0.9 spread + 2x0.2 slippage).
    df = _narrow_uptrend()
    free = Backtester(stop_loss_pips=1, take_profit_pips=1,
                      strategy_fn=always_buy, cost=ZERO_COST).run(df)
    paid = Backtester(stop_loss_pips=1, take_profit_pips=1,
                      strategy_fn=always_buy, cost=RETAIL_TYPICAL).run(df)

    assert paid.n_trades == free.n_trades
    assert paid.total_costs == approx(free.n_trades * 0.13, rel=1e-6)

    # Gross: every trade reaches the target for +$0.10.
    assert free.wins == free.n_trades
    assert free.total_profit > 0
    # Net: the round trip costs more than the target pays.
    assert paid.wins == 0
    assert paid.total_profit < 0
    assert paid.gross_profit_factor > paid.profit_factor


# --- swap attribution ------------------------------------------------------


def test_nights_between_ignores_synthetic_bar_indexes():
    assert _nights_between(0, 39) == 0          # test data uses range(n)
    assert _nights_between(None, 10) == 0
    assert _nights_between("x", "y") == 0


def test_nights_between_counts_whole_days_on_epoch_times():
    start = 1_761_618_600
    assert _nights_between(start, start + 86_400) == 1
    assert _nights_between(start, start + 86_399) == 0
    assert _nights_between(start, start + 3 * 86_400 + 5) == 3
    assert _nights_between(start + 100, start) == 0   # exit before entry


def test_swap_is_charged_on_real_timestamps_only():
    cost = CostModel(swap_pips_per_night_long=-1.0)
    day = 86_400

    rows = []
    for i in range(40):
        close = 1.2000 + i * 1e-5
        rows.append({"time": 1_761_618_600 + i * day,
                     "open": close, "high": close + 0.0005,
                     "low": close - 0.0005, "close": close})
    df = pd.DataFrame(rows)

    r = Backtester(strategy_fn=_buy_once(), cost=cost).run(df)
    t = r.trades[0]
    assert t.nights > 0
    assert t.cost == approx(t.nights * 1.0 * 0.01 * 10.0)
