from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.gbpusd_satellite import (
    SatelliteParams,
    can_enter_today,
    duplicate_signal,
    evaluate_setup,
    mark_entry,
    normalized_risk_percent,
    stop_and_target_pips,
)
from mt5_ai_bridge.gbpusd_portfolio import _direction_allowed


class NoDataClient:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self):
        self.calls = []

    def copy_rates_from_pos(self, *args):
        self.calls.append(args)
        return None


def test_wrong_symbol_is_rejected_before_market_reads():
    client = NoDataClient()
    setup, diagnostics = evaluate_setup(client, "EURUSD")
    assert setup is None
    assert "restricted" in diagnostics["reason"].lower()
    assert client.calls == []


def test_satellite_risk_is_clamped(monkeypatch):
    monkeypatch.setenv("SATELLITE_RISK_PERCENT", "0.50")
    assert normalized_risk_percent(SatelliteParams()) == 0.15
    monkeypatch.setenv("SATELLITE_RISK_PERCENT", "0.01")
    assert normalized_risk_percent(SatelliteParams()) == 0.10


def test_stop_and_target_are_atr_based_and_clipped():
    stop, target = stop_and_target_pips(0.0010, 0.0001)
    assert stop == 17.5
    assert target == 30.625
    stop, target = stop_and_target_pips(0.0100, 0.0001)
    assert stop == 35.0
    assert target == 61.25


def test_one_entry_per_day_and_duplicate_signal():
    state = {"last_signal_end": None, "last_entry_date": None}
    signal_end = datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)
    assert can_enter_today(state, signal_end)
    assert not duplicate_signal(state, signal_end)
    mark_entry(state, signal_end)
    assert not can_enter_today(state, signal_end)
    assert duplicate_signal(state, signal_end)


def test_opposing_portfolio_direction_is_blocked():
    client = NoDataClient()
    position = SimpleNamespace(type=client.POSITION_TYPE_BUY, magic=260704)
    allowed, _ = _direction_allowed(client, [position], Signal.BUY)
    blocked, _ = _direction_allowed(client, [position], Signal.SELL)
    assert allowed is True
    assert blocked is False
