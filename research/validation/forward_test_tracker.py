"""Pre-registered GBPUSD V4 forward-test tracker.

Record every eligible signal, including rejected and skipped orders. Do not
change the parameter hash during a forward-test cohort.

Usage:
    python forward_test_tracker.py --trades forward_test.csv
    python forward_test_tracker.py --trades forward_test.csv --json report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FROZEN_PARAMETER_SHA256 = (
    "dec29542446673e043b16b20556f2a0bcaa65f096b81e5ecd71e61bbdb301e6b"
)

REQUIRED_COLUMNS = {
    "signal_id",
    "signal_time",
    "entry_time",
    "exit_time",
    "side",
    "status",
    "initial_risk_usd",
    "net_pnl_usd",
    "entry_spread_pips",
    "entry_slippage_pips",
    "exit_slippage_pips",
    "parameter_sha256",
    "hard_stop_attached",
    "notes",
}


def load_forward_log(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    for column in ("signal_time", "entry_time", "exit_time"):
        df[column] = pd.to_datetime(df[column], utc=True, errors="coerce")

    for column in (
        "initial_risk_usd",
        "net_pnl_usd",
        "entry_spread_pips",
        "entry_slippage_pips",
        "exit_slippage_pips",
    ):
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["hard_stop_attached"] = (
        df["hard_stop_attached"]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"1", "true", "yes", "y"})
    )
    return df


def maximum_drawdown_percent(closed: pd.DataFrame) -> float:
    equity = 5000.0 + closed["net_pnl_usd"].fillna(0).cumsum()
    peak = equity.cummax()
    drawdown = (peak - equity) / peak.replace(0, np.nan)
    return float(drawdown.max() * 100) if len(drawdown) else 0.0


def summarize(df: pd.DataFrame) -> dict:
    duplicates = sorted(
        df.loc[df.signal_id.duplicated(False), "signal_id"]
        .astype(str)
        .unique()
    )
    wrong_hash = (
        df["parameter_sha256"].astype(str) != FROZEN_PARAMETER_SHA256
    )

    managed_statuses = {
        "FILLED",
        "CLOSED",
        "STOPPED",
        "TARGET",
        "TRAIL",
        "TIME_EXIT",
    }
    eligible = df[
        df["status"].astype(str).str.upper().isin(managed_statuses)
    ].copy()
    closed = (
        eligible[eligible["exit_time"].notna()]
        .sort_values("exit_time")
        .copy()
    )
    closed["r_multiple"] = np.where(
        closed["initial_risk_usd"] > 0,
        closed["net_pnl_usd"] / closed["initial_risk_usd"],
        np.nan,
    )

    gross_profit = closed.loc[
        closed.net_pnl_usd > 0, "net_pnl_usd"
    ].sum()
    gross_loss = -closed.loc[
        closed.net_pnl_usd < 0, "net_pnl_usd"
    ].sum()
    profit_factor = (
        float(gross_profit / gross_loss)
        if gross_loss > 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )
    expectancy_r = (
        float(closed["r_multiple"].mean()) if len(closed) else 0.0
    )
    drawdown = maximum_drawdown_percent(closed)

    avg_spread = (
        float(eligible.entry_spread_pips.mean()) if len(eligible) else 0.0
    )
    p95_spread = (
        float(eligible.entry_spread_pips.quantile(0.95))
        if len(eligible)
        else 0.0
    )
    execution_slippage = pd.concat(
        [
            eligible.entry_slippage_pips,
            eligible.exit_slippage_pips,
        ],
        ignore_index=True,
    )
    avg_slippage = (
        float(execution_slippage.mean()) if len(execution_slippage) else 0.0
    )
    p95_slippage = (
        float(execution_slippage.quantile(0.95))
        if len(execution_slippage)
        else 0.0
    )

    integrity_ok = (
        not duplicates
        and not bool(wrong_hash.any())
        and (
            bool(eligible.hard_stop_attached.all())
            if len(eligible)
            else True
        )
    )

    gate20 = {
        "enough_trades": len(closed) >= 20,
        "profit_factor_at_least_1_25": profit_factor >= 1.25,
        "expectancy_r_positive": expectancy_r > 0,
        "max_drawdown_no_more_than_4pct": drawdown <= 4.0,
        "average_spread_no_more_than_1_5_pips": avg_spread <= 1.5,
        "p95_spread_no_more_than_2_pips": p95_spread <= 2.0,
        "average_slippage_no_more_than_0_6_pips": avg_slippage <= 0.6,
        "p95_slippage_no_more_than_1_pip": p95_slippage <= 1.0,
        "data_and_execution_integrity": integrity_ok,
    }
    gate20["pass"] = all(gate20.values())

    gate30 = {
        "enough_trades": len(closed) >= 30,
        "profit_factor_at_least_1_50": profit_factor >= 1.50,
        "expectancy_r_at_least_0_20": expectancy_r >= 0.20,
        "max_drawdown_no_more_than_5pct": drawdown <= 5.0,
        "data_and_execution_integrity": integrity_ok,
    }
    gate30["pass"] = all(gate30.values())

    return {
        "frozen_parameter_sha256": FROZEN_PARAMETER_SHA256,
        "logged_signals": int(len(df)),
        "filled_or_managed_positions": int(len(eligible)),
        "completed_positions": int(len(closed)),
        "net_pnl_usd": (
            float(closed.net_pnl_usd.sum()) if len(closed) else 0.0
        ),
        "profit_factor": profit_factor,
        "win_rate": (
            float((closed.net_pnl_usd > 0).mean()) if len(closed) else 0.0
        ),
        "expectancy_r": expectancy_r,
        "maximum_closed_equity_drawdown_percent": drawdown,
        "average_entry_spread_pips": avg_spread,
        "p95_entry_spread_pips": p95_spread,
        "average_slippage_pips": avg_slippage,
        "p95_slippage_pips": p95_slippage,
        "duplicate_signal_ids": duplicates,
        "wrong_parameter_hash_rows": int(wrong_hash.sum()),
        "positions_without_hard_stop": (
            int((~eligible.hard_stop_attached).sum()) if len(eligible) else 0
        ),
        "twenty_trade_gate": gate20,
        "thirty_trade_gate": gate30,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    result = summarize(load_forward_log(args.trades))
    rendered = json.dumps(result, indent=2, allow_nan=True)
    print(rendered)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
