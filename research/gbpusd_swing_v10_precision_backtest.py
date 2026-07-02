"""Backtest the V10 precision timing policy on the exact V5 swing ledger.

The input ledger was produced by the raw H4 simulator. This script joins every
trade to its completed signal candle, applies the V10 precision gate, and then
replays the recorded R outcome using the selected risk tier. It is a
signal-selection/risk replay, not a new intrabar execution simulation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from mt5_ai_bridge.gbpusd_swing_v10_precision import evaluate_swing_timing

ORIGINAL_RISK = {
    "PRIMARY_16UTC_BREAKOUT": 0.35,
    "SECONDARY_12UTC_BREAKOUT": 0.35,
    "GBPUSD_SWING_V5_PULLBACK_ADDON": 0.20,
}


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()


def load_h4(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=None, engine="python").rename(
        columns={
            "<DATE>": "date",
            "<TIME>": "clock",
            "<OPEN>": "open",
            "<HIGH>": "high",
            "<LOW>": "low",
            "<CLOSE>": "close",
            "<TICKVOL>": "tick_volume",
        }
    )
    frame["time"] = pd.to_datetime(
        frame["date"].astype(str) + " " + frame["clock"].astype(str),
        format="%Y.%m.%d %H:%M:%S",
        utc=True,
    )
    frame = frame.sort_values("time").reset_index(drop=True)
    frame["atr14"] = _atr(frame)
    frame["ema20_h4"] = frame["close"].ewm(
        span=20, adjust=False, min_periods=20
    ).mean()
    frame["ema50_h4"] = frame["close"].ewm(
        span=50, adjust=False, min_periods=50
    ).mean()
    frame["volume_ratio"] = frame["tick_volume"] / frame[
        "tick_volume"
    ].rolling(20, min_periods=20).mean()
    frame["atr_ratio"] = frame["atr14"] / frame["atr14"].rolling(
        20, min_periods=20
    ).mean()
    frame["range_atr"] = (frame["high"] - frame["low"]) / frame["atr14"]
    return frame


def enrich_trades(trades_path: Path, h4_path: Path) -> pd.DataFrame:
    trades = pd.read_csv(trades_path)
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True)
    trades["signal_bar_time"] = trades["entry_time"] - pd.Timedelta(hours=8)
    h4 = load_h4(h4_path)
    frame = trades.merge(
        h4[
            [
                "time",
                "open",
                "close",
                "atr14",
                "ema20_h4",
                "ema50_h4",
                "volume_ratio",
                "atr_ratio",
                "range_atr",
            ]
        ],
        left_on="signal_bar_time",
        right_on="time",
        how="left",
        validate="many_to_one",
    )
    if frame["time"].isna().any():
        raise ValueError("One or more trades could not be matched to an H4 signal bar")
    frame["fill_factor"] = frame.apply(
        lambda row: row["initial_risk_dollars"]
        / (
            row["balance_before"]
            * ORIGINAL_RISK[row["variant"]]
            / 100.0
        ),
        axis=1,
    )
    return frame


def precision_decision(row: pd.Series):
    return evaluate_swing_timing(
        setup=str(row["variant"]),
        side=int(row["side"]),
        open_price=float(row["open"]),
        close_price=float(row["close"]),
        atr14=float(row["atr14"]),
        volume_ratio=float(row["volume_ratio"]),
        range_atr=float(row["range_atr"]),
        atr_ratio=float(row["atr_ratio"]),
        ema20_h4=float(row["ema20_h4"]),
        ema50_h4=float(row["ema50_h4"]),
    )


def replay(
    frame: pd.DataFrame,
    *,
    precision: bool,
    cost_r: float = 0.0,
    starting_balance: float = 5_000.0,
) -> tuple[dict, pd.DataFrame]:
    balance = starting_balance
    peak = balance
    maximum_drawdown = 0.0
    rows: list[dict] = []
    for _, row in frame.sort_values("exit_time").iterrows():
        decision = precision_decision(row)
        risk_percent = decision.risk_percent if precision else 0.40
        if precision and not decision.allowed:
            continue
        risk_dollars = balance * risk_percent / 100.0 * row["fill_factor"]
        adjusted_r = float(row["r_multiple"]) - cost_r
        pnl = risk_dollars * adjusted_r
        balance += pnl
        peak = max(peak, balance)
        drawdown = (peak - balance) / peak * 100.0
        maximum_drawdown = max(maximum_drawdown, drawdown)
        rows.append(
            {
                "entry_time": row["entry_time"].isoformat(),
                "exit_time": row["exit_time"].isoformat(),
                "variant": row["variant"],
                "side": int(row["side"]),
                "grade": decision.grade if precision else "BASELINE",
                "risk_percent": risk_percent,
                "original_r_multiple": float(row["r_multiple"]),
                "cost_r": cost_r,
                "adjusted_r_multiple": adjusted_r,
                "pnl": pnl,
                "balance": balance,
                "drawdown_percent": drawdown,
                "timing_reason": decision.reason,
            }
        )
    ledger = pd.DataFrame(rows)
    gross_profit = float(ledger.loc[ledger["pnl"] > 0, "pnl"].sum())
    gross_loss = float(-ledger.loc[ledger["pnl"] < 0, "pnl"].sum())
    metrics = {
        "starting_balance": starting_balance,
        "ending_balance": balance,
        "net_profit": balance - starting_balance,
        "return_percent": (balance - starting_balance) / starting_balance * 100.0,
        "trades": int(len(ledger)),
        "wins": int((ledger["pnl"] > 0).sum()),
        "losses": int((ledger["pnl"] <= 0).sum()),
        "win_rate": float((ledger["pnl"] > 0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "maximum_drawdown_percent": maximum_drawdown,
    }
    return metrics, ledger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trades", type=Path)
    parser.add_argument("h4", type=Path)
    parser.add_argument("--out", type=Path, default=Path("v10_swing_precision"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    frame = enrich_trades(args.trades, args.h4)
    baseline, baseline_ledger = replay(frame, precision=False)
    precision, precision_ledger = replay(frame, precision=True)
    development = frame[frame["entry_time"] < pd.Timestamp("2023-01-01", tz="UTC")]
    validation = frame[frame["entry_time"] >= pd.Timestamp("2023-01-01", tz="UTC")]
    payload = {
        "methodology": (
            "Completed-signal-bar quality selection and risk-tier replay over the "
            "exact raw-H4 V5 trade ledger."
        ),
        "baseline_0_40_percent": baseline,
        "v10_precision": precision,
        "improvement": {
            "net_profit_dollars": precision["net_profit"] - baseline["net_profit"],
            "net_profit_percent": (
                precision["net_profit"] / baseline["net_profit"] - 1.0
            )
            * 100.0,
            "drawdown_change_percentage_points": (
                precision["maximum_drawdown_percent"]
                - baseline["maximum_drawdown_percent"]
            ),
        },
        "development_before_2023": {
            "baseline": replay(development, precision=False)[0],
            "precision": replay(development, precision=True)[0],
        },
        "validation_2023_onward": {
            "baseline": replay(validation, precision=False)[0],
            "precision": replay(validation, precision=True)[0],
        },
        "cost_stress": {
            f"{cost:.2f}R": replay(frame, precision=True, cost_r=cost)[0]
            for cost in (0.03, 0.05, 0.10)
        },
        "limitations": [
            "Thresholds were selected after studying this history and require forward validation.",
            "The replay filters and reweights recorded trades; it does not create new intrabar fills.",
            "No strategy can identify the exact maximum-profit entry in advance.",
        ],
    }
    (args.out / "results.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    baseline_ledger.to_csv(args.out / "baseline_trades.csv", index=False)
    precision_ledger.to_csv(args.out / "precision_trades.csv", index=False)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
