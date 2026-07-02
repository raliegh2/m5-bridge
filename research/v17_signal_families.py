"""Reusable completed-H4 swing signal families for V17 research."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

import v13_expanded_assets_backtest as base


@dataclass(frozen=True)
class SwingParams:
    family: str
    lookback: int
    adx_min: float
    body_min: float
    touch_atr: float
    stop_atr: float
    target_r: float
    trail_atr: float
    max_bars: int
    hours: tuple[int, ...]
    risk_percent: float


def stats(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    years = max((end - start).total_seconds() / (365.25 * 86400), 0.25)
    if frame.empty:
        return {"trades": 0, "trades_per_year": 0.0, "net_r": 0.0,
                "profit_factor": 0.0, "win_rate": 0.0}
    values = frame["r_multiple"].astype(float)
    gp = values[values > 0].sum()
    gl = -values[values < 0].sum()
    return {
        "trades": int(len(frame)),
        "trades_per_year": float(len(frame) / years),
        "net_r": float(values.sum()),
        "profit_factor": float(gp / gl) if gl else float("inf"),
        "win_rate": float((values > 0).mean()),
    }


def _row(symbol: str, family: str, side: int, signal: pd.Series,
         exit_time: pd.Timestamp, result_r: float, risk: float) -> dict:
    return {
        "symbol": symbol,
        "engine": f"{symbol}_{family.upper()}",
        "setup": family.upper(),
        "side": int(side),
        "entry_time": pd.Timestamp(signal["end"]),
        "exit_time": pd.Timestamp(exit_time),
        "risk_percent": float(risk),
        "r_multiple": float(result_r),
    }


def breakout(symbol: str, h4: pd.DataFrame, p: SwingParams) -> pd.DataFrame:
    high = h4["high"].rolling(p.lookback, min_periods=p.lookback).max().shift(1)
    low = h4["low"].rolling(p.lookback, min_periods=p.lookback).min().shift(1)
    hour = h4["end"].dt.hour
    long_signal = (
        hour.isin(p.hours)
        & (h4["dclose"] > h4["dema20"])
        & (h4["dema20"] > h4["dema50"])
        & (h4["ema20"] > h4["ema50"])
        & (h4["adx14"] >= p.adx_min)
        & (h4["body_ratio"] >= p.body_min)
        & (h4["close_location"] >= 0.60)
        & (h4["close"] > high)
    )
    short_signal = (
        hour.isin(p.hours)
        & (h4["dclose"] < h4["dema20"])
        & (h4["dema20"] < h4["dema50"])
        & (h4["ema20"] < h4["ema50"])
        & (h4["adx14"] >= p.adx_min)
        & (h4["body_ratio"] >= p.body_min)
        & (h4["close_location"] <= 0.40)
        & (h4["close"] < low)
    )
    rows = []
    for i in np.flatnonzero((long_signal | short_signal).to_numpy()):
        if i + 1 >= len(h4):
            continue
        side = 1 if bool(long_signal.iloc[i]) else -1
        exit_time, result_r = base.simulate_h4_trade(
            h4, int(i), side, p.stop_atr, p.target_r,
            p.trail_atr, p.max_bars,
        )
        rows.append(_row(symbol, "swing_breakout", side, h4.iloc[i],
                         exit_time, result_r, p.risk_percent))
    return pd.DataFrame(rows)


def pullback(symbol: str, h4: pd.DataFrame, p: SwingParams) -> pd.DataFrame:
    rows = []
    allowed = set(p.hours)
    for i in range(60, len(h4) - 1):
        row = h4.iloc[i]
        if int(pd.Timestamp(row["end"]).hour) not in allowed:
            continue
        required = (row["atr14"], row["ema20"], row["ema50"], row["adx14"],
                    row["dclose"], row["dema20"], row["dema50"],
                    row["body_ratio"])
        if any(not np.isfinite(value) for value in required):
            continue
        atr_value = float(row["atr14"])
        long_regime = (
            row["dclose"] > row["dema20"] > row["dema50"]
            and row["ema20"] > row["ema50"]
        )
        short_regime = (
            row["dclose"] < row["dema20"] < row["dema50"]
            and row["ema20"] < row["ema50"]
        )
        side = 0
        if (
            long_regime and row["adx14"] >= p.adx_min
            and row["low"] <= row["ema20"] + p.touch_atr * atr_value
            and row["low"] >= row["ema50"] - 0.50 * atr_value
            and row["close"] > row["ema20"] and row["close"] > row["open"]
            and row["body_ratio"] >= p.body_min
        ):
            side = 1
        elif (
            short_regime and row["adx14"] >= p.adx_min
            and row["high"] >= row["ema20"] - p.touch_atr * atr_value
            and row["high"] <= row["ema50"] + 0.50 * atr_value
            and row["close"] < row["ema20"] and row["close"] < row["open"]
            and row["body_ratio"] >= p.body_min
        ):
            side = -1
        if not side:
            continue
        exit_time, result_r = base.simulate_h4_trade(
            h4, i, side, p.stop_atr, p.target_r, p.trail_atr, p.max_bars
        )
        rows.append(_row(symbol, "swing_pullback", side, row,
                         exit_time, result_r, p.risk_percent))
    return pd.DataFrame(rows)


def generate(symbol: str, h4: pd.DataFrame, p: SwingParams) -> pd.DataFrame:
    return breakout(symbol, h4, p) if p.family == "breakout" else pullback(symbol, h4, p)


def parameter_grid(symbol: str):
    risk = 0.20 if symbol in {"EURUSD", "GBPJPY"} else 0.25
    hour_sets = ((0, 4, 8, 12, 16, 20), (4, 8, 12, 16), (8, 12, 16, 20))
    for lookback in (20, 40):
        for adx in (15.0, 20.0):
            for body in (0.20, 0.30):
                for hours in hour_sets:
                    for stop, target in ((1.25, 2.5), (1.50, 3.0)):
                        yield SwingParams("breakout", lookback, adx, body, 0.25,
                                          stop, target, 2.0, 30, hours, risk)
    for adx in (12.0, 18.0):
        for body in (0.20, 0.30):
            for touch in (0.20, 0.40):
                for hours in hour_sets:
                    for stop, target in ((1.25, 2.0), (1.50, 2.5)):
                        yield SwingParams("pullback", 20, adx, body, touch,
                                          stop, target, 1.75, 24, hours, risk)


def params_dict(items: list[SwingParams]) -> list[dict]:
    return [asdict(item) for item in items]
