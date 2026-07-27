"""End-to-end wiring test: one loop iteration with everything injected."""

from unittest.mock import patch

from mt5_ai_bridge.app import (_bot_thinking, _manage_positions, account_snapshot,
                               connect, make_strategy, make_trade_manager, run)
from mt5_ai_bridge.enums import Mode, Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.reasoning import ReasoningStrategy
from mt5_ai_bridge.strategy import Decision, evaluate_strategy
from mt5_ai_bridge.trade_agent import TradeAction
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_position, make_settings, make_symbol_info, make_tick)


class _RecJournal:
    """Minimal journal double capturing log_order calls."""

    def __init__(self):
        self.orders = []

    def log_order(self, *a, **k):
        self.orders.append(a)
        return len(self.orders)


def test_manage_positions_runs_deterministic_breakeven_floor_without_agent():
    # +25 pips on a buy, trigger 15 -> floor moves SL to entry, no agent needed.
    pos = make_position(ticket=1, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                        price_open=1.2700, price_current=1.2725, sl=0.0, volume=0.10)
    client = FakeMT5Client(positions=[pos], tick=make_tick(),
                           symbol_info=make_symbol_info(), order_result=make_order_result())
    settings = make_settings(symbols=("GBPUSD",), mode=Mode.AUTO,
                             breakeven_trigger_pips=15, trade_manager=False)
    _manage_positions(client, _RecJournal(), settings, tm_agent=None)
    sltp = [r for r in client.sent_requests if r["action"] == client.TRADE_ACTION_SLTP]
    assert sltp and sltp[-1]["sl"] == 1.2700


def test_manage_positions_agent_exit_closes_full_position(monkeypatch):
    pos = make_position(ticket=2, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                        price_open=1.2700, price_current=1.2705, sl=0.0, volume=0.10)
    client = FakeMT5Client(positions=[pos], tick=make_tick(bid=1.2705, ask=1.2707),
                           symbol_info=make_symbol_info(), order_result=make_order_result())
    settings = make_settings(symbols=("GBPUSD",), mode=Mode.AUTO, trade_manager=True)
    monkeypatch.setattr("mt5_ai_bridge.app.market_snapshot",
                        lambda *a, **k: {"rsi_14": 40})
    agent = lambda ctx: TradeAction("EXIT", 0.0, 0.9, "reversal")  # noqa: E731
    _manage_positions(client, _RecJournal(), settings, tm_agent=agent)
    deals = [r for r in client.sent_requests if r["action"] == client.TRADE_ACTION_DEAL]
    assert deals and deals[-1]["position"] == 2 and deals[-1]["volume"] == 0.10


def test_manage_positions_agent_partial_banks_bounded_fraction(monkeypatch):
    pos = make_position(ticket=3, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                        price_open=1.2700, price_current=1.2705, sl=0.0, volume=0.10)
    client = FakeMT5Client(positions=[pos], tick=make_tick(bid=1.2705, ask=1.2707),
                           symbol_info=make_symbol_info(), order_result=make_order_result())
    settings = make_settings(symbols=("GBPUSD",), mode=Mode.AUTO, trade_manager=True,
                             max_partial_fraction=0.5)
    monkeypatch.setattr("mt5_ai_bridge.app.market_snapshot", lambda *a, **k: {})
    agent = lambda ctx: TradeAction("PARTIAL", 0.5, 0.9, "cooling")  # noqa: E731
    _manage_positions(client, _RecJournal(), settings, tm_agent=agent)
    deals = [r for r in client.sent_requests if r["action"] == client.TRADE_ACTION_DEAL]
    assert deals and deals[-1]["volume"] == 0.05


def test_manage_positions_noop_in_read_only():
    pos = make_position(ticket=4, price_open=1.2700, price_current=1.2740, volume=0.10)
    client = FakeMT5Client(positions=[pos], tick=make_tick(),
                           symbol_info=make_symbol_info(), order_result=make_order_result())
    settings = make_settings(symbols=("GBPUSD",), mode=Mode.READ_ONLY, trade_manager=True)
    _manage_positions(client, _RecJournal(), settings, tm_agent=lambda ctx: TradeAction("EXIT"))
    assert client.sent_requests == []


def test_make_trade_manager_off_by_default_on_when_enabled():
    assert make_trade_manager(make_settings()) is None
    agent = make_trade_manager(make_settings(trade_manager=True))
    assert agent is not None


def test_entry_gate_confirms_veto_and_fail_open():
    from mt5_ai_bridge.app import _entry_gate_ok
    # No gate -> always allowed (fail-open).
    ok, _ = _entry_gate_ok(None, {"rsi_14": 60}, Signal.BUY)
    assert ok is True
    # Agent agreeing with the intended side -> confirm.
    agree = lambda snap: Decision(Signal.BUY, "confluence up", 0.8)  # noqa: E731
    ok, reason = _entry_gate_ok(agree, {"rsi_14": 60}, Signal.BUY)
    assert ok is True and "confluence up" in reason
    # Agent disagreeing (WAIT or opposite) -> veto.
    wait = lambda snap: Decision(Signal.WAIT, "chop", 0.2)           # noqa: E731
    ok, reason = _entry_gate_ok(wait, {"rsi_14": 50}, Signal.BUY)
    assert ok is False and "WAIT" in reason
    opp = lambda snap: Decision(Signal.SELL, "bearish", 0.9)         # noqa: E731
    ok, reason = _entry_gate_ok(opp, {"rsi_14": 40}, Signal.BUY)
    assert ok is False
    # Empty snapshot -> allow (fail-open).
    assert _entry_gate_ok(agree, None, Signal.BUY)[0] is True


def test_entry_gate_relays_the_engine_proposal_to_the_analyst():
    """The engine's proposed side + its confluence reasoning are passed verbatim
    into the analyst's context, so it confirms/vetoes THAT specific trade."""
    from mt5_ai_bridge.app import _entry_gate_ok
    seen = {}

    def capture(ctx):
        seen.update(ctx)
        return Decision(Signal.BUY, "agrees", 0.8)

    _entry_gate_ok(capture, {"rsi_14": 61, "symbol": "GBPUSD"}, Signal.BUY,
                   proposed_reason="Bull confluence: ema20/50 trend, macd cross")
    assert seen["proposed_side"] == "BUY"
    assert seen["proposed_reason"] == "Bull confluence: ema20/50 trend, macd cross"
    assert seen["rsi_14"] == 61                    # original snapshot preserved


def test_make_entry_gate_off_by_default_on_when_enabled():
    from mt5_ai_bridge.app import make_entry_gate
    assert make_entry_gate(make_settings()) is None
    gate = make_entry_gate(make_settings(entry_gate=True))
    assert gate is not None


def test_position_context_carries_shared_entry_read():
    """The manager sees the SAME confluence engine's current verdict, and
    whether it supports or opposes the open position."""
    from mt5_ai_bridge.app import _position_context
    client = FakeMT5Client(tick=make_tick(bid=1.2800, ask=1.2802),
                           symbol_info=make_symbol_info())
    settings = make_settings(reasoning_threshold=0.5)
    # A clean BULL snapshot (all confluence up) on a BUY position -> supports.
    bull = {"ema_20": 1.281, "ema_50": 1.279, "ema_200": 1.270, "close": 1.282,
            "rsi_14": 62, "macd": 0.6, "macd_signal": 0.2, "macd_hist": 0.4}
    pos = make_position(ticket=1, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                        price_open=1.2750, price_current=1.2800)
    ctx = _position_context(client, settings, pos, 0.0001, bull)
    assert ctx["entry_read"]["signal"] == "BUY"
    assert ctx["read_vs_position"] == "supports"
    # Same bull read but a SELL position -> the engine now opposes it (reversal).
    sell_pos = make_position(ticket=2, ptype=FakeMT5Client.POSITION_TYPE_SELL,
                             price_open=1.2850, price_current=1.2800)
    ctx2 = _position_context(client, settings, sell_pos, 0.0001, bull)
    assert ctx2["read_vs_position"] == "opposes"


def test_desk_note_is_one_line_with_purpose_and_thought():
    from mt5_ai_bridge.app import _desk_note
    off = make_settings(trade_manager=False)
    on = make_settings(trade_manager=True)          # backend defaults to ollama
    # Disabled -> nothing appended.
    assert _desk_note(off, [{"symbol": "GBPUSD", "action": "EXIT"}]) == ""
    # Enabled, no trades -> states purpose + idle, single line.
    assert _desk_note(on, []) == "desk[ollama] idle — no open trades"
    # Enabled, all holding -> surfaces a held position's live thought.
    holding = [{"symbol": "GBPUSD", "action": "HOLD",
                "reason": "engine still supports long, momentum strong"},
               {"symbol": "EURUSD", "action": "HOLD", "reason": "trend intact"}]
    note = _desk_note(on, holding)
    assert note.startswith("desk[ollama] holding 2 — GBPUSD: ")
    assert "engine still supports long" in note and "\n" not in note
    # Enabled, acting -> names symbol, action, and its live reason, single line.
    acting = [{"symbol": "XAUUSD", "action": "EXIT",
               "reason": "engine opposes position, momentum weakening"},
              {"symbol": "GBPUSD", "action": "PARTIAL", "reason": "cooling"}]
    note = _desk_note(on, acting)
    assert note.startswith("desk[ollama] XAUUSD EXIT +1: ")
    assert "\n" not in note


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


def test_thinking_surfaces_the_analyst_agent_and_its_words():
    """The thinking payload carries the Analyst's own decision (who is deciding
    plus its reason/confidence) so the dashboard can show 'the agent thinking'."""
    def snapshot(_client, _symbol, timeframe, _bars):
        return {"tf": timeframe, "close": 1.10, "ema_200": 1.20}

    def strategy(market):
        return Decision(Signal.BUY, "model says up on GBPUSD", 0.82)

    settings = make_settings(multi_book=True, timeframe="M15", strategy="ollama")
    with patch("mt5_ai_bridge.app.market_snapshot", side_effect=snapshot):
        thinking = _bot_thinking(_client(), settings, strategy, symbol="GBPUSD")

    a = thinking["analyst"]
    assert a["agent"] == "ollama"
    assert a["signal"] == Signal.BUY.value
    assert a["confidence"] == 0.82
    assert a["reason"] == "model says up on GBPUSD"      # the agent's own words
    assert a["blurb"]                                     # human description present
    # Each timeframe read also carries the agent's own sentence.
    assert all(row["agent_reason"] == "model says up on GBPUSD"
               for row in thinking["timeframes"])
