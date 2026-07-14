"""Rebuild the missing V14.3 selected ICT ledger from the committed deduped source.

The filters are frozen in V14_3_UNDER10_DD_TARGET_COMBINED_REPORT.md:
- exclude GBPJPY breakout_60_fade;
- exclude GBPUSD sweep_reclaim_15;
- exclude Tuesday entries;
- exclude 07:00 and 13:00 entry hours.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "research" / "v14_3_under10_target_out" / "deduped_liquidity_fade_gap60.csv"
OUTPUT = ROOT / "research" / "v14_3_under10_target_out" / "selected_under10_target_trades.csv"


def build(source: Path = SOURCE, output: Path = OUTPUT) -> pd.DataFrame:
    if not source.exists():
        raise FileNotFoundError(f"Committed ICT source ledger not found: {source}")
    frame = pd.read_csv(source)
    required = {"entry_time", "exit_time", "r", "symbol", "setup"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"ICT source ledger is missing columns: {missing}")

    frame["entry_time"] = pd.to_datetime(frame["entry_time"], errors="raise")
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], errors="raise")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame["setup"] = frame["setup"].astype(str)

    keep = ~((frame["symbol"] == "GBPJPY") & (frame["setup"] == "breakout_60_fade"))
    keep &= ~((frame["symbol"] == "GBPUSD") & (frame["setup"] == "sweep_reclaim_15"))
    keep &= frame["entry_time"].dt.weekday != 1
    keep &= ~frame["entry_time"].dt.hour.isin([7, 13])

    selected = frame.loc[keep].copy().sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)
    selected.insert(0, "trade_id", range(1, len(selected) + 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output, index=False)
    print(f"Rebuilt {len(selected)} selected ICT trades from {len(frame)} committed source rows")
    return selected


if __name__ == "__main__":
    build()
