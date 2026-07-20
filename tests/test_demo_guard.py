"""Demo-account safety guard: never auto-trade a REAL account by default."""

from types import SimpleNamespace

from mt5_ai_bridge.app import run, _account_kind, _is_real_account
from mt5_ai_bridge.enums import Mode, Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.strategy import Decision
from tests.fakes import (FakeMT5Client, make_order_result, make_symbol_info,
                         make_settings, make_tick)


def _rates(n=60):
    return [{"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
            for i in range(n)]


def _acct(trade_mode):
    return SimpleNamespace(balance=10000, equity=10000, margin=0,
                           margin_free=10000, profit=0, login=1,
                           trade_mode=trade_mode)


def _client(trade_mode):
    return FakeMT5Client(account=_acct(trade_mode), positions=[],
                         tick=make_tick(bid=1.2343, ask=1.2345),
                         symbol_info=make_symbol_info(), rates=_rates(),
                         order_result=make_order_result(order=777))


def _buy(_m):
    return Decision(Signal.BUY, "buy", 0.6)


def test_helpers():
    assert _account_kind(SimpleNamespace(trade_mode=0)) == "DEMO"
    assert _account_kind(SimpleNamespace(trade_mode=2)) == "REAL"
    assert _account_kind(SimpleNamespace()) == "UNKNOWN"
    assert _is_real_account(SimpleNamespace(trade_mode=2)) is True
    assert _is_real_account(SimpleNamespace(trade_mode=0)) is False


def test_real_account_blocks_auto_trading(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client(trade_mode=2)  # REAL
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, require_demo=True),
        client=client, journal=Journal(db), strategy_fn=_buy, max_iterations=1)
    assert client.sent_requests == []  # no trades on a real account


def test_demo_account_allows_auto_trading(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client(trade_mode=0)  # DEMO
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, require_demo=True),
        client=client, journal=Journal(db), strategy_fn=_buy, max_iterations=1)
    assert len(client.sent_requests) >= 1


def test_require_demo_false_overrides(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client(trade_mode=2)  # REAL but override off
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, require_demo=False),
        client=client, journal=Journal(db), strategy_fn=_buy, max_iterations=1)
    assert len(client.sent_requests) >= 1
