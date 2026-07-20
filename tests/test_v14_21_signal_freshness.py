from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_ai_bridge.v14_21_signal_freshness import (
    load_current_m1_gbp_ict_signals,
)


class FakeClient:
    def __init__(self, epochs: dict[str, int]) -> None:
        self.epochs = epochs

    def copy_rates_from_pos(self, symbol, _timeframe, _start, _count):
        return [{"time": self.epochs[symbol]}]


def _signal(symbol: str, epoch: int):
    return SimpleNamespace(
        symbol=symbol,
        signal_time=datetime.fromtimestamp(epoch, tz=timezone.utc),
    )


def test_incremental_loader_keeps_only_latest_completed_m1(monkeypatch) -> None:
    current = 1_700_000_040
    client = FakeClient({"GBPUSD": current, "GBPJPY": current})
    signals = [
        _signal("GBPUSD", current),
        _signal("GBPUSD", current - 60),
        _signal("GBPJPY", current),
        _signal("GBPJPY", current - 600),
    ]
    monkeypatch.setattr(
        "mt5_ai_bridge.v14_21_signal_freshness._load_legacy_gbp_ict_signals",
        lambda _client, _map: (signals, "READY"),
    )

    selected, status = load_current_m1_gbp_ict_signals(
        client,
        {"GBPUSD": "GBPUSD", "GBPJPY": "GBPJPY"},
    )

    assert status == "READY"
    assert len(selected) == 2
    assert {item.symbol for item in selected} == {"GBPUSD", "GBPJPY"}
    assert all(int(item.signal_time.timestamp()) == current for item in selected)


def test_missing_completed_bar_fails_closed(monkeypatch) -> None:
    class MissingClient:
        def copy_rates_from_pos(self, *_args):
            return None

    monkeypatch.setattr(
        "mt5_ai_bridge.v14_21_signal_freshness._load_legacy_gbp_ict_signals",
        lambda _client, _map: ([_signal("GBPUSD", 1_700_000_040)], "READY"),
    )
    selected, status = load_current_m1_gbp_ict_signals(
        MissingClient(),
        {"GBPUSD": "GBPUSD", "GBPJPY": "GBPJPY"},
    )
    assert selected == []
    assert status == "READY"
