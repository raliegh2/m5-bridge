"""V13 commodity strategy research with frozen out-of-sample admission.

This script tests three distinct completed-H4 strategy families on AUDUSD,
USDCAD and NZDUSD:

1. D1/H4 trend pullback continuation;
2. H4 breakout-and-retest continuation;
3. low-ADX Bollinger re-entry mean reversion.

Parameters are selected using only the first 70% of each symbol's history. The
last 30% is untouched until final validation. A commodity pair is admitted only
when its selected strategy records at least 60 validation trades, positive net
R and a validation profit factor of at least 1.10.

The script is research-only. It does not import MetaTrader5 or send orders.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import v13_expanded_assets_backtest as base

OUT = base.ROOT / "research" / "v13_commodity_output"
OUT.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class CommodityParams:
    family: str
    stop_atr: float
    target_r: float
    trail_atr: float
    max_h4_bars: int
    risk_percent: float = 0.25
    adx_threshold: float = 18.0
    touch_atr: float = 0.25
    body_ratio_min: float = 0.20
    lookback: int = 30
    search_bars: int = 12
    band_period: int = 20
    band_std: float = 2.0


def _simulate(h4: pd.DataFrame, index: int, side: int, p: CommodityParams):
    return base.simulate_h4_trade(
        h4, index, side, p.stop_atr, p.target_r,
        p.trail_atr, p.max_h4_bars,
    )


def trend_pullback_candidates(symbol: str, h4: pd.DataFrame,
                              p: CommodityParams) -> pd.DataFrame:
    rows = []
    for i in range(60, len(h4) - 1):
        row = h4.iloc[i]
        required = [
            row["atr14"], row["ema20"], row["ema50"], row["adx14"],
            row["dclose"], row["dema20"], row["dema50"], row["body_ratio"],
        ]
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
            long_regime and row["adx14"] >= p.adx_threshold
            and row["low"] <= row["ema20"] + p.touch_atr * atr_value
            and row["low"] >= row["ema50"] - 0.35 * atr_value
            and row["close"] > row["ema20"] and row["close"] > row["open"]
            and row["body_ratio"] >= p.body_ratio_min
        ):
            side = 1
        elif (
            short_regime and row["adx14"] >= p.adx_threshold
            and row["high"] >= row["ema20"] - p.touch_atr * atr_value
            and row["high"] <= row["ema50"] + 0.35 * atr_value
            and row["close"] < row["ema20"] and row["close"] < row["open"]
            and row["body_ratio"] >= p.body_ratio_min
        ):
            side = -1
        if not side:
            continue
        exit_time, result_r = _simulate(h4, i, side, p)
        rows.append({
            "symbol": symbol, "engine": f"{symbol}_TREND_PULLBACK",
            "setup": "D1_H4_EMA_PULLBACK", "side": side,
            "entry_time": pd.Timestamp(row["end"]), "exit_time": exit_time,
            "risk_percent": p.risk_percent, "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def breakout_retest_candidates(symbol: str, h4: pd.DataFrame,
                               p: CommodityParams) -> pd.DataFrame:
    prior_high = h4["high"].rolling(p.lookback, min_periods=p.lookback).max().shift(1)
    prior_low = h4["low"].rolling(p.lookback, min_periods=p.lookback).min().shift(1)
    breakout_side = np.where(
        (h4["dclose"] > h4["dema20"]) & (h4["dema20"] > h4["dema50"])
        & (h4["close"] > prior_high) & (h4["adx14"] >= p.adx_threshold),
        1,
        np.where(
            (h4["dclose"] < h4["dema20"]) & (h4["dema20"] < h4["dema50"])
            & (h4["close"] < prior_low) & (h4["adx14"] >= p.adx_threshold),
            -1, 0,
        ),
    )
    levels = np.where(breakout_side > 0, prior_high, np.where(breakout_side < 0, prior_low, np.nan))
    rows = []
    last_breakout = None
    for i in range(p.lookback + 2, len(h4) - 1):
        if breakout_side[i] != 0:
            last_breakout = (i, int(breakout_side[i]), float(levels[i]))
            continue
        if last_breakout is None:
            continue
        breakout_index, side, level = last_breakout
        age = i - breakout_index
        if age < 1 or age > p.search_bars:
            if age > p.search_bars:
                last_breakout = None
            continue
        row = h4.iloc[i]
        atr_value = float(row["atr14"])
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        body_ok = float(row["body_ratio"]) >= p.body_ratio_min
        if side > 0:
            valid = (
                row["low"] <= level + p.touch_atr * atr_value
                and row["low"] >= level - 0.75 * atr_value
                and row["close"] > level and row["close"] > row["open"]
                and row["ema20"] > row["ema50"] and body_ok
            )
        else:
            valid = (
                row["high"] >= level - p.touch_atr * atr_value
                and row["high"] <= level + 0.75 * atr_value
                and row["close"] < level and row["close"] < row["open"]
                and row["ema20"] < row["ema50"] and body_ok
            )
        if not valid:
            continue
        exit_time, result_r = _simulate(h4, i, side, p)
        rows.append({
            "symbol": symbol, "engine": f"{symbol}_BREAKOUT_RETEST",
            "setup": "H4_BREAKOUT_RETEST", "side": side,
            "entry_time": pd.Timestamp(row["end"]), "exit_time": exit_time,
            "risk_percent": p.risk_percent, "r_multiple": float(result_r),
        })
        last_breakout = None
    return pd.DataFrame(rows)


def mean_reversion_candidates(symbol: str, h4: pd.DataFrame,
                              p: CommodityParams) -> pd.DataFrame:
    mean = h4["close"].rolling(p.band_period, min_periods=p.band_period).mean()
    std = h4["close"].rolling(p.band_period, min_periods=p.band_period).std(ddof=0)
    upper = mean + p.band_std * std
    lower = mean - p.band_std * std
    prev_close = h4["close"].shift(1)
    prev_upper = upper.shift(1)
    prev_lower = lower.shift(1)
    rows = []
    for i in range(max(60, p.band_period + 2), len(h4) - 1):
        row = h4.iloc[i]
        if not all(np.isfinite(value) for value in (
            row["atr14"], row["adx14"], upper.iloc[i], lower.iloc[i],
            prev_close.iloc[i], prev_upper.iloc[i], prev_lower.iloc[i],
        )):
            continue
        if row["adx14"] > p.adx_threshold:
            continue
        side = 0
        if (
            prev_close.iloc[i] < prev_lower.iloc[i]
            and row["close"] > lower.iloc[i] and row["close"] > row["open"]
            and row["body_ratio"] >= p.body_ratio_min
        ):
            side = 1
        elif (
            prev_close.iloc[i] > prev_upper.iloc[i]
            and row["close"] < upper.iloc[i] and row["close"] < row["open"]
            and row["body_ratio"] >= p.body_ratio_min
        ):
            side = -1
        if not side:
            continue
        exit_time, result_r = _simulate(h4, i, side, p)
        rows.append({
            "symbol": symbol, "engine": f"{symbol}_MEAN_REVERSION",
            "setup": "LOW_ADX_BAND_REENTRY", "side": side,
            "entry_time": pd.Timestamp(row["end"]), "exit_time": exit_time,
            "risk_percent": p.risk_percent, "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def candidates(symbol: str, h4: pd.DataFrame, p: CommodityParams) -> pd.DataFrame:
    if p.family == "trend_pullback":
        return trend_pullback_candidates(symbol, h4, p)
    if p.family == "breakout_retest":
        return breakout_retest_candidates(symbol, h4, p)
    if p.family == "mean_reversion":
        return mean_reversion_candidates(symbol, h4, p)
    raise ValueError(p.family)


def parameter_grid():
    for stop in (1.0, 1.25, 1.5):
        for target in (2.0, 2.5, 3.0):
            for adx in (15.0, 18.0, 22.0):
                for touch in (0.15, 0.30, 0.50):
                    for body in (0.15, 0.25):
                        yield CommodityParams(
                            "trend_pullback", stop, target, 1.75, 30,
                            adx_threshold=adx, touch_atr=touch,
                            body_ratio_min=body,
                        )
    for stop in (1.0, 1.25, 1.5):
        for target in (2.0, 2.5, 3.0):
            for adx in (15.0, 18.0, 22.0):
                for lookback in (20, 30, 40):
                    for search in (6, 12, 18):
                        yield CommodityParams(
                            "breakout_retest", stop, target, 1.75, 30,
                            adx_threshold=adx, lookback=lookback,
                            search_bars=search, touch_atr=0.30,
                        )
    for stop in (1.0, 1.25, 1.5):
        for target in (1.5, 2.0, 2.5):
            for adx in (16.0, 20.0, 24.0):
                for period in (16, 20, 30):
                    for std in (1.75, 2.0, 2.25):
                        yield CommodityParams(
                            "mean_reversion", stop, target, 1.5, 20,
                            adx_threshold=adx, band_period=period,
                            band_std=std, body_ratio_min=0.15,
                        )


def select(symbol: str, h4: pd.DataFrame):
    split = h4["time"].min() + (h4["time"].max() - h4["time"].min()) * 0.70
    scored = []
    for p in parameter_grid():
        frame = candidates(symbol, h4, p)
        train = frame[frame["entry_time"] < split]
        result = base.stats(train)
        score = result["net_r"]
        score -= max(0.0, 1.12 - result["profit_factor"]) * 35
        if result["trades"] < 60:
            score -= (60 - result["trades"]) * 0.35
        scored.append((score, p, result))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = scored[0][1]
    frame = candidates(symbol, h4, selected)
    development = base.stats(frame[frame["entry_time"] < split])
    validation = base.stats(frame[frame["entry_time"] >= split])
    passed = (
        validation["trades"] >= 60
        and validation["net_r"] > 0
        and validation["profit_factor"] >= 1.10
    )
    return selected, frame, {
        "split": split.isoformat(), "development": development,
        "validation": validation, "passed": passed,
        "top_scores": [
            {"score": float(score), "params": asdict(p), "stats": result}
            for score, p, result in scored[:10]
        ],
    }


def main() -> None:
    symbols = ("AUDUSD", "USDCAD", "NZDUSD")
    reports = {}
    selected = {}
    for symbol in symbols:
        _, h4, _ = base.prepare(symbol)
        p, frame, report = select(symbol, h4)
        selected[symbol] = p
        reports[symbol] = report
        frame.to_csv(OUT / f"{symbol}_selected_candidates.csv", index=False)
    passing = [symbol for symbol in symbols if reports[symbol]["passed"]]
    winner = max(
        passing,
        key=lambda symbol: (
            reports[symbol]["validation"]["net_r"],
            reports[symbol]["validation"]["profit_factor"],
        ),
        default=None,
    )
    result = {
        "validation_gate": {
            "minimum_validation_trades": 60,
            "minimum_validation_profit_factor": 1.10,
            "minimum_validation_net_r": 0.0,
        },
        "selected_commodity": winner,
        "selected_parameters": {symbol: asdict(selected[symbol]) for symbol in symbols},
        "reports": reports,
    }
    (OUT / "results.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
