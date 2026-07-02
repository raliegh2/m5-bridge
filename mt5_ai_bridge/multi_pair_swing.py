"""Modular multi-pair swing models for research, demo and forward testing.

GBPUSD remains delegated to the frozen V4 engine. EURUSD and GBPJPY are disabled
by default and must pass independent walk-forward and forward-test gates before
being enabled in approval or automatic execution.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .enums import Signal
from .gbpusd_v4 import _adx, _atr, _ema, _rates


@dataclass(frozen=True)
class PairRisk:
    risk_percent: float
    partial_fraction: float
    partial_r: float
    trail_atr: float
    max_hold_h4_bars: int = 72


@dataclass(frozen=True)
class EventPolicy:
    calendar_path: str = "research/events/macro_events.csv"
    block_high_impact_day: bool = True
    minutes_before: int = 120
    minutes_after: int = 120
    require_calendar: bool = True


@dataclass(frozen=True)
class EURUSDParams:
    enabled: bool = False
    long_enabled: bool = True
    short_enabled: bool = True
    d1_adx_min_long: float = 20.0
    d1_adx_min_short: float = 20.0
    atr_percentile_min: float = 0.25
    atr_percentile_max: float = 0.80
    prior_day_location_long: float = 0.55
    prior_day_location_short: float = 0.45
    max_gap_atr: float = 0.20
    max_overnight_atr: float = 0.85
    max_spread_atr: float = 0.015
    pullback_zone_atr: float = 0.20
    body_ratio_min: float = 0.30
    rsi_long_min: float = 45.0
    rsi_long_max: float = 68.0
    rsi_short_min: float = 32.0
    rsi_short_max: float = 55.0
    stop_atr: float = 1.10
    target_r: float = 2.50
    risk: PairRisk = field(default_factory=lambda: PairRisk(0.25, 0.50, 1.0, 2.0))


@dataclass(frozen=True)
class GBPJPYParams:
    enabled: bool = False
    long_enabled: bool = True
    short_enabled: bool = True
    d1_adx_min_long: float = 24.0
    d1_adx_min_short: float = 24.0
    d1_atr_percentile_min: float = 0.55
    bollinger_expansion_min: float = 1.05
    max_gap_atr: float = 0.25
    max_overnight_atr: float = 1.00
    max_spread_atr: float = 0.025
    breakout_lookback: int = 20
    body_ratio_min: float = 0.45
    rsi_long_min: float = 57.0
    rsi_short_max: float = 43.0
    pullback_zone_atr: float = 0.25
    stop_atr: float = 1.75
    target_r: float = 2.75
    use_breakout: bool = True
    use_deep_pullback: bool = True
    risk: PairRisk = field(default_factory=lambda: PairRisk(0.20, 0.40, 1.25, 2.75))


@dataclass(frozen=True)
class SwingSetup:
    symbol: str
    side: Signal
    setup_name: str
    signal_time: datetime
    atr_price: float
    stop_atr: float
    target_r: float
    risk_percent: float
    partial_fraction: float
    partial_r: float
    trail_atr: float
    max_hold_h4_bars: int
    reason: str
    regime: dict


@dataclass(frozen=True)
class PortfolioRules:
    max_positions: int = 3
    max_positions_per_symbol: int = 1
    max_risk_per_trade: float = 0.50
    max_open_risk_percent: float = 0.75
    max_daily_new_risk_percent: float = 0.75
    gbp_cluster_risk_percent: float = 0.50
    drawdown_throttle_percent: float = 3.0
    drawdown_pause_percent: float = 6.0
    daily_loss_dollars: float = 250.0
    total_loss_dollars: float = 500.0


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = average_gain / average_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _resample(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    source = frame.set_index("time")
    result = source.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
        spread=("spread", "median"),
    ).dropna()
    return result.reset_index()


def prepare_features(h1: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Create completed H4/D1/W1 features without forward-looking joins."""
    h4 = _resample(h1, "4h")
    d1 = _resample(h1, "1D")
    w1 = _resample(h1, "W-MON")

    for frame in (h4, d1, w1):
        frame["atr14"] = _atr(frame, 14)
        frame["adx14"] = _adx(frame, 14)
        frame["rsi14"] = _rsi(frame["close"], 14)

    h4["ema21"] = _ema(h4["close"], 21)
    h4["ema50"] = _ema(h4["close"], 50)
    h4["body_ratio"] = (
        (h4["close"] - h4["open"]).abs()
        / (h4["high"] - h4["low"]).replace(0, np.nan)
    )
    h4["atr_percentile"] = h4["atr14"].rolling(252, min_periods=100).rank(pct=True)
    h4["swing_high"] = h4["high"].shift(1).rolling(20).max()
    h4["swing_low"] = h4["low"].shift(1).rolling(20).min()

    d1["ema100"] = _ema(d1["close"], 100)
    d1["ema200"] = _ema(d1["close"], 200)
    d1["atr_percentile_d1"] = d1["atr14"].rolling(252, min_periods=100).rank(pct=True)
    d1["prior_range"] = d1["high"].shift(1) - d1["low"].shift(1)
    d1["prior_location"] = (
        (d1["close"].shift(1) - d1["low"].shift(1))
        / d1["prior_range"].replace(0, np.nan)
    )
    d1["gap_atr"] = (d1["open"] - d1["close"].shift(1)).abs() / d1["atr14"]
    d1["bb_mid"] = d1["close"].rolling(20).mean()
    d1["bb_std"] = d1["close"].rolling(20).std(ddof=0)
    d1["bb_width"] = 4 * d1["bb_std"] / d1["bb_mid"].replace(0, np.nan)
    d1["bb_expansion"] = d1["bb_width"] / d1["bb_width"].rolling(20).mean()

    source = h1.copy()
    source["date"] = source["time"].dt.floor("D")
    source["hour"] = source["time"].dt.hour
    overnight = source[source["hour"] < 7].groupby("date").agg(
        overnight_high=("high", "max"),
        overnight_low=("low", "min"),
    )
    overnight["overnight_range"] = overnight["overnight_high"] - overnight["overnight_low"]
    d1 = d1.merge(overnight, left_on=d1["time"].dt.floor("D"), right_index=True, how="left")
    d1 = d1.drop(columns=["key_0"])
    d1["overnight_atr"] = d1["overnight_range"] / d1["atr14"]

    w1["ema20w"] = _ema(w1["close"], 20)
    w1["weekly_slope"] = w1["ema20w"].diff()

    d1["available_time"] = d1["time"] + pd.Timedelta(days=1)
    w1["available_time"] = w1["time"] + pd.Timedelta(days=7)
    merged = pd.merge_asof(
        h4.sort_values("time"),
        d1[[
            "available_time", "close", "ema100", "ema200", "adx14",
            "atr_percentile_d1", "prior_location", "gap_atr", "overnight_atr",
            "bb_expansion",
        ]].sort_values("available_time"),
        left_on="time",
        right_on="available_time",
        direction="backward",
        suffixes=("", "_d1"),
    )
    merged = pd.merge_asof(
        merged.sort_values("time"),
        w1[["available_time", "weekly_slope"]].sort_values("available_time"),
        left_on="time",
        right_on="available_time",
        direction="backward",
        suffixes=("", "_w1"),
    )
    pip = 0.0001 if symbol != "GBPJPY" else 0.01
    merged["spread_atr"] = merged["spread"].fillna(0) * pip / merged["atr14"]
    merged["symbol"] = symbol
    return merged


def load_event_days(policy: EventPolicy) -> dict[str, set[pd.Timestamp]]:
    path = Path(policy.calendar_path)
    if not path.exists():
        if policy.require_calendar:
            raise FileNotFoundError(
                f"Required event calendar missing: {path}. No event-blind promotion allowed."
            )
        return {}
    result: dict[str, set[pd.Timestamp]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("impact", "")).upper() != "HIGH":
                continue
            symbol = str(row.get("symbol", "")).upper()
            event_time = pd.Timestamp(row["event_time_utc"])
            if event_time.tzinfo is None:
                event_time = event_time.tz_localize("UTC")
            result.setdefault(symbol, set()).add(event_time.floor("D"))
    return result


def is_event_day(symbol: str, when: datetime, event_days: dict[str, set[pd.Timestamp]]) -> bool:
    day = pd.Timestamp(when).tz_convert("UTC").floor("D")
    return day in event_days.get(symbol.upper(), set())


def evaluate_eurusd(row: pd.Series, params: EURUSDParams, event_day: bool) -> Optional[SwingSetup]:
    if not params.enabled or event_day:
        return None
    weekly_long = row["weekly_slope"] > 0
    weekly_short = row["weekly_slope"] < 0
    volatility_ok = params.atr_percentile_min <= row["atr_percentile"] <= params.atr_percentile_max
    execution_ok = (
        row["gap_atr"] <= params.max_gap_atr
        and row["overnight_atr"] <= params.max_overnight_atr
        and row["spread_atr"] <= params.max_spread_atr
    )
    if not volatility_ok or not execution_ok:
        return None

    long_regime = (
        params.long_enabled and row["close_d1"] > row["ema200"]
        and row["adx14_d1"] >= params.d1_adx_min_long and weekly_long
        and row["prior_location"] >= params.prior_day_location_long
    )
    short_regime = (
        params.short_enabled and row["close_d1"] < row["ema200"]
        and row["adx14_d1"] >= params.d1_adx_min_short and weekly_short
        and row["prior_location"] <= params.prior_day_location_short
    )
    long_trigger = (
        row["low"] <= max(row["ema21"], row["ema50"]) + params.pullback_zone_atr * row["atr14"]
        and row["close"] > row["ema21"] and row["close"] > row["open"]
        and row["body_ratio"] >= params.body_ratio_min
        and params.rsi_long_min <= row["rsi14"] <= params.rsi_long_max
    )
    short_trigger = (
        row["high"] >= min(row["ema21"], row["ema50"]) - params.pullback_zone_atr * row["atr14"]
        and row["close"] < row["ema21"] and row["close"] < row["open"]
        and row["body_ratio"] >= params.body_ratio_min
        and params.rsi_short_min <= row["rsi14"] <= params.rsi_short_max
    )
    side = Signal.BUY if long_regime and long_trigger else (
        Signal.SELL if short_regime and short_trigger else None
    )
    if side is None:
        return None
    return SwingSetup(
        "EURUSD", side, "EURUSD_D1_H4_PULLBACK", row["time"].to_pydatetime(),
        float(row["atr14"]), params.stop_atr, params.target_r,
        params.risk.risk_percent, params.risk.partial_fraction,
        params.risk.partial_r, params.risk.trail_atr,
        params.risk.max_hold_h4_bars,
        "D1/weekly trend with H4 EMA-zone pullback and regime filters.",
        {
            "atr_percentile": float(row["atr_percentile"]),
            "prior_day_location": float(row["prior_location"]),
            "gap_atr": float(row["gap_atr"]),
            "overnight_atr": float(row["overnight_atr"]),
            "spread_atr": float(row["spread_atr"]),
        },
    )


def evaluate_gbpjpy(row: pd.Series, params: GBPJPYParams, event_day: bool) -> Optional[SwingSetup]:
    if not params.enabled or event_day:
        return None
    weekly_long = row["weekly_slope"] > 0
    weekly_short = row["weekly_slope"] < 0
    execution_ok = (
        row["gap_atr"] <= params.max_gap_atr
        and row["overnight_atr"] <= params.max_overnight_atr
        and row["spread_atr"] <= params.max_spread_atr
    )
    expansion_ok = (
        row["atr_percentile_d1"] >= params.d1_atr_percentile_min
        and row["bb_expansion"] >= params.bollinger_expansion_min
    )
    if not execution_ok or not expansion_ok:
        return None
    long_regime = (
        params.long_enabled and row["ema100"] > row["ema200"]
        and row["adx14_d1"] >= params.d1_adx_min_long and weekly_long
    )
    short_regime = (
        params.short_enabled and row["ema100"] < row["ema200"]
        and row["adx14_d1"] >= params.d1_adx_min_short and weekly_short
    )
    breakout_long = (
        params.use_breakout and row["close"] > row["swing_high"]
        and row["body_ratio"] >= params.body_ratio_min
        and row["rsi14"] >= params.rsi_long_min
    )
    breakout_short = (
        params.use_breakout and row["close"] < row["swing_low"]
        and row["body_ratio"] >= params.body_ratio_min
        and row["rsi14"] <= params.rsi_short_max
    )
    pullback_long = (
        params.use_deep_pullback
        and row["low"] <= row["ema50"] + params.pullback_zone_atr * row["atr14"]
        and row["close"] > row["ema21"] and row["close"] > row["open"]
        and row["body_ratio"] >= params.body_ratio_min
    )
    pullback_short = (
        params.use_deep_pullback
        and row["high"] >= row["ema50"] - params.pullback_zone_atr * row["atr14"]
        and row["close"] < row["ema21"] and row["close"] < row["open"]
        and row["body_ratio"] >= params.body_ratio_min
    )
    side = Signal.BUY if long_regime and (breakout_long or pullback_long) else (
        Signal.SELL if short_regime and (breakout_short or pullback_short) else None
    )
    if side is None:
        return None
    name = "GBPJPY_BREAKOUT" if (
        breakout_long if side is Signal.BUY else breakout_short
    ) else "GBPJPY_DEEP_PULLBACK"
    return SwingSetup(
        "GBPJPY", side, name, row["time"].to_pydatetime(),
        float(row["atr14"]), params.stop_atr, params.target_r,
        params.risk.risk_percent, params.risk.partial_fraction,
        params.risk.partial_r, params.risk.trail_atr,
        params.risk.max_hold_h4_bars,
        "D1 momentum regime with H4 breakout/deep-pullback trigger.",
        {
            "d1_atr_percentile": float(row["atr_percentile_d1"]),
            "bb_expansion": float(row["bb_expansion"]),
            "gap_atr": float(row["gap_atr"]),
            "overnight_atr": float(row["overnight_atr"]),
            "spread_atr": float(row["spread_atr"]),
        },
    )


def portfolio_allows(
    proposed: SwingSetup,
    open_positions: Iterable[dict],
    balance: float,
    open_risk_percent: float,
    daily_new_risk_percent: float,
    current_drawdown_percent: float,
    rules: PortfolioRules = PortfolioRules(),
) -> tuple[bool, str]:
    positions = list(open_positions)
    if current_drawdown_percent >= rules.drawdown_pause_percent:
        return False, "Portfolio drawdown pause is active."
    if len(positions) >= rules.max_positions:
        return False, "Maximum portfolio position count reached."
    if any(position.get("symbol") == proposed.symbol for position in positions):
        return False, "A position is already open for this symbol."
    proposed_risk = min(proposed.risk_percent, rules.max_risk_per_trade)
    if open_risk_percent + proposed_risk > rules.max_open_risk_percent:
        return False, "Aggregate open-risk cap would be exceeded."
    if daily_new_risk_percent + proposed_risk > rules.max_daily_new_risk_percent:
        return False, "Daily new-risk cap would be exceeded."
    if proposed.symbol in {"GBPUSD", "GBPJPY"}:
        gbp_risk = sum(
            float(position.get("risk_percent", 0.0))
            for position in positions
            if position.get("symbol") in {"GBPUSD", "GBPJPY"}
        )
        if gbp_risk + proposed_risk > rules.gbp_cluster_risk_percent:
            return False, "GBP correlation-cluster risk cap would be exceeded."
    return True, "Portfolio risk permits the entry."


def load_pair_config(path: str) -> tuple[EURUSDParams, GBPJPYParams, PortfolioRules]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    eur = EURUSDParams(**payload.get("EURUSD", {}))
    gj = GBPJPYParams(**payload.get("GBPJPY", {}))
    portfolio = PortfolioRules(**payload.get("portfolio", {}))
    return eur, gj, portfolio
