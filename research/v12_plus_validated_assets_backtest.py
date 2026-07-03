"""Research-only V12 rebuild plus independently validated AUDUSD/USDJPY engines.

This runner intentionally keeps the profitable V10/V12 GBPUSD precision and
V12 H4/H1 swing families separate from the later generic V17 candidate mix.
It then adds the two V13 families that passed independent validation:

* AUDUSD D1/H4 EMA pullback continuation with a development-selected quality
  gate and a frozen 04:00/08:00 UTC timing filter.
* USDJPY D1-trend / H4 40-bar breakout.

The V12 adaptive guard is repaired so a mature engine can take exactly one
50%-risk recovery probe after cooldown. No orders are sent and MT5 is never
imported. Public OHLC is used only for research replay.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import v13_expanded_assets_backtest as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v12_plus_validated_assets_output"
OUT.mkdir(parents=True, exist_ok=True)
STARTING_BALANCE = 5000.0
LEGACY_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY")
NEW_SYMBOLS = ("AUDUSD", "USDJPY")
ALL_SYMBOLS = LEGACY_SYMBOLS + NEW_SYMBOLS
PRECISION_ENGINE = "GBPUSD_V10_PRECISION"


@dataclass(frozen=True)
class GuardConfig:
    rolling: int = 12
    minimum: int = 12
    full_pf: float = 1.15
    full_net_r: float = 0.0
    reduced_pf: float = 0.90
    reduced_net_r: float = -1.20
    reduced_multiplier: float = 0.50
    cooldown_days: int = 60
    probe_multiplier: float = 0.50


@dataclass(frozen=True)
class GuardDecision:
    multiplier: float
    reason: str
    is_probe: bool = False


@dataclass(frozen=True)
class PortfolioConfig:
    name: str
    max_positions: int
    max_open_risk_percent: float
    precision_symbol_cap_percent: float
    legacy_symbol_cap_percent: float
    new_symbol_cap_percent: float
    aligned_gbp_cap_percent: float = 0.90
    mixed_gbp_cap_percent: float = 0.65


@dataclass(frozen=True)
class AUDUSDParams:
    adx_min: float
    touch_atr: float
    body_ratio_min: float
    allowed_hours: tuple[int, ...] = (4, 8)
    stop_atr: float = 1.25
    target_r: float = 2.0
    trail_atr: float = 1.5
    max_bars: int = 20
    risk_percent: float = 0.25


ORIGINAL_CAPS = PortfolioConfig(
    name="original_v12_caps",
    max_positions=3,
    max_open_risk_percent=1.00,
    precision_symbol_cap_percent=0.75,
    legacy_symbol_cap_percent=0.75,
    new_symbol_cap_percent=0.25,
)

CAPACITY_CAPS = PortfolioConfig(
    name="validated_asset_capacity",
    max_positions=5,
    max_open_risk_percent=1.50,
    precision_symbol_cap_percent=0.75,
    legacy_symbol_cap_percent=0.75,
    new_symbol_cap_percent=0.25,
)


def _profit_factor(values: Iterable[float]) -> float:
    values = list(values)
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    if gross_loss:
        return float(gross_profit / gross_loss)
    return math.inf if gross_profit else 0.0


def _stats(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "profit_factor": 0.0, "win_rate": 0.0}
    values = frame["r_multiple"].astype(float)
    return {
        "trades": int(len(values)),
        "net_r": float(values.sum()),
        "profit_factor": _profit_factor(values),
        "win_rate": float((values > 0).mean()),
    }


def _guard_decision(
    engine: str,
    history: dict[str, list[float]],
    now: pd.Timestamp,
    disabled_until: dict[str, pd.Timestamp],
    probe_active_until: dict[str, pd.Timestamp],
    config: GuardConfig,
) -> GuardDecision:
    if engine == PRECISION_ENGINE:
        return GuardDecision(1.0, "precision_passthrough")

    probe_until = probe_active_until.get(engine)
    if probe_until is not None:
        if now < probe_until:
            return GuardDecision(0.0, "probe_in_flight")
        probe_active_until.pop(engine, None)

    cooldown = disabled_until.get(engine)
    if cooldown is not None:
        if now < cooldown:
            return GuardDecision(0.0, "cooldown")
        return GuardDecision(config.probe_multiplier, "recovery_probe", True)

    values = history.get(engine, [])[-config.rolling :]
    if len(values) < config.minimum:
        return GuardDecision(1.0, "warmup")
    pf = _profit_factor(values)
    net_r = float(sum(values))
    if pf >= config.full_pf and net_r > config.full_net_r:
        return GuardDecision(1.0, "full_performance")
    if pf >= config.reduced_pf and net_r > config.reduced_net_r:
        return GuardDecision(config.reduced_multiplier, "reduced_performance")
    disabled_until[engine] = now + pd.Timedelta(days=config.cooldown_days)
    return GuardDecision(0.0, "new_cooldown")


def _simulate(
    frame: pd.DataFrame,
    signal_index: int,
    side: int,
    stop_atr: float,
    target_r: float,
    trail_atr: float,
    max_bars: int,
) -> tuple[pd.Timestamp, float]:
    if signal_index + 1 >= len(frame):
        return pd.Timestamp(frame.iloc[signal_index]["end"]), 0.0
    signal = frame.iloc[signal_index]
    entry_row = frame.iloc[signal_index + 1]
    entry = float(entry_row["open"])
    risk = float(signal["atr14"]) * stop_atr
    if not np.isfinite(risk) or risk <= 0:
        return pd.Timestamp(entry_row["end"]), 0.0
    stop = entry - side * risk
    target = entry + side * target_r * risk
    best_stop = stop
    last_index = min(len(frame) - 1, signal_index + max_bars)
    for j in range(signal_index + 1, last_index + 1):
        row = frame.iloc[j]
        low, high = float(row["low"]), float(row["high"])
        stop_hit = low <= best_stop if side > 0 else high >= best_stop
        target_hit = high >= target if side > 0 else low <= target
        if stop_hit:
            return pd.Timestamp(row["end"]), float((best_stop - entry) * side / risk)
        if target_hit:
            return pd.Timestamp(row["end"]), float(target_r)
        favorable = high - entry if side > 0 else entry - low
        if favorable >= risk and np.isfinite(row["atr14"]):
            candidate = float(row["close"] - side * trail_atr * row["atr14"])
            candidate = max(candidate, entry) if side > 0 else min(candidate, entry)
            best_stop = max(best_stop, candidate) if side > 0 else min(best_stop, candidate)
    last = frame.iloc[last_index]
    return pd.Timestamp(last["end"]), float((float(last["close"]) - entry) * side / risk)


def _prepare(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    h1, h4, d1 = base.prepare(symbol)
    h1 = h1.copy()
    h4 = h4.copy()
    d1 = d1.copy()

    h1["ema_sep_atr"] = (h1["ema20"] - h1["ema50"]).abs() / h1["atr14"]
    h1["hour"] = h1["end"].dt.hour

    d1["daily_atr14"] = base.atr(d1)
    d1["daily_ema20"] = base.ema(d1["close"], 20)
    d1["daily_ema50"] = base.ema(d1["close"], 50)
    d1["daily_ema20_slope"] = d1["daily_ema20"].diff(5) / 5
    d1["available_v12"] = d1["time"] + pd.Timedelta(days=1)
    h4 = pd.merge_asof(
        h4.sort_values("time"),
        d1[["available_v12", "daily_atr14", "daily_ema20_slope"]].sort_values("available_v12"),
        left_on="time",
        right_on="available_v12",
        direction="backward",
    )
    h4["ema_sep_atr"] = (h4["ema20"] - h4["ema50"]).abs() / h4["atr14"]
    h4["atr_pct_252"] = h4["atr14"].rolling(252, min_periods=100).rank(pct=True)
    h4["prior_high"] = h4["high"].rolling(55, min_periods=55).max().shift(1)
    h4["prior_low"] = h4["low"].rolling(55, min_periods=55).min().shift(1)
    h4["directional_di_gap_long"] = h4["plus_di"] - h4["minus_di"]
    h4["directional_di_gap_short"] = h4["minus_di"] - h4["plus_di"]
    long = (
        (h4["dclose"] > h4["dema20"])
        & (h4["dema20"] > h4["dema50"])
        & (h4["close"] > h4["ema20"])
        & (h4["adx14"] >= 20)
        & (h4["close"] > h4["prior_high"])
    )
    short = (
        (h4["dclose"] < h4["dema20"])
        & (h4["dema20"] < h4["dema50"])
        & (h4["close"] < h4["ema20"])
        & (h4["adx14"] >= 20)
        & (h4["close"] < h4["prior_low"])
    )
    h4["breakout_side"] = np.where(long, 1, np.where(short, -1, 0))
    h4["breakout_level"] = np.where(
        h4["breakout_side"] > 0,
        h4["prior_high"],
        np.where(h4["breakout_side"] < 0, h4["prior_low"], np.nan),
    )
    h4["directional_di_gap"] = np.where(
        h4["breakout_side"] > 0,
        h4["directional_di_gap_long"],
        h4["directional_di_gap_short"],
    )
    h4["daily_slope_dir"] = np.where(
        h4["breakout_side"] > 0,
        h4["daily_ema20_slope"] / h4["daily_atr14"],
        -h4["daily_ema20_slope"] / h4["daily_atr14"],
    )
    return h1, h4, d1


def _candidate(
    symbol: str,
    engine: str,
    setup: str,
    side: int,
    signal_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    risk_percent: float,
    r_multiple: float,
) -> dict:
    return {
        "symbol": symbol,
        "engine": engine,
        "setup": setup,
        "side": int(side),
        "entry_time": pd.Timestamp(signal_time),
        "exit_time": pd.Timestamp(exit_time),
        "risk_percent": float(risk_percent),
        "r_multiple": float(r_multiple),
    }


def _v12_core_candidates(symbol: str, h4: pd.DataFrame) -> pd.DataFrame:
    risks = {"GBPUSD": 0.20, "EURUSD": 0.25, "GBPJPY": 0.15}
    rows: list[dict] = []
    for i in np.flatnonzero((h4["breakout_side"] != 0).to_numpy()):
        row = h4.iloc[int(i)]
        side = int(row["breakout_side"])
        if symbol == "EURUSD":
            if not (
                float(row["ema_sep_atr"]) <= 1.30
                and float(row["directional_di_gap"]) >= 17.0
            ):
                continue
        elif symbol == "GBPJPY":
            slope = float(row["daily_slope_dir"])
            gap = float(row["directional_di_gap"])
            if not (slope <= 0.02 or (slope > 0.13 and gap <= 26.0)):
                continue
        exit_time, result_r = _simulate(h4, int(i), side, 1.25, 3.0, 2.5, 24)
        rows.append(
            _candidate(
                symbol,
                f"{symbol}_SWING_CORE",
                "H4_DONCHIAN_BREAKOUT",
                side,
                row["end"],
                exit_time,
                risks[symbol],
                result_r,
            )
        )
    return pd.DataFrame(rows)


def _gbpusd_retest_candidates(h4: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    latest: tuple[int, int, float] | None = None
    for i in range(56, len(h4) - 1):
        row = h4.iloc[i]
        if int(row["breakout_side"]) != 0:
            latest = (i, int(row["breakout_side"]), float(row["breakout_level"]))
            continue
        if latest is None:
            continue
        breakout_index, side, level = latest
        age = i - breakout_index
        if age > 9:
            latest = None
            continue
        if age < 1:
            continue
        atr_value = float(row["atr14"])
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        body_atr = abs(float(row["close"] - row["open"])) / atr_value
        if side > 0:
            valid = (
                row["low"] <= level + 0.30 * atr_value
                and row["low"] >= level - 0.80 * atr_value
                and row["close"] > level
                and row["close"] > row["open"]
                and row["ema20"] > row["ema50"]
                and body_atr >= 0.20
            )
        else:
            valid = (
                row["high"] >= level - 0.30 * atr_value
                and row["high"] <= level + 0.80 * atr_value
                and row["close"] < level
                and row["close"] < row["open"]
                and row["ema20"] < row["ema50"]
                and body_atr >= 0.20
            )
        if not valid:
            continue
        exit_time, result_r = _simulate(h4, i, side, 1.50, 4.0, 2.0, 36)
        rows.append(
            _candidate(
                "GBPUSD",
                "GBPUSD_SWING_RETEST",
                "H4_BREAKOUT_RETEST",
                side,
                row["end"],
                exit_time,
                0.15,
                result_r,
            )
        )
        latest = None
    return pd.DataFrame(rows)


def _h1_retest_candidates(symbol: str, h1: pd.DataFrame, h4: pd.DataFrame) -> pd.DataFrame:
    risk = 0.10 if symbol == "EURUSD" else 0.20
    breakout_rows = h4[h4["breakout_side"] != 0].copy()
    if symbol in {"EURUSD", "GBPJPY"}:
        breakout_rows = breakout_rows[breakout_rows["atr_pct_252"].notna()]
        breakout_rows = breakout_rows[breakout_rows["atr_pct_252"] <= 0.80]
    events = list(breakout_rows.itertuples())
    rows: list[dict] = []
    event_index = 0
    latest = None
    consumed = False
    for i in range(60, len(h1) - 1):
        row = h1.iloc[i]
        while event_index < len(events) and pd.Timestamp(events[event_index].end) <= pd.Timestamp(row["time"]):
            event = events[event_index]
            latest = {
                "end": pd.Timestamp(event.end),
                "side": int(event.breakout_side),
                "level": float(event.breakout_level),
            }
            consumed = False
            event_index += 1
        if latest is None or consumed:
            continue
        bars_since = int((pd.Timestamp(row["end"]) - latest["end"]).total_seconds() // 3600)
        if bars_since < 1:
            continue
        if bars_since > 48:
            latest = None
            continue
        atr_value = float(row["atr14"])
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        side = int(latest["side"])
        level = float(latest["level"])
        body_atr = abs(float(row["close"] - row["open"])) / atr_value
        if side > 0:
            valid = (
                row["low"] <= level + 0.20 * atr_value
                and row["low"] >= level - 0.50 * atr_value
                and row["close"] > level
                and row["close"] > row["open"]
                and row["ema20"] > row["ema50"]
                and body_atr >= 0.20
            )
        else:
            valid = (
                row["high"] >= level - 0.20 * atr_value
                and row["high"] <= level + 0.50 * atr_value
                and row["close"] < level
                and row["close"] < row["open"]
                and row["ema20"] < row["ema50"]
                and body_atr >= 0.20
            )
        if valid and symbol == "EURUSD":
            valid = float(row["ema_sep_atr"]) >= 1.80 and int(row["hour"]) <= 12
        elif valid and symbol == "GBPJPY":
            valid = int(row["hour"]) <= 12
        if not valid:
            continue
        exit_time, result_r = _simulate(h1, i, side, 1.25, 3.0, 1.5, 96)
        rows.append(
            _candidate(
                symbol,
                f"{symbol}_SWING_RETEST",
                "H1_BREAKOUT_RETEST",
                side,
                row["end"],
                exit_time,
                risk,
                result_r,
            )
        )
        consumed = True
    return pd.DataFrame(rows)


def _gbpusd_pullback_addon(h4: pd.DataFrame) -> pd.DataFrame:
    frame = h4.copy()
    frame["ema20_slope_h4"] = frame["ema20"].diff(3) / 3
    frame["prior3_low"] = frame["low"].rolling(3, min_periods=3).min().shift(1)
    frame["prior3_high"] = frame["high"].rolling(3, min_periods=3).max().shift(1)
    rows: list[dict] = []
    for i in range(60, len(frame) - 1):
        row = frame.iloc[i]
        hour = int(pd.Timestamp(row["end"]).hour)
        weekday = int(pd.Timestamp(row["end"]).weekday())
        if hour not in {8, 12, 16} or weekday >= 5:
            continue
        required = [
            row["atr14"], row["ema20"], row["ema50"], row["ema20_slope_h4"],
            row["dclose"], row["dema20"], row["dema50"], row["adx14"],
            row["body_ratio"], row["volume_ratio"], row["atr_ratio"],
            row["prior3_low"], row["prior3_high"],
        ]
        if any(not np.isfinite(value) for value in required):
            continue
        atr_value = float(row["atr14"])
        long_bias = (
            row["dclose"] > row["dema20"] > row["dema50"]
            and row["ema20"] > row["ema50"]
            and row["ema20_slope_h4"] > 0
        )
        short_bias = (
            row["dclose"] < row["dema20"] < row["dema50"]
            and row["ema20"] < row["ema50"]
            and row["ema20_slope_h4"] < 0
        )
        quality = (
            row["adx14"] >= 20
            and row["body_ratio"] >= 0.55
            and row["volume_ratio"] >= 1.0
            and row["atr_ratio"] >= 1.0
        )
        side = 0
        if (
            long_bias and quality
            and row["prior3_low"] <= row["ema20"] + 0.30 * atr_value
            and row["close"] > row["open"]
            and row["close_location"] >= 0.60
        ):
            side = 1
        elif (
            short_bias and quality
            and row["prior3_high"] >= row["ema20"] - 0.30 * atr_value
            and row["close"] < row["open"]
            and row["close_location"] <= 0.40
        ):
            side = -1
        if not side:
            continue
        directional_ema_gap = side * (float(row["ema20"]) - float(row["ema50"])) / atr_value
        if directional_ema_gap > 1.237:
            continue
        exit_time, result_r = _simulate(frame, i, side, 1.25, 2.50, 2.0, 36)
        rows.append(
            _candidate(
                "GBPUSD",
                PRECISION_ENGINE,
                "GBPUSD_SWING_V5_PULLBACK_ADDON",
                side,
                row["end"],
                exit_time,
                0.40,
                result_r,
            )
        )
    return pd.DataFrame(rows)


def _gbpusd_precision(h4: pd.DataFrame) -> pd.DataFrame:
    precision = base.gbpusd_precision_candidates(h4).copy()
    pullback = _gbpusd_pullback_addon(h4)
    frames = [frame for frame in (precision, pullback) if not frame.empty]
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values(["entry_time", "setup"]).drop_duplicates(
        ["entry_time", "setup", "side"], keep="first"
    ).reset_index(drop=True)


def _audusd_candidates(h4: pd.DataFrame, params: AUDUSDParams) -> pd.DataFrame:
    rows: list[dict] = []
    for i in range(60, len(h4) - 1):
        row = h4.iloc[i]
        if int(pd.Timestamp(row["end"]).hour) not in params.allowed_hours:
            continue
        required = [
            row["atr14"], row["ema20"], row["ema50"], row["adx14"],
            row["dclose"], row["dema20"], row["dema50"], row["body_ratio"],
        ]
        if any(not np.isfinite(value) for value in required):
            continue
        atr_value = float(row["atr14"])
        long_regime = row["dclose"] > row["dema20"] > row["dema50"] and row["ema20"] > row["ema50"]
        short_regime = row["dclose"] < row["dema20"] < row["dema50"] and row["ema20"] < row["ema50"]
        side = 0
        if (
            long_regime
            and row["adx14"] >= params.adx_min
            and row["low"] <= row["ema20"] + params.touch_atr * atr_value
            and row["low"] >= row["ema50"] - 0.35 * atr_value
            and row["close"] > row["ema20"]
            and row["close"] > row["open"]
            and row["body_ratio"] >= params.body_ratio_min
        ):
            side = 1
        elif (
            short_regime
            and row["adx14"] >= params.adx_min
            and row["high"] >= row["ema20"] - params.touch_atr * atr_value
            and row["high"] <= row["ema50"] + 0.35 * atr_value
            and row["close"] < row["ema20"]
            and row["close"] < row["open"]
            and row["body_ratio"] >= params.body_ratio_min
        ):
            side = -1
        if not side:
            continue
        exit_time, result_r = _simulate(
            h4, i, side, params.stop_atr, params.target_r, params.trail_atr, params.max_bars
        )
        rows.append(
            _candidate(
                "AUDUSD",
                "AUDUSD_TREND_PULLBACK",
                "D1_H4_EMA_PULLBACK_04_08UTC",
                side,
                row["end"],
                exit_time,
                params.risk_percent,
                result_r,
            )
        )
    return pd.DataFrame(rows)


def _select_audusd(h4: pd.DataFrame) -> tuple[AUDUSDParams, pd.DataFrame, dict]:
    split = h4["time"].min() + (h4["time"].max() - h4["time"].min()) * 0.70
    scored = []
    for adx in (15.0, 18.0, 22.0):
        for touch in (0.15, 0.30, 0.50):
            for body in (0.15, 0.25):
                params = AUDUSDParams(adx, touch, body)
                frame = _audusd_candidates(h4, params)
                development = _stats(frame[frame["entry_time"] < split])
                score = development["net_r"]
                score -= max(0.0, 1.12 - development["profit_factor"]) * 35
                if development["trades"] < 60:
                    score -= (60 - development["trades"]) * 0.35
                scored.append((score, params, development, frame))
    scored.sort(key=lambda item: item[0], reverse=True)
    score, selected, development, frame = scored[0]
    validation = _stats(frame[frame["entry_time"] >= split])
    passed = validation["trades"] >= 60 and validation["net_r"] > 0 and validation["profit_factor"] >= 1.10
    return selected, frame, {
        "split": split.isoformat(),
        "selected_score": float(score),
        "selected_params": asdict(selected),
        "development": development,
        "validation": validation,
        "passed": bool(passed),
        "top_scores": [
            {"score": float(item[0]), "params": asdict(item[1]), "development": item[2]}
            for item in scored[:5]
        ],
    }


def _usdjpy_candidates(h4: pd.DataFrame) -> pd.DataFrame:
    prior_high = h4["high"].rolling(40, min_periods=40).max().shift(1)
    prior_low = h4["low"].rolling(40, min_periods=40).min().shift(1)
    rows: list[dict] = []
    for i in range(60, len(h4) - 1):
        row = h4.iloc[i]
        required = [row["atr14"], row["dclose"], row["dema20"], row["dema50"], prior_high.iloc[i], prior_low.iloc[i]]
        if any(not np.isfinite(value) for value in required):
            continue
        side = 0
        if row["dclose"] > row["dema20"] > row["dema50"] and row["close"] > prior_high.iloc[i]:
            side = 1
        elif row["dclose"] < row["dema20"] < row["dema50"] and row["close"] < prior_low.iloc[i]:
            side = -1
        if not side:
            continue
        exit_time, result_r = _simulate(h4, i, side, 1.50, 3.0, 2.0, 30)
        rows.append(
            _candidate(
                "USDJPY",
                "USDJPY_SAFE_HAVEN_BREAKOUT",
                "D1_H4_40BAR_BREAKOUT",
                side,
                row["end"],
                exit_time,
                0.25,
                result_r,
            )
        )
    return pd.DataFrame(rows)


def _validation_report(frame: pd.DataFrame, h4: pd.DataFrame) -> dict:
    split = h4["time"].min() + (h4["time"].max() - h4["time"].min()) * 0.70
    development = _stats(frame[frame["entry_time"] < split])
    validation = _stats(frame[frame["entry_time"] >= split])
    passed = validation["trades"] >= 60 and validation["net_r"] > 0 and validation["profit_factor"] >= 1.10
    return {
        "split": split.isoformat(),
        "development": development,
        "validation": validation,
        "passed": bool(passed),
    }


def _symbol_cap(symbol: str, engine: str, config: PortfolioConfig) -> float:
    if engine == PRECISION_ENGINE:
        return config.precision_symbol_cap_percent
    if symbol in NEW_SYMBOLS:
        return config.new_symbol_cap_percent
    return config.legacy_symbol_cap_percent


def _position_reason(active: list[dict], row: object, config: PortfolioConfig) -> str | None:
    if len(active) >= config.max_positions:
        return "max_positions"
    risk = float(row.risk_percent)
    if sum(float(item["risk_percent"]) for item in active) + risk > config.max_open_risk_percent + 1e-9:
        return "max_open_risk"
    cap = _symbol_cap(str(row.symbol), str(row.engine), config)
    if sum(float(item["risk_percent"]) for item in active if item["symbol"] == row.symbol) + risk > cap + 1e-9:
        return "symbol_cap"
    if str(row.symbol).startswith("GBP"):
        gbp = [item for item in active if str(item["symbol"]).startswith("GBP")]
        directions = {int(item["side"]) for item in gbp}
        directions.add(int(row.side))
        gbp_cap = config.mixed_gbp_cap_percent if len(directions) > 1 else config.aligned_gbp_cap_percent
        if sum(float(item["risk_percent"]) for item in gbp) + risk > gbp_cap + 1e-9:
            return "gbp_cap"
    return None


def _proxy(values: dict) -> object:
    return type("Candidate", (), values)


def _replay(
    candidates: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    config: PortfolioConfig,
    guard: GuardConfig = GuardConfig(),
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    data = candidates[(candidates["entry_time"] >= start) & (candidates["entry_time"] <= end)].copy()
    data = data.sort_values(["entry_time", "engine", "setup"]).reset_index(drop=True)
    balance = peak = STARTING_BALANCE
    max_dd = stress_dd = 0.0
    active: list[dict] = []
    accepted: list[dict] = []
    rejected: list[dict] = []
    histories: dict[str, list[float]] = {}
    disabled_until: dict[str, pd.Timestamp] = {}
    probe_active_until: dict[str, pd.Timestamp] = {}

    def close_due(now: pd.Timestamp) -> None:
        nonlocal balance, peak, max_dd
        due = sorted([item for item in active if item["exit_time"] <= now], key=lambda item: item["exit_time"])
        for item in due:
            balance += float(item["risk_dollars"]) * float(item["r_multiple"])
            histories.setdefault(str(item["engine"]), []).append(float(item["r_multiple"]))
            if item.get("is_recovery_probe"):
                probe_active_until.pop(str(item["engine"]), None)
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak * 100 if peak else 0.0)
            active.remove(item)

    for row in data.itertuples(index=False):
        entry_time = pd.Timestamp(row.entry_time)
        close_due(entry_time)
        engine = str(row.engine)
        decision = _guard_decision(
            engine, histories, entry_time, disabled_until, probe_active_until, guard
        )
        if decision.multiplier <= 0:
            rejected.append({**row._asdict(), "reason": f"guard:{decision.reason}"})
            continue
        adjusted = row._asdict()
        adjusted["risk_percent"] = float(row.risk_percent) * decision.multiplier
        reason = _position_reason(active, _proxy(adjusted), config)
        if reason:
            rejected.append({**adjusted, "reason": reason})
            continue
        risk_dollars = balance * float(adjusted["risk_percent"]) / 100.0
        item = {
            **adjusted,
            "risk_dollars": risk_dollars,
            "guard_reason": decision.reason,
            "guard_multiplier": decision.multiplier,
            "is_recovery_probe": decision.is_probe,
        }
        active.append(item)
        accepted.append(item)
        if decision.is_probe:
            disabled_until.pop(engine, None)
            probe_active_until[engine] = pd.Timestamp(row.exit_time)
        stressed = balance - sum(float(position["risk_dollars"]) for position in active)
        stress_dd = max(stress_dd, (peak - stressed) / peak * 100 if peak else 0.0)

    close_due(pd.Timestamp.max.tz_localize("UTC"))
    accepted_frame = pd.DataFrame(accepted)
    rejected_frame = pd.DataFrame(rejected)
    if accepted_frame.empty:
        gross_income = gross_loss = 0.0
    else:
        pnl = accepted_frame["risk_dollars"] * accepted_frame["r_multiple"]
        gross_income = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "starting_balance": STARTING_BALANCE,
        "ending_balance": balance,
        "gross_income": gross_income,
        "gross_loss": gross_loss,
        "net_profit": balance - STARTING_BALANCE,
        "return_percent": (balance / STARTING_BALANCE - 1) * 100,
        "average_monthly_profit": (balance - STARTING_BALANCE) / max(1.0, (end - start).days / 30.4375),
        "trades": int(len(accepted_frame)),
        "profit_factor": gross_income / gross_loss if gross_loss else (math.inf if gross_income else 0.0),
        "max_drawdown_percent": max_dd,
        "stress_drawdown_percent": stress_dd,
        "rejections": rejected_frame["reason"].value_counts().to_dict() if not rejected_frame.empty else {},
    }
    return summary, accepted_frame, rejected_frame


def _attribution(frame: pd.DataFrame, scenario: str, window: str, column: str) -> pd.DataFrame:
    rows = []
    expected = ALL_SYMBOLS if column == "symbol" else sorted(frame[column].unique()) if not frame.empty else []
    for value in expected:
        group = frame[frame[column] == value].copy() if not frame.empty else pd.DataFrame()
        if group.empty:
            rows.append({
                "scenario": scenario, "window": window, column: value,
                "trades": 0, "gross_income": 0.0, "gross_loss": 0.0,
                "net_profit": 0.0, "profit_factor": 0.0,
            })
            continue
        pnl = group["risk_dollars"] * group["r_multiple"]
        gross_income = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
        rows.append({
            "scenario": scenario, "window": window, column: value,
            "trades": int(len(group)), "gross_income": gross_income,
            "gross_loss": gross_loss, "net_profit": gross_income - gross_loss,
            "profit_factor": gross_income / gross_loss if gross_loss else math.inf,
        })
    return pd.DataFrame(rows)


def _scenario_candidates(
    legacy: pd.DataFrame,
    audusd: pd.DataFrame,
    usdjpy: pd.DataFrame,
) -> dict[str, tuple[pd.DataFrame, PortfolioConfig]]:
    def merge(*frames: pd.DataFrame) -> pd.DataFrame:
        valid = [frame for frame in frames if not frame.empty]
        return pd.concat(valid, ignore_index=True).sort_values(["entry_time", "engine", "setup"]).reset_index(drop=True)

    return {
        "v12_legacy_rebuilt": (legacy, ORIGINAL_CAPS),
        "v12_plus_audusd_same_caps": (merge(legacy, audusd), ORIGINAL_CAPS),
        "v12_plus_usdjpy_same_caps": (merge(legacy, usdjpy), ORIGINAL_CAPS),
        "v12_plus_both_same_caps": (merge(legacy, audusd, usdjpy), ORIGINAL_CAPS),
        "v12_plus_both_capacity": (merge(legacy, audusd, usdjpy), CAPACITY_CAPS),
    }


def main() -> None:
    prepared = {symbol: _prepare(symbol) for symbol in ALL_SYMBOLS}

    legacy_frames = [_gbpusd_precision(prepared["GBPUSD"][1])]
    for symbol in LEGACY_SYMBOLS:
        h1, h4, _ = prepared[symbol]
        legacy_frames.append(_v12_core_candidates(symbol, h4))
        if symbol == "GBPUSD":
            legacy_frames.append(_gbpusd_retest_candidates(h4))
        else:
            legacy_frames.append(_h1_retest_candidates(symbol, h1, h4))
    legacy = pd.concat([frame for frame in legacy_frames if not frame.empty], ignore_index=True)
    legacy = legacy.sort_values(["entry_time", "engine", "setup"]).reset_index(drop=True)

    audusd_params, audusd, audusd_validation = _select_audusd(prepared["AUDUSD"][1])
    usdjpy = _usdjpy_candidates(prepared["USDJPY"][1])
    usdjpy_validation = _validation_report(usdjpy, prepared["USDJPY"][1])

    if not audusd_validation["passed"]:
        audusd = audusd.iloc[0:0].copy()
    if not usdjpy_validation["passed"]:
        usdjpy = usdjpy.iloc[0:0].copy()

    legacy.to_csv(OUT / "legacy_v12_candidates.csv", index=False)
    audusd.to_csv(OUT / "audusd_validated_candidates.csv", index=False)
    usdjpy.to_csv(OUT / "usdjpy_validated_candidates.csv", index=False)

    common_end = min(prepared[symbol][1]["time"].max() for symbol in ALL_SYMBOLS)
    common_start = max(prepared[symbol][1]["time"].min() for symbol in ALL_SYMBOLS)
    windows = {
        "max": common_start,
        "5y": max(common_start, common_end - pd.DateOffset(years=5)),
        "3y": max(common_start, common_end - pd.DateOffset(years=3)),
        "2y": max(common_start, common_end - pd.DateOffset(years=2)),
        "1y": max(common_start, common_end - pd.DateOffset(years=1)),
        "6m": max(common_start, common_end - pd.DateOffset(months=6)),
    }

    scenarios = _scenario_candidates(legacy, audusd, usdjpy)
    results = {
        "status": "RESEARCH_ONLY_DO_NOT_DEPLOY",
        "data_source": base.DATA_URL,
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "starting_balance": STARTING_BALANCE,
        "guard": asdict(GuardConfig()),
        "portfolio_configs": {
            ORIGINAL_CAPS.name: asdict(ORIGINAL_CAPS),
            CAPACITY_CAPS.name: asdict(CAPACITY_CAPS),
        },
        "audusd_validation": audusd_validation,
        "usdjpy_validation": usdjpy_validation,
        "audusd_selected_params": asdict(audusd_params),
        "scenarios": {},
    }
    summary_rows = []
    symbol_rows = []
    engine_rows = []
    for scenario, (candidates, config) in scenarios.items():
        results["scenarios"][scenario] = {"portfolio_config": asdict(config), "windows": {}}
        candidates.to_csv(OUT / f"{scenario}_candidates.csv", index=False)
        for window, start in windows.items():
            summary, accepted, rejected = _replay(candidates, start, common_end, config)
            results["scenarios"][scenario]["windows"][window] = summary
            summary_rows.append({"scenario": scenario, "window": window, **summary})
            symbol_rows.append(_attribution(accepted, scenario, window, "symbol"))
            engine_rows.append(_attribution(accepted, scenario, window, "engine"))
            accepted.to_csv(OUT / f"accepted_{scenario}_{window}.csv", index=False)
            rejected.to_csv(OUT / f"rejected_{scenario}_{window}.csv", index=False)

    summary_frame = pd.DataFrame(summary_rows)
    symbol_frame = pd.concat(symbol_rows, ignore_index=True)
    engine_frame = pd.concat(engine_rows, ignore_index=True)
    summary_frame.to_csv(OUT / "scenario_summary.csv", index=False)
    symbol_frame.to_csv(OUT / "profit_by_symbol.csv", index=False)
    engine_frame.to_csv(OUT / "profit_by_engine.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    max_rows = summary_frame[summary_frame["window"] == "max"].copy()
    lines = [
        "# V12 Plus Independently Validated Symbols",
        "",
        "Status: **RESEARCH ONLY — DO NOT DEPLOY**",
        "",
        f"Common public-data coverage: `{common_start.isoformat()}` through `{common_end.isoformat()}`.",
        "",
        "## Independent admission",
        "",
        f"- AUDUSD passed: **{audusd_validation['passed']}**; selected params: `{json.dumps(asdict(audusd_params), sort_keys=True)}`.",
        f"- USDJPY passed: **{usdjpy_validation['passed']}**.",
        "",
        "## Maximum-history portfolio comparison",
        "",
        "| Scenario | Trades | Net profit | Ending balance | Return | PF | Max DD | Stress DD | Avg monthly |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in max_rows.itertuples(index=False):
        lines.append(
            f"| {row.scenario} | {row.trades} | ${row.net_profit:.2f} | ${row.ending_balance:.2f} | "
            f"{row.return_percent:.2f}% | {row.profit_factor:.3f} | {row.max_drawdown_percent:.2f}% | "
            f"{row.stress_drawdown_percent:.2f}% | ${row.average_monthly_profit:.2f} |"
        )
    (OUT / "V12_PLUS_VALIDATED_ASSETS_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
