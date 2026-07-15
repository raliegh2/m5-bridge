from __future__ import annotations

import pandas as pd

from mt5_ai_bridge.v14_3_all_symbol_ict_admission import ADMISSION, apply_shadow_admission


def candidates(symbol: str) -> pd.DataFrame:
    rows = []
    for hour, weekday, side in [
        (14, 1, "BUY"),
        (17, 4, "SELL"),
        (15, 2, "SELL"),
        (18, 3, "SELL"),
        (14, 0, "SELL"),
        (16, 2, "BUY"),
    ]:
        date = pd.Timestamp("2020-01-06", tz="UTC") + pd.Timedelta(days=weekday)
        rows.append(
            {
                "symbol": symbol,
                "engine": f"{symbol}_ICT",
                "setup": "TEST",
                "side": side,
                "entry_time": date.normalize() + pd.Timedelta(hours=hour),
                "exit_time": date.normalize() + pd.Timedelta(hours=hour + 1),
                "r_multiple": 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_registry_covers_all_new_ict_symbols() -> None:
    assert set(ADMISSION) == {"EURUSD", "AUDUSD", "USDJPY"}


def test_eurusd_admission_uses_stable_hours_and_tuesday_to_friday() -> None:
    output = apply_shadow_admission("EURUSD", candidates("EURUSD"))
    assert set(pd.to_datetime(output["entry_time"], utc=True).dt.hour) <= {14, 17}
    assert set(pd.to_datetime(output["entry_time"], utc=True).dt.weekday) <= {1, 2, 3, 4}


def test_audusd_admission_keeps_sell_signals_only() -> None:
    output = apply_shadow_admission("AUDUSD", candidates("AUDUSD"))
    assert not output.empty
    assert set(output["side"]) == {"SELL"}


def test_usdjpy_admission_requires_sell_and_stable_hours() -> None:
    output = apply_shadow_admission("USDJPY", candidates("USDJPY"))
    assert not output.empty
    assert set(output["side"]) == {"SELL"}
    assert set(pd.to_datetime(output["entry_time"], utc=True).dt.hour) <= {13, 15, 16, 18}
