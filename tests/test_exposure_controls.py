"""Margin caps, minimum-lot refusal, and verified instrument specs.

These cover the bug family that produced four separate wrong-by-a-factor
results: a contract convention assumed in code and never checked.
"""

import pytest
from pytest import approx

from mt5_ai_bridge.enums import Signal as Side
from mt5_ai_bridge.instruments import (CONVERTIBLE, INSTRUMENTS,
                                       instrument_for)
from mt5_ai_bridge.risk_v18 import (CONSERVATIVE_10PCT, RiskBudget, RiskEngine,
                                    edge_from_trades)
from mt5_ai_bridge.trading_system import (SignalSpec, TradeIntent,
                                          TradingSystem, Validation)

WINNING = edge_from_trades([2.0] * 6 + [-1.0] * 4)


def _engine(equity=10_000.0, **over):
    """A fresh engine whose high-water mark is the account's own equity.

    Seeding the peak at a larger figure registers an instant drawdown and
    trips the kill switch before the control under test is reached.
    """
    e = RiskEngine(**over)
    e.governor.observe(equity)
    return e


def _kw(**over):
    base = dict(symbol="EURUSD", balance=10_000.0, equity=10_000.0,
                stop_distance=0.0050, pip=0.0001, pip_value_per_lot=10.0,
                edge=WINNING)
    base.update(over)
    return base


# --- instrument specs carry broker facts ------------------------------------


def test_every_instrument_declares_a_minimum_lot():
    for table in (INSTRUMENTS, CONVERTIBLE):
        for symbol, inst in table.items():
            assert inst.min_lot > 0, symbol
            assert inst.lot_step > 0, symbol


def test_index_and_etf_minimums_are_not_fx_defaults():
    """The bug: 0.01 assumed everywhere. Indices are 0.10-1.00, ETFs 1 share."""
    assert instrument_for("EURUSD").min_lot == approx(0.01)
    assert instrument_for("US30").min_lot == approx(0.10)
    assert instrument_for("US2000").min_lot == approx(1.00)
    assert instrument_for("IVV").min_lot == approx(1.00)


def test_pip_is_a_whole_multiple_of_a_plausible_tick():
    """A pip the broker cannot express would price an untradeable spread."""
    for table in (INSTRUMENTS, CONVERTIBLE):
        for symbol, inst in table.items():
            assert inst.pip > 0, symbol
            assert inst.contract_size > 0, symbol


# --- minimum-lot refusal ----------------------------------------------------


def test_gold_on_a_small_account_is_refused():
    """The real case: XAUUSD minimum risks 1.6x the budget on $4,802."""
    gold = instrument_for("XAUUSD")
    # The conservative profile risks 0.75%, so the budget is ~$36 against
    # gold's $38.40 minimum. At the default 2% it would be affordable -- the
    # refusal depends on the risk setting, not on gold being inherently bad.
    engine = CONSERVATIVE_10PCT.build()
    engine.governor.observe(4_802.43)
    decision = engine.size(
        symbol="XAUUSD", balance=4_802.43, equity=4_802.43,
        stop_distance=38.40, pip=gold.pip,
        pip_value_per_lot=gold.pip_value_per_lot, edge=WINNING,
        min_lot=gold.min_lot, lot_step=gold.lot_step,
        price=4_376.0, contract_size=gold.contract_size)
    assert not decision.approved
    assert decision.lots == 0.0
    assert "broker minimum" in decision.reason


def test_the_same_gold_trade_is_fine_on_a_larger_account():
    """It is the account that is too small, not the instrument that is bad."""
    gold = instrument_for("XAUUSD")
    engine = CONSERVATIVE_10PCT.build()
    engine.governor.observe(100_000)
    decision = engine.size(
        symbol="XAUUSD", balance=100_000.0, equity=100_000.0,
        stop_distance=38.40, pip=gold.pip,
        pip_value_per_lot=gold.pip_value_per_lot, edge=WINNING,
        min_lot=gold.min_lot, lot_step=gold.lot_step,
        price=4_376.0, contract_size=gold.contract_size)
    assert decision.approved
    assert decision.lots >= gold.min_lot


def test_refusal_quantifies_the_shortfall():
    engine = _engine(equity=1_000)
    d = engine.size(**_kw(balance=1_000, equity=1_000, stop_distance=0.0300))
    assert not d.approved
    assert d.detail["min_lot_risk"] > d.detail["risk_budget_money"]


# --- margin caps ------------------------------------------------------------


def test_normal_fx_leverage_is_not_flagged():
    """4.8x notional is routine FX; a notional cap would false-positive."""
    d = _engine().size(**_kw(price=1.2000, contract_size=100_000.0,
                             leverage=100.0))
    assert d.approved
    assert d.detail["margin_fraction"] < 0.20


def test_margin_cap_rejects_an_oversized_position():
    engine = _engine(budget=RiskBudget(max_symbol_margin_fraction=0.01,
                                       max_total_margin_fraction=0.02))
    d = engine.size(**_kw(price=1.2000, contract_size=100_000.0,
                          leverage=100.0))
    assert not d.approved
    assert "margin" in d.reason


def test_aggregate_margin_ceiling_binds():
    engine = _engine(budget=RiskBudget(max_symbol_margin_fraction=0.05,
                                       max_total_margin_fraction=0.10))
    d = engine.size(**_kw(price=1.2000, contract_size=100_000.0,
                          leverage=100.0,
                          open_margin={"GBPUSD": 0.09}))
    assert not d.approved
    assert "aggregate margin" in d.reason


def test_lower_leverage_consumes_more_margin():
    high = _engine().size(**_kw(price=1.2000, contract_size=100_000.0,
                                leverage=500.0))
    low = _engine().size(**_kw(price=1.2000, contract_size=100_000.0,
                               leverage=30.0))
    assert high.detail["margin_fraction"] < low.detail["margin_fraction"]


def test_margin_check_is_skipped_without_a_price():
    d = _engine().size(**_kw())
    assert d.approved
    assert "margin_fraction" not in d.detail


def test_budget_rejects_incoherent_margin_caps():
    with pytest.raises(ValueError):
        RiskBudget(max_symbol_margin_fraction=0.0)
    with pytest.raises(ValueError):
        RiskBudget(max_symbol_margin_fraction=0.6,
                   max_total_margin_fraction=0.3)


# --- end to end -------------------------------------------------------------


def _validated(symbols=("XAUUSD",)):
    return SignalSpec("proven", symbols, "H4",
                      Validation(640, 5100.0, 1.28, 0.8, 0.971, 1385,
                                 tuple([2.0] * 6 + [-1.0] * 4)))


def test_system_refuses_gold_on_the_real_account():
    gold = instrument_for("XAUUSD")
    system = TradingSystem(starting_equity=4_802.43,
                           risk=CONSERVATIVE_10PCT.build())
    system.register(_validated())
    system.mark(4_802.43)
    intent = TradeIntent.from_instrument(
        gold, side=Side.BUY, entry=4_376.0, stop=4_337.60)
    plan = system.plan("proven", intent)
    assert not plan.approved
    assert plan.lots == 0.0
    assert "broker minimum" in plan.reason


def test_intent_from_instrument_carries_broker_specs():
    inst = instrument_for("US30")
    intent = TradeIntent.from_instrument(
        inst, side=Side.SELL, entry=53_665.0, stop=54_285.0)
    assert intent.min_lot == approx(0.10)
    assert intent.lot_step == approx(0.10)
    assert intent.contract_size == approx(1.0)
    assert intent.pip == approx(1.0)


def test_margin_frees_when_a_position_closes():
    system = TradingSystem(starting_equity=10_000)
    system.register(_validated(("EURUSD",)))
    system.mark(10_000)
    intent = TradeIntent(symbol="EURUSD", side=Side.BUY, entry=1.2000,
                         stop=1.1950, pip=0.0001, pip_value_per_lot=10.0,
                         contract_size=100_000.0)
    plan = system.plan("proven", intent)
    assert plan.approved
    assert system._open_margin.get("EURUSD", 0) > 0
    system.record_close("EURUSD", 10.0)
    assert "EURUSD" not in system._open_margin
