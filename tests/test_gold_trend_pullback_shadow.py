from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import mt5_ai_bridge.gold_trend_pullback_engine as pullback
from v14_3_satellite_bot_m1 import _scan_gold_pullback_shadow_once


def _frame(end: str, periods: int, frequency: str) -> pd.DataFrame:
    times = pd.date_range(end=end, periods=periods, freq=frequency)
    return pd.DataFrame({
        "time": times,
        "open": 99.5,
        "high": 101.5,
        "low": 99.0,
        "close": 101.0,
        "tick_volume": 100.0,
    })


def _patch_ready_frames(monkeypatch) -> None:
    m30 = _frame("2026-07-22 13:30:00+00:00", 160, "30min")
    h4 = _frame("2026-07-22 09:00:00+00:00", 120, "4h")
    h4[["open", "high", "low", "close"]] = [109.0, 111.0, 108.0, 110.0]
    m15 = _frame("2026-07-22 13:45:00+00:00", 48, "15min")
    frames = {"M30": m30, "H4": h4, "M15": m15}
    monkeypatch.setattr(
        pullback,
        "_completed_rates",
        lambda _client, _symbol, timeframe, _start, _count: frames[timeframe].copy(),
    )
    monkeypatch.setattr(
        pullback, "_atr", lambda frame, _period: pd.Series(1.0, index=frame.index)
    )
    monkeypatch.setattr(
        pullback, "_adx", lambda frame, _period: pd.Series(30.0, index=frame.index)
    )

    def fake_ema(series, period):
        if len(series) >= 150:
            value = 100.0 if period == 20 else 95.0
        elif len(series) >= 100:
            value = 105.0 if period == 20 else 100.0
        else:
            value = 100.0 if period == 20 else 95.0
        return pd.Series(value, index=series.index)

    monkeypatch.setattr(pullback, "_ema", fake_ema)
    monkeypatch.setattr(pullback, "pip_size", lambda _client, _symbol: 0.1)


def test_pullback_reports_shadow_candidate_without_order_authority(monkeypatch) -> None:
    _patch_ready_frames(monkeypatch)
    evaluation = pullback.evaluate_gold_pullback_diagnostic(object(), "XAUUSD")
    assert evaluation.code == "SHADOW_SETUP_READY"
    assert evaluation.setup is not None
    assert evaluation.setup.side.value == "BUY"
    assert evaluation.facts["execution_authority"] == "SHADOW_ONLY"


def test_shadow_scan_journals_candidate_and_cannot_call_executor(monkeypatch) -> None:
    evaluation = pullback.GoldPullbackEvaluation(
        setup=SimpleNamespace(),
        code="SHADOW_SETUP_READY",
        reason="ready",
        signal_end=None,
        facts={"execution_authority": "SHADOW_ONLY"},
    )
    monkeypatch.setattr(
        pullback, "evaluate_gold_pullback_diagnostic",
        lambda *_args, **_kwargs: evaluation,
    )

    class Audit:
        def __init__(self):
            self.calls = []

        def record(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    audit = Audit()
    _scan_gold_pullback_shadow_once(
        object(),
        "XAUUSD",
        completed_bar_time=123,
        audit=audit,
    )
    assert audit.calls[0][0][:2] == (
        "GOLD_PULLBACK_M30",
        "SHADOW_CANDIDATE",
    )
    assert audit.calls[0][1]["details"]["order_sent"] is False
