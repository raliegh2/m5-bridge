"""End-to-end wiring test: one loop iteration with everything injected."""

from unittest.mock import patch

from mt5_ai_bridge.app import (_bot_thinking, account_snapshot, connect,
                               make_strategy, run)
from mt5_ai_bridge.enums import Mode, Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.reasoning import ReasoningStrategy
from mt5_ai_bridge.strategy import Decision, evaluate_strategy
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_settings, make_symbol_info, make_tick)


def _rates(n=250):
    return [
        {"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
         "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
        for i in range(n)
    ]


def _client(**kw):
    defaults = dict(
        account=make_account(balance=10000, equity=10000),
        positions=[], tick=make_tick(), symbol_info=make_symbol_info(),
        rates=_rates(), order_result=make_order_result(),
    )
    defaults.update(kw)
    return FakeMT5Client(**defaults)


def test_make_strategy_selects_by_name():
    assert make_strategy(make_settings(strategy="trend")) is evaluate_strategy
    assert isinstance(make_strategy(make_settings(strategy="reasoning")),
                      ReasoningStrategy)


def test_make_strategy_passes_veto_thresholds():
    strat = make_strategy(make_settings(strategy="reasoning", rsi_overbought=100,
                                        rsi_oversold=0))
    assert strat.config.rsi_overbought == 100
    assert strat.config.rsi_oversold == 0


def test_connect_requires_credentials():
    client = FakeMT5Client()
    try:
        connect(client, make_settings(login=None, password=None, server=None))
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "credentials" in str(e).lower()


def test_account_snapshot_shape():
    snap = account_snapshot(_client(), "GBPUSD")
    assert snap["symbol"] == "GBPUSD"
    assert snap["open_positions"] == 0
    assert "balance" in snap


def test_read_only_iteration_journals_signal_and_risk(tmp_path):
    client = _client()
    db = str(tmp_path / "j.db")
    run(settings=make_settings(db_path=db), client=client,
        journal=Journal(db), max_iterations=1)

    j = Journal(db)
    signals = j.recent_signals()
    j.close()
    assert len(signals) == 1
    assert signals[0]["symbol"] == "GBPUSD"


def test_read_only_never_sends_orders(tmp_path):
    client = _client()
    db = str(tmp_path / "j.db")
    run(settings=make_settings(mode=Mode.READ_ONLY, db_path=db), client=client,
        journal=Journal(db), max_iterations=2)
    assert client.sent_requests == []


def test_reasoning_strategy_runs_in_loop(tmp_path):
    client = _client()
    db = str(tmp_path / "j.db")
    run(settings=make_settings(strategy="reasoning", db_path=db), client=client,
        journal=Journal(db), max_iterations=1)
    j = Journal(db)
    assert len(j.recent_signals()) == 1
    j.close()


def test_thinking_waits_when_confirmation_timeframes_disagree():
    signals = {"M15": Signal.SELL, "M30": Signal.SELL,
               "H4": Signal.BUY, "D1": Signal.SELL}

    def snapshot(_client, _symbol, timeframe, _bars):
        return {"tf": timeframe, "close": 1.10, "ema_200": 1.20}

    def strategy(market):
        sig = signals[market["tf"]]
        return Decision(sig, "test", 0.8)

    settings = make_settings(multi_book=True, timeframe="M15")
    with patch("mt5_ai_bridge.app.market_snapshot", side_effect=snapshot):
        thinking = _bot_thinking(_client(), settings, strategy)

    assert thinking["aligned"] is False
    assert thinking["setup_valid"] is False
    assert thinking["bias"] == "NONE"
    assert [row["tf"] for row in thinking["timeframes"]] == [
        "M15", "M30", "H4", "D1"]
    assert all(row["reason"] for row in thinking["timeframes"])
    assert [engine["name"] for engine in thinking["engines"]] == [
        "Intraday", "Swing"]
    assert not any(engine["ready"] for engine in thinking["engines"])
