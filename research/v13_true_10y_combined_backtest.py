"""True synchronized 10-year V12 Final + V11 intraday research replay.

V11 candidates are regenerated from public M15/M30/H1 OHLC, simulated on M15
with conservative stop-first ordering and a 0.05R execution-cost deduction,
then merged chronologically with the exact V12 Final candidates and replayed
through the shared V12/V13 portfolio capacity governor.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import v12_plus_validated_assets_backtest as study  # noqa: E402
import v12_targeted_weak_engine_optimization as targeted  # noqa: E402
from v12_final_runner import build_final_candidates  # noqa: E402

BASE_URL = "https://raw.githubusercontent.com/ejtraderLabs/historical-data/main"
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY")
OUT = ROOT / "research" / "v13_true_10y_output"
ALLOWED_GBPUSD_HOURS = frozenset({7, 10, 11, 12, 14, 15, 16})


def _load(symbol: str, timeframe: str) -> pd.DataFrame:
    url = f"{BASE_URL}/{symbol}/{symbol}{timeframe.lower()}.csv"
    frame = pd.read_csv(url).rename(columns={"Date": "time"})
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    scale = 1000.0 if symbol.endswith("JPY") else 100000.0
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].median() > 1000:
            frame[column] = frame[column] / scale
    frame["tick_volume"] = pd.to_numeric(frame["tick_volume"], errors="coerce")
    return frame.dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)


def _ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    tr = pd.concat([frame["high"] - frame["low"],
                    (frame["high"] - previous).abs(),
                    (frame["low"] - previous).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up, down = frame["high"].diff(), -frame["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    atr = _atr(frame, period)
    plus = 100 * plus_dm.ewm(alpha=1 / period, adjust=False,
                             min_periods=period).mean() / atr
    minus = 100 * minus_dm.ewm(alpha=1 / period, adjust=False,
                               min_periods=period).mean() / atr
    dx = 100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _rsi(values: pd.Series, period: int = 14) -> pd.Series:
    delta = values.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False,
                                   min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False,
                                      min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _prepare(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    m15, m30, h1 = (_load(symbol, tf) for tf in ("m15", "m30", "h1"))
    m15["end"] = m15["time"] + pd.Timedelta(minutes=15)
    m15["atr14"] = _atr(m15)
    m15["rsi14"] = _rsi(m15["close"])
    m15["ema20"] = _ema(m15["close"], 20)
    m15["ema50"] = _ema(m15["close"], 50)
    m15["body_ratio"] = (m15["close"] - m15["open"]).abs() / (
        m15["high"] - m15["low"]).replace(0, np.nan)
    m15["vol_ratio"] = m15["tick_volume"] / m15["tick_volume"].rolling(20).mean()
    m15["range_atr"] = (m15["high"] - m15["low"]) / m15["atr14"]

    m30["end"] = m30["time"] + pd.Timedelta(minutes=30)
    m30["ema20_m30"] = _ema(m30["close"], 20)
    m30["ema50_m30"] = _ema(m30["close"], 50)
    touch = (m30["low"] <= m30["ema20_m30"]) & (m30["high"] >= m30["ema20_m30"])
    m30["recent_m30_pullback_touch"] = touch.rolling(4).max().fillna(0).astype(bool)

    h1["end"] = h1["time"] + pd.Timedelta(hours=1)
    h1["ema20_h1"] = _ema(h1["close"], 20)
    h1["ema50_h1"] = _ema(h1["close"], 50)
    h1["ema20_slope_h1"] = h1["ema20_h1"].diff(3) / 3
    h1["adx14_h1"] = _adx(h1)
    h1["atr14_h1"] = _atr(h1)
    h1["atr_q55_h1"] = h1["atr14_h1"].rolling(252, min_periods=100).quantile(.55)
    h1["atr_q60_h1"] = h1["atr14_h1"].rolling(252, min_periods=100).quantile(.60)
    h1["close_h1"] = h1["close"]

    joined = pd.merge_asof(
        m15.sort_values("end"),
        m30[["end", "ema20_m30", "ema50_m30", "recent_m30_pullback_touch"]]
        .sort_values("end"), on="end", direction="backward")
    joined = pd.merge_asof(
        joined.sort_values("end"),
        h1[["end", "ema20_h1", "ema50_h1", "ema20_slope_h1", "adx14_h1",
            "atr14_h1", "atr_q55_h1", "atr_q60_h1", "close_h1"]].sort_values("end"),
        on="end", direction="backward")
    joined["weekday"] = joined["end"].dt.weekday
    joined["hour"] = joined["end"].dt.hour + joined["end"].dt.minute / 60
    joined["date"] = joined["end"].dt.date
    minutes = joined["end"].dt.hour * 60 + joined["end"].dt.minute

    asian = joined[minutes <= 420].groupby("date").agg(
        asian_high=("high", "max"), asian_low=("low", "min"))
    asian["asian_range"] = asian["asian_high"] - asian["asian_low"]
    asian["asian_med20"] = asian["asian_range"].shift(1).rolling(20).median()
    day = joined.groupby("date").agg(day_high=("high", "max"), day_low=("low", "min"))
    day["prev_range"] = (day["day_high"] - day["day_low"]).shift(1)
    day["prior_day_min_low"] = day["day_low"].shift(1)
    london = joined[(minutes >= 420) & (minutes < 720)].groupby("date").agg(
        london_high=("high", "max"), london_low=("low", "min"))
    for column in asian.columns:
        joined[column] = joined["date"].map(asian[column])
    for column in ("prev_range", "prior_day_min_low"):
        joined[column] = joined["date"].map(day[column])
    for column in london.columns:
        joined[column] = joined["date"].map(london[column])

    joined["prior_2_low"] = joined["low"].shift(1).rolling(2).min()
    joined["prior_16_high"] = joined["high"].shift(1).rolling(16).max()
    joined["prior_16_low"] = joined["low"].shift(1).rolling(16).min()
    above_asian = joined["close"] > joined["asian_high"]
    joined["recent_asian_up_break"] = above_asian.groupby(joined["date"]).transform(
        lambda value: value.shift(1).rolling(4).max()).fillna(0).astype(bool)
    joined["recent_london_high_break"] = (joined["close"] > joined["london_high"]).groupby(
        joined["date"]).transform(lambda value: value.shift(1).rolling(6).max()).fillna(0).astype(bool)
    joined["recent_london_low_break"] = (joined["close"] < joined["london_low"]).groupby(
        joined["date"]).transform(lambda value: value.shift(1).rolling(6).max()).fillna(0).astype(bool)
    joined["recent_long_pullback"] = (
        (joined["low"] <= joined["ema20"] - .20 * joined["atr14"])
        | (joined["close"] < joined["ema20"])).shift(1).rolling(5).max().fillna(0).astype(bool)
    joined["recent_short_pullback"] = (
        (joined["high"] >= joined["ema20"] + .20 * joined["atr14"])
        | (joined["close"] > joined["ema20"])).shift(1).rolling(5).max().fillna(0).astype(bool)
    joined["previous_high_2"] = joined["high"].shift(1).rolling(2).max()
    joined["previous_low_2"] = joined["low"].shift(1).rolling(2).min()
    return joined, m30, h1


def _rows(frame: pd.DataFrame, mask: pd.Series, *, symbol: str, engine: str,
          setup: str, side: int, risk: float, stop_atr: float, target_r: float,
          break_even_r: float, max_bars: int) -> pd.DataFrame:
    result = frame.loc[mask, ["end", "atr14"]].copy()
    result["symbol"], result["engine"], result["setup"] = symbol, engine, setup
    result["side"], result["risk_percent"] = side, risk
    result["stop_atr"], result["target_r"] = stop_atr, target_r
    result["break_even_r"], result["max_bars"] = break_even_r, max_bars
    return result.rename(columns={"end": "entry_time", "atr14": "signal_atr"})


def _signals(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    weekday = frame["weekday"] < 5
    bullish = ((frame["ema20_h1"] > frame["ema50_h1"])
               & (frame["close_h1"] > frame["ema20_h1"])
               & (frame["ema20_slope_h1"] > 0))
    bearish = ((frame["ema20_h1"] < frame["ema50_h1"])
               & (frame["close_h1"] < frame["ema20_h1"])
               & (frame["ema20_slope_h1"] < 0))
    output = []
    if symbol == "EURUSD":
        common = dict(symbol=symbol, engine="EURUSD_V11_INTRADAY", risk=.35, break_even_r=1.0)
        compression = (weekday & frame["hour"].between(7, 12, inclusive="left") & bullish
            & (frame["adx14_h1"] <= 18) & (frame["asian_range"] <= 1.2 * frame["asian_med20"])
            & (frame["asian_range"] <= frame["prev_range"]) & frame["recent_asian_up_break"]
            & (frame["low"] <= frame["asian_high"] + .15 * frame["atr14"])
            & (frame["close"] > frame["asian_high"]) & (frame["close"] > frame["open"])
            & (frame["body_ratio"] >= .30) & (frame["vol_ratio"] >= .85)
            & frame["rsi14"].between(50, 70))
        output.append(_rows(frame, compression, setup="EUR_COMPRESSION_LONG", side=1,
                            stop_atr=1.05, target_r=2.2, max_bars=48, **common))
        momentum = (weekday & frame["hour"].between(7, 12, inclusive="left") & bearish
            & (frame["adx14_h1"] >= 20) & (frame["close"] < frame["prior_16_low"])
            & (frame["close"] < frame["open"]) & (frame["body_ratio"] >= .55)
            & (frame["vol_ratio"] >= 1.10) & frame["rsi14"].between(28, 48))
        output.append(_rows(frame, momentum, setup="EUR_MOMENTUM_SHORT", side=-1,
                            stop_atr=1.2, target_r=1.75, max_bars=28, **common))
        ny = (weekday & frame["hour"].between(12.5, 14.5, inclusive="left") & bearish
            & (frame["adx14_h1"] >= 18) & (frame["prior_day_min_low"] < frame["london_low"])
            & (frame["high"] >= frame["london_low"] - .15 * frame["atr14"])
            & (frame["close"] < frame["london_low"]) & (frame["close"] < frame["open"])
            & (frame["body_ratio"] >= .45) & (frame["vol_ratio"] >= .90)
            & frame["rsi14"].between(30, 46))
        output.append(_rows(frame, ny, setup="EUR_NY_RETEST_SHORT", side=-1,
                            stop_atr=1.15, target_r=2.0, max_bars=40, **common))
    elif symbol == "GBPJPY":
        session = weekday & frame["hour"].between(7, 16, inclusive="left")
        common = dict(symbol=symbol, engine="GBPJPY_V11_INTRADAY", risk=.35, break_even_r=1.0)
        momentum = (session & bullish & (frame["adx14_h1"] >= 18)
            & (frame["atr14_h1"] >= frame["atr_q60_h1"])
            & (frame["close"] > frame["prior_16_high"]) & (frame["close"] > frame["open"])
            & (frame["body_ratio"] >= .45) & (frame["vol_ratio"] >= 1.10)
            & frame["rsi14"].between(54, 74) & (frame["range_atr"] <= 2.0))
        output.append(_rows(frame, momentum, setup="GJ_MOMENTUM_LONG", side=1,
                            stop_atr=1.5, target_r=2.0, max_bars=32, **common))
        pullback = (session & bearish & (frame["ema20_m30"] < frame["ema50_m30"])
            & (frame["adx14_h1"] >= 18) & (frame["atr14_h1"] >= frame["atr_q55_h1"])
            & frame["recent_m30_pullback_touch"] & (frame["close"] < frame["prior_2_low"])
            & (frame["close"] < frame["open"]) & (frame["body_ratio"] >= .55)
            & (frame["vol_ratio"] >= 1.10) & frame["rsi14"].between(30, 48))
        output.append(_rows(frame, pullback, setup="GJ_PULLBACK_SHORT", side=-1,
                            stop_atr=1.6, target_r=2.0, max_bars=40, **common))
    else:
        hour_ok = frame["end"].dt.hour.isin(ALLOWED_GBPUSD_HOURS)
        long_h1 = (frame["ema20_h1"] > frame["ema50_h1"]) & (frame["close_h1"] > frame["ema20_h1"])
        short_h1 = (frame["ema20_h1"] < frame["ema50_h1"]) & (frame["close_h1"] < frame["ema20_h1"])
        long_m30 = (frame["ema20_m30"] > frame["ema50_m30"])
        short_m30 = (frame["ema20_m30"] < frame["ema50_m30"])
        common = dict(symbol=symbol, engine="GBPUSD_V11_INTRADAY", risk=.30)
        london_quality = (frame["body_ratio"] >= .50) & (frame["vol_ratio"] >= .80)
        london_long = (weekday & frame["hour"].between(7, 12, inclusive="left") & hour_ok
            & long_h1 & long_m30 & frame["recent_long_pullback"] & london_quality
            & (frame["close"] > frame["ema20"]) & (frame["close"] > frame["open"])
            & (frame["rsi14"] >= 52) & (frame["close"] > frame["previous_high_2"]))
        london_short = (weekday & frame["hour"].between(7, 12, inclusive="left") & hour_ok
            & short_h1 & short_m30 & frame["recent_short_pullback"] & london_quality
            & (frame["close"] < frame["ema20"]) & (frame["close"] < frame["open"])
            & (frame["rsi14"] <= 48) & (frame["close"] < frame["previous_low_2"]))
        output.append(_rows(frame, london_long, setup="LONDON_PULLBACK_V2", side=1,
                            stop_atr=1.75, target_r=1.75, break_even_r=1.0, max_bars=32, **common))
        output.append(_rows(frame, london_short, setup="LONDON_PULLBACK_V2", side=-1,
                            stop_atr=1.75, target_r=1.75, break_even_r=1.0, max_bars=32, **common))
        ny_quality = (frame["body_ratio"] >= .10) & (frame["vol_ratio"] >= .80)
        ny_long = (weekday & frame["hour"].between(12, 17, inclusive="left") & hour_ok
            & long_h1 & frame["recent_london_high_break"] & ny_quality
            & (frame["low"] <= frame["london_high"]) & (frame["close"] > frame["london_high"])
            & (frame["close"] > frame["open"]) & (frame["rsi14"] >= 54))
        ny_short = (weekday & frame["hour"].between(12, 17, inclusive="left") & hour_ok
            & short_h1 & frame["recent_london_low_break"] & ny_quality
            & (frame["high"] >= frame["london_low"]) & (frame["close"] < frame["london_low"])
            & (frame["close"] < frame["open"]) & (frame["rsi14"] <= 46))
        output.append(_rows(frame, ny_long, setup="NEW_YORK_RETEST_V2", side=1,
                            stop_atr=1.25, target_r=3.0, break_even_r=1.5, max_bars=20, **common))
        output.append(_rows(frame, ny_short, setup="NEW_YORK_RETEST_V2", side=-1,
                            stop_atr=1.25, target_r=3.0, break_even_r=1.5, max_bars=20, **common))
    return pd.concat(output, ignore_index=True).sort_values("entry_time")


def _simulate_candidates(frame: pd.DataFrame, candidates: pd.DataFrame,
                         symbol: str, cost_r: float = .05) -> pd.DataFrame:
    rows = []
    times = pd.Index(frame["end"])
    pip = .01 if symbol.endswith("JPY") else .0001
    for candidate in candidates.itertuples(index=False):
        position = times.searchsorted(candidate.entry_time)
        if position >= len(frame) - 1:
            continue
        signal_index = position
        entry_index = signal_index + 1
        entry = float(frame.iloc[entry_index]["open"])
        distance = float(candidate.signal_atr) * float(candidate.stop_atr)
        if symbol == "GBPUSD":
            min_pips = 5 if candidate.setup == "LONDON_PULLBACK_V2" else 6
            distance = min(max(distance, min_pips * pip), 30 * pip)
        if not np.isfinite(distance) or distance <= 0:
            continue
        side = int(candidate.side)
        stop, target = entry - side * distance, entry + side * distance * candidate.target_r
        best_stop = stop
        exit_time, result_r = frame.iloc[entry_index]["end"], 0.0
        last_index = min(len(frame) - 1, signal_index + int(candidate.max_bars))
        for index in range(entry_index, last_index + 1):
            bar = frame.iloc[index]
            if bar["end"].date() != candidate.entry_time.date() or bar["end"].hour >= 20:
                result_r = (float(bar["open"]) - entry) * side / distance
                exit_time = bar["end"]
                break
            stop_hit = float(bar["low"]) <= best_stop if side > 0 else float(bar["high"]) >= best_stop
            target_hit = float(bar["high"]) >= target if side > 0 else float(bar["low"]) <= target
            if stop_hit:
                result_r, exit_time = (best_stop - entry) * side / distance, bar["end"]
                break
            if target_hit:
                result_r, exit_time = float(candidate.target_r), bar["end"]
                break
            favorable = float(bar["high"]) - entry if side > 0 else entry - float(bar["low"])
            if favorable >= float(candidate.break_even_r) * distance:
                best_stop = max(best_stop, entry) if side > 0 else min(best_stop, entry)
            result_r = (float(bar["close"]) - entry) * side / distance
            exit_time = bar["end"]
        row = candidate._asdict()
        row["exit_time"] = pd.Timestamp(exit_time)
        row["r_multiple"] = float(result_r) - cost_r
        rows.append(row)
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["date"] = result["entry_time"].dt.date
    caps = {"GBPUSD": 2, "EURUSD": 1, "GBPJPY": 2}
    return result.groupby(["symbol", "date"], sort=False).head(caps[symbol]).drop(
        columns=["date"])


def _summary_attribution(frame: pd.DataFrame) -> list[dict]:
    rows = []
    if frame.empty:
        return rows
    for engine, group in frame.groupby("engine"):
        pnl = group["risk_dollars"] * group["r_multiple"]
        gain, loss = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        rows.append({"engine": engine, "trades": len(group), "net_profit": float(pnl.sum()),
                     "profit_factor": float(gain / loss) if loss else math.inf})
    return rows


def main() -> None:
    intraday_frames, v11_parts = {}, []
    for symbol in SYMBOLS:
        print(f"loading and evaluating {symbol} M15/M30/H1", flush=True)
        prepared, _, _ = _prepare(symbol)
        intraday_frames[symbol] = prepared
        v11_parts.append(_simulate_candidates(prepared, _signals(symbol, prepared), symbol))
    v11 = pd.concat(v11_parts, ignore_index=True).sort_values("entry_time")

    prepared_v12 = {symbol: study._prepare(symbol) for symbol in study.ALL_SYMBOLS}
    v12_candidates = build_final_candidates(prepared_v12)
    baseline, _ = targeted.baseopt.build_baseline_candidates(prepared_v12)
    expected_v12 = targeted.filter_losers(baseline)
    if len(v12_candidates) != len(expected_v12):
        raise AssertionError("V12 candidate parity failed before combined replay")
    common_start = max(
        max(prepared_v12[s][1]["time"].min() for s in study.ALL_SYMBOLS),
        max(frame["time"].min() for frame in intraday_frames.values()),
    )
    common_end = min(
        min(prepared_v12[s][1]["time"].max() for s in study.ALL_SYMBOLS),
        min(frame["time"].max() for frame in intraday_frames.values()),
    )
    combined = pd.concat([v12_candidates, v11], ignore_index=True, sort=False)
    original_guard = study._guard_decision
    study._guard_decision = targeted.targeted_guard_decision
    try:
        v12_summary, v12_accepted, _ = study._replay(
            v12_candidates, common_start, common_end, study.CAPACITY_CAPS)
        v11_summary, v11_accepted, v11_rejected = study._replay(
            v11, common_start, common_end, study.CAPACITY_CAPS)
        combined_summary, combined_accepted, combined_rejected = study._replay(
            combined, common_start, common_end, study.CAPACITY_CAPS)
    finally:
        study._guard_decision = original_guard

    v12_keys = set(zip(v12_accepted.engine, v12_accepted.setup, v12_accepted.entry_time))
    combined_v12 = combined_accepted[~combined_accepted.engine.str.contains("V11_INTRADAY")]
    combined_keys = set(zip(combined_v12.engine, combined_v12.setup, combined_v12.entry_time))
    payload = {
        "status": "TRUE_SYNCHRONIZED_10Y_RESEARCH_REPLAY",
        "methodology": "Raw M15/M30/H1 V11 candidates plus exact V12 candidates; chronological shared-capacity replay; V11 outcomes include 0.05R cost.",
        "common_start": common_start.isoformat(), "common_end": common_end.isoformat(),
        "v12_final_only": v12_summary, "v11_intraday_only": v11_summary,
        "v13_combined": combined_summary,
        "combined_improvement_vs_v12": combined_summary["net_profit"] - v12_summary["net_profit"],
        "v11_raw_candidates": len(v11),
        "v11_accepted": len(v11_accepted), "v11_rejected": len(v11_rejected),
        "v12_trades_displaced_by_v11": len(v12_keys - combined_keys),
        "combined_attribution": _summary_attribution(combined_accepted),
        "combined_rejections": combined_rejected["reason"].value_counts().to_dict(),
        "limitations": [
            "M15 OHLC cannot resolve tick order inside a candle; stop-first ordering is conservative.",
            "Costs are represented by a fixed 0.05R deduction on V11 trades.",
            "Public-source timestamps and prices may differ from the target broker feed.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(payload, indent=2, default=str) + "\n",
                                       encoding="utf-8")
    v11.to_csv(OUT / "v11_candidates.csv", index=False)
    combined_accepted.to_csv(OUT / "combined_accepted.csv", index=False)
    combined_rejected.to_csv(OUT / "combined_rejected.csv", index=False)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
