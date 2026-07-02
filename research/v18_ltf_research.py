from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LtfParams:
    h1_adx_min: float
    body_min: float
    pullback_atr: float
    stop_atr: float
    target_r: float
    max_bars: int = 96
    risk_percent: float = 0.10
    allowed_hours: tuple[int, ...] = (9, 10, 11, 12, 13, 14, 15, 16)


def simulate_trade(frame: pd.DataFrame, index: int, side: int, params: LtfParams):
    if index + 1 >= len(frame):
        return pd.Timestamp(frame.iloc[index]["end"]), 0.0
    signal = frame.iloc[index]
    entry_row = frame.iloc[index + 1]
    entry = float(entry_row["open"])
    risk = float(signal["atr14"]) * params.stop_atr
    if not np.isfinite(risk) or risk <= 0:
        return pd.Timestamp(entry_row["time"]), 0.0
    stop = entry - side * risk
    target = entry + side * params.target_r * risk
    active_stop = stop
    for j in range(index + 1, min(len(frame), index + 1 + params.max_bars)):
        row = frame.iloc[j]
        low, high = float(row["low"]), float(row["high"])
        if low <= active_stop if side > 0 else high >= active_stop:
            return pd.Timestamp(row["end"]), (active_stop - entry) * side / risk
        if high >= target if side > 0 else low <= target:
            return pd.Timestamp(row["end"]), params.target_r
        favorable = high - entry if side > 0 else entry - low
        if favorable >= risk:
            active_stop = max(active_stop, entry) if side > 0 else min(active_stop, entry)
    last = frame.iloc[min(len(frame) - 1, index + params.max_bars)]
    return pd.Timestamp(last["end"]), (float(last["close"]) - entry) * side / risk


def generate(symbol: str, frame: pd.DataFrame, params: LtfParams) -> pd.DataFrame:
    rows = []
    for i in range(220, len(frame) - 1):
        row = frame.iloc[i]
        if pd.Timestamp(row["end"]).hour not in params.allowed_hours:
            continue
        required = [row.get(name, np.nan) for name in (
            "atr14", "body_ratio", "h1_close", "h1_ema20", "h1_ema50",
            "h1_adx14", "d1_close", "d1_ema20", "d1_ema50",
        )]
        if any(not np.isfinite(value) for value in required):
            continue
        if row["body_ratio"] < params.body_min or row["h1_adx14"] < params.h1_adx_min:
            continue
        long_regime = row["d1_close"] > row["d1_ema20"] > row["d1_ema50"] and row["h1_close"] > row["h1_ema20"] > row["h1_ema50"]
        short_regime = row["d1_close"] < row["d1_ema20"] < row["d1_ema50"] and row["h1_close"] < row["h1_ema20"] < row["h1_ema50"]
        side = 0
        if long_regime and row["low"] <= row["h1_ema20"] + params.pullback_atr * row["atr14"] and row["close"] > row["h1_ema20"] and row["close"] > row["open"]:
            side = 1
        elif short_regime and row["high"] >= row["h1_ema20"] - params.pullback_atr * row["atr14"] and row["close"] < row["h1_ema20"] and row["close"] < row["open"]:
            side = -1
        if not side:
            continue
        exit_time, result_r = simulate_trade(frame, i, side, params)
        rows.append({
            "symbol": symbol,
            "engine": f"{symbol}_M15_H1_CONTINUATION",
            "setup": "M15_H1_D1_PULLBACK",
            "side": side,
            "entry_time": pd.Timestamp(row["end"]),
            "exit_time": exit_time,
            "risk_percent": params.risk_percent,
            "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def statistics(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    years = max((end - start).total_seconds() / (365.25 * 86400), 0.25)
    if frame.empty:
        return {"trades": 0, "trades_per_year": 0.0, "net_r": 0.0, "profit_factor": 0.0}
    values = frame["r_multiple"].astype(float)
    gross_profit = values[values > 0].sum()
    gross_loss = -values[values < 0].sum()
    return {
        "trades": int(len(frame)),
        "trades_per_year": float(len(frame) / years),
        "net_r": float(values.sum()),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else float("inf"),
    }


def remove_h4_duplicates(ltf: pd.DataFrame, h4: pd.DataFrame) -> pd.DataFrame:
    if ltf.empty or h4.empty:
        return ltf
    keep = []
    h4_times = list(pd.to_datetime(h4["entry_time"], utc=True))
    for entry_time in pd.to_datetime(ltf["entry_time"], utc=True):
        keep.append(not any(abs(entry_time - h4_time) <= pd.Timedelta(hours=4) for h4_time in h4_times))
    return ltf.loc[keep].reset_index(drop=True)


def select(symbol: str, frame: pd.DataFrame, h4_symbol: pd.DataFrame):
    start, end = frame["time"].min(), frame["time"].max()
    split1 = start + (end - start) * 0.60
    split2 = start + (end - start) * 0.80
    candidates = []
    for adx in (16.0, 20.0, 24.0):
        for body in (0.30, 0.40):
            for pullback in (0.20, 0.35):
                for stop, target in ((1.0, 1.8), (1.2, 2.2), (1.4, 2.6)):
                    params = LtfParams(adx, body, pullback, stop, target)
                    frame_all = remove_h4_duplicates(generate(symbol, frame, params), h4_symbol)
                    development = frame_all[frame_all["entry_time"] < split1]
                    validation = frame_all[(frame_all["entry_time"] >= split1) & (frame_all["entry_time"] < split2)]
                    dev = statistics(development, start, split1)
                    val = statistics(validation, split1, split2)
                    if dev["trades_per_year"] < 15 or val["trades_per_year"] < 15:
                        continue
                    if dev["net_r"] <= 0 or dev["profit_factor"] < 1.08:
                        continue
                    if val["net_r"] <= 0 or val["profit_factor"] < 1.10:
                        continue
                    score = val["net_r"] + 12 * (val["profit_factor"] - 1) + 0.1 * dev["net_r"]
                    candidates.append((score, params, frame_all, dev, val))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return pd.DataFrame(), {"admitted": False, "reason": "no development and validation candidate passed"}
    _, params, selected, development, validation = candidates[0]
    holdout = selected[selected["entry_time"] >= split2]
    holdout_stats = statistics(holdout, split2, end)
    admitted = holdout_stats["trades_per_year"] >= 15 and holdout_stats["net_r"] > 0 and holdout_stats["profit_factor"] >= 1.05
    report = {
        "admitted": admitted,
        "parameters": asdict(params),
        "development": development,
        "validation": validation,
        "holdout": holdout_stats,
        "reason": "passed chronological holdout" if admitted else "failed untouched holdout",
    }
    return (selected if admitted else pd.DataFrame()), report
