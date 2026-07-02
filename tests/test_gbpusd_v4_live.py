from types import SimpleNamespace

from mt5_ai_bridge.gbpusd_v4 import (
    LiveParams,
    _effective_risk,
    _normalize_partial_volume,
    evaluate_setup,
)


class NoDataClient:
    def __init__(self):
        self.calls = []

    def copy_rates_from_pos(self, *args):
        self.calls.append(args)
        return None


class SymbolClient:
    def symbol_info(self, symbol):
        return SimpleNamespace(volume_min=0.01, volume_step=0.01, volume_max=2.0)


def test_wrong_symbol_is_rejected_without_market_request():
    client = NoDataClient()
    setup, frame = evaluate_setup(client, "EURUSD")
    assert setup is None
    assert frame is None
    assert client.calls == []


def test_drawdown_risk_states():
    params = LiveParams()

    normal_state = {"peak_equity": 5000.0}
    risk, drawdown, paused = _effective_risk(
        SimpleNamespace(equity=5000.0), normal_state, params
    )
    assert risk == 0.35
    assert drawdown == 0.0
    assert paused is False

    defensive_state = {"peak_equity": 5000.0}
    risk, drawdown, paused = _effective_risk(
        SimpleNamespace(equity=4825.0), defensive_state, params
    )
    assert risk == 0.20
    assert round(drawdown, 2) == 3.50
    assert paused is False

    paused_state = {"peak_equity": 5000.0}
    risk, drawdown, paused = _effective_risk(
        SimpleNamespace(equity=4695.0), paused_state, params
    )
    assert risk == 0.0
    assert round(drawdown, 2) == 6.10
    assert paused is True


def test_partial_volume_uses_broker_step():
    assert _normalize_partial_volume(SymbolClient(), "GBPUSD", 0.056) == 0.05
