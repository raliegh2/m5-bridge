import json

from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.ollama_strategy import OllamaStrategy, OllamaStrategyConfig
from mt5_ai_bridge.strategy import Decision


def _market(**overrides):
    base = {"ema_20": 1.30, "ema_50": 1.29, "close": 1.31, "rsi_14": 60,
            "macd": 0.5, "macd_signal": 0.2}
    base.update(overrides)
    return base


def _fake_transport(responses):
    """responses: list of payload dicts or Exception instances, consumed in order."""
    calls = []
    queue = list(responses)

    def transport(payload):
        calls.append(payload)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"message": {"role": "assistant", "content": json.dumps(item)}}

    transport.calls = calls
    return transport


def test_none_market_returns_wait_without_calling_transport():
    transport = _fake_transport([])
    strat = OllamaStrategy(client=transport)
    d = strat(None)
    assert d.signal is Signal.WAIT
    assert transport.calls == []


def test_high_confidence_buy_is_passed_through():
    transport = _fake_transport(
        [{"signal": "BUY", "confidence": 0.9, "reason": "strong confluence"}])
    strat = OllamaStrategy(client=transport)
    d = strat(_market())
    assert d.signal is Signal.BUY
    assert d.confidence == 0.9
    assert len(transport.calls) == 1
    # sent to Ollama's chat schema, model comes from config
    assert transport.calls[0]["model"] == OllamaStrategyConfig().model
    assert transport.calls[0]["messages"][0]["role"] == "system"


def test_low_confidence_trade_signal_is_downgraded_to_wait():
    transport = _fake_transport(
        [{"signal": "SELL", "confidence": 0.3, "reason": "weak"}])
    strat = OllamaStrategy(
        OllamaStrategyConfig(min_confidence=0.65), client=transport)
    d = strat(_market())
    assert d.signal is Signal.WAIT


def test_connection_error_falls_back():
    transport = _fake_transport([RuntimeError("connection refused")])
    calls = []

    def fallback(market):
        calls.append(market)
        return Decision(Signal.WAIT, "fallback", 0.0)

    strat = OllamaStrategy(client=transport, fallback=fallback)
    d = strat(_market())
    assert d.signal is Signal.WAIT
    assert d.reason == "fallback"
    assert len(calls) == 1


def test_custom_model_and_host_are_used():
    transport = _fake_transport(
        [{"signal": "WAIT", "confidence": 0.5, "reason": "chop"}])
    strat = OllamaStrategy(
        OllamaStrategyConfig(model="mistral", host="http://192.168.1.50:11434"),
        client=transport)
    strat(_market())
    assert transport.calls[0]["model"] == "mistral"


def test_per_symbol_cache_matches_claude_strategy_behaviour():
    transport = _fake_transport([
        {"signal": "BUY", "confidence": 0.9, "reason": "gbpusd"},
        {"signal": "SELL", "confidence": 0.9, "reason": "eurusd"},
    ])
    strat = OllamaStrategy(
        OllamaStrategyConfig(min_interval_seconds=999), client=transport)

    gbp = strat(_market(symbol="GBPUSD", time="2026-01-01T00:00:00"))
    eur = strat(_market(symbol="EURUSD", time="2026-01-01T00:00:00"))

    assert gbp.signal is Signal.BUY
    assert eur.signal is Signal.SELL
    assert len(transport.calls) == 2


def test_per_symbol_confidence_override_gates_independently():
    """Same prompt, same model, one strategy instance -- but GBPUSD demands more
    conviction than the global bar, so an identical 0.8 signal is a trade on
    EURUSD and a WAIT on GBPUSD. This is the per-symbol independent reasoning."""
    transport = _fake_transport([
        {"signal": "BUY", "confidence": 0.8, "reason": "gbpusd"},
        {"signal": "BUY", "confidence": 0.8, "reason": "eurusd"},
    ])
    strat = OllamaStrategy(OllamaStrategyConfig(
        min_confidence=0.65,
        per_symbol_confidence={"GBPUSD": 0.9},
    ), client=transport)

    gbp = strat(_market(symbol="GBPUSD", time="t1"))
    eur = strat(_market(symbol="EURUSD", time="t1"))

    assert gbp.signal is Signal.WAIT      # 0.8 < GBPUSD's 0.9 bar -> downgraded
    assert gbp.confidence == 0.8
    assert eur.signal is Signal.BUY       # 0.8 >= global 0.65 -> trades


def test_min_confidence_and_interval_resolve_per_symbol():
    strat = OllamaStrategy(OllamaStrategyConfig(
        min_confidence=0.65, min_interval_seconds=60,
        per_symbol_confidence={"GBPUSD": 0.9},
        per_symbol_interval={"XAUUSD": 30},
    ))
    assert strat._min_confidence_for("GBPUSD") == 0.9    # override
    assert strat._min_confidence_for("gbpusd") == 0.9    # case-insensitive
    assert strat._min_confidence_for("EURUSD") == 0.65   # global fallback
    assert strat._min_interval_for("XAUUSD") == 30       # override
    assert strat._min_interval_for("EURUSD") == 60       # global fallback
