"""Run the V14.6.1 raw-candle intraday trend test reproducibly.

The V14.6 swing and wide-ICT candidate fixtures are restored by CI from the
successful V14.6 artifact. New GBPUSD, GBPJPY and AUDUSD intraday ICT trades
are generated from the repository's established public H1/H4/D1 history
loader, including the new partial-profit and break-even exit simulation.

This wrapper replaces only the two unavailable local-CSV baseline builders.
It does not replace, fabricate or duplicate the new raw-candle ICT trades.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v14_6_1_intraday_ict_trend_backtest as study  # noqa: E402

FIXTURE = ROOT / "research" / "v14_6_candidate_fixture"
OUT = ROOT / "research" / "v14_6_2_raw_intraday_output"


def load_fixture(name: str) -> pd.DataFrame:
    path = FIXTURE / name
    if not path.exists():
        raise FileNotFoundError(
            f"Missing fixture {path}. Restore the V14.6 Actions artifact first."
        )
    frame = pd.read_csv(path)
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["side"] = frame["side"].astype(str).str.upper()
    return frame.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def main() -> None:
    swing = load_fixture("all_swing_candidates.csv")
    wide_ict = load_fixture("all_wide_ict_candidates.csv")
    study.OUT = OUT
    study.base.build_continuous_swing_candidates = lambda: swing.copy()
    study.base.build_continuous_ict_candidates = lambda: wide_ict.copy()
    study.main()


if __name__ == "__main__":
    main()
