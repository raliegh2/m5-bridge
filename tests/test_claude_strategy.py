import json

from mt5_ai_bridge.claude_strategy import (ClaudeStrategy,
                                           ClaudeStrategyConfig)
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.strategy import Decision


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, payload: dict):
        self.content = [_FakeTextBlock(json.dumps(payload))]


class _FakeMessages:
    def __init__(self, responses):
        # responses: list of dicts (payloads) or Exception instances, consumed in order
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


class _FakeAnthropicClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _market():
    return {"ema_20": 1.30, "ema_50": 1.29, "close": 1.31, "rsi_14": 60,
            "macd": 0.5, "macd_signal": 0.2}


def test_none_market_returns_wait_without_calling_client():
    client = _FakeAnthropicClient([])
    strat = ClaudeStrategy(client=client)
    d = strat(None)
    assert d.signal is Signal.WAIT
    assert d.confidence == 0.0
    assert client.messages.calls == []


def test_high_confidence_buy_is_passed_through():
    client = _FakeAnthropicClient(
        [{"signal": "BUY", "confidence": 0.9, "reason": "strong confluence"}])
    strat = ClaudeStrategy(client=client)
    d = strat(_market())
    assert d.signal is Signal.BUY
    assert d.confidence == 0.9
    assert len(client.messages.calls) == 1


def test_low_confidence_trade_signal_is_downgraded_to_wait():
    client = _FakeAnthropicClient(
        [{"signal": "SELL", "confidence": 0.3, "reason": "weak"}])
    strat = ClaudeStrategy(
        ClaudeStrategyConfig(min_confidence=0.65), client=client)
    d = strat(_market())
    assert d.signal is Signal.WAIT


def test_api_error_falls_back_to_injected_fallback():
    client = _FakeAnthropicClient([RuntimeError("rate limited")])
    fallback_calls = []

    def fallback(market):
        fallback_calls.append(market)
        return Decision(Signal.BUY, "fallback decision", 0.8)

    strat = ClaudeStrategy(client=client, fallback=fallback)
    d = strat(_market())
    assert d.signal is Signal.BUY
    assert d.reason == "fallback decision"
    assert len(fallback_calls) == 1


def test_malformed_json_falls_back():
    class _BadResponse:
        content = [_FakeTextBlock("not json at all")]

    client = _FakeAnthropicClient([])
    client.messages._responses = [None]  # placeholder, overridden below

    def create(**kwargs):
        return _BadResponse()
    client.messages.create = create

    calls = []

    def fallback(market):
        calls.append(market)
        return Decision(Signal.WAIT, "fallback", 0.0)

    strat = ClaudeStrategy(client=client, fallback=fallback)
    d = strat(_market())
    assert d.signal is Signal.WAIT
    assert len(calls) == 1


def test_second_call_within_interval_reuses_last_decision():
    client = _FakeAnthropicClient(
        [{"signal": "BUY", "confidence": 0.9, "reason": "first"}])
    strat = ClaudeStrategy(
        ClaudeStrategyConfig(min_interval_seconds=999), client=client)
    d1 = strat(_market())
    d2 = strat(_market())
    assert d1 == d2
    assert len(client.messages.calls) == 1  # second call was throttled


def test_markdown_fenced_json_is_parsed():
    class _FencedResponse:
        content = [_FakeTextBlock(
            '```json\n{"signal": "WAIT", "confidence": 0.4, "reason": "chop"}\n```'
        )]

    client = _FakeAnthropicClient([])
    client.messages.create = lambda **kwargs: _FencedResponse()
    strat = ClaudeStrategy(client=client)
    d = strat(_market())
    assert d.signal is Signal.WAIT
    assert d.confidence == 0.4


def test_throttled_cache_is_per_symbol_not_global():
    # Regression test: make_strategy() shares ONE ClaudeStrategy across every
    # symbol/book the loop trades. A global "last decision" cache would leak
    # symbol A's decision onto symbol B during the throttle window.
    client = _FakeAnthropicClient([
        {"signal": "BUY", "confidence": 0.9, "reason": "gbpusd bullish"},
        {"signal": "SELL", "confidence": 0.9, "reason": "eurusd bearish"},
    ])
    strat = ClaudeStrategy(
        ClaudeStrategyConfig(min_interval_seconds=999), client=client)

    gbp = strat({**_market(), "symbol": "GBPUSD", "time": "2026-01-01T00:00:00"})
    eur = strat({**_market(), "symbol": "EURUSD", "time": "2026-01-01T00:00:00"})

    assert gbp.signal is Signal.BUY
    assert eur.signal is Signal.SELL  # not GBPUSD's cached decision
    assert len(client.messages.calls) == 2

    # A second call for GBPUSD on the SAME candle reuses its own cache,
    # not EURUSD's.
    gbp_again = strat({**_market(), "symbol": "GBPUSD",
                       "time": "2026-01-01T00:00:00"})
    assert gbp_again.signal is Signal.BUY
    assert len(client.messages.calls) == 2  # no new call


def test_new_candle_for_same_symbol_triggers_fresh_call():
    client = _FakeAnthropicClient([
        {"signal": "BUY", "confidence": 0.9, "reason": "first candle"},
        {"signal": "WAIT", "confidence": 0.5, "reason": "second candle"},
    ])
    strat = ClaudeStrategy(
        ClaudeStrategyConfig(min_interval_seconds=999), client=client)

    d1 = strat({**_market(), "symbol": "GBPUSD", "time": "2026-01-01T00:00:00"})
    d2 = strat({**_market(), "symbol": "GBPUSD", "time": "2026-01-01T00:05:00"})

    assert d1.signal is Signal.BUY
    assert d2.signal is Signal.WAIT
    assert len(client.messages.calls) == 2  # new candle = fresh call
