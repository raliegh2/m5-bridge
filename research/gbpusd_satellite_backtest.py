"""Standalone research backtester for the GBPUSD H1/M30 satellite engine."""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PIP = 0.0001
PIP_VALUE_PER_LOT = 10.0


@dataclass(frozen=True)
class SatelliteConfig:
    initial_balance: float = 5000.0
    risk_percent: float = 0.12
    h1_adx_min: float = 20.0
    channel_bars: int = 12
    body_ratio_min: float = 0.40
    volume_ratio_min: float = 1.00
    long_rsi_min: float = 55.0
    short_rsi_max: float = 45.0
    session_start_hour_utc: int = 9
    session_end_hour_utc: int = 17
    force_flat_hour_utc: int = 20
    stop_atr: float = 1.75
    target_r: float = 1.75
    min_stop_pips: float = 8.0
    max_stop_pips: float = 35.0
    max_hold_bars: int = 12
    spread_floor_pips: float = 0.8
    slippage_pips: float = 0.3
    min_lot: float = 0.01
    max_lot: float = 2.0
    lot_step: float = 0.01
    max_spread_pips: float = 2.0
    daily_loss_limit: float = 250.0
    total_loss_limit: float = 500.0


@dataclass
class Position:
    side: int
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    initial_risk_price: float
    initial_risk_dollars: float
    lots: float
    bars: int = 0


def load_mt5_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep="\t")
    if len(frame.columns) == 1:
        frame = pd.read_csv(path)
    frame = frame.rename(columns={
        "<OPEN>": "open", "<HIGH>": "high", "<LOW>": "low",
        "<CLOSE>": "close", "<TICKVOL>": "tick_volume",
        "<SPREAD>": "spread_points", "spread": "spread_points",
    })
    if "<DATE>" in frame and "<TIME>" in frame:
        frame["time"] = pd.to_datetime(
            frame["<DATE>"].astype(str) + " " + frame["<TIME>"].astype(str),
            format="%Y.%m.%d %H:%M:%S", utc=True,
        )
    elif "time" in frame:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
    else:
        raise ValueError("CSV requires time or <DATE>/<TIME> columns")
    if "spread_points" not in frame:
        frame["spread_points"] = 0.0
    required = {"time", "open", "high", "low", "close", "tick_volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    return frame[[
        "time", "open", "high", "low", "close", "tick_volume", "spread_points"
    ]].sort_values("time").drop_duplicates("time").reset_index(drop=True)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=frame.index)
    average_range = atr(frame, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / average_range
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / average_range
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def prepare_data(m30: pd.DataFrame, cfg: SatelliteConfig) -> pd.DataFrame:
    frame = m30.copy()
    frame["bar_end"] = frame["time"] + pd.Timedelta(minutes=30)
    frame["atr14"] = atr(frame, 14)
    frame["rsi14"] = rsi(frame["close"], 14)
    frame["average_volume"] = frame["tick_volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["tick_volume"] / frame["average_volume"]
    candle_range = (frame["high"] - frame["low"]).replace(0, np.nan)
    frame["body_ratio"] = (frame["close"] - frame["open"]).abs() / candle_range
    frame["channel_high"] = frame["high"].shift(1).rolling(cfg.channel_bars).max()
    frame["channel_low"] = frame["low"].shift(1).rolling(cfg.channel_bars).min()

    h1 = frame.set_index("time").resample("1h", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), tick_volume=("tick_volume", "sum"),
    ).dropna()
    h1["h1_time"] = h1.index + pd.Timedelta(hours=1)
    h1["ema20_h1"] = ema(h1["close"], 20)
    h1["ema50_h1"] = ema(h1["close"], 50)
    h1["adx14_h1"] = adx(h1.reset_index(drop=True), 14).to_numpy()

    return pd.merge_asof(
        frame.sort_values("bar_end"),
        h1[["h1_time", "close", "ema20_h1", "ema50_h1", "adx14_h1"]].sort_values("h1_time"),
        left_on="bar_end", right_on="h1_time", direction="backward", suffixes=("", "_h1"),
    )


def signal(row: pd.Series, cfg: SatelliteConfig) -> int:
    if row["bar_end"].dayofweek >= 5:
        return 0
    if not cfg.session_start_hour_utc <= row["bar_end"].hour < cfg.session_end_hour_utc:
        return 0
    required = [
        row["atr14"], row["rsi14"], row["volume_ratio"], row["body_ratio"],
        row["channel_high"], row["channel_low"], row["ema20_h1"],
        row["ema50_h1"], row["adx14_h1"], row["close_h1"],
    ]
    if any(pd.isna(value) for value in required):
        return 0
    quality = (
        row["body_ratio"] >= cfg.body_ratio_min
        and row["volume_ratio"] >= cfg.volume_ratio_min
        and row["adx14_h1"] >= cfg.h1_adx_min
    )
    if not quality:
        return 0
    long_trend = row["ema20_h1"] > row["ema50_h1"] and row["close_h1"] > row["ema20_h1"]
    short_trend = row["ema20_h1"] < row["ema50_h1"] and row["close_h1"] < row["ema20_h1"]
    if long_trend and row["rsi14"] >= cfg.long_rsi_min and row["close"] > row["channel_high"]:
        return 1
    if short_trend and row["rsi14"] <= cfg.short_rsi_max and row["close"] < row["channel_low"]:
        return -1
    return 0


def _round_lot(raw: float, cfg: SatelliteConfig) -> float:
    stepped = math.floor(raw / cfg.lot_step) * cfg.lot_step
    return round(min(max(stepped, cfg.min_lot), cfg.max_lot), 2)


def backtest(m30: pd.DataFrame, cfg: SatelliteConfig = SatelliteConfig(),
             start: Optional[str] = None, end: Optional[str] = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    data = prepare_data(m30, cfg)
    start_ts = pd.Timestamp(start, tz="UTC") if start else None
    end_ts = pd.Timestamp(end, tz="UTC") if end else None
    balance = cfg.initial_balance
    peak_equity = balance
    maximum_drawdown = 0.0
    position: Optional[Position] = None
    last_entry_date = None
    current_day = None
    day_start_balance = balance
    disabled = False
    trades: list[dict] = []
    equity_rows: list[dict] = []

    for index in range(120, len(data) - 1):
        row = data.iloc[index]
        timestamp = row["bar_end"]
        if start_ts is not None and timestamp < start_ts:
            continue
        if end_ts is not None and timestamp >= end_ts:
            break
        if timestamp.date() != current_day:
            current_day = timestamp.date()
            day_start_balance = balance
        equity = balance
        if position is not None:
            unrealized = position.side * (row["close"] - position.entry) / PIP * PIP_VALUE_PER_LOT * position.lots
            equity = balance + unrealized
            peak_equity = max(peak_equity, equity)
            maximum_drawdown = max(maximum_drawdown, (peak_equity - equity) / peak_equity if peak_equity else 0.0)
            exit_price = None
            reason = None
            if position.side == 1:
                stop_hit = row["low"] <= position.stop
                target_hit = row["high"] >= position.target
            else:
                stop_hit = row["high"] >= position.stop
                target_hit = row["low"] <= position.target
            if stop_hit:
                exit_price, reason = position.stop, "STOP"
            elif target_hit:
                exit_price, reason = position.target, "TARGET"
            elif position.bars >= cfg.max_hold_bars:
                exit_price, reason = row["close"], "TIME_EXIT"
            elif timestamp.hour >= cfg.force_flat_hour_utc:
                exit_price, reason = row["close"], "SESSION_EXIT"
            if exit_price is not None:
                spread = max(float(row["spread_points"]) / 10, cfg.spread_floor_pips)
                executed = exit_price - position.side * (spread / 2 + cfg.slippage_pips) * PIP
                pnl = position.side * (executed - position.entry) / PIP * PIP_VALUE_PER_LOT * position.lots
                before = balance
                balance += pnl
                trades.append({
                    **asdict(position), "exit_time": timestamp, "exit": executed,
                    "pnl": pnl, "balance_before": before, "balance": balance,
                    "return_fraction": pnl / before if before else 0.0,
                    "r_multiple": pnl / position.initial_risk_dollars,
                    "reason": reason, "engine": "SATELLITE_INTRADAY",
                })
                position = None
                equity = balance
                peak_equity = max(peak_equity, equity)
            else:
                position.bars += 1
        equity_rows.append({"time": timestamp, "balance": balance, "equity": equity})
        if position is not None or disabled:
            continue
        if max(0.0, day_start_balance - balance) >= cfg.daily_loss_limit:
            continue
        if max(0.0, cfg.initial_balance - balance) >= cfg.total_loss_limit:
            disabled = True
            continue
        if last_entry_date == timestamp.date():
            continue
        selected = signal(row, cfg)
        if selected == 0:
            continue
        next_bar = data.iloc[index + 1]
        next_time = next_bar["bar_end"]
        if end_ts is not None and next_time >= end_ts:
            continue
        spread = max(float(next_bar["spread_points"]) / 10, cfg.spread_floor_pips)
        if spread > cfg.max_spread_pips:
            continue
        entry = next_bar["open"] + selected * (spread / 2 + cfg.slippage_pips) * PIP
        stop_distance = min(
            max(cfg.stop_atr * row["atr14"], cfg.min_stop_pips * PIP),
            cfg.max_stop_pips * PIP,
        )
        risk_budget = balance * cfg.risk_percent / 100
        lots = _round_lot(risk_budget / ((stop_distance / PIP) * PIP_VALUE_PER_LOT), cfg)
        actual_risk = (stop_distance / PIP) * PIP_VALUE_PER_LOT * lots
        position = Position(
            side=selected, entry_time=next_time, entry=entry,
            stop=entry - selected * stop_distance,
            target=entry + selected * cfg.target_r * stop_distance,
            initial_risk_price=stop_distance, initial_risk_dollars=actual_risk, lots=lots,
        )
        last_entry_date = timestamp.date()

    trades_frame = pd.DataFrame(trades)
    equity_frame = pd.DataFrame(equity_rows)
    gross_profit = trades_frame.loc[trades_frame.pnl > 0, "pnl"].sum() if not trades_frame.empty else 0.0
    gross_loss = -trades_frame.loc[trades_frame.pnl < 0, "pnl"].sum() if not trades_frame.empty else 0.0
    metrics = {
        "start": str(start_ts or data["bar_end"].min()),
        "end": str(end_ts or data["bar_end"].max()),
        "initial_balance": cfg.initial_balance,
        "ending_balance": balance,
        "net_profit": balance - cfg.initial_balance,
        "return_percent": (balance / cfg.initial_balance - 1) * 100,
        "trades": len(trades_frame),
        "win_rate": float((trades_frame.pnl > 0).mean()) if not trades_frame.empty else 0.0,
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else float("inf"),
        "maximum_drawdown_percent": maximum_drawdown * 100,
    }
    return trades_frame, equity_frame, metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m30", type=Path, required=True)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--risk-percent", type=float, default=0.12)
    parser.add_argument("--out", type=Path, default=Path("satellite_trades.csv"))
    parser.add_argument("--equity-out", type=Path, default=Path("satellite_equity.csv"))
    args = parser.parse_args()
    config = SatelliteConfig(risk_percent=args.risk_percent)
    trades, equity, metrics = backtest(load_mt5_csv(args.m30), config, args.start, args.end)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(args.out, index=False)
    equity.to_csv(args.equity_out, index=False)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
