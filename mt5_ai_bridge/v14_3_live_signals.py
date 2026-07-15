"""Completed-candle live signal adapters for the enhanced V14.3 portfolio.

V12 signals for all five symbols and the new EURUSD/AUDUSD/USDJPY ICT modes are
built directly from closed MT5 H1/H4/D1 bars. Existing GBPUSD/GBPJPY V14.3 ICT
signals can be loaded through the optional legacy provider module because their
exact generator was not committed with the historical ledger branch.
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .v12_weak_symbol_profile import apply_weak_symbol_profile
from .v14_3_all_symbol_ict import PROFILES, generate_candidates, prepare_frames
from .v14_3_live_execution import LiveSignal, pip_size, resolve_broker_symbol
from .v14_3_satellite_symbol_profile import RISK, apply_satellite_v12_risk, filter_satellite_ict

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

import v12_plus_validated_assets_backtest as study  # noqa: E402
import v13_expanded_assets_backtest as base  # noqa: E402


SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
AUDUSD_PARAMS = study.AUDUSDParams(15.0, 0.30, 0.25)
V12_EXIT_MAP = {
    ("GBPUSD_V10_PRECISION", "PRIMARY_16UTC_BREAKOUT"): (1.50, 3.0),
    ("GBPUSD_V10_PRECISION", "SECONDARY_12UTC_BREAKOUT"): (1.50, 3.0),
    ("GBPUSD_V10_PRECISION", "GBPUSD_SWING_V5_PULLBACK_ADDON"): (1.25, 2.50),
    ("GBPUSD_SWING_RETEST", "H4_BREAKOUT_RETEST"): (1.50, 4.0),
    ("EURUSD_SWING_CORE", "H4_DONCHIAN_BREAKOUT"): (1.25, 3.0),
    ("EURUSD_SWING_RETEST", "H1_BREAKOUT_RETEST"): (1.25, 3.0),
    ("GBPJPY_SWING_CORE", "H4_DONCHIAN_BREAKOUT"): (1.25, 3.0),
    ("AUDUSD_TREND_PULLBACK", "D1_H4_EMA_PULLBACK_04_08UTC"): (1.25, 2.0),
    ("USDJPY_SAFE_HAVEN_BREAKOUT", "D1_H4_40BAR_BREAKOUT"): (1.50, 3.0),
}
SELECTED_ICT_PROFILE = {
    "EURUSD": "eu_ny_20",
    "AUDUSD": "au_london_relaxed",
    "USDJPY": "uj_ny_relaxed",
}


def _utc(values) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce").astype("datetime64[ns, UTC]")


def _frame(rates: Any) -> pd.DataFrame:
    frame = pd.DataFrame(rates)
    if frame.empty:
        return frame
    frame["time"] = _utc(pd.to_datetime(frame["time"], unit="s", utc=True))
    required = ["time", "open", "high", "low", "close", "tick_volume"]
    return frame[required].dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)


def prepare_v12_frames(client: Any, broker_symbol: str,
                       h1_count: int = 3000, h4_count: int = 2500,
                       d1_count: int = 800) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    h1 = _frame(client.copy_rates_from_pos(broker_symbol, "H1", 1, h1_count))
    h4 = _frame(client.copy_rates_from_pos(broker_symbol, "H4", 1, h4_count))
    d1 = _frame(client.copy_rates_from_pos(broker_symbol, "D1", 1, d1_count))
    if min(len(h1), len(h4), len(d1)) < 100:
        raise RuntimeError(f"{broker_symbol}: insufficient closed-bar history")

    h4["atr14"] = base.atr(h4)
    h4["ema20"] = base.ema(h4["close"], 20)
    h4["ema50"] = base.ema(h4["close"], 50)
    h4["avg_volume"] = h4["tick_volume"].rolling(20, min_periods=20).mean()
    h4["volume_ratio"] = h4["tick_volume"] / h4["avg_volume"]
    h4["atr_ratio"] = h4["atr14"] / h4["atr14"].rolling(20, min_periods=20).mean()
    h4["range"] = h4["high"] - h4["low"]
    h4["body_ratio"] = (h4["close"] - h4["open"]).abs() / h4["range"].replace(0, np.nan)
    h4["close_location"] = (h4["close"] - h4["low"]) / h4["range"].replace(0, np.nan)
    h4["plus_di"], h4["minus_di"], h4["adx14"] = base.directional(h4)
    h4["end"] = _utc(h4["time"] + pd.Timedelta(hours=4))

    h1["atr14"] = base.atr(h1)
    h1["ema20"] = base.ema(h1["close"], 20)
    h1["ema50"] = base.ema(h1["close"], 50)
    h1["end"] = _utc(h1["time"] + pd.Timedelta(hours=1))
    h1["ema_sep_atr"] = (h1["ema20"] - h1["ema50"]).abs() / h1["atr14"]
    h1["hour"] = h1["end"].dt.hour

    d1["ema20"] = base.ema(d1["close"], 20)
    d1["ema50"] = base.ema(d1["close"], 50)
    d1["available"] = _utc(d1["time"] + pd.Timedelta(days=1))
    daily = d1[["available", "close", "ema20", "ema50"]].rename(
        columns={"close": "dclose", "ema20": "dema20", "ema50": "dema50"}
    )
    h4 = pd.merge_asof(
        h4.sort_values("time"), daily.sort_values("available"),
        left_on="time", right_on="available", direction="backward",
    )

    d1["daily_atr14"] = base.atr(d1)
    d1["daily_ema20"] = base.ema(d1["close"], 20)
    d1["daily_ema50"] = base.ema(d1["close"], 50)
    d1["daily_ema20_slope"] = d1["daily_ema20"].diff(5) / 5
    d1["available_v12"] = _utc(d1["time"] + pd.Timedelta(days=1))
    h4 = pd.merge_asof(
        h4.sort_values("time"),
        d1[["available_v12", "daily_atr14", "daily_ema20_slope"]].sort_values("available_v12"),
        left_on="time", right_on="available_v12", direction="backward",
    )
    h4["ema_sep_atr"] = (h4["ema20"] - h4["ema50"]).abs() / h4["atr14"]
    h4["atr_pct_252"] = h4["atr14"].rolling(252, min_periods=100).rank(pct=True)
    h4["prior_high"] = h4["high"].rolling(55, min_periods=55).max().shift(1)
    h4["prior_low"] = h4["low"].rolling(55, min_periods=55).min().shift(1)
    h4["directional_di_gap_long"] = h4["plus_di"] - h4["minus_di"]
    h4["directional_di_gap_short"] = h4["minus_di"] - h4["plus_di"]
    long = (
        (h4["dclose"] > h4["dema20"]) & (h4["dema20"] > h4["dema50"])
        & (h4["close"] > h4["ema20"]) & (h4["adx14"] >= 20)
        & (h4["close"] > h4["prior_high"])
    )
    short = (
        (h4["dclose"] < h4["dema20"]) & (h4["dema20"] < h4["dema50"])
        & (h4["close"] < h4["ema20"]) & (h4["adx14"] >= 20)
        & (h4["close"] < h4["prior_low"])
    )
    h4["breakout_side"] = np.where(long, 1, np.where(short, -1, 0))
    h4["breakout_level"] = np.where(
        h4["breakout_side"] > 0, h4["prior_high"],
        np.where(h4["breakout_side"] < 0, h4["prior_low"], np.nan),
    )
    h4["directional_di_gap"] = np.where(
        h4["breakout_side"] > 0, h4["directional_di_gap_long"], h4["directional_di_gap_short"]
    )
    h4["daily_slope_dir"] = np.where(
        h4["breakout_side"] > 0,
        h4["daily_ema20_slope"] / h4["daily_atr14"],
        -h4["daily_ema20_slope"] / h4["daily_atr14"],
    )
    return h1, h4, d1


def build_v12_candidates(prepared: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    _, gbp_h4, _ = prepared["GBPUSD"]
    frames.extend([study._gbpusd_precision(gbp_h4), study._gbpusd_retest_candidates(gbp_h4)])
    eur_h1, eur_h4, _ = prepared["EURUSD"]
    frames.extend([study._v12_core_candidates("EURUSD", eur_h4), study._h1_retest_candidates("EURUSD", eur_h1, eur_h4)])
    _, gbpjpy_h4, _ = prepared["GBPJPY"]
    frames.append(study._v12_core_candidates("GBPJPY", gbpjpy_h4))
    _, aud_h4, _ = prepared["AUDUSD"]
    frames.append(study._audusd_candidates(aud_h4, AUDUSD_PARAMS))
    _, jpy_h4, _ = prepared["USDJPY"]
    frames.append(study._usdjpy_candidates(jpy_h4))
    usable = [frame for frame in frames if not frame.empty]
    if not usable:
        return pd.DataFrame()
    combined = pd.concat(usable, ignore_index=True).sort_values(["entry_time", "engine", "setup"])
    combined = combined.drop_duplicates(["entry_time", "engine", "setup", "side"]).reset_index(drop=True)
    return apply_satellite_v12_risk(apply_weak_symbol_profile(combined))


def _v12_atr(prepared: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]], row: Any) -> float:
    timeframe = "H1" if str(row.setup) == "H1_BREAKOUT_RETEST" else "H4"
    frame = prepared[str(row.symbol)][0 if timeframe == "H1" else 1]
    matches = frame[frame["end"] == pd.Timestamp(row.entry_time)]
    if matches.empty:
        raise RuntimeError(f"ATR row missing for {row.symbol}/{row.engine}/{row.setup}")
    return float(matches.iloc[-1]["atr14"])


def build_v12_live_signals(client: Any, broker_map: dict[str, str], lookback_hours: int = 8) -> list[LiveSignal]:
    prepared = {symbol: prepare_v12_frames(client, broker_map[symbol]) for symbol in SYMBOLS}
    candidates = build_v12_candidates(prepared)
    if candidates.empty:
        return []
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=lookback_hours)
    recent = candidates[pd.to_datetime(candidates["entry_time"], utc=True) >= cutoff]
    signals: list[LiveSignal] = []
    for row in recent.itertuples(index=False):
        key = (str(row.engine), str(row.setup))
        if key not in V12_EXIT_MAP:
            continue
        info = client.symbol_info(broker_map[str(row.symbol)])
        pip = pip_size(info, str(row.symbol))
        atr_value = _v12_atr(prepared, row)
        stop_atr, target_r = V12_EXIT_MAP[key]
        stop_pips = atr_value * stop_atr / pip
        signals.append(LiveSignal(
            symbol=str(row.symbol), broker_symbol=broker_map[str(row.symbol)],
            engine=str(row.engine), setup=str(row.setup), mode="V12",
            side="BUY" if int(row.side) > 0 else "SELL",
            signal_time=pd.Timestamp(row.entry_time).to_pydatetime(),
            requested_risk_percent=float(row.risk_percent),
            stop_pips=float(stop_pips), target_pips=float(stop_pips * target_r),
            metadata={"source": "closed_mt5_v12", "timeframe": "H1" if str(row.setup) == "H1_BREAKOUT_RETEST" else "H4"},
        ))
    return signals


def _profile(symbol: str):
    name = SELECTED_ICT_PROFILE[symbol]
    return next(profile for profile in PROFILES[symbol] if profile.name == name)


def build_satellite_ict_live_signals(client: Any, broker_map: dict[str, str], lookback_hours: int = 8) -> list[LiveSignal]:
    raw_candidates: list[pd.DataFrame] = []
    prepared_h1: dict[str, pd.DataFrame] = {}
    for symbol in ("EURUSD", "AUDUSD", "USDJPY"):
        broker = broker_map[symbol]
        h1 = _frame(client.copy_rates_from_pos(broker, "H1", 1, 3000))
        h4 = _frame(client.copy_rates_from_pos(broker, "H4", 1, 2500))
        d1 = _frame(client.copy_rates_from_pos(broker, "D1", 1, 800))
        if min(len(h1), len(h4), len(d1)) < 100:
            continue
        h1_prepared, _, _ = prepare_frames(h1, h4, d1)
        prepared_h1[symbol] = h1_prepared
        candidates = generate_candidates(symbol, h1_prepared, _profile(symbol))
        if not candidates.empty:
            raw_candidates.append(candidates)
    if not raw_candidates:
        return []
    raw = pd.concat(raw_candidates, ignore_index=True, sort=False)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=lookback_hours)
    raw = raw[pd.to_datetime(raw["entry_time"], utc=True) >= cutoff]
    selected = filter_satellite_ict(raw)
    signals: list[LiveSignal] = []
    for row in selected.itertuples(index=False):
        symbol = str(row.symbol)
        profile = _profile(symbol)
        frame = prepared_h1[symbol]
        match = frame[frame["end"] == pd.Timestamp(row.entry_time)]
        if match.empty:
            continue
        candle = match.iloc[-1]
        side = str(row.side).upper()
        atr_value = float(candle["atr14"])
        stop_price = (
            min(float(candle["low"]), float(row.session_low)) - profile.stop_buffer_atr * atr_value
            if side == "BUY"
            else max(float(candle["high"]), float(row.session_high)) + profile.stop_buffer_atr * atr_value
        )
        tick = client.symbol_info_tick(broker_map[symbol])
        info = client.symbol_info(broker_map[symbol])
        if tick is None or info is None:
            continue
        entry = float(tick.ask if side == "BUY" else tick.bid)
        distance = (entry - stop_price) if side == "BUY" else (stop_price - entry)
        pip = pip_size(info, symbol)
        if not np.isfinite(distance) or distance <= 0:
            continue
        stop_pips = distance / pip
        signals.append(LiveSignal(
            symbol=symbol, broker_symbol=broker_map[symbol], engine=str(row.engine),
            setup=str(row.setup), mode="ICT", side=side,
            signal_time=pd.Timestamp(row.entry_time).to_pydatetime(),
            requested_risk_percent=RISK[symbol].ict_risk_percent,
            stop_pips=float(stop_pips), target_pips=float(stop_pips * profile.target_r),
            metadata={
                "source": "closed_mt5_satellite_ict", "profile": profile.name,
                "session_high": float(row.session_high), "session_low": float(row.session_low),
                "signal_atr": float(row.signal_atr), "range_atr": float(row.range_atr),
            },
        ))
    return signals


def load_legacy_gbp_ict_signals(client: Any, broker_map: dict[str, str]) -> tuple[list[LiveSignal], str]:
    """Load exact legacy GBP ICT signals when the original local provider is present.

    The provider module must expose ``build_live_signals(client)`` and return
    dictionaries or LiveSignal instances. Missing providers fail closed rather
    than substituting an unverified GBP strategy.
    """
    module_name = os.getenv("V14_3_LEGACY_GBP_ICT_PROVIDER", "v14_3_signals").strip()
    if not module_name:
        return [], "DISABLED"
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return [], "PROVIDER_NOT_INSTALLED"
    builder = getattr(module, "build_live_signals", None)
    if not callable(builder):
        return [], "PROVIDER_INTERFACE_MISSING"
    values = builder(client) or []
    signals: list[LiveSignal] = []
    for value in values:
        if isinstance(value, LiveSignal):
            signal = value
        else:
            payload = dict(value)
            symbol = str(payload["symbol"]).upper()
            if symbol not in {"GBPUSD", "GBPJPY"}:
                continue
            engine = str(payload.get("engine") or f"ICT_V14_3_{symbol}")
            signal = LiveSignal(
                symbol=symbol, broker_symbol=broker_map[symbol], engine=engine,
                setup=str(payload["setup"]), mode="ICT", side=str(payload["side"]).upper(),
                signal_time=pd.Timestamp(payload["signal_time"]).to_pydatetime(),
                requested_risk_percent=float(payload["risk_percent"]),
                stop_pips=float(payload["stop_pips"]), target_pips=float(payload["target_pips"]),
                metadata={"source": module_name, **dict(payload.get("metadata", {}))},
            )
        signals.append(signal)
    return signals, "READY"


def resolve_all_symbols(client: Any) -> dict[str, str]:
    return {symbol: resolve_broker_symbol(client, symbol) for symbol in SYMBOLS}


def build_all_live_signals(client: Any, lookback_hours: int = 8) -> tuple[list[LiveSignal], dict[str, Any]]:
    broker_map = resolve_all_symbols(client)
    diagnostics: dict[str, Any] = {"broker_symbols": broker_map}
    signals: list[LiveSignal] = []
    try:
        v12 = build_v12_live_signals(client, broker_map, lookback_hours)
        signals.extend(v12)
        diagnostics["v12_candidates"] = len(v12)
    except Exception as exc:  # noqa: BLE001
        diagnostics["v12_error"] = f"{type(exc).__name__}: {exc}"
    try:
        ict = build_satellite_ict_live_signals(client, broker_map, lookback_hours)
        signals.extend(ict)
        diagnostics["satellite_ict_candidates"] = len(ict)
    except Exception as exc:  # noqa: BLE001
        diagnostics["satellite_ict_error"] = f"{type(exc).__name__}: {exc}"
    legacy, status = load_legacy_gbp_ict_signals(client, broker_map)
    signals.extend(legacy)
    diagnostics["legacy_gbp_ict_provider"] = status
    diagnostics["legacy_gbp_ict_candidates"] = len(legacy)
    unique = {signal.key: signal for signal in signals}
    return sorted(unique.values(), key=lambda signal: signal.signal_time), diagnostics
