"""Re-run V14.7.2 with exact V14.6.1 intraday incumbent candidates."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from mt5_ai_bridge.v14_6_1_intraday_ict_trend import generate_symbol_profiles  # noqa: E402
from research import v14_7_2_frozen_all_ten as study  # noqa: E402
from research.v13_expanded_assets_backtest import load_frame  # noqa: E402

OUT = ROOT / "research" / "v14_7_2_1_all_ten_output"
ORIGINAL_RAW = study.raw_candidates


def combined_raw_candidates() -> pd.DataFrame:
    frames = [ORIGINAL_RAW()]
    for symbol in ("GBPUSD", "GBPJPY", "AUDUSD"):
        generated = generate_symbol_profiles(
            symbol,
            load_frame(symbol, "h1"),
            load_frame(symbol, "h4"),
            load_frame(symbol, "d1"),
        )
        if generated.empty:
            continue
        generated = generated.copy()
        generated["mode"] = "ICT"
        generated["family"] = "V14_6_1_INTRADAY"
        generated["selection_cost_r"] = 0.12
        frames.append(generated)
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    output["side"] = output["side"].astype(str).str.upper()
    return output.sort_values(["entry_time", "symbol", "mode", "engine"]).reset_index(drop=True)


def main() -> None:
    study.OUT = OUT
    study.raw_candidates = combined_raw_candidates
    study.main()


if __name__ == "__main__":
    main()
