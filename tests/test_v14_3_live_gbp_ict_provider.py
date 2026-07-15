from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from v14_3_signals import (
    ProviderConfig,
    apply_locked_filters,
    build_live_signals,
    deduplicate_gap_stream,
    generate_raw_candidates,
)
from v14_3_satellite_bot_m1 import (
    GBP_ICT_SYMBOLS,
    _closed_bar_signature,
    _merge_gbp_scan,
)


def _candles(end: pd.Timestamp, count: int = 100) -> pd.DataFrame:
    times = pd.date_range(end=end, periods=count, freq="min", tz="UTC")
    frame = pd.DataFrame({
        "time": times,
        "open": 1.1000,
        "high": 1.1010,
        "low": 1.0990,
        "close": 1.1000,
    })
    # Completed-candle bearish sweep of all 15/30/60-minute highs.
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [
        1.1000,
        1.1020,
        1.0995,
        1.1005,
    ]
    return frame


def test_raw_generator_uses_no_future_outcomes_and_gap_priority() -> None:
    config = ProviderConfig()
    raw = generate_raw_candidates(
        "GBPUSD",
        _candles(pd.Timestamp("2026-07-15 12:00:00", tz="UTC")),
        config,
    )
    latest = raw[raw["entry_time"] == raw["entry_time"].max()]
    assert set(latest["setup"]) >= {
        "sweep_reclaim_15",
        "sweep_reclaim_30",
        "sweep_reclaim_60",
    }
    assert "r" not in raw.columns
    assert "exit_time" not in raw.columns

    deduped = deduplicate_gap_stream(latest, 60)
    assert len(deduped) == 1
    assert deduped.iloc[0]["setup"] == "sweep_reclaim_60"


def test_locked_filters_block_original_setup_day_and_hour_rules() -> None:
    candidates = pd.DataFrame([
        {
            "entry_time": "2026-07-15 12:00:00+00:00",
            "symbol": "GBPUSD",
            "setup": "sweep_reclaim_60",
        },
        {
            "entry_time": "2026-07-15 12:01:00+00:00",
            "symbol": "GBPUSD",
            "setup": "sweep_reclaim_15",
        },
        {
            "entry_time": "2026-07-15 12:02:00+00:00",
            "symbol": "GBPJPY",
            "setup": "breakout_30_fade",
        },
        {
            "entry_time": "2026-07-14 12:03:00+00:00",
            "symbol": "GBPJPY",
            "setup": "sweep_reclaim_60",
        },
        {
            "entry_time": "2026-07-15 13:03:00+00:00",
            "symbol": "GBPJPY",
            "setup": "sweep_reclaim_60",
        },
    ])
    selected = apply_locked_filters(candidates)
    assert len(selected) == 1
    assert selected.iloc[0]["symbol"] == "GBPUSD"
    assert selected.iloc[0]["setup"] == "sweep_reclaim_60"


class FakeProviderClient:
    def __init__(self) -> None:
        now = pd.Timestamp("2026-07-15 12:00:00", tz="UTC")
        self.frames = {
            "GBPUSD": _candles(now),
            "GBPJPY": _candles(now).assign(
                open=lambda frame: frame["open"] * 150,
                high=lambda frame: frame["high"] * 150,
                low=lambda frame: frame["low"] * 150,
                close=lambda frame: frame["close"] * 150,
            ),
        }
        self.calls: list[tuple[str, str, int, int]] = []

    def symbol_info(self, symbol: str):
        if symbol == "GBPJPY":
            return SimpleNamespace(visible=True, point=0.001, digits=3)
        if symbol == "GBPUSD":
            return SimpleNamespace(visible=True, point=0.00001, digits=5)
        return None

    def symbol_info_tick(self, symbol: str):
        if symbol == "GBPJPY":
            return SimpleNamespace(bid=165.000, ask=165.010)
        return SimpleNamespace(bid=1.1000, ask=1.1001)

    def symbols_get(self):
        return []

    def symbol_select(self, _symbol: str, _enable: bool = True):
        return True

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: str,
        start: int,
        count: int,
    ):
        self.calls.append((symbol, timeframe, start, count))
        frame = self.frames[symbol].tail(count).copy()
        frame["time"] = frame["time"].astype("int64") // 10**9
        return frame.to_records(index=False)


def test_live_provider_reads_completed_m1_only_and_returns_valid_payloads(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "v14_3_signals._utc_now",
        lambda: pd.Timestamp("2026-07-15 12:05:00", tz="UTC"),
    )
    monkeypatch.setenv("V14_3_GBP_ICT_LOOKBACK_MINUTES", "90")

    # The raw detector is covered independently above. Keep this integration test
    # deterministic across Windows/Linux and focus it on completed-bar retrieval,
    # provider filtering, payload construction, and engine naming.
    def deterministic_candidates(symbol, candles, _config):
        candle = candles.iloc[-1]
        return pd.DataFrame([{
            "entry_time": pd.Timestamp(candle["time"]),
            "symbol": symbol,
            "setup": "sweep_reclaim_60",
            "direction": -1,
            "priority": 0.0,
            "candle_high": float(candle["high"]),
            "candle_low": float(candle["low"]),
            "signal_atr": float(candle["high"] - candle["low"]),
        }])

    monkeypatch.setattr(
        "v14_3_signals.generate_raw_candidates",
        deterministic_candidates,
    )

    client = FakeProviderClient()
    signals = build_live_signals(client)

    assert client.calls
    assert all(
        timeframe == "M1" and start == 1
        for _, timeframe, start, _ in client.calls
    )
    assert {item["engine"] for item in signals} == {
        "ICT_V14_3_GBPUSD",
        "ICT_V14_3_GBPJPY",
    }
    assert all(item["side"] == "SELL" for item in signals)
    assert all(
        item["stop_pips"] > 0 and item["target_pips"] > 0
        for item in signals
    )
    assert all(item["metadata"]["completed_candle_only"] for item in signals)
    assert all(item["metadata"]["timeframe"] == "M1" for item in signals)


class SignatureClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, int]] = []

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.calls.append((symbol, timeframe, start, count))
        return [{"time": 100 if symbol == "GBPUSD" else 200}]


def test_m1_scheduler_signature_excludes_forming_bar() -> None:
    client = SignatureClient()
    broker_map = {symbol: symbol for symbol in GBP_ICT_SYMBOLS}
    signature = _closed_bar_signature(
        client,
        broker_map,
        "M1",
        GBP_ICT_SYMBOLS,
    )
    assert signature == (("GBPUSD", 100), ("GBPJPY", 200))
    assert all(start == 1 and count == 1 for _, _, start, count in client.calls)


def test_gbp_scan_merge_preserves_h1_engine_statuses() -> None:
    prior = {
        "generation": {"v12_candidates": 2},
        "engines": [
            {"engine": "GBPUSD_V10_PRECISION", "status": "WAITING"},
            {"engine": "ICT_V14_3_GBPUSD", "status": "PROVIDER_WAIT"},
        ],
        "symbols": {"EURUSD": {"v12_candidates": 1}},
    }
    update = {
        "generation": {"legacy_gbp_ict_provider": "READY"},
        "engines": [
            {"engine": "ICT_V14_3_GBPUSD", "status": "SIGNAL"},
            {"engine": "ICT_V14_3_GBPJPY", "status": "WAITING"},
        ],
        "symbols": {"GBPUSD": {"ict_candidates": 1}},
    }
    merged = _merge_gbp_scan(prior, update)
    by_engine = {
        item["engine"]: item["status"] for item in merged["engines"]
    }
    assert by_engine["GBPUSD_V10_PRECISION"] == "WAITING"
    assert by_engine["ICT_V14_3_GBPUSD"] == "SIGNAL"
    assert merged["generation"]["v12_candidates"] == 2
    assert merged["generation"]["legacy_gbp_ict_provider"] == "READY"
    assert "EURUSD" in merged["symbols"]
    assert "GBPUSD" in merged["symbols"]
