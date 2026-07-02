from __future__ import annotations

import pandas as pd

from v17_signal_families import stats


def merge_frames(frames):
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return pd.DataFrame(columns=["symbol", "engine", "setup", "side", "entry_time", "exit_time", "risk_percent", "r_multiple"])
    merged = pd.concat(usable, ignore_index=True).sort_values(["entry_time", "engine"])
    return merged.drop_duplicates(["symbol", "entry_time", "side"], keep="first").reset_index(drop=True)


def rank_score(item):
    result = item[0]
    return result["net_r"] + 15 * (result["profit_factor"] - 1) + 0.1 * min(result["trades_per_year"], 30)


def qualified(item):
    result = item[0]
    return result["trades_per_year"] >= 15 and result["net_r"] > 0 and result["profit_factor"] >= 1.08
