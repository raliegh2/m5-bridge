from __future__ import annotations

import numpy as np
import pandas as pd

from mt5_ai_bridge import v14_3_live_signals as live
from mt5_ai_bridge.v14_3_live_signals import study


def _h4_frame() -> pd.DataFrame:
    end = pd.date_range("2026-07-01", periods=62, freq="4h", tz="UTC")
    frame = pd.DataFrame({
        "end": end,
        "high": np.full(62, 100.0),
        "low": np.full(62, 99.0),
        "close": np.full(62, 99.5),
        "atr14": np.full(62, 1.0),
        "dclose": np.full(62, 105.0),
        "dema20": np.full(62, 104.0),
        "dema50": np.full(62, 103.0),
    })
    frame.loc[61, ["high", "close"]] = [101.5, 101.0]
    return frame


def test_live_usdjpy_includes_latest_unresolved_h4_candidate() -> None:
    frame = _h4_frame()

    historical = study._usdjpy_candidates(frame)
    live = study._usdjpy_candidates(frame, include_unresolved=True)

    assert historical.empty
    assert len(live) == 1
    assert live.iloc[0]["entry_time"] == frame.iloc[-1]["end"]


def test_live_adapter_enables_unresolved_mode_for_delayed_sleeves(
    monkeypatch,
) -> None:
    frame = pd.DataFrame()
    prepared = {
        symbol: (frame, frame, frame)
        for symbol in live.SYMBOLS
    }
    unresolved_calls: list[str] = []

    def unresolved(name):
        def candidate(*_args, include_unresolved=False, **_kwargs):
            if include_unresolved:
                unresolved_calls.append(name)
            return pd.DataFrame()
        return candidate

    monkeypatch.setattr(study, "_gbpusd_precision", unresolved("GBPUSD_PRECISION"))
    monkeypatch.setattr(study, "_gbpusd_retest_candidates", unresolved("GBPUSD_RETEST"))
    monkeypatch.setattr(study, "_h1_retest_candidates", unresolved("EURUSD_RETEST"))
    monkeypatch.setattr(study, "_audusd_candidates", unresolved("AUDUSD_PULLBACK"))
    monkeypatch.setattr(study, "_usdjpy_candidates", unresolved("USDJPY_BREAKOUT"))
    monkeypatch.setattr(study, "_v12_core_candidates", lambda *_args: pd.DataFrame())

    assert live.build_v12_candidates(prepared).empty
    assert set(unresolved_calls) == {
        "GBPUSD_PRECISION",
        "GBPUSD_RETEST",
        "EURUSD_RETEST",
        "AUDUSD_PULLBACK",
    }
