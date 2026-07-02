from types import SimpleNamespace

from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.gbpusd_satellite_v2 import (
    SatelliteV2Params,
    SatelliteV2Setup,
    evaluate_setup,
    risk_capped_lot,
    setup_stop_target_pips,
)
from mt5_ai_bridge.gbpusd_portfolio_v2 import (
    _direction_allowed,
    _open_risk_percent,
)


class FakeClient:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self):
        self.calls = []

    def copy_rates_from_pos(self, *args):
        self.calls.append(args)
        return None

    def symbol_info(self, symbol):
        return SimpleNamespace(
            volume_min=0.01,
            volume_step=0.01,
            volume_max=2.0,
            point=0.00001,
            digits=5,
        )


def test_wrong_symbol_is_rejected_without_market_reads():
    client = FakeClient()
    setup, diagnostics = evaluate_setup(client, "EURUSD")
    assert setup is None
    assert "restricted" in diagnostics["reason"].lower()
    assert client.calls == []


def test_base_lot_is_clipped_by_quarter_percent_risk(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("SATELLITE_V2_BASE_LOT", "0.08")

    volume, risk = risk_capped_lot(
        client,
        "GBPUSD",
        balance=5000.0,
        stop_pips=20.0,
        pip_value_per_lot=10.0,
    )
    assert volume == 0.06
    assert risk == 12.0

    volume, risk = risk_capped_lot(
        client,
        "GBPUSD",
        balance=5000.0,
        stop_pips=10.0,
        pip_value_per_lot=10.0,
    )
    assert volume == 0.08
    assert risk == 8.0


def test_setup_stop_and_target_use_setup_specific_parameters():
    setup = SatelliteV2Setup(
        side=Signal.BUY,
        name="LONDON_PULLBACK_V2",
        signal_end=None,
        atr_price=0.0008,
        stop_atr=1.75,
        min_stop_pips=5.0,
        max_stop_pips=30.0,
        target_r=1.75,
        break_even_r=1.0,
        max_hold_m15_bars=32,
        reason="test",
    )
    stop, target = setup_stop_target_pips(setup, 0.0001)
    assert round(stop, 2) == 14.0
    assert round(target, 2) == 24.5


def test_opposing_direction_is_blocked():
    client = FakeClient()
    position = SimpleNamespace(type=client.POSITION_TYPE_BUY, magic=260704)
    allowed, _ = _direction_allowed(client, [position], Signal.BUY)
    blocked, _ = _direction_allowed(client, [position], Signal.SELL)
    assert allowed is True
    assert blocked is False


def test_open_risk_is_calculated_from_visible_stops():
    client = FakeClient()
    position = SimpleNamespace(
        magic=260731,
        sl=1.2980,
        price_open=1.3000,
        volume=0.05,
    )
    risk_percent, valid = _open_risk_percent(
        client,
        [position],
        "GBPUSD",
        balance=5000.0,
        pip_value_per_lot=10.0,
    )
    assert valid is True
    assert round(risk_percent, 2) == 0.20


def test_missing_stop_blocks_new_portfolio_risk():
    client = FakeClient()
    position = SimpleNamespace(
        magic=260731,
        sl=0.0,
        price_open=1.3000,
        volume=0.05,
    )
    risk_percent, valid = _open_risk_percent(
        client,
        [position],
        "GBPUSD",
        balance=5000.0,
        pip_value_per_lot=10.0,
    )
    assert valid is False
    assert risk_percent == 100.0
