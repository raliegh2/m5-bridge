from __future__ import annotations

import pandas as pd

from v12_final_runner import _utc_ns


def test_utc_ns_normalizes_mixed_datetime_units_for_asof_merge() -> None:
    left = pd.DataFrame({
        "time": pd.Series(
            pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T04:00:00Z"])
        ).astype("datetime64[s, UTC]"),
        "value": [1, 2],
    })
    right = pd.DataFrame({
        "available": pd.Series(
            pd.to_datetime(["2025-12-31T00:00:00Z", "2026-01-01T00:00:00Z"])
        ).astype("datetime64[us, UTC]"),
        "daily": [10, 20],
    })

    left["time"] = _utc_ns(left["time"])
    right["available"] = _utc_ns(right["available"])

    assert str(left["time"].dtype) == "datetime64[ns, UTC]"
    assert str(right["available"].dtype) == "datetime64[ns, UTC]"

    merged = pd.merge_asof(
        left.sort_values("time"),
        right.sort_values("available"),
        left_on="time",
        right_on="available",
        direction="backward",
    )
    assert list(merged["daily"]) == [20, 20]
