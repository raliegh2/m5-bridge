from __future__ import annotations

import pandas as pd

import mt5_ai_bridge.gold_intraday_engine as gold


def _entry_frame(close: float = 102.0) -> pd.DataFrame:
    times = pd.date_range(
        end="2026-07-22 13:30:00+00:00", periods=160, freq="30min"
    )
    frame = pd.DataFrame(
        {
            "time": times,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "tick_volume": 100.0,
        }
    )
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [
        100.0,
        max(102.5, close),
        99.5,
        close,
    ]
    return frame


def _trend_frame() -> pd.DataFrame:
    times = pd.date_range(
        end="2026-07-22 09:00:00+00:00", periods=120, freq="4h"
    )
    return pd.DataFrame(
        {
            "time": times,
            "open": 109.0,
            "high": 111.0,
            "low": 108.0,
            "close": 110.0,
            "tick_volume": 100.0,
        }
    )


def _patch_indicators(monkeypatch, entry: pd.DataFrame) -> None:
    trend = _trend_frame()
    monkeypatch.setattr(
        gold,
        "_completed_rates",
        lambda _client, _symbol, timeframe, _start, _count: (
            entry.copy() if timeframe == "M30" else trend.copy()
        ),
    )
    monkeypatch.setattr(
        gold, "_atr", lambda frame, _period: pd.Series(1.0, index=frame.index)
    )
    monkeypatch.setattr(
        gold, "_adx", lambda frame, _period: pd.Series(30.0, index=frame.index)
    )
    monkeypatch.setattr(
        gold,
        "_ema",
        lambda series, period: pd.Series(
            105.0 if period == 20 else 100.0, index=series.index
        ),
    )
    monkeypatch.setattr(gold, "pip_size", lambda _client, _symbol: 0.1)


def test_gold_diagnostic_explains_ready_setup(monkeypatch) -> None:
    _patch_indicators(monkeypatch, _entry_frame())
    evaluation = gold.evaluate_gold_setup_diagnostic(object(), "XAUUSD")
    assert evaluation.code == "SETUP_READY"
    assert evaluation.setup is not None
    assert evaluation.facts["channel_break"] == "BUY"
    assert evaluation.facts["h4_trend"] == "UP"


def test_gold_diagnostic_explains_no_channel_break(monkeypatch) -> None:
    _patch_indicators(monkeypatch, _entry_frame(close=100.0))
    evaluation = gold.evaluate_gold_setup_diagnostic(object(), "XAUUSD")
    assert evaluation.setup is None
    assert evaluation.code == "NO_55_BAR_CHANNEL_BREAK"
