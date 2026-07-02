from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v10_multisymbol import (
    PortfolioRiskGate,
    V10MultiSymbolConfig,
    normalize_volume,
    resolve_broker_symbol,
)


class FakeClient:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self):
        self.selected = []
        self._symbols = {
            "GBPUSD.a": SimpleNamespace(name="GBPUSD.a", visible=False),
            "EURUSDm": SimpleNamespace(name="EURUSDm", visible=True),
            "GBPJPYi": SimpleNamespace(name="GBPJPYi", visible=True),
        }

    def symbol_info(self, name):
        return self._symbols.get(name)

    def symbols_get(self):
        return list(self._symbols.values())

    def symbol_select(self, name, enabled):
        self.selected.append((name, enabled))
        return True

    def order_calc_profit(self, order_type, symbol, volume, entry, stop):
        return -100.0 * volume


def position(symbol, side, risk=25.0):
    # order_calc_profit returns $100 risk per lot.
    return SimpleNamespace(
        symbol=symbol,
        type=0 if side > 0 else 1,
        volume=risk / 100.0,
        price_open=1.20,
        sl=1.19,
    )


def test_config_defaults_are_shared_and_safe(monkeypatch):
    monkeypatch.delenv("SYMBOLS", raising=False)
    config = V10MultiSymbolConfig.from_env()
    assert config.symbols == ("GBPUSD", "EURUSD", "GBPJPY")
    assert config.max_open_risk_percent == 0.75
    assert config.spec("EURUSD").risk_percent == 0.35
    assert config.spec("GBPJPY").magic != config.spec("EURUSD").magic


def test_symbol_resolver_handles_suffixes():
    client = FakeClient()
    assert resolve_broker_symbol(client, "GBPUSD") == "GBPUSD.a"
    assert client.selected == [("GBPUSD.a", True)]


def test_volume_normalization_respects_min_step_and_max():
    info = SimpleNamespace(volume_min=0.01, volume_max=2.0, volume_step=0.01)
    assert normalize_volume(info, 0.016) == 0.01
    assert normalize_volume(info, 0.027) == 0.02
    assert normalize_volume(info, 3.0) == 2.0


def test_risk_gate_blocks_mixed_gbp_exposure():
    client = FakeClient()
    config = V10MultiSymbolConfig()
    gate = PortfolioRiskGate(config)
    account = SimpleNamespace(balance=5000.0)
    # Existing GBP long reserves $10 = 0.20%. A new short GBP risk of $17.50
    # remains under the total cap but exceeds the 0.50% mixed-direction cap.
    decision = gate.evaluate(
        client=client,
        account=account,
        positions=[position("GBPUSD.a", 1, 10.0)],
        canonical_symbol="GBPJPY",
        side=-1,
        new_risk_dollars=17.50,
    )
    assert not decision.allowed
    assert decision.reason == "gbp_currency_risk_cap"


def test_risk_gate_allows_non_gbp_under_total_cap():
    client = FakeClient()
    gate = PortfolioRiskGate(V10MultiSymbolConfig())
    decision = gate.evaluate(
        client=client,
        account=SimpleNamespace(balance=5000.0),
        positions=[position("GBPUSD.a", 1, 10.0)],
        canonical_symbol="EURUSD",
        side=1,
        new_risk_dollars=17.50,
    )
    assert decision.allowed
