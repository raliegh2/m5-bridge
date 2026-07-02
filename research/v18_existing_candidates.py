from __future__ import annotations

import numpy as np
import pandas as pd

import v13_expanded_assets_backtest as base
from v17_quality_policy import evaluate_quality_window


def audusd_candidates(h4: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i in range(60, len(h4) - 1):
        row = h4.iloc[i]
        hour = pd.Timestamp(row["end"]).hour
        if hour not in {4, 8}:
            continue
        if not np.isfinite(row["atr14"]) or row["adx14"] < 10 or row["body_ratio"] < 0.20:
            continue
        long_regime = row["dclose"] > row["dema20"] > row["dema50"] and row["ema20"] > row["ema50"]
        short_regime = row["dclose"] < row["dema20"] < row["dema50"] and row["ema20"] < row["ema50"]
        side = 0
        if long_regime and row["low"] <= row["ema20"] + 0.30 * row["atr14"] and row["close"] > row["ema20"] and row["close"] > row["open"]:
            side = 1
        elif short_regime and row["high"] >= row["ema20"] - 0.30 * row["atr14"] and row["close"] < row["ema20"] and row["close"] < row["open"]:
            side = -1
        if not side:
            continue
        exit_time, result_r = base.simulate_h4_trade(h4, i, side, 1.25, 2.0, 1.5, 20)
        rows.append({
            "symbol": "AUDUSD",
            "engine": "AUDUSD_TREND_PULLBACK_04_08UTC",
            "setup": "D1_H4_EMA_PULLBACK",
            "side": side,
            "entry_time": pd.Timestamp(row["end"]),
            "exit_time": exit_time,
            "risk_percent": 0.25,
            "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def quality_filter(frame: pd.DataFrame, engine: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    mask = frame["entry_time"].apply(
        lambda value: evaluate_quality_window(engine, pd.Timestamp(value).to_pydatetime()).allowed
    )
    return frame.loc[mask].reset_index(drop=True)


def generate(prepared: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = [base.gbpusd_precision_candidates(prepared["GBPUSD"])]
    for symbol in ("EURUSD", "GBPJPY"):
        params = base.StrategyParams(55, 20.0, 1.25, 3.0, 2.5, 24, 0.20)
        frame = base.generic_candidates(symbol, pd.DataFrame(), prepared[symbol], params)
        frame["engine"] = f"{symbol}_H4_VALIDATED"
        frames.append(quality_filter(frame, f"{symbol}_H4_VALIDATED"))
    frames.append(audusd_candidates(prepared["AUDUSD"]))
    usd_params = base.StrategyParams(40, 20.0, 1.50, 3.0, 2.0, 30, 0.25, 0.30)
    usd = base.generic_candidates("USDJPY", pd.DataFrame(), prepared["USDJPY"], usd_params)
    usd = usd[usd["entry_time"].dt.hour.isin({8, 12, 16, 20})].copy()
    usd["engine"] = "USDJPY_H4_QUALITY_FILTERED"
    frames.append(usd)
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    return pd.concat(usable, ignore_index=True).sort_values(["entry_time", "engine"]).reset_index(drop=True)
