"""GBPUSD adaptation of the robust daily trend-pullback swing framework.

This module is deliberately isolated from the live MT5 engine. It is a research
proxy for adapting the stock/ETF strategy to GBPUSD using the available CSVs.

Adaptation:
- D1 defines the macro regime.
- H4 defines trend, pullback, reversal, tick-volume confirmation, and entries.
- Entries occur at the next H4 open after a completed H4 signal.
- One position maximum because only GBPUSD is traded.
- Stop: wider of pullback structure and 1.5 H4 ATR.
- Target: 2.5R.
- Trail: 3 H4 ATR.
- Maximum hold: 90 H4 bars (~15 trading days).
- Risk: 0.75% per trade with a 1.25 gap-risk buffer.
- Spread: 1.5 pips round trip.
- Conservative stop-first same-bar handling.

Usage:
    python research/gbpusd_stock_style_swing_proxy.py \
        --h4 GBPUSD_H4.csv --d1 GBPUSD_D1.csv
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    previous_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    average_up = up.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    average_down = down.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    rs = average_up / average_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )
    smoothed_tr = true_range(df).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / smoothed_tr
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / smoothed_tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["sma200"] = sma(out["close"], 200)
    out["atr"] = atr(out, 14)
    out["rsi"] = rsi(out["close"], 14)
    out["adx"] = adx(out, 14)
    out["avg_tick_volume"] = sma(out["tick_volume"], 20)
    return out


def prepare(h4: pd.DataFrame, d1: pd.DataFrame) -> pd.DataFrame:
    h4 = enrich(h4)
    d1 = enrich(d1)
    d1_view = d1[
        ["time", "close", "ema20", "ema50", "sma200", "atr", "rsi", "adx"]
    ].copy()
    d1_view["available"] = d1_view["time"] + pd.Timedelta(days=1)
    h4["bar_end"] = h4["time"] + pd.Timedelta(hours=4)
    return pd.merge_asof(
        h4.sort_values("bar_end"),
        d1_view.sort_values("available"),
        left_on="bar_end",
        right_on="available",
        direction="backward",
        suffixes=("", "_d1"),
    )


def run(h4_path: Path, d1_path: Path, d1_regime: str = "sma200") -> pd.DataFrame:
    data = prepare(load_csv(h4_path), load_csv(d1_path))
    balance = 100_000.0
    peak = balance
    max_drawdown = 0.0
    position = None
    trades: list[dict] = []

    pip = 0.0001
    round_trip_spread_pips = 1.5
    risk_fraction = 0.0075
    gap_buffer = 1.25

    for index in range(1, len(data) - 1):
        row = data.iloc[index]

        if position is not None:
            position["bars"] += 1
            position["highest"] = max(position["highest"], row["high"])
            position["lowest"] = min(position["lowest"], row["low"])
            exit_price = None
            reason = None

            if position["side"] == "LONG":
                position["stop"] = max(
                    position["stop"], position["highest"] - 3.0 * row["atr"]
                )
                if row["open"] <= position["stop"]:
                    exit_price, reason = row["open"], "GAP_STOP"
                elif row["low"] <= position["stop"]:
                    exit_price, reason = position["stop"], "STOP_OR_TRAIL"
                elif row["high"] >= position["target"]:
                    exit_price, reason = position["target"], "TARGET"
                elif position["bars"] >= 90:
                    exit_price, reason = row["close"], "TIME"
                elif position["bars"] >= 18 and row["close"] < row["ema50"]:
                    exit_price, reason = row["close"], "REVERSAL"
            else:
                position["stop"] = min(
                    position["stop"], position["lowest"] + 3.0 * row["atr"]
                )
                if row["open"] >= position["stop"]:
                    exit_price, reason = row["open"], "GAP_STOP"
                elif row["high"] >= position["stop"]:
                    exit_price, reason = position["stop"], "STOP_OR_TRAIL"
                elif row["low"] <= position["target"]:
                    exit_price, reason = position["target"], "TARGET"
                elif position["bars"] >= 90:
                    exit_price, reason = row["close"], "TIME"
                elif position["bars"] >= 18 and row["close"] > row["ema50"]:
                    exit_price, reason = row["close"], "REVERSAL"

            if exit_price is not None:
                half_spread = round_trip_spread_pips * pip / 2
                adjusted_exit = (
                    exit_price - half_spread
                    if position["side"] == "LONG"
                    else exit_price + half_spread
                )
                move = (
                    adjusted_exit - position["entry"]
                    if position["side"] == "LONG"
                    else position["entry"] - adjusted_exit
                )
                pnl = (move / pip) * 10.0 * position["lots"]
                balance += pnl
                trades.append(
                    {
                        **position,
                        "exit_time": row["time"],
                        "exit": adjusted_exit,
                        "pnl": pnl,
                        "reason": reason,
                    }
                )
                position = None

        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, (peak - balance) / peak)
        if position is not None:
            continue

        required = [
            "ema20", "ema50", "sma200", "atr", "rsi", "adx",
            "avg_tick_volume", "close_d1", "ema20_d1", "ema50_d1",
            "sma200_d1",
        ]
        if any(pd.isna(row.get(column, np.nan)) for column in required):
            continue

        previous = data.iloc[index - 1]
        body = abs(row["close"] - row["open"])
        lower_wick = min(row["open"], row["close"]) - row["low"]
        upper_wick = row["high"] - max(row["open"], row["close"])
        bullish_reversal = row["close"] > row["open"] and (
            row["close"] > previous["high"] or lower_wick >= body
        )
        bearish_reversal = row["close"] < row["open"] and (
            row["close"] < previous["low"] or upper_wick >= body
        )
        volume_ok = row["tick_volume"] >= 0.8 * row["avg_tick_volume"]
        tolerance = 0.35 * row["atr"]

        if d1_regime == "ema":
            d1_long = row["ema20_d1"] > row["ema50_d1"]
            d1_short = row["ema20_d1"] < row["ema50_d1"]
        else:
            d1_long = row["close_d1"] > row["sma200_d1"]
            d1_short = row["close_d1"] < row["sma200_d1"]

        long_signal = (
            d1_long
            and row["close"] > row["sma200"]
            and row["ema20"] > row["ema50"]
            and row["adx"] >= 20
            and row["low"] <= row["ema20"] + tolerance
            and row["close"] >= row["ema20"]
            and 40 <= row["rsi"] <= 58
            and volume_ok
            and bullish_reversal
        )
        short_signal = (
            d1_short
            and row["close"] < row["sma200"]
            and row["ema20"] < row["ema50"]
            and row["adx"] >= 20
            and row["high"] >= row["ema20"] - tolerance
            and row["close"] <= row["ema20"]
            and 42 <= row["rsi"] <= 60
            and volume_ok
            and bearish_reversal
        )
        if not (long_signal or short_signal):
            continue

        next_bar = data.iloc[index + 1]
        side = "LONG" if long_signal else "SHORT"
        half_spread = round_trip_spread_pips * pip / 2
        entry = (
            next_bar["open"] + half_spread
            if side == "LONG"
            else next_bar["open"] - half_spread
        )

        if side == "LONG":
            stop = min(row["low"] - 0.25 * row["atr"], entry - 1.5 * row["atr"])
            risk_distance = entry - stop
            target = entry + 2.5 * risk_distance
        else:
            stop = max(row["high"] + 0.25 * row["atr"], entry + 1.5 * row["atr"])
            risk_distance = stop - entry
            target = entry - 2.5 * risk_distance

        risk_budget = balance * risk_fraction
        raw_lots = risk_budget / ((risk_distance / pip) * 10.0 * gap_buffer)
        lots = min(2.0, max(0.01, math.floor(raw_lots * 100) / 100))
        position = {
            "side": side,
            "entry_time": next_bar["time"],
            "entry": entry,
            "stop": stop,
            "target": target,
            "initial_risk": risk_distance,
            "lots": lots,
            "bars": 0,
            "highest": entry,
            "lowest": entry,
        }

    if position is not None:
        row = data.iloc[-1]
        half_spread = round_trip_spread_pips * pip / 2
        adjusted_exit = (
            row["close"] - half_spread
            if position["side"] == "LONG"
            else row["close"] + half_spread
        )
        move = (
            adjusted_exit - position["entry"]
            if position["side"] == "LONG"
            else position["entry"] - adjusted_exit
        )
        pnl = (move / pip) * 10.0 * position["lots"]
        balance += pnl
        trades.append(
            {
                **position,
                "exit_time": row["time"],
                "exit": adjusted_exit,
                "pnl": pnl,
                "reason": "END_OF_DATA",
            }
        )

    result = pd.DataFrame(trades)
    if result.empty:
        print("No trades generated.")
        return result

    gross_profit = result.loc[result["pnl"] > 0, "pnl"].sum()
    gross_loss = -result.loc[result["pnl"] < 0, "pnl"].sum()
    profit_factor = gross_profit / gross_loss if gross_loss else np.inf

    print(f"D1 regime: {d1_regime}")
    print(f"Starting balance: $100,000.00")
    print(f"Ending balance: ${balance:,.2f}")
    print(f"Net P&L: ${balance - 100_000:,.2f}")
    print(f"Trades: {len(result)}")
    print(f"Win rate: {(result['pnl'] > 0).mean():.2%}")
    print(f"Profit factor: {profit_factor:.2f}")
    print(f"Max closed-equity drawdown: {max_drawdown:.2%}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--h4", type=Path, required=True)
    parser.add_argument("--d1", type=Path, required=True)
    parser.add_argument(
        "--d1-regime", choices=("sma200", "ema"), default="sma200"
    )
    parser.add_argument("--trades-out", type=Path, default=Path("gbpusd_trades.csv"))
    args = parser.parse_args()
    trades = run(args.h4, args.d1, args.d1_regime)
    if not trades.empty:
        trades.to_csv(args.trades_out, index=False)


if __name__ == "__main__":
    main()
