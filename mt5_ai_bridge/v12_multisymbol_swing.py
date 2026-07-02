"""V12 quality-gated swing layer for GBPUSD, EURUSD and GBPJPY.

V12 retains V11's completed-candle H4/D1 breakout and retest manager, then
adds pair-specific quality gates and edge-weighted risk. The existing V10
account-level gate remains authoritative.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import v11_multisymbol_swing as v11
from .v10_multisymbol import (
    AtomicStateStore,
    V10MultiSymbolConfig,
    _canonical_from_name,
    _gbpusd_intents,
    _mode_value,
    _position_side,
    _v7_intent,
    execute_intent,
    manage_multisymbol_positions,
    position_risk_dollars,
    resolve_broker_symbol,
)

SwingProfile = v11.SwingProfile
SwingSignal = v11.SwingSignal
SwingRiskDecision = v11.SwingRiskDecision

PROFILES = {
    "GBPUSD": SwingProfile(
        "GBPUSD", 61001, 61002,
        retest_stop_atr=1.50, retest_target_r=4.0,
        retest_trail_atr=2.0, retest_max_hold_bars=36,
        retest_timeframe="H4", retest_search_bars=9,
        retest_tolerance_atr=0.30, retest_penetration_atr=0.80,
    ),
    "EURUSD": SwingProfile(
        "EURUSD", 62001, 62002,
        core_risk_percent=0.25, retest_risk_percent=0.10,
    ),
    "GBPJPY": SwingProfile(
        "GBPJPY", 63001, 63002,
        core_risk_percent=0.15, retest_risk_percent=0.20,
    ),
}


@dataclass(frozen=True)
class V12SwingConfig:
    state_path: str = "state/v12_multisymbol_swing_state.json"
    max_positions: int = 3
    max_open_risk_percent: float = 0.50
    max_symbol_risk_percent: float = 0.40
    aligned_gbp_cap_percent: float = 0.50
    mixed_gbp_cap_percent: float = 0.35

    @classmethod
    def from_env(cls) -> "V12SwingConfig":
        config = cls(
            state_path=os.getenv("V12_SWING_STATE_PATH", cls.state_path),
            max_positions=int(os.getenv("V12_SWING_MAX_POSITIONS", "3")),
            max_open_risk_percent=float(os.getenv("V12_SWING_MAX_OPEN_RISK_PERCENT", "0.50")),
            max_symbol_risk_percent=float(os.getenv("V12_SWING_MAX_SYMBOL_RISK_PERCENT", "0.40")),
            aligned_gbp_cap_percent=float(os.getenv("V12_SWING_ALIGNED_GBP_CAP_PERCENT", "0.50")),
            mixed_gbp_cap_percent=float(os.getenv("V12_SWING_MIXED_GBP_CAP_PERCENT", "0.35")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.max_positions < 1:
            raise ValueError("V12_SWING_MAX_POSITIONS must be positive")
        if not 0 < self.max_symbol_risk_percent <= self.max_open_risk_percent <= 1.0:
            raise ValueError("Invalid V12 swing risk caps")
        if self.mixed_gbp_cap_percent > self.aligned_gbp_cap_percent:
            raise ValueError("Mixed GBP cap cannot exceed aligned cap")
        if self.aligned_gbp_cap_percent > self.max_open_risk_percent:
            raise ValueError("Aligned GBP cap cannot exceed total swing cap")


def _directional(frame: pd.DataFrame, period: int = 14):
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    atr = v11._atr(frame, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx, plus_di, minus_di


def _prepare_h4(client: Any, broker_symbol: str) -> pd.DataFrame:
    h4 = v11._rates(client, broker_symbol, "H4", 420)
    daily = v11._rates(client, broker_symbol, "D1", 120)
    h4["atr14"] = v11._atr(h4)
    h4["ema20"] = v11._ema(h4["close"], 20)
    h4["ema50"] = v11._ema(h4["close"], 50)
    h4["adx14"], h4["plus_di"], h4["minus_di"] = _directional(h4)
    h4["ema_sep_atr"] = (h4["ema20"] - h4["ema50"]).abs() / h4["atr14"]
    h4["atr_pct_252"] = h4["atr14"].rolling(252, min_periods=100).rank(pct=True)
    h4["prior_high"] = h4["high"].rolling(55, min_periods=55).max().shift(1)
    h4["prior_low"] = h4["low"].rolling(55, min_periods=55).min().shift(1)

    daily["daily_ema20"] = v11._ema(daily["close"], 20)
    daily["daily_ema50"] = v11._ema(daily["close"], 50)
    daily["daily_atr14"] = v11._atr(daily)
    daily["daily_ema20_slope"] = daily["daily_ema20"].diff(5) / 5
    daily = daily[[
        "time", "close", "daily_ema20", "daily_ema50",
        "daily_atr14", "daily_ema20_slope",
    ]].rename(columns={"close": "daily_close"})
    daily["available_time"] = daily["time"] + pd.Timedelta(days=1)
    h4 = pd.merge_asof(
        h4.sort_values("time"),
        daily[[
            "available_time", "daily_close", "daily_ema20", "daily_ema50",
            "daily_atr14", "daily_ema20_slope",
        ]].sort_values("available_time"),
        left_on="time", right_on="available_time", direction="backward",
    )
    long = (
        (h4["daily_close"] > h4["daily_ema20"])
        & (h4["daily_ema20"] > h4["daily_ema50"])
        & (h4["close"] > h4["ema20"])
        & (h4["adx14"] >= 20)
        & (h4["close"] > h4["prior_high"])
    )
    short = (
        (h4["daily_close"] < h4["daily_ema20"])
        & (h4["daily_ema20"] < h4["daily_ema50"])
        & (h4["close"] < h4["ema20"])
        & (h4["adx14"] >= 20)
        & (h4["close"] < h4["prior_low"])
    )
    h4["breakout_side"] = np.where(long, 1, np.where(short, -1, 0))
    h4["breakout_level"] = np.where(
        h4["breakout_side"] > 0, h4["prior_high"],
        np.where(h4["breakout_side"] < 0, h4["prior_low"], np.nan),
    )
    h4["directional_di_gap"] = np.where(
        h4["breakout_side"] > 0, h4["plus_di"] - h4["minus_di"],
        h4["minus_di"] - h4["plus_di"],
    )
    h4["daily_slope_dir"] = np.where(
        h4["breakout_side"] > 0,
        h4["daily_ema20_slope"] / h4["daily_atr14"],
        -h4["daily_ema20_slope"] / h4["daily_atr14"],
    )
    h4["end"] = h4["time"] + pd.Timedelta(hours=4)
    return h4


def _latest_core_signal(canonical: str, broker_symbol: str, h4: pd.DataFrame) -> Optional[SwingSignal]:
    row = h4.iloc[-1]
    side = int(row["breakout_side"])
    if side == 0 or pd.isna(row["atr14"]):
        return None
    if canonical == "EURUSD":
        if not (float(row["ema_sep_atr"]) <= 1.30 and float(row["directional_di_gap"]) >= 17.0):
            return None
    elif canonical == "GBPJPY":
        slope, gap = float(row["daily_slope_dir"]), float(row["directional_di_gap"])
        if not (slope <= 0.02 or (slope > 0.13 and gap <= 26.0)):
            return None
    profile = PROFILES[canonical]
    return SwingSignal(
        canonical, broker_symbol, side, f"{canonical}_H4_DONCHIAN_BREAKOUT",
        pd.Timestamp(row["end"]).to_pydatetime(), profile.core_risk_percent,
        profile.core_stop_atr, profile.core_target_r, float(row["atr14"]),
        profile.core_magic, "H4", profile.core_trail_atr,
        profile.core_trail_start_r, profile.core_max_hold_bars,
        "H4 breakout passed D1 trend, ADX and V12 pair-quality filters.",
    )


def _latest_retest_signal(client: Any, canonical: str, broker_symbol: str,
                          h4: pd.DataFrame) -> Optional[SwingSignal]:
    profile = PROFILES[canonical]
    previous = h4.iloc[:-1]
    previous = previous[previous["breakout_side"] != 0]
    if previous.empty:
        return None
    breakout = previous.iloc[-1]
    side, level = int(breakout["breakout_side"]), float(breakout["breakout_level"])
    if canonical in {"EURUSD", "GBPJPY"}:
        atr_pct = float(breakout["atr_pct_252"])
        if not np.isfinite(atr_pct) or atr_pct > 0.80:
            return None

    if profile.retest_timeframe == "H4":
        current = h4.iloc[-1]
        bars_since = len(h4) - 1 - int(breakout.name)
        signal_end = pd.Timestamp(current["end"])
    else:
        frame = v11._rates(client, broker_symbol, "H1", 260)
        frame["atr14"] = v11._atr(frame)
        frame["ema20"] = v11._ema(frame["close"], 20)
        frame["ema50"] = v11._ema(frame["close"], 50)
        frame["ema_sep_atr"] = (frame["ema20"] - frame["ema50"]).abs() / frame["atr14"]
        frame["hour"] = frame["time"].dt.hour
        frame["end"] = frame["time"] + pd.Timedelta(hours=1)
        eligible = frame[frame["time"] >= pd.Timestamp(breakout["end"])]
        if eligible.empty:
            return None
        bars_since, current = len(eligible), frame.iloc[-1]
        signal_end = pd.Timestamp(current["end"])

    if not 1 <= bars_since <= profile.retest_search_bars:
        return None
    atr_price = float(current["atr14"])
    if not np.isfinite(atr_price) or atr_price <= 0:
        return None
    body_atr = abs(float(current["close"] - current["open"])) / atr_price
    if side > 0:
        valid = (
            current["low"] <= level + profile.retest_tolerance_atr * atr_price
            and current["low"] >= level - profile.retest_penetration_atr * atr_price
            and current["close"] > level and current["close"] > current["open"]
            and current["ema20"] > current["ema50"]
            and body_atr >= profile.retest_body_atr
        )
    else:
        valid = (
            current["high"] >= level - profile.retest_tolerance_atr * atr_price
            and current["high"] <= level + profile.retest_penetration_atr * atr_price
            and current["close"] < level and current["close"] < current["open"]
            and current["ema20"] < current["ema50"]
            and body_atr >= profile.retest_body_atr
        )
    if valid and canonical == "EURUSD":
        valid = float(current["ema_sep_atr"]) >= 1.80 and int(current["hour"]) <= 12
    elif valid and canonical == "GBPJPY":
        valid = int(current["hour"]) <= 12
    if not valid:
        return None
    return SwingSignal(
        canonical, broker_symbol, side,
        f"{canonical}_{profile.retest_timeframe}_BREAKOUT_RETEST",
        signal_end.to_pydatetime(), profile.retest_risk_percent,
        profile.retest_stop_atr, profile.retest_target_r, atr_price,
        profile.retest_magic, profile.retest_timeframe, profile.retest_trail_atr,
        profile.retest_trail_start_r, profile.retest_max_hold_bars,
        "Breakout retest passed V12 volatility, trend-separation and hour filters.",
    )


def evaluate_swing_signals(client: Any, canonical: str,
                           broker_symbol: str) -> list[SwingSignal]:
    canonical = canonical.upper()
    h4 = _prepare_h4(client, broker_symbol)
    return [signal for signal in (
        _latest_core_signal(canonical, broker_symbol, h4),
        _latest_retest_signal(client, canonical, broker_symbol, h4),
    ) if signal is not None]


def swing_risk_gate(client: Any, account: Any, positions: list[Any],
                    signal: SwingSignal,
                    config: V12SwingConfig) -> SwingRiskDecision:
    magics = v11._swing_magics()
    swing_positions = [
        p for p in positions if int(getattr(p, "magic", 0) or 0) in magics
    ]
    balance = float(getattr(account, "balance", 0.0) or 0.0)
    if balance <= 0:
        return SwingRiskDecision(False, "invalid_account_balance", 0.0)
    new_risk = balance * signal.risk_percent / 100.0
    open_risk = sum(position_risk_dollars(client, p) for p in swing_positions)
    if len(swing_positions) >= config.max_positions:
        return SwingRiskDecision(False, "v12_swing_max_positions", open_risk)
    if open_risk + new_risk > balance * config.max_open_risk_percent / 100.0 + 1e-9:
        return SwingRiskDecision(False, "v12_swing_open_risk", open_risk)
    same_symbol = [
        p for p in swing_positions
        if _canonical_from_name(str(p.symbol)) == signal.canonical_symbol
    ]
    symbol_risk = sum(position_risk_dollars(client, p) for p in same_symbol)
    if symbol_risk + new_risk > balance * config.max_symbol_risk_percent / 100.0 + 1e-9:
        return SwingRiskDecision(False, "v12_swing_symbol_risk", open_risk)
    if signal.canonical_symbol.startswith("GBP"):
        gbp = [
            p for p in swing_positions
            if (_canonical_from_name(str(p.symbol)) or "").startswith("GBP")
        ]
        gbp_risk = sum(position_risk_dollars(client, p) for p in gbp)
        sides = {_position_side(client, p) for p in gbp}
        sides.add(signal.side)
        cap = config.mixed_gbp_cap_percent if len(sides) > 1 else config.aligned_gbp_cap_percent
        if gbp_risk + new_risk > balance * cap / 100.0 + 1e-9:
            return SwingRiskDecision(False, "v12_swing_gbp_cap", open_risk)
    return SwingRiskDecision(True, "allowed", open_risk)


def run_v12_multisymbol_cycle(
    client: Any, journal: Any, settings: Any, account: Any,
    risk_ok: bool, active: bool,
    base_config: Optional[V10MultiSymbolConfig] = None,
    swing_config: Optional[V12SwingConfig] = None,
) -> dict:
    base_config = base_config or V10MultiSymbolConfig.from_env()
    swing_config = swing_config or V12SwingConfig.from_env()
    store = AtomicStateStore(Path(swing_config.state_path))
    state = store.load()
    state.setdefault("swing_positions", {})
    management = manage_multisymbol_positions(client, base_config, state)
    swing_management = v11.manage_swing_positions(
        client, state, base_config.max_slippage_points
    )
    positions = list(client.positions_get() or [])
    outcomes, symbol_views = [], []

    for canonical in base_config.symbols:
        spec = base_config.spec(canonical)
        try:
            broker = state["symbol_map"].get(canonical)
            if not broker or client.symbol_info(broker) is None:
                broker = resolve_broker_symbol(client, canonical)
                state["symbol_map"][canonical] = broker
            if canonical == "GBPUSD":
                satellites = [
                    intent for intent in _gbpusd_intents(client, broker, base_config)
                    if int(intent.magic) == int(os.getenv("GBPUSD_SATELLITE_MAGIC", "51002"))
                ]
            else:
                satellite = _v7_intent(client, canonical, broker, spec)
                satellites = [satellite] if satellite is not None else []
            swings = evaluate_swing_signals(client, canonical, broker)
            candidates = [(intent, None) for intent in satellites]
            candidates += [(signal.to_intent(), signal) for signal in swings]
            symbol_views.append({
                "symbol": canonical, "broker_symbol": broker,
                "signals": len(candidates),
                "setups": [intent.setup for intent, _ in candidates],
            })

            for intent, signal in candidates:
                marker = f"{canonical}:{intent.setup}:{intent.signal_time.isoformat()}"
                if state["signals"].get(marker):
                    outcomes.append({"status": "SKIPPED", "reason": "duplicate_signal"})
                    continue
                state["signals"][marker] = datetime.now(timezone.utc).isoformat()
                if not risk_ok or not active:
                    outcomes.append({"status": "REJECTED", "reason": "global_risk_or_pause"})
                    continue
                if signal is not None:
                    decision = swing_risk_gate(
                        client, account, positions, signal, swing_config
                    )
                    if not decision.allowed:
                        outcomes.append({
                            "status": "REJECTED", "reason": decision.reason,
                            "setup": intent.setup,
                        })
                        continue
                result = execute_intent(
                    client=client, journal=journal, settings=settings,
                    account=account, positions=positions,
                    intent=intent, config=base_config,
                )
                outcomes.append({"symbol": canonical, "setup": intent.setup, **result})
                if result.get("status") == "FILLED":
                    if signal is not None:
                        v11._save_filled_swing_state(state, result, signal)
                    positions = list(client.positions_get() or [])
        except Exception as exc:
            symbol_views.append({"symbol": canonical, "error": str(exc), "signals": 0})

    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=45)
    state["signals"] = {
        key: value for key, value in state["signals"].items()
        if pd.Timestamp(value) >= cutoff
    }
    store.save(state)
    return {
        "strategy_version": "V12_SWING_QUALITY",
        "mode": _mode_value(settings),
        "symbols": symbol_views,
        "outcomes": outcomes,
        "management": management,
        "swing_management": swing_management,
        "open_positions": len(positions),
        "note": "V12 adds pair-specific quality gates and edge-weighted swing risk.",
    }
