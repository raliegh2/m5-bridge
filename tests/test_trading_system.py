"""The edge gate, and that it cannot be bypassed."""

import pytest
from pytest import approx

from mt5_ai_bridge.enums import Signal as Side
from mt5_ai_bridge.risk_v18 import RiskBudget, RiskEngine
from mt5_ai_bridge.trading_system import (EdgeGate, SignalSpec, TradeIntent,
                                          TradingSystem, Validation)


def _passing_validation(**over):
    base = dict(
        out_of_sample_trades=500,
        out_of_sample_profit=4_200.0,
        profit_factor=1.35,
        positive_fold_fraction=0.8,
        deflated_sharpe=0.97,
        n_trials=12,
        trade_profits=tuple([2.0] * 6 + [-1.0] * 4),
    )
    base.update(over)
    return Validation(**base)


def _spec(name="proven", **over):
    return SignalSpec(name=name, symbols=("EURUSD", "GBPUSD"),
                      timeframe="H4", validation=_passing_validation(**over))


def _intent(symbol="EURUSD", side=Side.BUY, entry=1.2000, stop=1.1950):
    return TradeIntent(symbol=symbol, side=side, entry=entry, stop=stop,
                       pip=0.0001, pip_value_per_lot=10.0)


# --- the gate ---------------------------------------------------------------


def test_a_fully_validated_signal_passes():
    result = EdgeGate().evaluate(_spec())
    assert result.passed
    assert result.failures == ()
    assert "PASS" in result.explain()


def test_a_signal_without_validation_is_blocked():
    result = EdgeGate().evaluate(SignalSpec("bare", ("EURUSD",), "H4"))
    assert not result.passed
    assert "no validation supplied" in result.failures


@pytest.mark.parametrize("field,bad,fragment", [
    ("out_of_sample_profit", -1.0, "positive out-of-sample profit"),
    ("profit_factor", 1.05, "profit factor"),
    ("out_of_sample_trades", 150, "OOS trades"),
    ("positive_fold_fraction", 0.4, "folds positive"),
    ("deflated_sharpe", 0.5, "deflated Sharpe"),
])
def test_each_gate_condition_blocks_independently(field, bad, fragment):
    result = EdgeGate().evaluate(_spec(**{field: bad}))
    assert not result.passed
    assert any(fragment in f for f in result.failures)


def test_the_deflated_sharpe_check_names_the_trial_count():
    """The trial count is the easiest number to understate, so surface it."""
    result = EdgeGate().evaluate(_spec(deflated_sharpe=0.2, n_trials=1379))
    assert any("1379 trials" in f for f in result.failures)


# --- intents ----------------------------------------------------------------


def test_intent_rejects_a_stop_on_the_wrong_side():
    with pytest.raises(ValueError, match="long stop"):
        _intent(side=Side.BUY, entry=1.2000, stop=1.2050).validate()
    with pytest.raises(ValueError, match="short stop"):
        _intent(side=Side.SELL, entry=1.2000, stop=1.1950).validate()


def test_intent_rejects_a_zero_stop_distance():
    with pytest.raises(ValueError, match="stop must differ"):
        _intent(entry=1.2000, stop=1.2000).validate()


def test_stop_distance_is_absolute():
    assert _intent(entry=1.2000, stop=1.1950).stop_distance == approx(0.0050)


# --- the system -------------------------------------------------------------


def test_a_validated_signal_produces_a_sized_order():
    system = TradingSystem(starting_equity=10_000)
    assert system.register(_spec()).passed
    plan = system.plan("proven", _intent())
    assert plan.approved
    assert plan.lots > 0
    assert 0 < plan.risk_fraction <= 0.02
    assert "EURUSD" in plan.describe()


def test_an_unvalidated_signal_gets_zero_size_not_a_small_one():
    """The central design rule, stated as a test."""
    system = TradingSystem(starting_equity=10_000)
    system.register(SignalSpec("unproven", ("EURUSD",), "H4"))
    plan = system.plan("unproven", _intent())
    assert not plan.approved
    assert plan.lots == 0.0
    assert "edge gate" in plan.reason


def test_there_is_no_way_to_trade_a_blocked_signal():
    """No override flag exists; the gate is structural."""
    system = TradingSystem(starting_equity=10_000)
    system.register(_spec("weak", deflated_sharpe=0.10))
    for _ in range(5):
        plan = system.plan("weak", _intent())
        assert not plan.approved
        assert plan.lots == 0.0


def test_an_unregistered_signal_cannot_trade():
    system = TradingSystem(starting_equity=10_000)
    plan = system.plan("ghost", _intent())
    assert not plan.approved
    assert "unknown signal" in plan.reason


def test_a_symbol_outside_the_validated_set_is_refused():
    """Evidence for EURUSD is not evidence for XAUUSD."""
    system = TradingSystem(starting_equity=10_000)
    system.register(_spec())
    plan = system.plan("proven", _intent(symbol="XAUUSD"))
    assert not plan.approved
    assert "not in" in plan.reason


def test_open_risk_accumulates_and_the_budget_binds():
    system = TradingSystem(
        starting_equity=10_000,
        risk=RiskEngine(budget=RiskBudget(max_total_risk_fraction=0.02,
                                          max_symbol_risk_fraction=0.02,
                                          max_currency_risk_fraction=0.99)))
    system.risk.governor.observe(10_000)
    system.register(_spec())
    first = system.plan("proven", _intent(symbol="EURUSD"))
    assert first.approved
    second = system.plan("proven", _intent(symbol="GBPUSD"))
    assert not second.approved
    assert "risk budget" in second.reason


def test_closing_a_trade_frees_its_risk_and_books_the_result():
    system = TradingSystem(starting_equity=10_000)
    system.register(_spec())
    system.plan("proven", _intent(symbol="EURUSD"))
    assert "EURUSD" in system.status().open_risk

    system.record_close("EURUSD", -50.0)
    assert "EURUSD" not in system.status().open_risk
    assert system.balance == approx(9_950.0)


def test_a_losing_run_stops_the_system():
    system = TradingSystem(starting_equity=10_000)
    system.register(_spec())
    system.mark(10_000)
    for _ in range(system.risk.kill_switch.max_consecutive_losses):
        system.record_close("EURUSD", -20.0)
    plan = system.plan("proven", _intent())
    assert not plan.approved
    assert "kill switch" in plan.reason


def test_drawdown_reduces_size_before_it_stops_trading():
    """A slow bleed across days tapers size; it does not trip the day limit.

    An 11% fall inside one session would hit the 2% daily kill switch first --
    which is the correct ordering, and why this test spreads the loss out.
    """
    from datetime import datetime, timezone

    system = TradingSystem(starting_equity=10_000)
    system.register(_spec())
    system.mark(10_000, now=datetime(2026, 8, 16, tzinfo=timezone.utc))
    full = system.plan("proven", _intent(symbol="EURUSD"))
    system.record_close("EURUSD", 0.0)

    # Same 11% drawdown from the peak, but reached over several days, so each
    # day's loss stays inside the daily limit.
    for day, equity in enumerate((9_800, 9_500, 9_200, 8_900), start=17):
        system.mark(equity, balance=equity,
                    now=datetime(2026, 8, day, tzinfo=timezone.utc))

    tapered = system.plan("proven", _intent(symbol="EURUSD"))
    assert tapered.approved, tapered.reason
    assert tapered.risk_fraction < full.risk_fraction
    assert system.status().drawdown == approx(0.11, abs=0.005)


def test_status_and_report_describe_what_is_blocked_and_why():
    system = TradingSystem(starting_equity=10_000)
    system.register(_spec("good"))
    system.register(_spec("bad", deflated_sharpe=0.01))

    status = system.status()
    assert status.tradeable_signals == ["good"]
    assert "bad" in status.blocked_signals
    assert "deflated Sharpe" in status.blocked_signals["bad"]

    report = system.report()
    assert "good" in report and "bad" in report
    assert "Allocating to" in report


def test_status_says_plainly_when_nothing_is_tradeable():
    system = TradingSystem(starting_equity=10_000)
    system.register(_spec("bad", deflated_sharpe=0.01))
    assert "nothing" in system.status().describe()


def test_plan_all_handles_several_signals_at_once():
    system = TradingSystem(starting_equity=10_000)
    system.register(_spec("good"))
    system.register(_spec("bad", profit_factor=1.0))
    plans = system.plan_all({
        "good": [_intent(symbol="EURUSD")],
        "bad": [_intent(symbol="GBPUSD")],
    })
    assert len(plans) == 2
    assert sum(1 for p in plans if p.approved) == 1


# --- the current repo state -------------------------------------------------


def test_the_repo_s_own_measured_signals_are_all_refused():
    """V15, V16 and V17 as measured: the system must allocate nothing.

    This is the honest end state of the investigation, pinned so a future
    change that quietly loosens a gate fails a test instead of a live account.
    """
    system = TradingSystem(starting_equity=10_000)
    system.register(SignalSpec("V15_momentum", ("XAUUSD",), "H4",
                               Validation(1006, 2217.49, 1.082, 0.6, 0.0,
                                          1379, (1.0, -1.0))))
    system.register(SignalSpec("V16_reversion", ("GBPUSD",), "H4",
                               Validation(2202, -145.48, 0.997, 0.2, 0.0,
                                          1379, (1.0, -1.0))))
    system.register(SignalSpec("V17_gated", ("GBPUSD", "EURUSD"), "H4",
                               Validation(670, -2835.13, 0.826, 0.2, 0.0,
                                          1379, (1.0, -1.0))))

    assert system.tradeable == []
    assert len(system.blocked) == 3
    for symbol, name in (("XAUUSD", "V15_momentum"),
                         ("GBPUSD", "V16_reversion")):
        plan = system.plan(name, _intent(symbol=symbol))
        assert not plan.approved
        assert plan.lots == 0.0
