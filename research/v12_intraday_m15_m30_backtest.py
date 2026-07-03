"""Separate M15-entry/M30-confirmation intraday research backtest.

This experiment copies V12's completed-bar, trend-alignment, fixed-fractional
risk, and fail-closed principles. It does not import or modify the final V12
signal model. Entries are decided on completed M15 bars and filled at the next
available M5 open. M5 bars are retained for conservative stop/target ordering.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


PIP = 0.0001
PIP_VALUE_PER_LOT = 10.0
STARTING_BALANCE = 5_000.0


@dataclass(frozen=True)
class IntradayParams:
    adx_min: float = 18.0
    stop_atr: float = 1.2
    reward_risk: float = 1.5
    channel_bars: int = 12
    max_hold_m5_bars: int = 36
    risk_percent: float = 0.25
    spread_pips: float = 1.0
    slippage_pips_each_side: float = 0.2
    session_start_utc: int = 7
    session_end_utc: int = 17


def ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False,
                          min_periods=period).mean()


def rsi(values: pd.Series, period: int = 14) -> pd.Series:
    change = values.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / period, adjust=False,
                                    min_periods=period).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / period, adjust=False,
                                       min_periods=period).mean()
    relative = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + relative)


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0),
                        index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0),
                         index=frame.index)
    atr_values = atr(frame, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False,
                                min_periods=period).mean() / atr_values
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False,
                                  min_periods=period).mean() / atr_values
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def load_m5(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing M5 columns: {sorted(missing)}")
    if pd.api.types.is_numeric_dtype(frame["time"]):
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    else:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame[["time", "open", "high", "low", "close"]].dropna().sort_values(
        "time").drop_duplicates("time").reset_index(drop=True)


def resample_bars(m5: pd.DataFrame, minutes: int) -> pd.DataFrame:
    expected = minutes // 5
    bars = m5.set_index("time").resample(
        f"{minutes}min", closed="left", label="left"
    ).agg(open=("open", "first"), high=("high", "max"),
          low=("low", "min"), close=("close", "last"),
          source_bars=("close", "count"))
    bars = bars[bars["source_bars"] == expected].dropna().reset_index()
    bars["bar_end"] = bars["time"] + pd.Timedelta(minutes=minutes)
    return bars


def prepare_signals(m5: pd.DataFrame, params: IntradayParams) -> pd.DataFrame:
    m15 = resample_bars(m5, 15)
    m30 = resample_bars(m5, 30)
    m15["atr14"] = atr(m15)
    m15["ema9"] = ema(m15["close"], 9)
    m15["ema20"] = ema(m15["close"], 20)
    m15["rsi14"] = rsi(m15["close"])
    m15["prior_high"] = m15["high"].shift(1).rolling(params.channel_bars).max()
    m15["prior_low"] = m15["low"].shift(1).rolling(params.channel_bars).min()

    m30["m30_ema20"] = ema(m30["close"], 20)
    m30["m30_ema50"] = ema(m30["close"], 50)
    m30["m30_adx14"] = adx(m30)
    m30 = m30.rename(columns={"close": "m30_close"})
    joined = pd.merge_asof(
        m15.sort_values("bar_end"),
        m30[["bar_end", "m30_close", "m30_ema20", "m30_ema50", "m30_adx14"]]
        .sort_values("bar_end"),
        on="bar_end", direction="backward", allow_exact_matches=True,
    )
    hour = joined["bar_end"].dt.hour
    in_session = hour.between(params.session_start_utc,
                              params.session_end_utc - 1)
    long_trend = (
        (joined["m30_close"] > joined["m30_ema20"])
        & (joined["m30_ema20"] > joined["m30_ema50"])
        & (joined["m30_adx14"] >= params.adx_min)
    )
    short_trend = (
        (joined["m30_close"] < joined["m30_ema20"])
        & (joined["m30_ema20"] < joined["m30_ema50"])
        & (joined["m30_adx14"] >= params.adx_min)
    )
    long_pullback = (
        (joined["close"].shift(1) <= joined["ema9"].shift(1))
        & (joined["close"] > joined["ema9"])
        & (joined["close"] > joined["open"])
        & joined["rsi14"].between(50, 68)
    )
    short_pullback = (
        (joined["close"].shift(1) >= joined["ema9"].shift(1))
        & (joined["close"] < joined["ema9"])
        & (joined["close"] < joined["open"])
        & joined["rsi14"].between(32, 50)
    )
    long_breakout = joined["close"] > joined["prior_high"]
    short_breakout = joined["close"] < joined["prior_low"]
    joined["side"] = np.where(
        in_session & long_trend & (long_pullback | long_breakout), 1,
        np.where(in_session & short_trend & (short_pullback | short_breakout), -1, 0),
    )
    joined["setup"] = np.where(
        joined["side"] == 0, "",
        np.where(long_breakout | short_breakout, "M15_BREAKOUT", "M15_PULLBACK"),
    )
    return joined.loc[joined["side"] != 0, [
        "bar_end", "side", "setup", "atr14", "m30_adx14"
    ]].dropna().drop_duplicates("bar_end").reset_index(drop=True)


def _volume(balance: float, risk_percent: float, stop_pips: float) -> float:
    raw = balance * risk_percent / 100 / (stop_pips * PIP_VALUE_PER_LOT)
    return math.floor((raw + 1e-12) / 0.01) * 0.01


def run_backtest(m5: pd.DataFrame, params: IntradayParams,
                 starting_balance: float = STARTING_BALANCE) -> tuple[dict, pd.DataFrame]:
    signals = prepare_signals(m5, params)
    bars = m5.set_index("time")
    balance = starting_balance
    peak = balance
    max_drawdown = 0.0
    next_available = m5["time"].min()
    day_start_balance = balance
    current_day = None
    trades: list[dict] = []
    half_spread = params.spread_pips * PIP / 2
    slip = params.slippage_pips_each_side * PIP

    for signal in signals.itertuples(index=False):
        if signal.bar_end < next_available or signal.bar_end not in bars.index:
            continue
        day = signal.bar_end.date()
        if day != current_day:
            current_day = day
            day_start_balance = balance
        if balance <= day_start_balance * 0.99:
            continue
        future = m5[m5["time"] >= signal.bar_end].head(params.max_hold_m5_bars)
        if future.empty:
            continue
        first = future.iloc[0]
        side = int(signal.side)
        entry = float(first["open"]) + side * (half_spread + slip)
        stop_distance = float(signal.atr14) * params.stop_atr
        stop_pips = stop_distance / PIP
        volume = _volume(balance, params.risk_percent, stop_pips)
        if volume < 0.01:
            continue
        stop = entry - side * stop_distance
        target = entry + side * stop_distance * params.reward_risk
        exit_price = None
        exit_time = None
        reason = "TIME"
        for bar in future.itertuples(index=False):
            bid_low = float(bar.low) - half_spread
            bid_high = float(bar.high) - half_spread
            ask_low = float(bar.low) + half_spread
            ask_high = float(bar.high) + half_spread
            stop_hit = bid_low <= stop if side > 0 else ask_high >= stop
            target_hit = bid_high >= target if side > 0 else ask_low <= target
            if stop_hit:  # Conservative when both occur inside one M5 bar.
                exit_price = stop - side * slip
                exit_time = bar.time + pd.Timedelta(minutes=5)
                reason = "STOP"
                break
            if target_hit:
                exit_price = target - side * slip
                exit_time = bar.time + pd.Timedelta(minutes=5)
                reason = "TARGET"
                break
        if exit_price is None:
            last = future.iloc[-1]
            exit_price = float(last["close"]) - side * (half_spread + slip)
            exit_time = last["time"] + pd.Timedelta(minutes=5)
        pnl = side * (exit_price - entry) / PIP * PIP_VALUE_PER_LOT * volume
        balance += pnl
        peak = max(peak, balance)
        drawdown = (peak - balance) / peak * 100
        max_drawdown = max(max_drawdown, drawdown)
        trades.append({
            "entry_time": signal.bar_end, "exit_time": exit_time,
            "side": side, "setup": signal.setup, "volume": volume,
            "risk_percent": params.risk_percent, "entry": entry,
            "stop": stop, "target": target, "exit": exit_price,
            "reason": reason, "pnl": pnl, "balance": balance,
            "drawdown_percent": drawdown,
        })
        next_available = exit_time

    ledger = pd.DataFrame(trades)
    if ledger.empty:
        return {"trades": 0, "net_profit": 0.0}, ledger
    gross_profit = float(ledger.loc[ledger.pnl > 0, "pnl"].sum())
    gross_loss = float(-ledger.loc[ledger.pnl < 0, "pnl"].sum())
    all_weeks = pd.date_range(
        m5.time.min().normalize(), m5.time.max().normalize(), freq="W-SUN", tz="UTC")
    weekly = ledger.set_index("exit_time")["pnl"].resample("W-SUN").sum().reindex(
        all_weeks, fill_value=0.0)
    metrics = {
        "start": m5.time.min().isoformat(), "end": m5.time.max().isoformat(),
        "starting_balance": starting_balance, "ending_balance": balance,
        "net_profit": balance - starting_balance,
        "return_percent": (balance / starting_balance - 1) * 100,
        "trades": len(ledger), "trades_per_week": len(ledger) / max(len(weekly), 1),
        "win_rate": float((ledger.pnl > 0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown_percent": max_drawdown,
        "average_weekly_profit": float(weekly.mean()),
        "median_weekly_profit": float(weekly.median()),
        "weeks_at_or_above_50": int((weekly >= 50).sum()),
        "total_weeks": len(weekly),
    }
    return metrics, ledger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("GBPUSD_M5.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("research/v12_intraday_m15_m30_output"))
    args = parser.parse_args()
    m5 = load_m5(args.data)
    split = int(len(m5) * 0.60)
    development = m5.iloc[:split].copy()
    validation = m5.iloc[split:].copy()
    candidates = []
    for adx_min in (16.0, 18.0, 20.0, 22.0):
        for stop_atr in (1.0, 1.2, 1.5):
            for reward_risk in (1.25, 1.5, 2.0):
                params = IntradayParams(adx_min=adx_min, stop_atr=stop_atr,
                                        reward_risk=reward_risk)
                metrics, _ = run_backtest(development, params)
                score = metrics.get("net_profit", 0.0)
                if metrics.get("trades", 0) < 30 or (metrics.get("profit_factor") or 0) < 1.0:
                    score = -math.inf
                candidates.append((score, params, metrics))
    _, selected, development_metrics = max(candidates, key=lambda item: item[0])
    validation_metrics, validation_ledger = run_backtest(validation, selected)
    full_metrics, full_ledger = run_backtest(m5, selected)
    payload = {
        "status": "RESEARCH_ONLY",
        "target": "$50 average profit per week (measurement, not a guarantee)",
        "selected_params": asdict(selected),
        "development_60_percent": development_metrics,
        "validation_40_percent": validation_metrics,
        "full_sample_reference": full_metrics,
        "limitations": [
            "Only 246 calendar days of GBPUSD M5 data are available.",
            "Parameters were selected on the first 60% and require longer out-of-sample testing.",
            "Fixed spread/slippage cannot reproduce every broker fill.",
            "A weekly dollar profit cannot be guaranteed or enforced safely.",
        ],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(payload, indent=2) + "\n",
                                            encoding="utf-8")
    validation_ledger.to_csv(args.out / "validation_trades.csv", index=False)
    full_ledger.to_csv(args.out / "full_sample_trades.csv", index=False)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
