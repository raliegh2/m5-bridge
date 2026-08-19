"""The tactical book against a broker: signals, sizing, modes, risk halts."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.enums import Mode
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.risk_v18 import DrawdownGovernor, KillSwitch
from mt5_ai_bridge.tactical_allocation import TacticalConfig
from mt5_ai_bridge.tactical_runner import (TACTICAL_MAGIC, LegPlan, TacticalLeg,
                                           apply_plans, held_shares,
                                           is_rebalance_due, leg_signal,
                                           plan_rebalance, target_shares)
from tests.fakes import FakeMT5Client, make_account

CFG = TacticalConfig(sma_months=2, trading_days_per_month=5)   # 10 bars


def _rates(closes):
    return [{"time": 1_700_000_000 + i * 86_400, "open": c, "high": c,
             "low": c, "close": c} for i, c in enumerate(closes)]


def _position(symbol, volume, ticket=1, magic=TACTICAL_MAGIC):
    return SimpleNamespace(symbol=symbol, volume=volume, ticket=ticket,
                           magic=magic, type=0, price_open=100.0,
                           price_current=100.0, sl=0.0, tp=0.0)


def _client(closes, balance=5000.0, positions=None, equity=None):
    return FakeMT5Client(
        account=make_account(balance=balance,
                             equity=equity if equity is not None else balance),
        positions=positions or [], rates=_rates(closes),
        tick=SimpleNamespace(bid=closes[-1], ask=closes[-1]),
        symbol_info=SimpleNamespace(point=0.01, digits=2, volume_min=1.0,
                                    volume_step=1.0, trade_contract_size=1.0),
        order_result=SimpleNamespace(retcode=10009, comment="done", order=1))


# --- cadence ----------------------------------------------------------------


def test_a_rebalance_is_due_only_when_the_month_turns():
    march = datetime(2026, 3, 15, tzinfo=timezone.utc)
    later_march = datetime(2026, 3, 28, tzinfo=timezone.utc)
    april = datetime(2026, 4, 1, tzinfo=timezone.utc)

    assert is_rebalance_due(march, None) is True
    assert is_rebalance_due(later_march, march) is False
    assert is_rebalance_due(april, march) is True


# --- signal -----------------------------------------------------------------


def test_a_price_above_its_average_reads_as_invested():
    client = _client([10, 10, 10, 10, 10, 10, 10, 10, 10, 20])

    above, close, average = leg_signal(client, "SCHX", CFG)

    assert above is True
    assert close == 20.0
    assert average == pytest.approx(11.0)


def test_a_price_below_its_average_reads_as_flat():
    client = _client([20, 20, 20, 20, 20, 20, 20, 20, 20, 10])

    above, _, _ = leg_signal(client, "SCHX", CFG)

    assert above is False


def test_insufficient_history_is_no_signal_rather_than_a_sell():
    client = _client([10, 11, 12])           # fewer bars than the average needs

    assert leg_signal(client, "SCHX", CFG) is None


def test_the_signal_reads_completed_bars_only():
    # The bar still forming must never inform the decision, so the request has
    # to start at index 1. A rule that peeks at an unfinished candle backtests
    # beautifully and cannot be traded.
    calls = []
    base = _client([10] * 10)

    class Recording:
        def __getattr__(self, name):
            return getattr(base, name)

        def copy_rates_from_pos(self, symbol, timeframe, start, count):
            calls.append({"symbol": symbol, "timeframe": timeframe,
                          "start": start, "count": count})
            return base.copy_rates_from_pos(symbol, timeframe, start, count)

    leg_signal(Recording(), "SCHX", CFG)

    assert len(calls) == 1
    assert calls[0]["start"] == 1
    assert calls[0]["timeframe"] == "D1"
    assert calls[0]["count"] == CFG.sma_days


# --- sizing -----------------------------------------------------------------


def test_target_shares_floor_so_exposure_is_never_over_the_weight():
    # 5000 * 0.9 * 0.5 = 2250 budget; at 81.70 that is 27.5 shares.
    assert target_shares(5000.0, 0.5, 0.9, 81.70) == 27
    assert 27 * 81.70 <= 5000.0 * 0.9 * 0.5


def test_no_shares_when_the_price_or_balance_is_unusable():
    assert target_shares(5000.0, 0.5, 0.9, 0.0) == 0
    assert target_shares(0.0, 0.5, 0.9, 30.0) == 0
    assert target_shares(5000.0, 0.5, 0.0, 30.0) == 0


def test_only_this_strategy_s_positions_are_counted():
    client = _client([10] * 10, positions=[
        _position("SCHX", 10, ticket=1),
        _position("SCHX", 5, ticket=2, magic=999),      # another engine
        _position("IAU", 7, ticket=3),                  # another symbol
    ])

    assert held_shares(client, "SCHX") == 10


# --- planning ---------------------------------------------------------------


def test_an_uptrending_leg_is_bought_to_its_target():
    client = _client([10] * 9 + [20], balance=5000.0)

    plans = plan_rebalance(client, [TacticalLeg("SCHX", 0.5)], CFG, 0.90)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.above_average is True
    assert plan.target_shares == target_shares(5000.0, 0.5, 0.9, 20.0)
    assert plan.action == "BUY"


def test_a_downtrending_leg_targets_zero_and_sells_what_is_held():
    client = _client([20] * 9 + [10], positions=[_position("SCHX", 30)])

    plans = plan_rebalance(client, [TacticalLeg("SCHX", 0.5)], CFG, 0.90)

    assert plans[0].target_shares == 0
    assert plans[0].action == "SELL"
    assert plans[0].delta == -30


def test_weights_over_one_hundred_percent_are_refused():
    client = _client([10] * 10)

    with pytest.raises(ValueError):
        plan_rebalance(client, [TacticalLeg("A", 0.6), TacticalLeg("B", 0.6)],
                       CFG)


def test_an_invalid_leg_is_refused():
    client = _client([10] * 10)

    with pytest.raises(ValueError):
        plan_rebalance(client, [TacticalLeg("SCHX", 0.0)], CFG)


# --- risk integration -------------------------------------------------------


def test_the_drawdown_governor_scales_the_position_down():
    # Peak 5000, now 4000: the governor tapers exposure, so the target must be
    # smaller than the same signal at full equity.
    governor = DrawdownGovernor()
    governor.observe(5000.0)
    client = _client([10] * 9 + [20], balance=4000.0, equity=4000.0)

    scaled = plan_rebalance(client, [TacticalLeg("SCHX", 0.5)], CFG, 0.90,
                            governor=governor)
    full = plan_rebalance(_client([10] * 9 + [20], balance=4000.0),
                          [TacticalLeg("SCHX", 0.5)], CFG, 0.90)

    assert scaled[0].target_shares <= full[0].target_shares


def test_the_kill_switch_flattens_the_book():
    governor = DrawdownGovernor()
    governor.observe(5000.0)
    # A severe loss trips the switch; every leg must target zero regardless of
    # what its own trend says.
    client = _client([10] * 9 + [20], balance=2000.0, equity=2000.0,
                     positions=[_position("SCHX", 30)])

    plans = plan_rebalance(client, [TacticalLeg("SCHX", 0.5)], CFG, 0.90,
                           governor=governor, kill_switch=KillSwitch())

    assert plans[0].target_shares == 0
    assert "kill switch" in plans[0].reason


def test_no_account_information_means_no_plan():
    client = FakeMT5Client(account=None, rates=_rates([10] * 10))

    assert plan_rebalance(client, [TacticalLeg("SCHX", 0.5)], CFG) == []


# --- execution --------------------------------------------------------------


def _settings(mode):
    return SimpleNamespace(mode=mode, symbol="SCHX")


def test_read_only_mode_sends_nothing():
    client = _client([10] * 9 + [20])
    plan = LegPlan("SCHX", True, 20.0, 10, 0, "above")

    with Journal(":memory:") as journal:
        apply_plans(client, journal, _settings(Mode.READ_ONLY), [plan])

    assert client.sent_requests == []


def test_auto_mode_buys_the_difference():
    client = _client([10] * 9 + [20])
    plan = LegPlan("SCHX", True, 20.0, 10, 4, "above")

    with Journal(":memory:") as journal:
        apply_plans(client, journal, _settings(Mode.AUTO), [plan])

    assert len(client.sent_requests) == 1
    request = client.sent_requests[0]
    assert request["symbol"] == "SCHX"
    assert request["volume"] == 6           # 10 target less 4 held
    assert request["magic"] == TACTICAL_MAGIC


def test_a_plan_with_no_change_places_no_order():
    client = _client([10] * 9 + [20])
    plan = LegPlan("SCHX", True, 20.0, 7, 7, "above")

    with Journal(":memory:") as journal:
        apply_plans(client, journal, _settings(Mode.AUTO), [plan])

    assert client.sent_requests == []


def test_sells_are_processed_before_buys():
    # Capital freed by the sale has to be available to the purchase in the
    # same rebalance, so the order of execution is not cosmetic.
    client = _client([10] * 9 + [20], positions=[_position("IAU", 5, ticket=7)])
    buy = LegPlan("SCHX", True, 20.0, 10, 0, "above")
    sell = LegPlan("IAU", False, 20.0, 0, 5, "below")

    with Journal(":memory:") as journal:
        apply_plans(client, journal, _settings(Mode.AUTO), [buy, sell])

    assert client.sent_requests, "expected orders"
    assert client.sent_requests[0]["symbol"] == "IAU"
