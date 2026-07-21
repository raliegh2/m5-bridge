"""AUTO must fail closed unless MT5 explicitly reports a demo account."""

from mt5_ai_bridge.app import _account_kind, _is_demo_account, run
from mt5_ai_bridge.enums import Mode, Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.strategy import Decision
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_settings, make_symbol_info, make_tick)


def _rates(n=250):
    return [{"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
            for i in range(n)]


def _client(trade_mode):
    return FakeMT5Client(
        account=make_account(trade_mode=trade_mode), positions=[],
        tick=make_tick(), symbol_info=make_symbol_info(), rates=_rates(),
        order_result=make_order_result(),
    )


def _buy(_market):
    return Decision(Signal.BUY, "test", 0.9)


def test_account_kind_and_demo_detection():
    assert _account_kind(make_account(trade_mode=0)) == "DEMO"
    assert _account_kind(make_account(trade_mode=2)) == "REAL"
    assert _is_demo_account(make_account(trade_mode=0)) is True
    assert _is_demo_account(make_account(trade_mode=2)) is False


def test_auto_real_account_is_blocked(tmp_path):
    client = _client(2)
    db = str(tmp_path / "real.db")
    run(settings=make_settings(mode=Mode.AUTO, require_demo=True, db_path=db),
        client=client, journal=Journal(db), strategy_fn=_buy, max_iterations=1)
    assert client.sent_requests == []


def test_auto_demo_account_can_trade(tmp_path):
    client = _client(0)
    db = str(tmp_path / "demo.db")
    run(settings=make_settings(mode=Mode.AUTO, require_demo=True, db_path=db),
        client=client, journal=Journal(db), strategy_fn=_buy, max_iterations=1)
    assert client.sent_requests


def test_auto_unknown_account_fails_closed(tmp_path):
    client = _client(None)
    db = str(tmp_path / "unknown.db")
    run(settings=make_settings(mode=Mode.AUTO, require_demo=True, db_path=db),
        client=client, journal=Journal(db), strategy_fn=_buy, max_iterations=1)
    assert client.sent_requests == []
