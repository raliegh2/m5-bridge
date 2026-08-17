"""The 10% ceiling, proven under adversarial conditions.

A stated drawdown limit is worth nothing unless it survives the case it exists
for: an unbroken run of maximum-size losses. These tests drive the engine into
that state deliberately.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pytest import approx

from mt5_ai_bridge.partial_exits import (HALF_AT_1R, PartialLeg, PartialPlan,
                                         summarise_outcomes)
from mt5_ai_bridge.risk_v18 import (BALANCED_20PCT, CONSERVATIVE_10PCT,
                                    PROFILES, edge_from_trades, risk_profile)
from mt5_ai_bridge.trading_system import (SignalSpec, TradeIntent,
                                          TradingSystem, Validation)
from mt5_ai_bridge.enums import Signal as Side


def _validated(profits=None):
    profits = profits or tuple([1.6] * 58 + [-1.0] * 42)
    return SignalSpec("proven", ("EURUSD", "GBPUSD"), "H4",
                      Validation(640, 5100.0, 1.28, 0.8, 0.971, 1385, profits))


def _intent(symbol="EURUSD"):
    return TradeIntent(symbol=symbol, side=Side.BUY, entry=1.2000,
                       stop=1.1950, pip=0.0001, pip_value_per_lot=10.0)


# --- profile coherence ------------------------------------------------------


def test_profiles_are_internally_consistent():
    for profile in PROFILES.values():
        engine = profile.build()
        assert engine.kill_switch.max_total_drawdown_fraction == \
            approx(profile.max_drawdown)
        assert engine.governor.hard_limit == approx(profile.max_drawdown)
        assert engine.max_risk_per_trade == approx(profile.max_risk_per_trade)
        # The taper must begin well before the hard stop, or it never acts.
        assert engine.governor.soft_limit < engine.governor.hard_limit / 2


def test_conservative_is_tighter_than_balanced_on_every_axis():
    c, b = CONSERVATIVE_10PCT.build(), BALANCED_20PCT.build()
    assert c.max_risk_per_trade < b.max_risk_per_trade
    assert c.kelly_fraction_used < b.kelly_fraction_used
    assert c.budget.max_total_risk_fraction < b.budget.max_total_risk_fraction
    assert c.budget.max_concurrent_positions < b.budget.max_concurrent_positions
    assert c.kill_switch.max_consecutive_losses < \
        b.kill_switch.max_consecutive_losses
    assert c.governor.soft_limit < b.governor.soft_limit


def test_lookup_rejects_typos():
    assert risk_profile("conservative-10pct") is CONSERVATIVE_10PCT
    with pytest.raises(ValueError, match="unknown risk profile"):
        risk_profile("reckless")


def test_build_returns_independent_engines():
    a, b = CONSERVATIVE_10PCT.build(), CONSERVATIVE_10PCT.build()
    a.governor.observe(50_000)
    assert b.governor.peak_equity == 0.0


# --- the ceiling holds ------------------------------------------------------


def test_ten_percent_ceiling_survives_an_unbroken_losing_run():
    """The case the limit exists for: nothing but maximum-size losses."""
    system = TradingSystem(starting_equity=10_000,
                           risk=CONSERVATIVE_10PCT.build())
    system.register(_validated())
    day = datetime(2026, 1, 1, tzinfo=timezone.utc)

    equity = 10_000.0
    for _ in range(400):
        system.mark(equity, balance=equity, now=day)
        plan = system.plan("proven", _intent())
        if not plan.approved:
            day += timedelta(days=1)      # a new session clears daily trips
            system.mark(equity, balance=equity, now=day)
            plan = system.plan("proven", _intent())
            if not plan.approved:
                break
        # Every trade is a full stop-out at the sized risk.
        loss = equity * plan.risk_fraction
        equity -= loss
        system.record_close("EURUSD", -loss)

    drawdown = (10_000 - equity) / 10_000
    assert drawdown <= 0.105, f"ceiling breached: {drawdown:.2%}"


def test_balanced_profile_holds_its_own_ceiling():
    system = TradingSystem(starting_equity=10_000,
                           risk=BALANCED_20PCT.build())
    system.register(_validated())
    day = datetime(2026, 1, 1, tzinfo=timezone.utc)
    equity = 10_000.0
    for _ in range(400):
        system.mark(equity, balance=equity, now=day)
        plan = system.plan("proven", _intent())
        if not plan.approved:
            day += timedelta(days=1)
            system.mark(equity, balance=equity, now=day)
            plan = system.plan("proven", _intent())
            if not plan.approved:
                break
        loss = equity * plan.risk_fraction
        equity -= loss
        system.record_close("EURUSD", -loss)
    assert (10_000 - equity) / 10_000 <= 0.205


def test_conservative_stops_sooner_than_balanced():
    def run(profile):
        system = TradingSystem(starting_equity=10_000, risk=profile.build())
        system.register(_validated())
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        equity, trades = 10_000.0, 0
        for _ in range(400):
            system.mark(equity, balance=equity, now=day)
            plan = system.plan("proven", _intent())
            if not plan.approved:
                day += timedelta(days=1)
                system.mark(equity, balance=equity, now=day)
                plan = system.plan("proven", _intent())
                if not plan.approved:
                    break
            loss = equity * plan.risk_fraction
            equity -= loss
            trades += 1
            system.record_close("EURUSD", -loss)
        return (10_000 - equity) / 10_000, trades

    cons_dd, _ = run(CONSERVATIVE_10PCT)
    bal_dd, _ = run(BALANCED_20PCT)
    assert cons_dd < bal_dd


def test_conservative_sizes_smaller_for_the_same_edge():
    strong = tuple([2.0] * 6 + [-1.0] * 4)
    cons = TradingSystem(starting_equity=10_000,
                         risk=CONSERVATIVE_10PCT.build())
    bal = TradingSystem(starting_equity=10_000, risk=BALANCED_20PCT.build())
    for s in (cons, bal):
        s.register(_validated(strong))
        s.mark(10_000)
    a = cons.plan("proven", _intent())
    b = bal.plan("proven", _intent())
    assert a.approved and b.approved
    assert a.risk_fraction < b.risk_fraction
    assert a.lots < b.lots


# --- partial exits ----------------------------------------------------------


def test_partial_plan_validation():
    with pytest.raises(ValueError, match="increasing R order"):
        PartialPlan(legs=(PartialLeg(2.0, 0.3), PartialLeg(1.0, 0.3))).validate()
    with pytest.raises(ValueError, match="less than 1"):
        PartialPlan(legs=(PartialLeg(1.0, 0.6),
                          PartialLeg(2.0, 0.5))).validate()
    with pytest.raises(ValueError):
        PartialLeg(1.0, 0.0).validate()
    with pytest.raises(ValueError):
        PartialLeg(-1.0, 0.5).validate()
    HALF_AT_1R.validate()


def test_partials_truncate_the_left_tail():
    """Banking half at 1R and moving to breakeven caps the downside at ~0."""
    import numpy as np
    import pandas as pd

    # A bar path that reaches +1R, then reverses hard through the entry.
    entry, stop = 1.2000, 1.1900        # 100 pip risk
    highs = [1.2050, 1.2110, 1.2050, 1.1990, 1.1890]
    lows = [1.1990, 1.2040, 1.1980, 1.1890, 1.1800]
    bars = pd.DataFrame({
        "open": [entry] * 5, "close": [entry] * 5,
        "high": highs, "low": lows, "end": range(5),
    })
    bars = pd.concat([bars.iloc[[0]], bars], ignore_index=True)

    from mt5_ai_bridge.partial_exits import simulate_with_partials
    flat = simulate_with_partials(bars, 0, 1, entry, stop, 3.0, 10,
                                  PartialPlan())
    part = simulate_with_partials(bars, 0, 1, entry, stop, 3.0, 10, HALF_AT_1R)

    assert flat.gross_r == approx(-1.0, abs=0.01)
    assert part.legs_filled == 1
    assert part.gross_r > flat.gross_r      # the left tail is truncated


def test_partial_costs_are_charged_per_leg():
    """Each scale-out crosses the spread again -- that is why it is not free."""
    import pandas as pd
    from mt5_ai_bridge.partial_exits import simulate_with_partials

    entry, stop = 1.2000, 1.1900
    bars = pd.DataFrame({
        "open": [entry] * 6, "close": [1.2300] * 6,
        "high": [1.2050, 1.2110, 1.2200, 1.2310, 1.2320, 1.2330],
        "low": [1.1990, 1.2040, 1.2100, 1.2200, 1.2300, 1.2310],
        "end": range(6),
    })
    plain = simulate_with_partials(bars, 0, 1, entry, stop, 3.0, 10,
                                   PartialPlan(), cost_r=0.05)
    with_leg = simulate_with_partials(bars, 0, 1, entry, stop, 3.0, 10,
                                      HALF_AT_1R, cost_r=0.05)
    assert plain.cost_r == approx(0.05)
    assert with_leg.cost_r == approx(0.10)   # two fills, two crossings


def test_summarise_reports_drawdown_and_expectancy():
    from mt5_ai_bridge.partial_exits import TradeOutcome
    outcomes = [TradeOutcome(None, i, 1, r, 0.0, 0, "x")
                for i, r in enumerate([1.0, -1.0, 2.0, -1.0, -1.0, 1.5])]
    stats = summarise_outcomes(outcomes, risk_fraction=0.01)
    assert stats["trades"] == 6
    assert stats["expectancy_r"] == approx(0.25, abs=1e-9)
    assert stats["max_drawdown_pct"] > 0
    assert stats["profit_factor"] > 1


def test_summarise_handles_an_empty_run():
    stats = summarise_outcomes([])
    assert stats["trades"] == 0
    assert stats["max_drawdown_pct"] == 0.0
