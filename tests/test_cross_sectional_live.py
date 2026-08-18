"""A ranking book meeting the live risk engine: sizing, caps, and neutrality."""

import pytest

from mt5_ai_bridge.cross_sectional_live import (RebalanceLeg, factor_exposure,
                                                plan_rebalance)
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.risk_v18 import RiskBudget, RiskEngine, edge_from_trades

BALANCE = 4_802.43


def _edge():
    """A signal with a genuine measured edge, so Kelly is positive."""
    return edge_from_trades([100.0] * 55 + [-90.0] * 45)


def _engine(budget=None):
    engine = RiskEngine(budget=budget or RiskBudget())
    engine.governor.observe(BALANCE)
    return engine


def _book(n=20, price=60.0, stop=6.0):
    legs = []
    for i in range(n):
        legs.append(RebalanceLeg(f"LNG{i}", Signal.BUY, price, stop))
        legs.append(RebalanceLeg(f"SHT{i}", Signal.SELL, price, stop))
    return legs


def _hedged_budget(positions=40):
    return RiskBudget(hedged=True, max_concurrent_positions=positions,
                      max_symbol_risk_fraction=0.0025,
                      max_total_risk_fraction=0.10)


def test_an_unproven_signal_is_not_sized_at_all():
    # Kelly is zero for a losing record, and zero Kelly means no position --
    # not a small one.
    losing = edge_from_trades([50.0] * 30 + [-100.0] * 70)
    plan = plan_rebalance(_book(2), balance=BALANCE, equity=BALANCE,
                          engine=_engine(), edge=losing)

    assert plan.accepted == []
    assert all("no measured edge" in leg.reason for leg in plan.rejected)


def test_the_directional_budget_cannot_host_a_forty_name_book():
    # The default budget allows 5 positions and caps one factor at 4%. Every
    # US equity loads that factor, so the book is refused almost entirely.
    plan = plan_rebalance(_book(), balance=BALANCE, equity=BALANCE,
                          engine=_engine(), edge=_edge())

    assert plan.requested == 40
    assert plan.accepted and len(plan.accepted) < 5


def test_the_hedged_budget_hosts_the_whole_book_and_stays_neutral():
    plan = plan_rebalance(_book(), balance=BALANCE, equity=BALANCE,
                          engine=_engine(_hedged_budget()), edge=_edge())

    assert len(plan.accepted) == 40
    assert plan.summary()["longs"] == 20
    assert plan.summary()["shorts"] == 20
    assert plan.net_notional == 0.0
    assert plan.is_balanced


def test_a_book_whose_shorts_were_refused_is_reported_as_not_neutral():
    # This is the failure that matters: a hedged strategy quietly becoming a
    # directional one because only one side fitted the budget.
    longs = [RebalanceLeg(f"LNG{i}", Signal.BUY, 60.0, 6.0) for i in range(4)]
    plan = plan_rebalance(longs, balance=BALANCE, equity=BALANCE,
                          engine=_engine(_hedged_budget()), edge=_edge())

    assert plan.accepted
    assert not plan.is_balanced
    assert plan.net_notional == plan.gross_notional


def test_whole_shares_only():
    plan = plan_rebalance(_book(), balance=BALANCE, equity=BALANCE,
                          engine=_engine(_hedged_budget()), edge=_edge())

    for leg in plan.accepted:
        assert leg.shares >= 1
        assert leg.shares == int(leg.shares)


def test_a_share_too_expensive_for_the_budget_is_refused_not_rounded_up():
    # One share with a $60 stop risks 1.2% of a $4,802 account, far past the
    # 0.25% per-symbol budget of a hedged book.
    dear = [RebalanceLeg("DEAR", Signal.BUY, 900.0, 60.0)]
    plan = plan_rebalance(dear, balance=BALANCE, equity=BALANCE,
                          engine=_engine(_hedged_budget()), edge=_edge())

    assert plan.accepted == []
    assert plan.rejected[0].shares == 0


def test_factor_exposure_nets_longs_against_shorts():
    plan = plan_rebalance(_book(), balance=BALANCE, equity=BALANCE,
                          engine=_engine(_hedged_budget()), edge=_edge())

    exposure = factor_exposure(plan)

    assert exposure["US_EQUITY"] == pytest.approx(0.0, abs=1e-6)


def test_legs_are_validated():
    engine = _engine(_hedged_budget())
    for bad in (RebalanceLeg("X", Signal.BUY, 0.0, 1.0),
                RebalanceLeg("X", Signal.BUY, 10.0, 0.0)):
        with pytest.raises(ValueError):
            plan_rebalance([bad], balance=BALANCE, equity=BALANCE,
                           engine=engine, edge=_edge())


def test_a_hedged_cap_lets_an_offsetting_trade_through_a_full_book():
    budget = _hedged_budget()
    # Long-heavy book at the factor cap; a short reduces net exposure.
    open_risk = {f"L{i}": 0.0025 for i in range(16)}
    sides = {s: 1 for s in open_risk}

    long_room, _ = budget.room_for("NEWLONG", 0.0025, open_risk, sides, 1)
    short_room, _ = budget.room_for("NEWSHORT", 0.0025, open_risk, sides, -1)

    assert short_room >= long_room


def test_gross_rule_is_unchanged_when_the_budget_is_not_hedged():
    budget = RiskBudget()          # hedged=False
    open_risk = {"AAPL": 0.04}
    sides = {"AAPL": 1}

    allowed, why = budget.room_for("XOM", 0.02, open_risk, sides, -1)

    assert allowed == 0.0
    assert "US_EQUITY" in why
