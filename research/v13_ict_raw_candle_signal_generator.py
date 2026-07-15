"""Generate raw ICT activity signals from MT5 candle CSV exports.

This is the research-side candle-to-signal generator for the V13/V14.3 ICT
satellite work. It creates the raw files expected by the V14.3 candidate stream
builder:

    research/v13_ict_high_activity_out/GBPUSD_all_activity_signals.csv
    research/v13_ict_high_activity_out/GBPJPY_all_activity_signals.csv

Important safety notes:
- Research only. This script does not connect to MT5.
- It does not place orders.
- It does not modify any live bot state.
- It reads historical candle CSVs and writes derived research CSVs.

Expected candle input format is the MT5 export format with columns such as:
<DATE>, <TIME>, <OPEN>, <HIGH>, <LOW>, <CLOSE>, <TICKVOL>, <VOL>, <SPREAD>

Typical usage after copying candle exports into research/market_data:

    python research/v13_ict_raw_candle_signal_generator.py \
      --input-dir research/market_data \
      --start-date 2023-01-01 \
      --end-date 2026-07-03

Or point directly at a folder containing files like:
    GBPUSD_M1_201601040000_202607031748.csv
    GBPJPY_M1_201601040000_202607031748.csv

The output schema is intentionally minimal and matches the downstream generator:
entry_time, exit_time, r, direction, symbol, setup
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIRS = [
    ROOT / "research" / "market_data",
    ROOT / "data",
    ROOT,
]
DEFAULT_OUT = ROOT / "research" / "v13_ict_high_activity_out"
SYMBOLS = ("GBPUSD", "GBPJPY")
WINDOWS_MINUTES = (15, 30, 60)
SETUP_PRIORITY = {
    "sweep_reclaim_60": 0.0,
    "sweep_reclaim_30": 1.0,
    "sweep_reclaim_15": 2.0,
    "breakout_60_fade": 3.2,
    "breakout_30_fade": 4.2,
    "breakout_15_fade": 5.2,
}


@dataclass(frozen=True)
class SignalConfig:
    start_date: str = "2023-01-01"
    end_date: str | None = None
    hold_minutes: int = 45
    target_r: float = 1.25
    atr_window: int = 14
    min_stop_pips_fx: float = 5.0
    min_stop_pips_jpy: float = 7.5
    atr_stop_mult: float = 0.35
    sweep_buffer_atr: float = 0.02
    min_break_pips_fx: float = 0.5
    min_break_pips_jpy: float = 0.75


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def pip_size(symbol: str) -> float:
    return 0.01 if symbol.endswith("JPY") else 0.0001


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for col in frame.columns:
        key = str(col).strip().lower().replace("<", "").replace(">", "")
        if key in {"date", "time", "open", "high", "low", "close", "tickvol", "vol", "spread"}:
            rename[col] = key
    out = frame.rename(columns=rename).copy()
    required = {"date", "time", "open", "high", "low", "close"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"candle file missing required columns: {sorted(missing)}")
    out["time"] = pd.to_datetime(out["date"].astype(str) + " " + out["time"].astype(str), format="%Y.%m.%d %H:%M:%S", errors="coerce")
    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    keep = ["time", "open", "high", "low", "close"]
    if "tickvol" in out.columns:
        keep.append("tickvol")
    if "spread" in out.columns:
        keep.append("spread")
    out = out[keep].dropna(subset=["time", "open", "high", "low", "close"])
    return out.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def read_mt5_csv(path: Path) -> pd.DataFrame:
    # MT5 exports are normally tab-separated, but some saved copies may be comma-separated.
    try:
        frame = pd.read_csv(path, sep="\t")
        if len(frame.columns) <= 1:
            frame = pd.read_csv(path)
    except UnicodeDecodeError:
        frame = pd.read_csv(path, sep="\t", encoding="latin1")
        if len(frame.columns) <= 1:
            frame = pd.read_csv(path, encoding="latin1")
    return _normalize_columns(frame)


def find_symbol_file(input_dirs: Iterable[Path], symbol: str) -> Path:
    patterns = [f"{symbol}_M1_*.csv", f"{symbol}*M1*.csv", f"{symbol}_*.csv"]
    hits: list[Path] = []
    for directory in input_dirs:
        if not directory.exists():
            continue
        for pattern in patterns:
            hits.extend(sorted(directory.glob(pattern)))
    if not hits:
        searched = ", ".join(str(path) for path in input_dirs)
        raise FileNotFoundError(f"No M1 candle CSV found for {symbol}. Searched: {searched}")
    # Prefer filenames that explicitly contain M1.
    hits = sorted(hits, key=lambda p: ("M1" not in p.name.upper(), len(p.name)))
    return hits[0]


def add_atr(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    out = frame.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(window, min_periods=window).mean()
    return out


def _future_outcome(
    frame: pd.DataFrame,
    start_index: int,
    direction: int,
    entry: float,
    stop_distance: float,
    hold_bars: int,
    target_r: float,
) -> tuple[pd.Timestamp, float]:
    target = entry + direction * target_r * stop_distance
    stop = entry - direction * stop_distance
    end_index = min(len(frame) - 1, start_index + hold_bars)
    if end_index <= start_index:
        return pd.Timestamp(frame.iloc[start_index]["time"]), 0.0

    for idx in range(start_index + 1, end_index + 1):
        row = frame.iloc[idx]
        high = float(row.high)
        low = float(row.low)
        if direction > 0:
            hit_stop = low <= stop
            hit_target = high >= target
        else:
            hit_stop = high >= stop
            hit_target = low <= target
        # Conservative when both hit inside the same candle.
        if hit_stop:
            return pd.Timestamp(row.time), -1.0
        if hit_target:
            return pd.Timestamp(row.time), target_r

    exit_row = frame.iloc[end_index]
    exit_close = float(exit_row.close)
    r = direction * (exit_close - entry) / stop_distance
    r = max(-1.0, min(target_r, r))
    return pd.Timestamp(exit_row.time), float(r)


def _append_signal(
    rows: list[dict],
    frame: pd.DataFrame,
    idx: int,
    symbol: str,
    setup: str,
    direction: int,
    cfg: SignalConfig,
) -> None:
    row = frame.iloc[idx]
    pip = pip_size(symbol)
    min_stop = (cfg.min_stop_pips_jpy if symbol.endswith("JPY") else cfg.min_stop_pips_fx) * pip
    atr = float(row.atr) if not pd.isna(row.atr) else min_stop / max(cfg.atr_stop_mult, 1e-9)
    buffer = max(atr * cfg.sweep_buffer_atr, 0.1 * pip)
    entry = float(row.close)

    if direction > 0:
        structure_stop = float(row.low) - buffer
        stop_distance = max(entry - structure_stop, min_stop, atr * cfg.atr_stop_mult)
    else:
        structure_stop = float(row.high) + buffer
        stop_distance = max(structure_stop - entry, min_stop, atr * cfg.atr_stop_mult)
    if stop_distance <= 0 or not math.isfinite(stop_distance):
        return

    exit_time, r = _future_outcome(
        frame=frame,
        start_index=idx,
        direction=direction,
        entry=entry,
        stop_distance=stop_distance,
        hold_bars=cfg.hold_minutes,
        target_r=cfg.target_r,
    )
    rows.append(
        {
            "entry_time": pd.Timestamp(row.time),
            "exit_time": exit_time,
            "r": r,
            "direction": int(direction),
            "symbol": symbol,
            "setup": setup,
        }
    )


def generate_symbol_signals(symbol: str, candles: pd.DataFrame, cfg: SignalConfig) -> pd.DataFrame:
    frame = candles.copy()
    start = pd.Timestamp(cfg.start_date)
    end = pd.Timestamp(cfg.end_date) if cfg.end_date else None
    warmup_start = start - pd.Timedelta(days=5)
    frame = frame[frame["time"] >= warmup_start].copy().reset_index(drop=True)
    if end is not None:
        frame = frame[frame["time"] <= end].copy().reset_index(drop=True)
    frame = add_atr(frame, cfg.atr_window)
    rows: list[dict] = []
    pip = pip_size(symbol)
    min_break = (cfg.min_break_pips_jpy if symbol.endswith("JPY") else cfg.min_break_pips_fx) * pip

    for minutes in WINDOWS_MINUTES:
        window = int(minutes)
        ref_high = frame["high"].rolling(window, min_periods=window).max().shift(1)
        ref_low = frame["low"].rolling(window, min_periods=window).min().shift(1)
        prev_close = frame["close"].shift(1)
        prev_ref_high = ref_high.shift(1)
        prev_ref_low = ref_low.shift(1)

        sweep_sell = (frame["high"] > ref_high + min_break) & (frame["close"] < ref_high)
        sweep_buy = (frame["low"] < ref_low - min_break) & (frame["close"] > ref_low)

        breakout_fade_sell = (prev_close > prev_ref_high + min_break) & (frame["close"] < ref_high) & (frame["high"] >= ref_high)
        breakout_fade_buy = (prev_close < prev_ref_low - min_break) & (frame["close"] > ref_low) & (frame["low"] <= ref_low)

        for idx in np.flatnonzero(sweep_sell.fillna(False).to_numpy()):
            if frame.iloc[idx].time >= start:
                _append_signal(rows, frame, int(idx), symbol, f"sweep_reclaim_{minutes}", -1, cfg)
        for idx in np.flatnonzero(sweep_buy.fillna(False).to_numpy()):
            if frame.iloc[idx].time >= start:
                _append_signal(rows, frame, int(idx), symbol, f"sweep_reclaim_{minutes}", 1, cfg)
        for idx in np.flatnonzero(breakout_fade_sell.fillna(False).to_numpy()):
            if frame.iloc[idx].time >= start:
                _append_signal(rows, frame, int(idx), symbol, f"breakout_{minutes}_fade", -1, cfg)
        for idx in np.flatnonzero(breakout_fade_buy.fillna(False).to_numpy()):
            if frame.iloc[idx].time >= start:
                _append_signal(rows, frame, int(idx), symbol, f"breakout_{minutes}_fade", 1, cfg)

    if not rows:
        return pd.DataFrame(columns=["entry_time", "exit_time", "r", "direction", "symbol", "setup"])
    out = pd.DataFrame(rows)
    out = out.drop_duplicates(["entry_time", "direction", "symbol", "setup"]).copy()
    out["priority"] = out["setup"].map(SETUP_PRIORITY).fillna(99.0)
    out = out.sort_values(["entry_time", "priority", "symbol", "setup"]).drop(columns=["priority"]).reset_index(drop=True)
    return out


def parse_input_dirs(raw_values: list[str] | None) -> list[Path]:
    if not raw_values:
        return DEFAULT_INPUT_DIRS
    paths: list[Path] = []
    for value in raw_values:
        for part in re.split(r"[;,]", value):
            part = part.strip().strip('"')
            if part:
                paths.append(Path(part))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate raw ICT activity signals from MT5 candle CSVs")
    parser.add_argument("--input-dir", action="append", help="Folder containing MT5 M1 CSV exports. Can be passed multiple times.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--hold-minutes", type=int, default=45)
    parser.add_argument("--target-r", type=float, default=1.25)
    parser.add_argument("--write-combined", action="store_true", help="Also write all_symbols_all_activity_signals.csv")
    args = parser.parse_args()

    cfg = SignalConfig(start_date=args.start_date, end_date=args.end_date, hold_minutes=args.hold_minutes, target_r=args.target_r)
    input_dirs = parse_input_dirs(args.input_dir)
    args.out.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "config": asdict(cfg),
        "input_dirs": [str(path) for path in input_dirs],
        "output_dir": str(args.out),
        "symbols": {},
    }
    combined: list[pd.DataFrame] = []

    for symbol in args.symbols:
        path = find_symbol_file(input_dirs, symbol)
        candles = read_mt5_csv(path)
        signals = generate_symbol_signals(symbol, candles, cfg)
        output_path = args.out / f"{symbol}_all_activity_signals.csv"
        signals.to_csv(output_path, index=False)
        combined.append(signals)
        summary["symbols"][symbol] = {
            "input_file": str(path),
            "rows_in_candles": int(len(candles)),
            "signals": int(len(signals)),
            "output_file": str(output_path),
            "by_setup": signals["setup"].value_counts().to_dict() if not signals.empty else {},
        }

    if args.write_combined and combined:
        all_signals = pd.concat(combined, ignore_index=True).sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)
        all_path = args.out / "all_symbols_all_activity_signals.csv"
        all_signals.to_csv(all_path, index=False)
        summary["combined_output_file"] = str(all_path)
        summary["combined_signals"] = int(len(all_signals))

    summary_path = args.out / "raw_candle_signal_generator_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_safe), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=_json_safe))


if __name__ == "__main__":
    main()
