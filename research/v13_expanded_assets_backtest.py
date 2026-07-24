"""Research-only V13 expanded-asset portfolio backtest.

Downloads Apache-2.0 historical OHLC files from ejtraderLabs/historical-data,
freezes AUDUSD and USDJPY parameters on an early development segment, reports
later out-of-sample validation, and replays GBPUSD, EURUSD, GBPJPY, AUDUSD and
USDJPY through one synchronized portfolio gate.

This script never connects to MT5 and never sends an order.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v13_output"
OUT.mkdir(parents=True, exist_ok=True)
DATA_URL = "https://raw.githubusercontent.com/ejtraderLabs/historical-data/main"
STARTING_BALANCE = 5000.0

BASKETS = {
    "EURUSD": {"EUROCENTRIC"},
    "AUDUSD": {"COMMODITY_BLOCK"},
    "USDJPY": {"SAFE_HAVEN"},
}


@dataclass(frozen=True)
class StrategyParams:
    lookback: int
    adx_min: float
    stop_atr: float
    target_r: float
    trail_atr: float
    max_h4_bars: int
    risk_percent: float
    body_ratio_min: float = 0.20


@dataclass(frozen=True)
class PortfolioConfig:
    max_positions: int = 5
    max_open_risk_percent: float = 1.50
    generic_symbol_cap_percent: float = 0.40
    gbpusd_precision_symbol_cap_percent: float = 0.65
    aligned_gbp_cap_percent: float = 0.90
    mixed_gbp_cap_percent: float = 0.65
    basket_cooldown_hours: float = 4.0


def load_frame(symbol: str, timeframe: str) -> pd.DataFrame:
    url = f"{DATA_URL}/{symbol}/{symbol}{timeframe.lower()}.csv"
    frame = pd.read_csv(url)
    frame = frame.rename(columns={"Date": "time"})
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    for column in ("open", "high", "low", "close", "tick_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def directional(frame: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    a = atr(frame, period)
    plus = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    minus = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / a
    dx = 100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return plus, minus, adx


def prepare(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    h1 = load_frame(symbol, "h1")
    h4 = load_frame(symbol, "h4")
    d1 = load_frame(symbol, "d1")
    h4["atr14"] = atr(h4)
    h4["ema20"] = ema(h4["close"], 20)
    h4["ema50"] = ema(h4["close"], 50)
    h4["avg_volume"] = h4["tick_volume"].rolling(20, min_periods=20).mean()
    h4["volume_ratio"] = h4["tick_volume"] / h4["avg_volume"]
    h4["atr_ratio"] = h4["atr14"] / h4["atr14"].rolling(20, min_periods=20).mean()
    h4["range"] = h4["high"] - h4["low"]
    h4["body_ratio"] = (h4["close"] - h4["open"]).abs() / h4["range"].replace(0, np.nan)
    h4["close_location"] = (h4["close"] - h4["low"]) / h4["range"].replace(0, np.nan)
    h4["plus_di"], h4["minus_di"], h4["adx14"] = directional(h4)
    h4["end"] = h4["time"] + pd.Timedelta(hours=4)

    h1["atr14"] = atr(h1)
    h1["ema20"] = ema(h1["close"], 20)
    h1["ema50"] = ema(h1["close"], 50)
    h1["end"] = h1["time"] + pd.Timedelta(hours=1)

    d1["ema20"] = ema(d1["close"], 20)
    d1["ema50"] = ema(d1["close"], 50)
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    daily = d1[["available", "close", "ema20", "ema50"]].rename(columns={
        "close": "dclose", "ema20": "dema20", "ema50": "dema50"
    })
    h4 = pd.merge_asof(
        h4.sort_values("time"), daily.sort_values("available"),
        left_on="time", right_on="available", direction="backward",
    )
    return h1, h4, d1


def simulate_h4_trade(h4: pd.DataFrame, signal_index: int, side: int,
                      stop_atr: float, target_r: float, trail_atr: float,
                      max_bars: int) -> tuple[pd.Timestamp, float]:
    if signal_index + 1 >= len(h4):
        return pd.Timestamp(h4.iloc[signal_index]["end"]), 0.0
    signal = h4.iloc[signal_index]
    entry_row = h4.iloc[signal_index + 1]
    entry = float(entry_row["open"])
    risk = float(signal["atr14"]) * stop_atr
    if not np.isfinite(risk) or risk <= 0:
        return pd.Timestamp(entry_row["time"]), 0.0
    stop = entry - side * risk
    target = entry + side * target_r * risk
    best_stop = stop
    for j in range(signal_index + 1, min(len(h4), signal_index + 1 + max_bars)):
        row = h4.iloc[j]
        low, high = float(row["low"]), float(row["high"])
        stop_hit = low <= best_stop if side > 0 else high >= best_stop
        target_hit = high >= target if side > 0 else low <= target
        if stop_hit:
            return pd.Timestamp(row["end"]), (best_stop - entry) * side / risk
        if target_hit:
            return pd.Timestamp(row["end"]), target_r
        favorable = (high - entry) if side > 0 else (entry - low)
        if favorable >= risk and np.isfinite(row["atr14"]):
            candidate = float(row["close"] - side * trail_atr * row["atr14"])
            candidate = max(candidate, entry) if side > 0 else min(candidate, entry)
            best_stop = max(best_stop, candidate) if side > 0 else min(best_stop, candidate)
    last = h4.iloc[min(len(h4) - 1, signal_index + max_bars)]
    return pd.Timestamp(last["end"]), (float(last["close"]) - entry) * side / risk


def generic_candidates(symbol: str, h1: pd.DataFrame, h4: pd.DataFrame,
                       params: StrategyParams) -> pd.DataFrame:
    high = h4["high"].rolling(params.lookback, min_periods=params.lookback).max().shift(1)
    low = h4["low"].rolling(params.lookback, min_periods=params.lookback).min().shift(1)
    long_signal = (
        (h4["dclose"] > h4["dema20"]) & (h4["dema20"] > h4["dema50"])
        & (h4["close"] > h4["ema20"]) & (h4["adx14"] >= params.adx_min)
        & (h4["body_ratio"] >= params.body_ratio_min) & (h4["close"] > high)
    )
    short_signal = (
        (h4["dclose"] < h4["dema20"]) & (h4["dema20"] < h4["dema50"])
        & (h4["close"] < h4["ema20"]) & (h4["adx14"] >= params.adx_min)
        & (h4["body_ratio"] >= params.body_ratio_min) & (h4["close"] < low)
    )
    rows = []
    for index in np.flatnonzero((long_signal | short_signal).to_numpy()):
        side = 1 if bool(long_signal.iloc[index]) else -1
        exit_time, result_r = simulate_h4_trade(
            h4, int(index), side, params.stop_atr, params.target_r,
            params.trail_atr, params.max_h4_bars,
        )
        rows.append({
            "symbol": symbol,
            "engine": f"{symbol}_H4_VALIDATED",
            "setup": "H4_D1_BREAKOUT",
            "side": side,
            "entry_time": pd.Timestamp(h4.iloc[index]["end"]),
            "exit_time": exit_time,
            "risk_percent": params.risk_percent,
            "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def gbpusd_precision_candidates(
    h4: pd.DataFrame,
    *,
    include_unresolved: bool = False,
) -> pd.DataFrame:
    rows = []
    primary_high = h4["high"].rolling(20, min_periods=20).max().shift(1)
    primary_low = h4["low"].rolling(20, min_periods=20).min().shift(1)
    secondary_high = h4["high"].rolling(45, min_periods=45).max().shift(1)
    secondary_low = h4["low"].rolling(45, min_periods=45).min().shift(1)
    stop = len(h4) if include_unresolved else len(h4) - 1
    for i in range(60, stop):
        row = h4.iloc[i]
        if not np.isfinite(row["atr14"]):
            continue
        long_regime = row["dema20"] > row["dema50"] and row["dclose"] > row["dema20"]
        short_regime = row["dema20"] < row["dema50"] and row["dclose"] < row["dema20"]
        side = 0
        setup = ""
        risk_percent = 0.0
        hour = pd.Timestamp(row["end"]).hour
        if (
            hour == 16 and row["adx14"] >= 18 and row["volume_ratio"] >= 0.70
            and row["body_ratio"] >= 0.30 and row["atr_ratio"] >= 1.0
        ):
            if long_regime and row["close_location"] >= 0.70 and row["close"] > primary_high.iloc[i]:
                side, setup = 1, "PRIMARY_16UTC_BREAKOUT"
            elif short_regime and row["close_location"] <= 0.30 and row["close"] < primary_low.iloc[i]:
                side, setup = -1, "PRIMARY_16UTC_BREAKOUT"
            if side:
                range_atr = row["range"] / row["atr14"]
                risk_percent = 0.50 if row["volume_ratio"] >= 1.248 and range_atr >= 1.555 else 0.20
        if not side and (
            hour == 12 and pd.Timestamp(row["end"]).weekday() != 4
            and row["adx14"] >= 12 and row["volume_ratio"] >= 0.70
            and row["body_ratio"] >= 0.50 and row["atr_ratio"] >= 1.0
        ):
            if long_regime and row["close_location"] >= 0.60 and row["close"] > secondary_high.iloc[i]:
                side, setup = 1, "SECONDARY_12UTC_BREAKOUT"
            elif short_regime and row["close_location"] <= 0.40 and row["close"] < secondary_low.iloc[i]:
                side, setup = -1, "SECONDARY_12UTC_BREAKOUT"
            if side:
                directional_body_atr = side * (row["close"] - row["open"]) / row["atr14"]
                if row["atr_ratio"] < 1.018 or directional_body_atr > 1.473:
                    side = 0
                else:
                    risk_percent = 0.40
        if not side:
            continue
        exit_time, result_r = simulate_h4_trade(h4, i, side, 1.5, 3.0, 2.5, 72)
        rows.append({
            "symbol": "GBPUSD", "engine": "GBPUSD_V10_PRECISION",
            "setup": setup, "side": side,
            "entry_time": pd.Timestamp(row["end"]), "exit_time": exit_time,
            "risk_percent": risk_percent, "r_multiple": float(result_r),
        })
    return pd.DataFrame(rows)


def stats(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "profit_factor": 0.0, "win_rate": 0.0}
    values = frame["r_multiple"].astype(float)
    gp = values[values > 0].sum()
    gl = -values[values < 0].sum()
    return {
        "trades": int(len(frame)), "net_r": float(values.sum()),
        "profit_factor": float(gp / gl) if gl else float("inf"),
        "win_rate": float((values > 0).mean()),
    }


def choose_params(symbol: str, h1: pd.DataFrame, h4: pd.DataFrame) -> tuple[StrategyParams, dict]:
    split = h4["time"].min() + (h4["time"].max() - h4["time"].min()) * 0.70
    grid = []
    for lookback in (30, 40, 55):
        for adx_min in (18.0, 20.0, 22.0):
            for stop_atr in (1.25, 1.50):
                for target_r in (2.5, 3.0):
                    p = StrategyParams(lookback, adx_min, stop_atr, target_r, 2.0, 30, 0.25)
                    candidates = generic_candidates(symbol, h1, h4, p)
                    train = candidates[candidates["entry_time"] < split]
                    result = stats(train)
                    score = result["net_r"] - max(0.0, 1.10 - result["profit_factor"]) * 25
                    if result["trades"] < 35:
                        score -= (35 - result["trades"]) * 0.25
                    grid.append((score, p, result))
    grid.sort(key=lambda item: item[0], reverse=True)
    selected = grid[0][1]
    all_candidates = generic_candidates(symbol, h1, h4, selected)
    train = stats(all_candidates[all_candidates["entry_time"] < split])
    validation = stats(all_candidates[all_candidates["entry_time"] >= split])
    return selected, {
        "split": split.isoformat(), "development": train,
        "validation": validation, "top_scores": [
            {"score": float(score), "params": asdict(params), "stats": result}
            for score, params, result in grid[:5]
        ],
    }


def gbp_exposure(symbol: str, side: int) -> int:
    if not symbol.startswith("GBP"):
        return 0
    return side


def replay(candidates: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
           config: PortfolioConfig) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    data = candidates[(candidates["entry_time"] >= start) & (candidates["entry_time"] <= end)].copy()
    data = data.sort_values(["entry_time", "engine"]).reset_index(drop=True)
    balance = STARTING_BALANCE
    peak = balance
    max_dd = 0.0
    max_stress_dd = 0.0
    active: list[dict] = []
    accepted = []
    rejected = []
    basket_last_entry: dict[str, pd.Timestamp] = {}

    def close_due(now: pd.Timestamp) -> None:
        nonlocal balance, peak, max_dd
        due = sorted([item for item in active if item["exit_time"] <= now], key=lambda x: x["exit_time"])
        for item in due:
            pnl = item["risk_dollars"] * item["r_multiple"]
            balance += pnl
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak * 100 if peak else 0.0)
            active.remove(item)

    for row in data.itertuples(index=False):
        entry_time = pd.Timestamp(row.entry_time)
        close_due(entry_time)
        reason = None
        if len(active) >= config.max_positions:
            reason = "max_positions"
        baskets = BASKETS.get(row.symbol, set())
        if reason is None and baskets:
            for basket in baskets:
                if any(basket in BASKETS.get(item["symbol"], set()) for item in active):
                    reason = f"basket_cap:{basket}"
                    break
                last = basket_last_entry.get(basket)
                if last is not None and (entry_time - last).total_seconds() < config.basket_cooldown_hours * 3600:
                    reason = f"basket_cooldown:{basket}"
                    break
        risk_percent = float(row.risk_percent)
        open_risk = sum(item["risk_percent"] for item in active)
        symbol_risk = sum(item["risk_percent"] for item in active if item["symbol"] == row.symbol)
        symbol_cap = (
            config.gbpusd_precision_symbol_cap_percent
            if row.symbol == "GBPUSD" and row.engine == "GBPUSD_V10_PRECISION"
            else config.generic_symbol_cap_percent
        )
        if reason is None and symbol_risk + risk_percent > symbol_cap + 1e-9:
            reason = "symbol_cap"
        if reason is None and open_risk + risk_percent > config.max_open_risk_percent + 1e-9:
            reason = "open_risk"
        incoming_gbp = gbp_exposure(row.symbol, int(row.side))
        if reason is None and incoming_gbp:
            gbp_active = [item for item in active if gbp_exposure(item["symbol"], item["side"])]
            gbp_risk = sum(item["risk_percent"] for item in gbp_active)
            directions = {gbp_exposure(item["symbol"], item["side"]) for item in gbp_active}
            directions.add(incoming_gbp)
            cap = config.mixed_gbp_cap_percent if len(directions) > 1 else config.aligned_gbp_cap_percent
            if gbp_risk + risk_percent > cap + 1e-9:
                reason = "gbp_cap"
        if reason:
            rejected.append({**row._asdict(), "reason": reason})
            continue
        risk_dollars = balance * risk_percent / 100.0
        record = {
            **row._asdict(), "risk_percent": risk_percent,
            "risk_dollars": risk_dollars,
        }
        active.append(record)
        accepted.append(record)
        for basket in baskets:
            basket_last_entry[basket] = entry_time
        stressed_equity = balance - sum(item["risk_dollars"] for item in active)
        max_stress_dd = max(max_stress_dd, (peak - stressed_equity) / peak * 100 if peak else 0.0)
    close_due(pd.Timestamp.max.tz_localize("UTC"))
    values = pd.DataFrame(accepted)
    pnl = values["risk_dollars"] * values["r_multiple"] if not values.empty else pd.Series(dtype=float)
    gp = pnl[pnl > 0].sum()
    gl = -pnl[pnl < 0].sum()
    summary = {
        "start": start.isoformat(), "end": end.isoformat(),
        "starting_balance": STARTING_BALANCE, "ending_balance": balance,
        "net_profit": balance - STARTING_BALANCE,
        "return_percent": (balance / STARTING_BALANCE - 1) * 100,
        "trades": int(len(values)),
        "profit_factor": float(gp / gl) if gl else float("inf"),
        "win_rate": float((values["r_multiple"] > 0).mean()) if not values.empty else 0.0,
        "max_drawdown_percent": max_dd,
        "stress_drawdown_percent": max_stress_dd,
        "average_monthly_profit": (balance - STARTING_BALANCE) / max(1.0, (end - start).days / 30.4375),
        "rejections": pd.Series([item["reason"] for item in rejected]).value_counts().to_dict(),
    }
    return summary, values, pd.DataFrame(rejected)


def main() -> None:
    symbols = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
    prepared = {symbol: prepare(symbol) for symbol in symbols}

    selected = {}
    validation = {}
    for symbol in ("AUDUSD", "USDJPY"):
        h1, h4, _ = prepared[symbol]
        params, report = choose_params(symbol, h1, h4)
        selected[symbol] = params
        validation[symbol] = report

    candidates = [gbpusd_precision_candidates(prepared["GBPUSD"][1])]
    existing_params = {
        "EURUSD": StrategyParams(55, 20.0, 1.25, 3.0, 2.5, 24, 0.20),
        "GBPJPY": StrategyParams(55, 20.0, 1.25, 3.0, 2.5, 24, 0.20),
    }
    for symbol, params in existing_params.items():
        h1, h4, _ = prepared[symbol]
        candidates.append(generic_candidates(symbol, h1, h4, params))
    for symbol, params in selected.items():
        h1, h4, _ = prepared[symbol]
        candidates.append(generic_candidates(symbol, h1, h4, params))
    all_candidates = pd.concat(candidates, ignore_index=True).sort_values("entry_time")
    all_candidates.to_csv(OUT / "all_candidates.csv", index=False)

    end = min(prepared[symbol][1]["time"].max() for symbol in symbols)
    start_all = max(prepared[symbol][1]["time"].min() for symbol in symbols)
    results = {
        "data_source": DATA_URL,
        "common_start": start_all.isoformat(), "common_end": end.isoformat(),
        "selected_parameters": {key: asdict(value) for key, value in selected.items()},
        "validation": validation,
        "portfolio_config": asdict(PortfolioConfig()),
        "windows": {},
    }
    for years in (10, 5, 3, 2):
        requested = end - pd.DateOffset(years=years)
        start = max(start_all, requested)
        summary, accepted, rejected = replay(all_candidates, start, end, PortfolioConfig())
        results["windows"][str(years)] = summary
        accepted.to_csv(OUT / f"accepted_{years}y.csv", index=False)
        rejected.to_csv(OUT / f"rejected_{years}y.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
