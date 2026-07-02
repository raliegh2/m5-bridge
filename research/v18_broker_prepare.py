from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import v13_expanded_assets_backtest as base
from v18_data_preflight import read_frame


def load_local(data_dir: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    return read_frame(data_dir / f"{symbol}_{timeframe}.csv")


def prepare_h4(data_dir: Path, symbol: str) -> pd.DataFrame:
    h4 = load_local(data_dir, symbol, "H4")
    d1 = load_local(data_dir, symbol, "D1")
    h4["atr14"] = base.atr(h4)
    h4["ema20"] = base.ema(h4["close"], 20)
    h4["ema50"] = base.ema(h4["close"], 50)
    h4["plus_di"], h4["minus_di"], h4["adx14"] = base.directional(h4)
    h4["range"] = h4["high"] - h4["low"]
    h4["body_ratio"] = (h4["close"] - h4["open"]).abs() / h4["range"].replace(0, np.nan)
    h4["close_location"] = (h4["close"] - h4["low"]) / h4["range"].replace(0, np.nan)
    volume = h4["tick_volume"] if "tick_volume" in h4.columns else pd.Series(1.0, index=h4.index)
    h4["avg_volume"] = volume.rolling(20, min_periods=20).mean()
    h4["volume_ratio"] = volume / h4["avg_volume"].replace(0, np.nan)
    h4["atr_ratio"] = h4["atr14"] / h4["atr14"].rolling(20, min_periods=20).mean()
    h4["end"] = h4["time"] + pd.Timedelta(hours=4)
    d1["ema20"] = base.ema(d1["close"], 20)
    d1["ema50"] = base.ema(d1["close"], 50)
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    daily = d1[["available", "close", "ema20", "ema50"]].rename(columns={
        "close": "dclose", "ema20": "dema20", "ema50": "dema50"
    })
    return pd.merge_asof(
        h4.sort_values("time"), daily.sort_values("available"),
        left_on="time", right_on="available", direction="backward",
    )


def prepare_ltf(data_dir: Path, symbol: str) -> pd.DataFrame:
    m15 = load_local(data_dir, symbol, "M15")
    h1 = load_local(data_dir, symbol, "H1")
    d1 = load_local(data_dir, symbol, "D1")
    m15["atr14"] = base.atr(m15)
    m15["range"] = m15["high"] - m15["low"]
    m15["body_ratio"] = (m15["close"] - m15["open"]).abs() / m15["range"].replace(0, np.nan)
    m15["end"] = m15["time"] + pd.Timedelta(minutes=15)
    h1["ema20"] = base.ema(h1["close"], 20)
    h1["ema50"] = base.ema(h1["close"], 50)
    _, _, h1["adx14"] = base.directional(h1)
    h1["available"] = h1["time"] + pd.Timedelta(hours=1)
    h1_context = h1[["available", "close", "ema20", "ema50", "adx14"]].rename(columns={
        "close": "h1_close", "ema20": "h1_ema20", "ema50": "h1_ema50", "adx14": "h1_adx14"
    })
    d1["ema20"] = base.ema(d1["close"], 20)
    d1["ema50"] = base.ema(d1["close"], 50)
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    d1_context = d1[["available", "close", "ema20", "ema50"]].rename(columns={
        "close": "d1_close", "ema20": "d1_ema20", "ema50": "d1_ema50"
    })
    merged = pd.merge_asof(
        m15.sort_values("time"), h1_context.sort_values("available"),
        left_on="time", right_on="available", direction="backward",
    )
    return pd.merge_asof(
        merged.sort_values("time"), d1_context.sort_values("available"),
        left_on="time", right_on="available", direction="backward",
    )
