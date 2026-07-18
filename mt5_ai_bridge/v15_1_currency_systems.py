"""V15.1 research-only currency-factor and session systems.

The module consumes completed FXCM H1 bid/ask candles. It builds two genuinely
independent strategy families:

* currency-factor rotation inferred from a network of FX crosses; and
* London/New-York session breakout and liquidity-fade setups.

All entries occur at 08:00, 12:00 or 16:00 UTC. Bid/ask execution is modeled
without MT5, a broker connection or order transmission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

CURRENCIES = ("AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD")
ENTRY_HOURS_UTC = {8, 12, 16}


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    tr = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high_diff = frame["high"].diff()
    low_diff = -frame["low"].diff()
    plus_dm = pd.Series(
        np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0),
        index=frame.index,
    )
    tr = atr(frame, period)
    plus_di = 100.0 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def resample_ohlc(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    work = frame.copy()
    work["time"] = pd.to_datetime(work["time"], utc=True)
    indexed = work.set_index("time")
    output = indexed.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
    )
    return output.dropna(subset=["open", "high", "low", "close"]).reset_index()


def pair_currencies(symbol: str) -> tuple[str, str] | None:
    text = str(symbol).upper()
    if len(text) != 6:
        return None
    base, quote = text[:3], text[3:]
    if base not in CURRENCIES or quote not in CURRENCIES:
        return None
    return base, quote


def merge_bid_ask(h1_bid: pd.DataFrame, h1_ask: pd.DataFrame) -> pd.DataFrame:
    bid = h1_bid.copy().rename(
        columns={column: f"bid_{column}" for column in ("open", "high", "low", "close", "tick_volume")}
    )
    ask = h1_ask.copy().rename(
        columns={column: f"ask_{column}" for column in ("open", "high", "low", "close", "tick_volume")}
    )
    frame = bid.merge(ask, on="time", how="inner").sort_values("time").reset_index(drop=True)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame["end"] = frame["time"] + pd.Timedelta(hours=1)
    signal = pd.DataFrame(
        {
            "time": frame["time"],
            "open": frame["bid_open"],
            "high": frame["bid_high"],
            "low": frame["bid_low"],
            "close": frame["bid_close"],
            "tick_volume": frame["bid_tick_volume"],
        }
    )
    frame["atr14"] = atr(signal)
    frame["ema20"] = ema(signal["close"], 20)
    frame["ema50"] = ema(signal["close"], 50)
    frame["ema200"] = ema(signal["close"], 200)
    frame["body_atr"] = (signal["close"] - signal["open"]).abs() / frame["atr14"].replace(0, np.nan)
    frame["atr_ratio"] = frame["atr14"] / frame["atr14"].rolling(120, min_periods=60).mean()
    frame["day"] = frame["time"].dt.floor("D")
    frame["hour"] = frame["time"].dt.hour
    frame["weekday"] = frame["time"].dt.weekday
    return frame


def completed_daily_features(h1_bid: pd.DataFrame) -> pd.DataFrame:
    d1 = resample_ohlc(h1_bid, "1D").sort_values("time").reset_index(drop=True)
    d1["d1_atr14"] = atr(d1)
    d1["d1_adx14"] = adx(d1)
    d1["d1_ema20"] = ema(d1["close"], 20)
    d1["d1_ema50"] = ema(d1["close"], 50)
    d1["d1_ema200"] = ema(d1["close"], 200)
    d1["d1_close"] = d1["close"]
    d1["previous_day_high"] = d1["high"].shift(1)
    d1["previous_day_low"] = d1["low"].shift(1)
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    return d1[
        [
            "available", "d1_atr14", "d1_adx14", "d1_ema20", "d1_ema50",
            "d1_ema200", "d1_close", "previous_day_high", "previous_day_low",
        ]
    ]


def prepare_session_frame(h1_bid: pd.DataFrame, h1_ask: pd.DataFrame) -> pd.DataFrame:
    frame = merge_bid_ask(h1_bid, h1_ask)
    daily = completed_daily_features(h1_bid)
    frame = pd.merge_asof(
        frame.sort_values("end"),
        daily.sort_values("available"),
        left_on="end",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    asia = frame[(frame["hour"] >= 0) & (frame["hour"] < 6)].groupby("day", as_index=False).agg(
        asia_high=("bid_high", "max"), asia_low=("bid_low", "min"), asia_bars=("time", "count")
    )
    london = frame[(frame["hour"] >= 6) & (frame["hour"] < 12)].groupby("day", as_index=False).agg(
        london_high=("bid_high", "max"), london_low=("bid_low", "min"), london_bars=("time", "count")
    )
    return frame.merge(asia, on="day", how="left").merge(london, on="day", how="left").sort_values("time").reset_index(drop=True)


@dataclass(frozen=True)
class SessionSpec:
    name: str
    family: str
    signal_hour: int
    stop_atr: float
    target_r: float
    max_holding_hours: int
    minimum_body_atr: float
    minimum_atr_ratio: float
    slippage_reserve_r: float = 0.035


def session_specs() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec("LONDON_ASIA_BREAKOUT_08", "SESSION_BREAKOUT", 7, 1.15, 2.40, 12, 0.18, 0.85),
        SessionSpec("LONDON_ASIA_BREAKOUT_12", "SESSION_BREAKOUT", 11, 1.25, 2.60, 16, 0.20, 0.90),
        SessionSpec("NY_LONDON_BREAKOUT_12", "SESSION_BREAKOUT", 11, 1.20, 2.30, 12, 0.18, 0.90),
        SessionSpec("NY_LONDON_BREAKOUT_16", "SESSION_BREAKOUT", 15, 1.25, 2.50, 12, 0.20, 0.90),
        SessionSpec("ASIA_FALSE_BREAK_FADE_08", "SESSION_FADE", 7, 1.00, 1.70, 10, 0.10, 0.70),
        SessionSpec("PREVIOUS_DAY_SWEEP_12", "LIQUIDITY_FADE", 11, 1.10, 1.90, 12, 0.12, 0.75),
        SessionSpec("PREVIOUS_DAY_SWEEP_16", "LIQUIDITY_FADE", 15, 1.10, 1.90, 12, 0.12, 0.75),
    )


def simulate_h1_exit(frame: pd.DataFrame, signal_index: int, side: int, stop_distance: float, target_r: float, max_holding_hours: int) -> tuple[pd.Timestamp, float]:
    entry_index = signal_index + 1
    if entry_index >= len(frame) or not np.isfinite(stop_distance) or stop_distance <= 0:
        return pd.Timestamp(frame.iloc[signal_index]["end"]), 0.0
    entry_row = frame.iloc[entry_index]
    entry = float(entry_row["ask_open"] if side > 0 else entry_row["bid_open"])
    stop = entry - side * stop_distance
    target = entry + side * target_r * stop_distance
    last_index = min(len(frame) - 1, entry_index + max_holding_hours - 1)
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        if side > 0:
            open_price, low, high = float(row["bid_open"]), float(row["bid_low"]), float(row["bid_high"])
            if open_price <= stop:
                return pd.Timestamp(row["end"]), float((open_price - entry) / stop_distance)
            if low <= stop:
                return pd.Timestamp(row["end"]), -1.0
            if high >= target:
                return pd.Timestamp(row["end"]), target_r
        else:
            open_price, high, low = float(row["ask_open"]), float(row["ask_high"]), float(row["ask_low"])
            if open_price >= stop:
                return pd.Timestamp(row["end"]), float((entry - open_price) / stop_distance)
            if high >= stop:
                return pd.Timestamp(row["end"]), -1.0
            if low <= target:
                return pd.Timestamp(row["end"]), target_r
    final = frame.iloc[last_index]
    exit_price = float(final["bid_close"] if side > 0 else final["ask_close"])
    return pd.Timestamp(final["end"]), float((exit_price - entry) * side / stop_distance)


def session_signal_masks(frame: pd.DataFrame, spec: SessionSpec) -> tuple[pd.Series, pd.Series]:
    trend_long = (frame["d1_close"] > frame["d1_ema20"]) & (frame["d1_ema20"] > frame["d1_ema50"])
    trend_short = (frame["d1_close"] < frame["d1_ema20"]) & (frame["d1_ema20"] < frame["d1_ema50"])
    common = (
        (frame["hour"] == spec.signal_hour)
        & frame["weekday"].isin((0, 1, 2, 3, 4))
        & (frame["body_atr"] >= spec.minimum_body_atr)
        & (frame["atr_ratio"] >= spec.minimum_atr_ratio)
        & frame["atr14"].notna()
    )
    if spec.name.startswith("LONDON_ASIA_BREAKOUT"):
        common &= frame["asia_bars"] >= 5
        long_signal = trend_long & (frame["bid_close"] > frame["asia_high"])
        short_signal = trend_short & (frame["bid_close"] < frame["asia_low"])
    elif spec.name.startswith("NY_LONDON_BREAKOUT"):
        common &= frame["london_bars"] >= 5
        long_signal = trend_long & (frame["bid_close"] > frame["london_high"])
        short_signal = trend_short & (frame["bid_close"] < frame["london_low"])
    elif spec.name == "ASIA_FALSE_BREAK_FADE_08":
        common &= (frame["asia_bars"] >= 5) & (frame["d1_adx14"] < 23.0)
        long_signal = (frame["bid_low"] < frame["asia_low"]) & (frame["bid_close"] > frame["asia_low"])
        short_signal = (frame["bid_high"] > frame["asia_high"]) & (frame["bid_close"] < frame["asia_high"])
    elif spec.name.startswith("PREVIOUS_DAY_SWEEP"):
        common &= frame["d1_adx14"] < 28.0
        long_signal = (frame["bid_low"] < frame["previous_day_low"]) & (frame["bid_close"] > frame["previous_day_low"])
        short_signal = (frame["bid_high"] > frame["previous_day_high"]) & (frame["bid_close"] < frame["previous_day_high"])
    else:
        raise ValueError(spec.name)
    return common & long_signal, common & short_signal


def generate_session_candidates(symbol: str, h1_bid: pd.DataFrame, h1_ask: pd.DataFrame, spec: SessionSpec) -> pd.DataFrame:
    frame = prepare_session_frame(h1_bid, h1_ask)
    long_signal, short_signal = session_signal_masks(frame, spec)
    rows: list[dict] = []
    unavailable_until = pd.Timestamp.min.tz_localize("UTC")
    for raw_index in np.flatnonzero((long_signal | short_signal).to_numpy()):
        index = int(raw_index)
        if index + 1 >= len(frame):
            continue
        entry_time = pd.Timestamp(frame.iloc[index + 1]["time"])
        if entry_time < unavailable_until or entry_time.hour not in ENTRY_HOURS_UTC:
            continue
        side = 1 if bool(long_signal.iloc[index]) else -1
        atr_value = float(frame.iloc[index]["atr14"])
        exit_time, raw_r = simulate_h1_exit(frame, index, side, spec.stop_atr * atr_value, spec.target_r, spec.max_holding_hours)
        if not np.isfinite(raw_r):
            continue
        rows.append({
            "symbol": symbol, "mode": "DIVERSIFIED", "engine": f"{symbol}_V15_1_{spec.name}".upper(),
            "family": spec.family, "profile": spec.name, "timeframe": "H1",
            "side": "BUY" if side > 0 else "SELL", "entry_time": entry_time, "exit_time": exit_time,
            "raw_r_multiple": float(raw_r), "selection_cost_r": float(spec.slippage_reserve_r),
            "r_multiple": float(raw_r - spec.slippage_reserve_r), "cost_r": float(spec.slippage_reserve_r),
            "stop_atr": float(spec.stop_atr), "target_r": float(spec.target_r), "strategy_group": "V15_1_SESSION",
        })
        unavailable_until = exit_time
    return pd.DataFrame(rows)


def daily_pair_frames(market: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    for symbol, (h1_bid, _) in market.items():
        if pair_currencies(symbol) is None:
            continue
        frame = resample_ohlc(h1_bid, "1D").sort_values("time").reset_index(drop=True)
        frame["log_return"] = np.log(frame["close"]).diff()
        frame["atr14"] = atr(frame)
        frame["ema100"] = ema(frame["close"], 100)
        frame["adx14"] = adx(frame)
        output[symbol] = frame
    return output


def infer_currency_returns(daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pair_returns = []
    for symbol, frame in daily.items():
        if pair_currencies(symbol) is None:
            continue
        temp = frame[["time", "log_return"]].dropna().copy()
        temp["symbol"] = symbol
        pair_returns.append(temp)
    panel = pd.concat(pair_returns, ignore_index=True, sort=False)
    non_usd = [currency for currency in CURRENCIES if currency != "USD"]
    rows: list[dict] = []
    for timestamp, group in panel.groupby("time", sort=True):
        matrix, values = [], []
        for item in group.itertuples(index=False):
            pair = pair_currencies(str(item.symbol))
            if pair is None or not np.isfinite(item.log_return):
                continue
            base, quote = pair
            vector = [0.0] * len(non_usd)
            if base != "USD":
                vector[non_usd.index(base)] += 1.0
            if quote != "USD":
                vector[non_usd.index(quote)] -= 1.0
            matrix.append(vector)
            values.append(float(item.log_return))
        if len(matrix) < len(non_usd):
            continue
        solution, *_ = np.linalg.lstsq(np.asarray(matrix), np.asarray(values), rcond=None)
        record = {"time": pd.Timestamp(timestamp), "USD": 0.0}
        record.update({currency: float(solution[index]) for index, currency in enumerate(non_usd)})
        rows.append(record)
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def currency_score_panel(currency_returns: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for currency in CURRENCIES:
        returns = pd.to_numeric(currency_returns[currency], errors="coerce")
        vol20 = returns.rolling(20, min_periods=20).std().replace(0, np.nan)
        score20 = returns.rolling(20, min_periods=20).sum() / (vol20 * np.sqrt(20.0))
        score60 = returns.rolling(60, min_periods=45).sum() / (vol20 * np.sqrt(60.0))
        score120 = returns.rolling(120, min_periods=90).sum() / (vol20 * np.sqrt(120.0))
        score5 = returns.rolling(5, min_periods=5).sum() / (vol20 * np.sqrt(5.0))
        rows.append(pd.DataFrame({
            "time": currency_returns["time"], "currency": currency,
            "score_20_60": 0.60 * score20 + 0.40 * score60,
            "score_60_120": 0.55 * score60 + 0.45 * score120,
            "score_20_60_120": 0.45 * score20 + 0.35 * score60 + 0.20 * score120,
            "shock_5": score5,
        }))
    return pd.concat(rows, ignore_index=True, sort=False)


def find_pair(strong: str, weak: str, allowed_symbols: set[str]) -> tuple[str, int] | None:
    direct, inverse = strong + weak, weak + strong
    if direct in allowed_symbols:
        return direct, 1
    if inverse in allowed_symbols:
        return inverse, -1
    return None


def h4_execution_frame(h1_bid: pd.DataFrame, h1_ask: pd.DataFrame) -> pd.DataFrame:
    bid = resample_ohlc(h1_bid, "4h").rename(columns={column: f"bid_{column}" for column in ("open", "high", "low", "close", "tick_volume")})
    ask = resample_ohlc(h1_ask, "4h").rename(columns={column: f"ask_{column}" for column in ("open", "high", "low", "close", "tick_volume")})
    frame = bid.merge(ask, on="time", how="inner").sort_values("time").reset_index(drop=True)
    frame["end"] = frame["time"] + pd.Timedelta(hours=4)
    return frame


def simulate_h4_factor_exit(frame: pd.DataFrame, entry_index: int, side: int, stop_distance: float, max_holding_bars: int, trail_bars: int, target_r: float | None) -> tuple[pd.Timestamp, float]:
    if entry_index >= len(frame) or not np.isfinite(stop_distance) or stop_distance <= 0:
        return pd.Timestamp(frame.iloc[max(0, entry_index - 1)]["end"]), 0.0
    entry_row = frame.iloc[entry_index]
    entry = float(entry_row["ask_open"] if side > 0 else entry_row["bid_open"])
    stop = entry - side * stop_distance
    target = None if target_r is None else entry + side * target_r * stop_distance
    long_trail = frame["bid_low"].rolling(trail_bars, min_periods=trail_bars).min().shift(1) if trail_bars else pd.Series(np.nan, index=frame.index)
    short_trail = frame["ask_high"].rolling(trail_bars, min_periods=trail_bars).max().shift(1) if trail_bars else pd.Series(np.nan, index=frame.index)
    last_index = min(len(frame) - 1, entry_index + max_holding_bars - 1)
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        if side > 0:
            open_price, low, high = float(row["bid_open"]), float(row["bid_low"]), float(row["bid_high"])
            if open_price <= stop:
                return pd.Timestamp(row["end"]), float((open_price - entry) / stop_distance)
            if low <= stop:
                return pd.Timestamp(row["end"]), -1.0
            trail = float(long_trail.iloc[index]) if np.isfinite(long_trail.iloc[index]) else np.nan
            if np.isfinite(trail) and trail > stop and low <= trail:
                return pd.Timestamp(row["end"]), float((trail - entry) / stop_distance)
            if target is not None and high >= target:
                return pd.Timestamp(row["end"]), float(target_r)
        else:
            open_price, high, low = float(row["ask_open"]), float(row["ask_high"]), float(row["ask_low"])
            if open_price >= stop:
                return pd.Timestamp(row["end"]), float((entry - open_price) / stop_distance)
            if high >= stop:
                return pd.Timestamp(row["end"]), -1.0
            trail = float(short_trail.iloc[index]) if np.isfinite(short_trail.iloc[index]) else np.nan
            if np.isfinite(trail) and trail < stop and high >= trail:
                return pd.Timestamp(row["end"]), float((entry - trail) / stop_distance)
            if target is not None and low <= target:
                return pd.Timestamp(row["end"]), float(target_r)
    final = frame.iloc[last_index]
    exit_price = float(final["bid_close"] if side > 0 else final["ask_close"])
    return pd.Timestamp(final["end"]), float((exit_price - entry) * side / stop_distance)


@dataclass(frozen=True)
class FactorSpec:
    name: str
    score_column: str
    reversal: bool
    stop_atr: float
    max_holding_h4_bars: int
    trailing_h4_bars: int
    target_r: float | None
    pairs_per_rebalance: int
    slippage_reserve_r: float = 0.025


def factor_specs() -> tuple[FactorSpec, ...]:
    return (
        FactorSpec("CURRENCY_MOMENTUM_20_60", "score_20_60", False, 2.2, 120, 40, None, 2),
        FactorSpec("CURRENCY_MOMENTUM_60_120", "score_60_120", False, 2.5, 180, 60, None, 2),
        FactorSpec("CURRENCY_MOMENTUM_20_60_120", "score_20_60_120", False, 2.4, 150, 50, None, 2),
        FactorSpec("CURRENCY_SHOCK_REVERSAL_5", "shock_5", True, 1.8, 30, 0, 1.8, 2),
    )


def generate_factor_candidates(market: dict[str, tuple[pd.DataFrame, pd.DataFrame]], allowed_symbols: Iterable[str], spec: FactorSpec) -> pd.DataFrame:
    allowed = {symbol for symbol in allowed_symbols if pair_currencies(symbol) is not None}
    daily = daily_pair_frames(market)
    scores = currency_score_panel(infer_currency_returns(daily))
    wide = scores.pivot(index="time", columns="currency", values=spec.score_column).sort_index()
    decision_dates = wide.index[wide.index.weekday == 0]
    execution = {symbol: h4_execution_frame(*market[symbol]) for symbol in allowed}
    unavailable_until = {symbol: pd.Timestamp.min.tz_localize("UTC") for symbol in allowed}
    rows: list[dict] = []
    for date in decision_dates:
        score_row = wide.loc[date].dropna()
        if len(score_row) < 6:
            continue
        ranked = score_row.sort_values()
        strong_order = list(ranked.index[:3] if spec.reversal else ranked.index[-3:][::-1])
        weak_order = list(ranked.index[-3:] if spec.reversal else ranked.index[:3])
        selected, used = [], set()
        for strong in strong_order:
            for weak in weak_order:
                if strong == weak or strong in used or weak in used:
                    continue
                pair = find_pair(strong, weak, allowed)
                if pair is None:
                    continue
                symbol, side = pair
                spread_score = float(score_row[strong] - score_row[weak])
                if not spec.reversal and spread_score < 0.75:
                    continue
                if spec.reversal and abs(spread_score) < 2.25:
                    continue
                selected.append((symbol, side, strong, weak, spread_score))
                used.update({strong, weak})
                break
            if len(selected) >= spec.pairs_per_rebalance:
                break
        for symbol, side, strong, weak, spread_score in selected:
            frame = execution[symbol]
            entries = frame[(frame["time"] >= pd.Timestamp(date) + pd.Timedelta(days=1)) & (frame["time"].dt.hour == 8)]
            if entries.empty:
                continue
            entry_index = int(entries.index[0])
            entry_time = pd.Timestamp(frame.loc[entry_index, "time"])
            if entry_time < unavailable_until[symbol]:
                continue
            completed = daily[symbol][daily[symbol]["time"] < entry_time.floor("D")]
            if completed.empty:
                continue
            last = completed.iloc[-1]
            atr_value, pair_price, pair_ema, pair_adx = float(last["atr14"]), float(last["close"]), float(last["ema100"]), float(last["adx14"])
            if not np.isfinite(atr_value) or atr_value <= 0:
                continue
            if not spec.reversal and ((side > 0 and pair_price <= pair_ema) or (side < 0 and pair_price >= pair_ema)):
                continue
            if spec.reversal and pair_adx >= 22.0:
                continue
            exit_time, raw_r = simulate_h4_factor_exit(frame, entry_index, side, spec.stop_atr * atr_value, spec.max_holding_h4_bars, spec.trailing_h4_bars, spec.target_r)
            if not np.isfinite(raw_r):
                continue
            rows.append({
                "symbol": symbol, "mode": "DIVERSIFIED", "engine": f"{symbol}_V15_1_{spec.name}".upper(),
                "family": "CURRENCY_FACTOR_REVERSAL" if spec.reversal else "CURRENCY_FACTOR_MOMENTUM",
                "profile": spec.name, "timeframe": "D1/H4", "side": "BUY" if side > 0 else "SELL",
                "entry_time": entry_time, "exit_time": exit_time, "raw_r_multiple": float(raw_r),
                "selection_cost_r": float(spec.slippage_reserve_r), "r_multiple": float(raw_r - spec.slippage_reserve_r),
                "cost_r": float(spec.slippage_reserve_r), "stop_atr": float(spec.stop_atr), "target_r": spec.target_r,
                "strategy_group": "V15_1_CURRENCY_FACTOR", "strong_currency": strong, "weak_currency": weak,
                "factor_spread_score": spread_score,
            })
            unavailable_until[symbol] = exit_time
    return pd.DataFrame(rows)


def generate_all_candidates(market: dict[str, tuple[pd.DataFrame, pd.DataFrame]], allowed_symbols: Iterable[str]) -> pd.DataFrame:
    allowed = sorted(set(allowed_symbols))
    frames: list[pd.DataFrame] = []
    for symbol in allowed:
        h1_bid, h1_ask = market[symbol]
        for spec in session_specs():
            generated = generate_session_candidates(symbol, h1_bid, h1_ask, spec)
            if not generated.empty:
                frames.append(generated)
    for spec in factor_specs():
        generated = generate_factor_candidates(market, allowed, spec)
        if not generated.empty:
            frames.append(generated)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    return output.sort_values(["entry_time", "symbol", "family", "profile"]).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "family", "profile", "side"]
    ).reset_index(drop=True)
