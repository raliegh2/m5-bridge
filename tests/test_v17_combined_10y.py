from __future__ import annotations

import pandas as pd

from v17_combined_10y_runner import (
    SATELLITE_ENGINES,
    SYMBOLS,
    classify_engine,
    profit_rows,
)


def test_only_admitted_precision_engine_is_satellite() -> None:
    assert SATELLITE_ENGINES == frozenset({"GBPUSD_V10_PRECISION"})
    assert classify_engine("GBPUSD_V10_PRECISION") == "satellite"
    assert classify_engine("GBPUSD_SWING_BREAKOUT") == "swing"


def test_profit_output_always_contains_all_five_symbols() -> None:
    accepted = pd.DataFrame(
        [
            {
                "symbol": "GBPUSD",
                "engine": "GBPUSD_SWING_BREAKOUT",
                "risk_dollars": 10.0,
                "r_multiple": 2.0,
            },
            {
                "symbol": "GBPUSD",
                "engine": "GBPUSD_V10_PRECISION",
                "risk_dollars": 10.0,
                "r_multiple": -1.0,
            },
        ]
    )
    result = profit_rows(accepted)
    assert tuple(result["symbol"]) == SYMBOLS
    gbpusd = result[result["symbol"] == "GBPUSD"].iloc[0]
    assert gbpusd["swing_net_profit"] == 20.0
    assert gbpusd["satellite_net_profit"] == -10.0
    assert gbpusd["net_profit"] == 10.0
    usdjpy = result[result["symbol"] == "USDJPY"].iloc[0]
    assert usdjpy["trades"] == 0
    assert usdjpy["net_profit"] == 0.0
