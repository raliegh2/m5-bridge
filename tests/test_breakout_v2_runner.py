from mt5_ai_bridge.breakout_v2_runner import automated_demo_settings
from mt5_ai_bridge.enums import Mode
from tests.fakes import make_settings


def test_automated_runner_preserves_symbols_and_adds_breakout():
    settings = automated_demo_settings(make_settings(
        symbol="EURUSD", strategy="trend", mode=Mode.READ_ONLY,
        require_demo=False, multi_book=True, risk_percent=5.0,
        max_open_positions=7, trail_enabled=True,
    ))

    assert settings.symbol == "EURUSD"
    assert settings.symbols == ("GBPUSD", "EURUSD")
    assert settings.strategy == "hybrid_breakout_v2"
    assert settings.mode is Mode.AUTO
    assert settings.require_demo is True
    assert settings.multi_book is True
    assert settings.risk_percent == 0.50
    assert settings.max_open_positions == 7
    assert settings.trail_enabled is True
