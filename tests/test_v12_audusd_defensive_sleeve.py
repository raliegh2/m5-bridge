from __future__ import annotations

import pandas as pd

from v12_audusd_defensive_sleeve import defensive_candidates


def test_defensive_sleeve_keeps_only_08utc_monday_thursday() -> None:
    frame = pd.DataFrame([
        {"signal_hour": 8, "signal_weekday": 0, "risk_percent": 0.25},
        {"signal_hour": 8, "signal_weekday": 3, "risk_percent": 0.25},
        {"signal_hour": 8, "signal_weekday": 4, "risk_percent": 0.25},
        {"signal_hour": 4, "signal_weekday": 0, "risk_percent": 0.25},
    ])
    result = defensive_candidates(frame, 0.15)
    assert len(result) == 2
    assert set(result["signal_weekday"]) == {0, 3}
    assert set(result["signal_hour"]) == {8}
    assert set(result["risk_percent"]) == {0.15}


def test_defensive_sleeve_labels_risk_tier() -> None:
    frame = pd.DataFrame([
        {"signal_hour": 8, "signal_weekday": 0, "risk_percent": 0.25}
    ])
    result = defensive_candidates(frame, 0.20)
    assert result.iloc[0]["setup"].endswith("0.20PCT")
