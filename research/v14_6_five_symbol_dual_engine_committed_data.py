"""Run V14.6 using repository-committed candidate data only.

The exported 2011-2026 MT5 bar directory referenced by the regeneration module
is not committed on this branch. This entrypoint therefore replaces only the
swing-data loader with the committed V12 final ledger, while retaining the
same five-symbol ICT generation, cost gates, risk search and target rules.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import research.v14_6_five_symbol_dual_engine_target as target
from research.v14_3_production_improved_backtest import load_v12

ROOT = Path(__file__).resolve().parents[1]
V12_LEDGER = ROOT / "research" / "v12_final_ledger_output" / "v12_final_trade_ledger.csv"


def build_committed_swing_candidates() -> pd.DataFrame:
    frame = load_v12(V12_LEDGER).copy()
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["side"] = frame["side"].astype(str).str.upper()
    frame = frame.sort_values(["entry_time", "symbol", "engine", "setup"])
    frame = frame.drop_duplicates(
        ["entry_time", "exit_time", "symbol", "engine", "side"]
    )
    return frame.reset_index(drop=True)


target.build_continuous_swing_candidates = build_committed_swing_candidates


if __name__ == "__main__":
    target.main()
