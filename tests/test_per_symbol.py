"""Per-symbol pip value + per-symbol risk overrides."""

import os
from types import SimpleNamespace

from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.execution import pip_value_per_lot

from tests.fakes import make_settings


class _Client:
    def __init__(self, info):
        self._info = info

    def symbol_info(self, symbol):
        return self._info


def test_pip_value_from_broker_tick_data_usd_and_jpy():
    # USD-quote 5-digit: tick_value $1 per 0.00001, pip 0.0001 -> ~$10/pip.
    usd = _Client(SimpleNamespace(trade_tick_value=1.0, trade_tick_size=0.00001))
    assert round(pip_value_per_lot(usd, "EURUSD", 0.0001, 10.0), 2) == 10.0
    # JPY-quote 3-digit: tick_value ~$0.065 per 0.001, pip 0.01 -> ~$0.65? scaled.
    jpy = _Client(SimpleNamespace(trade_tick_value=0.65, trade_tick_size=0.001))
    assert round(pip_value_per_lot(jpy, "USDJPY", 0.01, 10.0), 2) == 6.5


def test_pip_value_falls_back_without_tick_fields():
    bare = _Client(SimpleNamespace(digits=5, point=0.00001))
    assert pip_value_per_lot(bare, "EURUSD", 0.0001, 10.0) == 10.0
    assert pip_value_per_lot(None, "EURUSD", 0.0001, 7.0) == 7.0


def test_per_symbol_risk_overrides_from_env(monkeypatch):
    monkeypatch.setenv("SWING_RISK_PERCENT", "1.05")
    monkeypatch.setenv("INTRADAY_RISK_PERCENT", "0.11")
    monkeypatch.setenv("SWING_RISK_PERCENT_USDJPY", "0.7")
    monkeypatch.setenv("INTRADAY_RISK_PERCENT_EURUSD", "0.05")
    s = load_settings(dotenv=False)
    assert s.swing_risk_for("USDJPY") == 0.7        # override
    assert s.swing_risk_for("GBPUSD") == 1.05       # global fallback
    assert s.intraday_risk_for("EURUSD") == 0.05    # override
    assert s.intraday_risk_for("GBPUSD") == 0.11    # global fallback
    assert s.swing_risk_for("usdjpy") == 0.7        # case-insensitive


def test_make_settings_defaults_have_no_overrides():
    s = make_settings()
    assert s.swing_risk_for("ANYTHING") == s.swing_risk_percent
    assert s.intraday_risk_for("ANYTHING") == s.intraday_risk_percent


def test_gold_has_builtin_low_risk_default(monkeypatch):
    """Gold ships throttled below the global risk, with no .env override."""
    monkeypatch.setenv("SWING_RISK_PERCENT", "1.05")
    monkeypatch.setenv("INTRADAY_RISK_PERCENT", "0.11")
    s = load_settings(dotenv=False)
    # Built-in gold default applies (well below the 1.05 / 0.11 globals).
    assert s.swing_risk_for("XAUUSD") == 0.2
    assert s.intraday_risk_for("XAUUSD") == 0.1
    assert s.swing_risk_for("xauusd") == 0.2          # case-insensitive
    # Non-gold symbols still use the global.
    assert s.swing_risk_for("GBPUSD") == 1.05


def test_env_override_beats_builtin_gold_default(monkeypatch):
    """An explicit .env override always wins over the built-in gold default."""
    monkeypatch.setenv("SWING_RISK_PERCENT_XAUUSD", "0.05")
    monkeypatch.setenv("INTRADAY_RISK_PERCENT_XAUUSD", "0")
    s = load_settings(dotenv=False)
    assert s.swing_risk_for("XAUUSD") == 0.05
    assert s.intraday_risk_for("XAUUSD") == 0.0       # 0 disables that engine


def test_per_symbol_llm_overrides_from_env(monkeypatch):
    """The LLM analyst reasons independently per symbol: each can carry its own
    confidence bar and call interval, falling back to the global otherwise."""
    monkeypatch.setenv("CLAUDE_MIN_CONFIDENCE", "0.65")
    monkeypatch.setenv("CLAUDE_MIN_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("CLAUDE_MIN_CONFIDENCE_GBPUSD", "0.8")
    monkeypatch.setenv("CLAUDE_MIN_INTERVAL_SECONDS_XAUUSD", "30")
    monkeypatch.setenv("OLLAMA_MIN_CONFIDENCE_EURUSD", "0.6")
    monkeypatch.setenv("OLLAMA_MIN_INTERVAL_SECONDS_GBPUSD", "120")
    s = load_settings(dotenv=False)

    assert s.claude_confidence_for("GBPUSD") == 0.8       # override
    assert s.claude_confidence_for("EURUSD") == 0.65      # global fallback
    assert s.claude_confidence_for("gbpusd") == 0.8       # case-insensitive
    assert s.claude_interval_for("XAUUSD") == 30          # override
    assert s.claude_interval_for("GBPUSD") == 60          # global fallback
    assert s.ollama_confidence_for("EURUSD") == 0.6       # override
    assert s.ollama_confidence_for("GBPUSD") == 0.65      # global fallback
    assert s.ollama_interval_for("GBPUSD") == 120         # override

    # The global var (no trailing "_SYMBOL") must never be read as an override.
    assert s.claude_confidence_overrides == (("GBPUSD", 0.8),)


def test_make_settings_defaults_have_no_llm_overrides():
    s = make_settings()
    assert s.claude_confidence_for("ANYTHING") == s.claude_min_confidence
    assert s.claude_interval_for("ANYTHING") == s.claude_min_interval_seconds
    assert s.ollama_confidence_for("ANYTHING") == s.ollama_min_confidence
    assert s.ollama_interval_for("ANYTHING") == s.ollama_min_interval_seconds


def test_factor_caps_defaults_and_env(monkeypatch):
    s = load_settings(dotenv=False)
    assert s.factor_caps is True and s.max_currency_risk == 2.0   # on, loose
    monkeypatch.setenv("FACTOR_CAPS", "false")
    monkeypatch.setenv("MAX_CURRENCY_RISK", "1.5")
    s2 = load_settings(dotenv=False)
    assert s2.factor_caps is False and s2.max_currency_risk == 1.5
