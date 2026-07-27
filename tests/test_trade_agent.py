import json

from mt5_ai_bridge.trade_agent import (TradeAction, TradeManagerAgent,
                                       TradeManagerConfig, parse_trade_action,
                                       rule_trade_action)


def _ctx(**over):
    base = {"ticket": 1, "symbol": "GBPUSD", "side": "BUY",
            "entry": 1.2700, "current": 1.2725, "sl": 0.0, "pip": 0.0001}
    base.update(over)
    return base


def _reply(client_returns):
    """A fake transport: (system, user) -> str, returning queued JSON strings."""
    queue = list(client_returns)
    calls = []

    def transport(system, user):
        calls.append(json.loads(user))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    transport.calls = calls
    return transport


# --- parse_trade_action guardrails --------------------------------------

def test_parse_valid_actions_pass_through():
    a = parse_trade_action('{"action":"EXIT","confidence":0.9,"reason":"reversal"}',
                           0.6, 0.5)
    assert a.action == "EXIT" and a.confidence == 0.9


def test_parse_unknown_action_becomes_hold():
    a = parse_trade_action('{"action":"MOON","confidence":0.9}', 0.6, 0.5)
    assert a.action == "HOLD"


def test_parse_low_confidence_action_downgraded_to_hold():
    a = parse_trade_action('{"action":"EXIT","confidence":0.4}', 0.6, 0.5)
    assert a.action == "HOLD"


def test_parse_partial_fraction_is_clamped_to_max():
    a = parse_trade_action('{"action":"PARTIAL","fraction":0.9,"confidence":0.8}',
                           0.6, 0.5)
    assert a.action == "PARTIAL" and a.fraction == 0.5


def test_parse_partial_without_fraction_uses_max():
    a = parse_trade_action('{"action":"PARTIAL","confidence":0.8}', 0.6, 0.5)
    assert a.action == "PARTIAL" and a.fraction == 0.5


# --- rule fallback (safety net) -----------------------------------------

def test_rule_floor_breakeven_when_earned():
    cfg = TradeManagerConfig(breakeven_trigger_pips=15)
    a = rule_trade_action(_ctx(current=1.2725), cfg)      # +25 pips
    assert a.action == "BREAKEVEN"


def test_rule_floor_holds_when_not_earned():
    cfg = TradeManagerConfig(breakeven_trigger_pips=15)
    a = rule_trade_action(_ctx(current=1.2705), cfg)      # +5 pips
    assert a.action == "HOLD"


# --- TradeManagerAgent end to end ---------------------------------------

def test_agent_returns_parsed_action():
    t = _reply([{"action": "PARTIAL", "fraction": 0.3, "confidence": 0.8,
                 "reason": "cooling"}])
    agent = TradeManagerAgent(TradeManagerConfig(min_confidence=0.6), client=t)
    a = agent(_ctx())
    assert a.action == "PARTIAL" and a.fraction == 0.3
    assert len(t.calls) == 1


def test_agent_throttles_per_ticket():
    t = _reply([{"action": "EXIT", "confidence": 0.9, "reason": "one"}])
    agent = TradeManagerAgent(
        TradeManagerConfig(min_interval_seconds=999), client=t)
    first = agent(_ctx(ticket=7))
    second = agent(_ctx(ticket=7))            # within interval -> cached
    assert first.action == "EXIT" and second.action == "EXIT"
    assert len(t.calls) == 1                  # only called once


def test_agent_falls_back_to_rule_floor_on_error():
    t = _reply([RuntimeError("ollama down")])
    agent = TradeManagerAgent(
        TradeManagerConfig(breakeven_trigger_pips=15), client=t)
    a = agent(_ctx(current=1.2725))           # +25 pips -> BE floor
    assert a.action == "BREAKEVEN"
    assert "Rule floor" in a.reason


def test_agent_per_symbol_confidence_override():
    # XAUUSD demands 0.9; a 0.7 EXIT is held for gold but fires for GBPUSD.
    cfg = TradeManagerConfig(min_confidence=0.6,
                             per_symbol_confidence={"XAUUSD": 0.9})
    gold_t = _reply([{"action": "EXIT", "confidence": 0.7, "reason": "x"}])
    gbp_t = _reply([{"action": "EXIT", "confidence": 0.7, "reason": "x"}])
    assert TradeManagerAgent(cfg, client=gold_t)(_ctx(symbol="XAUUSD")).action == "HOLD"
    assert TradeManagerAgent(cfg, client=gbp_t)(_ctx(symbol="GBPUSD")).action == "EXIT"


def test_agent_no_context_holds():
    agent = TradeManagerAgent(TradeManagerConfig())
    assert agent(None).action == "HOLD"


def test_last_was_fresh_true_on_model_call_false_on_cache_hit():
    t = _reply([{"action": "EXIT", "confidence": 0.9, "reason": "one"}])
    agent = TradeManagerAgent(
        TradeManagerConfig(min_interval_seconds=999), client=t)
    agent(_ctx(ticket=1))
    assert agent.last_was_fresh is True          # real model call
    agent(_ctx(ticket=1))                         # throttled cache hit
    assert agent.last_was_fresh is False          # not a fresh decision
