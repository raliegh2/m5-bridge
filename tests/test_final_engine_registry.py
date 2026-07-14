from mt5_ai_bridge.final_engine_registry import (
    FINAL_ENGINES,
    FINAL_SYMBOLS,
    engines_for_symbol,
)
from mt5_ai_bridge.v12_final_risk import ENGINE_RULES


def test_final_registry_covers_every_symbol_and_executable_engine():
    assert set(FINAL_SYMBOLS) == {"GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"}
    assert set(FINAL_ENGINES) == set(ENGINE_RULES)
    assert len(FINAL_ENGINES) == 7
    assert all(engines_for_symbol(symbol) for symbol in FINAL_SYMBOLS)


def test_final_registry_keeps_only_backtested_execution_engines():
    names = set(FINAL_ENGINES)
    assert "GBPUSD_V11_INTRADAY" not in names
    assert "EURUSD_V11_INTRADAY" not in names
    assert "GBPJPY_V11_INTRADAY" not in names
    assert FINAL_ENGINES["EURUSD_SWING_RETEST"].adaptive is True
    assert FINAL_ENGINES["USDJPY_SAFE_HAVEN_BREAKOUT"].adaptive is True
    assert FINAL_ENGINES["GBPJPY_SWING_CORE"].adaptive is True
