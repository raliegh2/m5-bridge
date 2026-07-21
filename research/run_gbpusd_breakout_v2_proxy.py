"""Reproduce the GBPUSD breakout v2 proxy on an MT5 H4 export.

Usage:
    python research/run_gbpusd_breakout_v2_proxy.py \
      --h4 GBPUSD_H4_201601040000_202607011200.csv \
      --out gbpusd_breakout_v2_trades.csv

The input may be an MT5 tab-separated export with angle-bracket column names or
a normal CSV with: time,open,high,low,close,tick_volume,spread.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ProxyParams:
    channel_bars: int = 55
    adx_min: float = 15.0
    volume_ratio_min: float = 0.80
    stop_atr: float = 2.0
    target_r: float = 2.0
    trail_atr: float = 2.5
    trail_start_r: float = 1.0
    max_hold_h4_bars: int = 90
    entry_end_hours_utc: tuple[int, ...] = (12, 16)
    risk_multiplier_12utc: float = 1.0
    risk_multiplier_16utc: float = 1.0
    quality_volume_min: float | None = None
    quality_range_atr_min: float | None = None
    quality_risk_multiplier: float = 1.0
    standard_risk_multiplier: float = 1.0


def load_h4(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep="\t")
    if len(frame.columns) == 1:
        frame = pd.read_csv(path)

    rename = {
        "<OPEN>": "open",
        "<HIGH>": "high",
        "<LOW>": "low",
        "<CLOSE>": "close",
        "<TICKVOL>": "tick_volume",
        "<SPREAD>": "spread_points",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "tick_volume": "tick_volume",
        "spread": "spread_points",
    }
    frame = frame.rename(columns=rename)

    if "<DATE>" in frame and "<TIME>" in frame:
        frame["time"] = pd.to_datetime(
            frame["<DATE>"].astype(str) + " " + frame["<TIME>"].astype(str),
            format="%Y.%m.%d %H:%M:%S",
            utc=True,
        )
    elif "time" in frame:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
    elif "Date" in frame:
        # The repository's validated V12/V10 market export uses this format.
        frame["time"] = pd.to_datetime(frame["Date"], utc=True)
    elif "date" in frame:
        frame["time"] = pd.to_datetime(frame["date"], utc=True)
    else:
        raise ValueError(
            "Input requires MT5 <DATE>/<TIME> columns or a time/date column"
        )

    required = {"time", "open", "high", "low", "close", "tick_volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if "spread_points" not in frame:
        frame["spread_points"] = 0.0

    return frame[
        ["time", "open", "high", "low", "close", "tick_volume", "spread_points"]
    ].sort_values("time").drop_duplicates("time").reset_index(drop=True)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(frame).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=frame.index,
    )
    atr_series = atr(frame, period)
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr_series
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr_series
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def prepare(h4: pd.DataFrame, params: ProxyParams = ProxyParams()) -> pd.DataFrame:
    frame = h4.copy()
    frame["bar_end"] = frame["time"] + pd.Timedelta(hours=4)
    frame["atr14"] = atr(frame, 14)
    frame["adx14"] = adx(frame, 14)
    frame["average_volume"] = frame["tick_volume"].rolling(20, min_periods=20).mean()
    frame["volume_ratio"] = frame["tick_volume"] / frame["average_volume"]
    frame["channel_high"] = (
        frame["high"].shift(1).rolling(params.channel_bars).max()
    )
    frame["channel_low"] = (
        frame["low"].shift(1).rolling(params.channel_bars).min()
    )

    daily = frame.set_index("bar_end").resample(
        "1D", label="right", closed="right"
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
    ).dropna()
    daily["daily_time"] = daily.index
    daily["ema20_d1"] = ema(daily["close"], 20)
    daily["ema50_d1"] = ema(daily["close"], 50)

    return pd.merge_asof(
        frame.sort_values("bar_end"),
        daily[["daily_time", "close", "ema20_d1", "ema50_d1"]]
        .sort_values("daily_time"),
        left_on="bar_end",
        right_on="daily_time",
        direction="backward",
        suffixes=("", "_d1"),
    )


def run(
    h4: pd.DataFrame,
    risk_percent: float = 0.50,
    spread_floor_pips: float = 0.8,
    slippage_pips: float = 0.3,
    swap_pips_per_day: float = -0.2,
    params: ProxyParams = ProxyParams(),
    entry_start: str | pd.Timestamp | None = None,
    entry_end: str | pd.Timestamp | None = None,
    prepared: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    data = prepared if prepared is not None else prepare(h4, params)
    entry_start_ts = pd.Timestamp(entry_start, tz="UTC") if isinstance(entry_start, str) else entry_start
    entry_end_ts = pd.Timestamp(entry_end, tz="UTC") if isinstance(entry_end, str) else entry_end
    balance = 100_000.0
    peak = balance
    maximum_drawdown = 0.0
    position = None
    trades: list[dict] = []
    equity_rows: list[dict] = []

    pip = 0.0001
    for index in range(200, len(data) - 1):
        row = data.iloc[index]

        if position is not None:
            position["bars"] += 1
            position["highest"] = max(position["highest"], row["high"])
            position["lowest"] = min(position["lowest"], row["low"])
            side = position["side"]
            unrealized = (
                side * (row["close"] - position["entry"]) / pip
                * 10.0 * position["lots"]
            )
            equity = balance + unrealized
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)

            exit_price = None
            reason = None
            if side == 1:
                if row["open"] <= position["stop"]:
                    exit_price, reason = row["open"], "GAP_STOP"
                elif row["low"] <= position["stop"]:
                    exit_price, reason = position["stop"], "STOP_OR_TRAIL"
                elif row["high"] >= position["target"]:
                    exit_price, reason = position["target"], "TARGET"
                elif position["bars"] >= params.max_hold_h4_bars:
                    exit_price, reason = row["close"], "TIME"
                elif (row["close"] - position["entry"]) >= params.trail_start_r * position["risk"]:
                    position["stop"] = max(
                        position["stop"],
                        position["highest"] - params.trail_atr * row["atr14"],
                    )
            else:
                if row["open"] >= position["stop"]:
                    exit_price, reason = row["open"], "GAP_STOP"
                elif row["high"] >= position["stop"]:
                    exit_price, reason = position["stop"], "STOP_OR_TRAIL"
                elif row["low"] <= position["target"]:
                    exit_price, reason = position["target"], "TARGET"
                elif position["bars"] >= params.max_hold_h4_bars:
                    exit_price, reason = row["close"], "TIME"
                elif (position["entry"] - row["close"]) >= params.trail_start_r * position["risk"]:
                    position["stop"] = min(
                        position["stop"],
                        position["lowest"] + params.trail_atr * row["atr14"],
                    )

            if exit_price is not None:
                spread = max(float(row["spread_points"]) / 10.0, spread_floor_pips)
                adjusted_exit = exit_price - side * (
                    spread / 2.0 + slippage_pips
                ) * pip
                pnl = (
                    side * (adjusted_exit - position["entry"]) / pip
                    * 10.0 * position["lots"]
                )
                holding_days = max(1, math.ceil(position["bars"] / 6))
                swap = swap_pips_per_day * holding_days * 10.0 * position["lots"]
                pnl += swap
                balance += pnl
                trades.append(
                    {
                        **position,
                        "exit_time": row["bar_end"],
                        "exit": adjusted_exit,
                        "pnl": pnl,
                        "reason": reason,
                        "balance": balance,
                    }
                )
                position = None
                peak = max(peak, balance)

        equity_rows.append(
            {
                "time": row["bar_end"],
                "equity": balance if position is None else equity,
            }
        )
        if position is not None:
            continue

        required = [
            "atr14", "adx14", "volume_ratio", "channel_high", "channel_low",
            "close_d1", "ema20_d1", "ema50_d1",
        ]
        if any(pd.isna(row[column]) for column in required):
            continue
        next_bar = data.iloc[index + 1]
        if entry_start_ts is not None and next_bar["bar_end"] < entry_start_ts:
            continue
        if entry_end_ts is not None and next_bar["bar_end"] >= entry_end_ts:
            continue
        if row["bar_end"].hour not in params.entry_end_hours_utc:
            continue

        long_regime = row["ema20_d1"] > row["ema50_d1"] and row["close_d1"] > row["ema20_d1"]
        short_regime = row["ema20_d1"] < row["ema50_d1"] and row["close_d1"] < row["ema20_d1"]
        common = (
            row["adx14"] >= params.adx_min
            and row["volume_ratio"] >= params.volume_ratio_min
        )
        long_signal = common and long_regime and row["close"] > row["channel_high"]
        short_signal = common and short_regime and row["close"] < row["channel_low"]
        if not (long_signal or short_signal):
            continue

        side = 1 if long_signal else -1
        spread = max(float(next_bar["spread_points"]) / 10.0, spread_floor_pips)
        entry = next_bar["open"] + side * (spread / 2.0 + slippage_pips) * pip
        stop_distance = min(max(params.stop_atr * row["atr14"], 20 * pip), 150 * pip)
        stop = entry - side * stop_distance
        target = entry + side * params.target_r * stop_distance
        hour_multiplier = (
            params.risk_multiplier_12utc
            if row["bar_end"].hour == 12
            else params.risk_multiplier_16utc
        )
        quality_multiplier = params.standard_risk_multiplier
        if params.quality_volume_min is not None:
            range_atr = (row["high"] - row["low"]) / row["atr14"]
            if (
                row["volume_ratio"] >= params.quality_volume_min
                and (
                    params.quality_range_atr_min is None
                    or range_atr >= params.quality_range_atr_min
                )
            ):
                quality_multiplier = params.quality_risk_multiplier
        risk_amount = (
            balance * risk_percent * hour_multiplier * quality_multiplier / 100.0
        )
        lots = math.floor(
            risk_amount / ((stop_distance / pip) * 10.0) * 100
        ) / 100
        lots = min(max(lots, 0.01), 2.0)

        position = {
            "side": side,
            "entry_time": next_bar["bar_end"],
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk": stop_distance,
            "lots": lots,
            "bars": 0,
            "highest": entry,
            "lowest": entry,
        }

    trade_frame = pd.DataFrame(trades)
    equity_frame = pd.DataFrame(equity_rows)
    if trade_frame.empty:
        metrics = {
            "ending_balance": balance,
            "net_profit": balance - 100_000,
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": maximum_drawdown,
            "sharpe": 0.0,
        }
        return trade_frame, equity_frame, metrics

    gross_profit = trade_frame.loc[trade_frame.pnl > 0, "pnl"].sum()
    gross_loss = -trade_frame.loc[trade_frame.pnl < 0, "pnl"].sum()
    daily_returns = (
        equity_frame.set_index("time")["equity"]
        .resample("1D").last().ffill().pct_change().dropna()
    )
    sharpe = (
        np.sqrt(252) * daily_returns.mean() / daily_returns.std()
        if daily_returns.std() > 0 else 0.0
    )
    metrics = {
        "ending_balance": balance,
        "net_profit": balance - 100_000,
        "trades": len(trade_frame),
        "win_rate": float((trade_frame.pnl > 0).mean()),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else float("inf"),
        "max_drawdown": maximum_drawdown,
        "sharpe": float(sharpe),
    }
    return trade_frame, equity_frame, metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--h4", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("gbpusd_breakout_v2_trades.csv"))
    parser.add_argument("--equity-out", type=Path, default=Path("gbpusd_breakout_v2_equity.csv"))
    args = parser.parse_args()

    trades, equity, metrics = run(load_h4(args.h4))
    trades.to_csv(args.out, index=False)
    equity.to_csv(args.equity_out, index=False)
    print(f"Ending balance: ${metrics['ending_balance']:,.2f}")
    print(f"Net profit: ${metrics['net_profit']:,.2f}")
    print(f"Trades: {metrics['trades']}")
    print(f"Win rate: {metrics['win_rate']:.2%}")
    print(f"Profit factor: {metrics['profit_factor']:.2f}")
    print(f"Maximum drawdown: {metrics['max_drawdown']:.2%}")
    print(f"Daily Sharpe proxy: {metrics['sharpe']:.2f}")


if __name__ == "__main__":
    main()
