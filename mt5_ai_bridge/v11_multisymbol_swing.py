"""V11 multi-symbol swing layer for GBPUSD, EURUSD and GBPJPY.

The layer replaces the legacy GBPUSD swing entry with a completed-candle H4/D1
Donchian engine and adds a retest engine to all three pairs. EURUSD and GBPJPY
use H1 timing for retests; GBPUSD uses H4 timing until GBPUSD H1 history is
supplied and validated.

The strategy is intentionally conservative in live/demo mode:
- 0.20% risk for core breakouts;
- 0.15% risk for retests;
- 0.50% maximum open swing risk;
- 0.35% maximum swing risk per symbol;
- 0.50% aligned / 0.35% mixed GBP swing exposure.

It coordinates with the existing V10 account-level gate, so satellite and swing
positions still share the global portfolio limits.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .v10_multisymbol import (
    AtomicStateStore,
    TradeIntent,
    V10MultiSymbolConfig,
    _canonical_from_name,
    _check_retcode_ok,
    _gbpusd_intents,
    _mode_value,
    _position_side,
    _v7_intent,
    execute_intent,
    manage_multisymbol_positions,
    position_risk_dollars,
    resolve_broker_symbol,
)


@dataclass(frozen=True)
class SwingProfile:
    canonical: str
    core_magic: int
    retest_magic: int
    core_risk_percent: float = 0.20
    retest_risk_percent: float = 0.15
    core_stop_atr: float = 1.25
    core_target_r: float = 3.0
    core_trail_atr: float = 2.5
    core_trail_start_r: float = 1.0
    core_max_hold_bars: int = 24
    retest_stop_atr: float = 1.25
    retest_target_r: float = 3.0
    retest_trail_atr: float = 1.5
    retest_trail_start_r: float = 1.0
    retest_max_hold_bars: int = 96
    retest_timeframe: str = "H1"
    retest_search_bars: int = 48
    retest_tolerance_atr: float = 0.20
    retest_penetration_atr: float = 0.50
    retest_body_atr: float = 0.20


PROFILES = {
    "GBPUSD": SwingProfile(
        "GBPUSD", 61001, 61002,
        retest_stop_atr=1.50,
        retest_target_r=4.0,
        retest_trail_atr=2.0,
        retest_max_hold_bars=36,
        retest_timeframe="H4",
        retest_search_bars=9,
        retest_tolerance_atr=0.30,
        retest_penetration_atr=0.80,
    ),
    "EURUSD": SwingProfile("EURUSD", 62001, 62002),
    "GBPJPY": SwingProfile("GBPJPY", 63001, 63002),
}


@dataclass(frozen=True)
class SwingSignal:
    canonical_symbol: str
    broker_symbol: str
    side: int
    setup: str
    signal_time: datetime
    risk_percent: float
    stop_atr: float
    target_r: float
    atr_price: float
    magic: int
    management_timeframe: str
    trail_atr: float
    trail_start_r: float
    max_hold_bars: int
    reason: str

    def to_intent(self) -> TradeIntent:
        factor = 16 if self.management_timeframe == "H4" else 4
        return TradeIntent(
            canonical_symbol=self.canonical_symbol,
            broker_symbol=self.broker_symbol,
            side=self.side,
            setup=self.setup,
            signal_time=self.signal_time,
            risk_percent=self.risk_percent,
            stop_atr=self.stop_atr,
            target_r=self.target_r,
            break_even_r=self.trail_start_r,
            max_hold_m15_bars=self.max_hold_bars * factor,
            atr_price=self.atr_price,
            reason=self.reason,
            magic=self.magic,
            force_flat_hour_utc=None,
        )


@dataclass(frozen=True)
class SwingRiskDecision:
    allowed: bool
    reason: str
    open_risk_dollars: float


@dataclass(frozen=True)
class V11SwingConfig:
    state_path: str = "state/v11_multisymbol_swing_state.json"
    max_positions: int = 3
    max_open_risk_percent: float = 0.50
    max_symbol_risk_percent: float = 0.35
    aligned_gbp_cap_percent: float = 0.50
    mixed_gbp_cap_percent: float = 0.35

    @classmethod
    def from_env(cls) -> "V11SwingConfig":
        config = cls(
            state_path=os.getenv("V11_SWING_STATE_PATH", cls.state_path),
            max_positions=int(os.getenv("V11_SWING_MAX_POSITIONS", "3")),
            max_open_risk_percent=float(os.getenv("V11_SWING_MAX_OPEN_RISK_PERCENT", "0.50")),
            max_symbol_risk_percent=float(os.getenv("V11_SWING_MAX_SYMBOL_RISK_PERCENT", "0.35")),
            aligned_gbp_cap_percent=float(os.getenv("V11_SWING_ALIGNED_GBP_CAP_PERCENT", "0.50")),
            mixed_gbp_cap_percent=float(os.getenv("V11_SWING_MIXED_GBP_CAP_PERCENT", "0.35")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.max_positions < 1:
            raise ValueError("V11_SWING_MAX_POSITIONS must be positive")
        if not 0 < self.max_symbol_risk_percent <= self.max_open_risk_percent <= 1.0:
            raise ValueError("Invalid V11 swing risk caps")
        if self.mixed_gbp_cap_percent > self.aligned_gbp_cap_percent:
            raise ValueError("Mixed GBP swing cap cannot exceed aligned cap")
        if self.aligned_gbp_cap_percent > self.max_open_risk_percent:
            raise ValueError("Aligned GBP swing cap cannot exceed total swing cap")


def _rates(client: Any, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    raw = client.copy_rates_from_pos(symbol, timeframe, 1, count)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"No completed {timeframe} rates for {symbol}")
    frame = pd.DataFrame(raw)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").reset_index(drop=True)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=frame.index)
    atr = _atr(frame, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _prepare_h4(client: Any, broker_symbol: str) -> pd.DataFrame:
    h4 = _rates(client, broker_symbol, "H4", 420)
    daily = _rates(client, broker_symbol, "D1", 120)
    h4["atr14"] = _atr(h4)
    h4["ema20"] = _ema(h4["close"], 20)
    h4["ema50"] = _ema(h4["close"], 50)
    h4["adx14"] = _adx(h4)
    h4["prior_high"] = h4["high"].rolling(55, min_periods=55).max().shift(1)
    h4["prior_low"] = h4["low"].rolling(55, min_periods=55).min().shift(1)

    daily["daily_ema20"] = _ema(daily["close"], 20)
    daily["daily_ema50"] = _ema(daily["close"], 50)
    daily = daily[["time", "close", "daily_ema20", "daily_ema50"]].rename(columns={"close": "daily_close"})
    daily["available_time"] = daily["time"] + pd.Timedelta(days=1)
    h4 = pd.merge_asof(
        h4.sort_values("time"),
        daily[["available_time", "daily_close", "daily_ema20", "daily_ema50"]].sort_values("available_time"),
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
    h4["breakout_level"] = np.where(h4["breakout_side"] > 0, h4["prior_high"], np.where(h4["breakout_side"] < 0, h4["prior_low"], np.nan))
    h4["end"] = h4["time"] + pd.Timedelta(hours=4)
    return h4


def _latest_core_signal(canonical: str, broker_symbol: str, h4: pd.DataFrame) -> Optional[SwingSignal]:
    row = h4.iloc[-1]
    side = int(row["breakout_side"])
    if side == 0 or pd.isna(row["atr14"]):
        return None
    profile = PROFILES[canonical]
    return SwingSignal(
        canonical, broker_symbol, side, f"{canonical}_H4_DONCHIAN_BREAKOUT",
        pd.Timestamp(row["end"]).to_pydatetime(), profile.core_risk_percent,
        profile.core_stop_atr, profile.core_target_r, float(row["atr14"]),
        profile.core_magic, "H4", profile.core_trail_atr,
        profile.core_trail_start_r, profile.core_max_hold_bars,
        "Completed H4 close broke the prior 55-bar range with completed D1 trend and ADX confirmation.",
    )


def _latest_retest_signal(client: Any, canonical: str, broker_symbol: str, h4: pd.DataFrame) -> Optional[SwingSignal]:
    profile = PROFILES[canonical]
    current_h4 = h4.iloc[-1]
    previous_breakouts = h4.iloc[:-1]
    previous_breakouts = previous_breakouts[previous_breakouts["breakout_side"] != 0]
    if previous_breakouts.empty:
        return None
    breakout = previous_breakouts.iloc[-1]
    side = int(breakout["breakout_side"])
    level = float(breakout["breakout_level"])

    if profile.retest_timeframe == "H4":
        current = current_h4
        bars_since = len(h4) - 1 - int(breakout.name)
        signal_end = pd.Timestamp(current["end"])
    else:
        frame = _rates(client, broker_symbol, "H1", 260)
        frame["atr14"] = _atr(frame)
        frame["ema20"] = _ema(frame["close"], 20)
        frame["ema50"] = _ema(frame["close"], 50)
        frame["end"] = frame["time"] + pd.Timedelta(hours=1)
        eligible = frame[frame["time"] >= pd.Timestamp(breakout["end"])]
        if eligible.empty:
            return None
        bars_since = len(eligible)
        current = frame.iloc[-1]
        signal_end = pd.Timestamp(current["end"])

    if bars_since < 1 or bars_since > profile.retest_search_bars:
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
            and current["ema20"] > current["ema50"] and body_atr >= profile.retest_body_atr
        )
    else:
        valid = (
            current["high"] >= level - profile.retest_tolerance_atr * atr_price
            and current["high"] <= level + profile.retest_penetration_atr * atr_price
            and current["close"] < level and current["close"] < current["open"]
            and current["ema20"] < current["ema50"] and body_atr >= profile.retest_body_atr
        )
    if not valid:
        return None
    return SwingSignal(
        canonical, broker_symbol, side, f"{canonical}_{profile.retest_timeframe}_BREAKOUT_RETEST",
        signal_end.to_pydatetime(), profile.retest_risk_percent,
        profile.retest_stop_atr, profile.retest_target_r, atr_price,
        profile.retest_magic, profile.retest_timeframe, profile.retest_trail_atr,
        profile.retest_trail_start_r, profile.retest_max_hold_bars,
        f"Completed {profile.retest_timeframe} candle retested the latest H4 breakout and closed back with the trend.",
    )


def evaluate_swing_signals(client: Any, canonical: str, broker_symbol: str) -> list[SwingSignal]:
    canonical = canonical.upper()
    h4 = _prepare_h4(client, broker_symbol)
    signals = []
    core = _latest_core_signal(canonical, broker_symbol, h4)
    if core is not None:
        signals.append(core)
    retest = _latest_retest_signal(client, canonical, broker_symbol, h4)
    if retest is not None:
        signals.append(retest)
    return signals


def _swing_magics() -> set[int]:
    return {value for profile in PROFILES.values() for value in (profile.core_magic, profile.retest_magic)}


def _profile_for_magic(magic: int) -> tuple[SwingProfile, str]:
    for profile in PROFILES.values():
        if magic == profile.core_magic:
            return profile, "core"
        if magic == profile.retest_magic:
            return profile, "retest"
    raise KeyError(magic)


def swing_risk_gate(client: Any, account: Any, positions: list[Any], signal: SwingSignal, config: V11SwingConfig) -> SwingRiskDecision:
    magics = _swing_magics()
    swing_positions = [p for p in positions if int(getattr(p, "magic", 0) or 0) in magics]
    balance = float(getattr(account, "balance", 0.0) or 0.0)
    if balance <= 0:
        return SwingRiskDecision(False, "invalid_account_balance", 0.0)
    new_risk = balance * signal.risk_percent / 100.0
    open_risk = sum(position_risk_dollars(client, p) for p in swing_positions)
    if len(swing_positions) >= config.max_positions:
        return SwingRiskDecision(False, "v11_swing_max_positions", open_risk)
    if open_risk + new_risk > balance * config.max_open_risk_percent / 100.0 + 1e-9:
        return SwingRiskDecision(False, "v11_swing_open_risk", open_risk)
    same_symbol = [p for p in swing_positions if _canonical_from_name(str(p.symbol)) == signal.canonical_symbol]
    symbol_risk = sum(position_risk_dollars(client, p) for p in same_symbol)
    if symbol_risk + new_risk > balance * config.max_symbol_risk_percent / 100.0 + 1e-9:
        return SwingRiskDecision(False, "v11_swing_symbol_risk", open_risk)
    if signal.canonical_symbol.startswith("GBP"):
        gbp = [p for p in swing_positions if (_canonical_from_name(str(p.symbol)) or "").startswith("GBP")]
        gbp_risk = sum(position_risk_dollars(client, p) for p in gbp)
        sides = {_position_side(client, p) for p in gbp}
        sides.add(signal.side)
        cap = config.mixed_gbp_cap_percent if len(sides) > 1 else config.aligned_gbp_cap_percent
        if gbp_risk + new_risk > balance * cap / 100.0 + 1e-9:
            return SwingRiskDecision(False, "v11_swing_gbp_cap", open_risk)
    return SwingRiskDecision(True, "allowed", open_risk)


def _close_position(client: Any, position: Any, deviation: int) -> tuple[bool, str]:
    tick = client.symbol_info_tick(position.symbol)
    if tick is None:
        return False, "missing_tick"
    buy = _position_side(client, position) > 0
    request = {
        "action": getattr(client, "TRADE_ACTION_DEAL"),
        "position": int(position.ticket),
        "symbol": position.symbol,
        "volume": float(position.volume),
        "type": getattr(client, "ORDER_TYPE_SELL") if buy else getattr(client, "ORDER_TYPE_BUY"),
        "price": float(tick.bid if buy else tick.ask),
        "deviation": deviation,
        "magic": int(getattr(position, "magic", 0) or 0),
        "comment": "V11 swing time exit",
        "type_time": getattr(client, "ORDER_TIME_GTC"),
    }
    result = client.order_send(request)
    return _check_retcode_ok(client, result), str(getattr(result, "comment", ""))


def _modify_stop(client: Any, position: Any, stop: float) -> bool:
    info = client.symbol_info(position.symbol)
    digits = int(getattr(info, "digits", 5) or 5) if info is not None else 5
    request = {
        "action": getattr(client, "TRADE_ACTION_SLTP"),
        "position": int(position.ticket),
        "symbol": position.symbol,
        "sl": round(float(stop), digits),
        "tp": float(getattr(position, "tp", 0.0) or 0.0),
    }
    return _check_retcode_ok(client, client.order_send(request))


def manage_swing_positions(client: Any, state: dict, max_slippage_points: int) -> list[dict]:
    events = []
    positions = [p for p in (client.positions_get() or []) if int(getattr(p, "magic", 0) or 0) in _swing_magics()]
    active = {str(p.ticket) for p in positions}
    state.setdefault("swing_positions", {})
    state["swing_positions"] = {key: value for key, value in state["swing_positions"].items() if key in active}
    now = datetime.now(timezone.utc)

    for position in positions:
        magic = int(getattr(position, "magic", 0) or 0)
        profile, kind = _profile_for_magic(magic)
        timeframe = "H4" if kind == "core" else profile.retest_timeframe
        max_hold = profile.core_max_hold_bars if kind == "core" else profile.retest_max_hold_bars
        trail_atr = profile.core_trail_atr if kind == "core" else profile.retest_trail_atr
        trail_start = profile.core_trail_start_r if kind == "core" else profile.retest_trail_start_r
        key = str(position.ticket)
        record = state["swing_positions"].setdefault(key, {
            "opened": datetime.fromtimestamp(int(position.time), tz=timezone.utc).isoformat(),
            "initial_risk_price": abs(float(position.price_open) - float(position.sl)),
            "timeframe": timeframe,
            "max_hold_bars": max_hold,
            "trail_atr": trail_atr,
            "trail_start_r": trail_start,
        })
        opened = datetime.fromisoformat(record["opened"])
        bar_hours = 4 if record["timeframe"] == "H4" else 1
        if (now - opened).total_seconds() >= int(record["max_hold_bars"]) * bar_hours * 3600:
            ok, message = _close_position(client, position, max_slippage_points)
            events.append({"ticket": int(position.ticket), "action": "time_exit", "ok": ok, "message": message})
            continue
        risk_price = float(record.get("initial_risk_price", 0.0) or 0.0)
        if risk_price <= 0:
            continue
        favorable = float(position.price_current) - float(position.price_open) if _position_side(client, position) > 0 else float(position.price_open) - float(position.price_current)
        if favorable < float(record["trail_start_r"]) * risk_price:
            continue
        rates = _rates(client, position.symbol, str(record["timeframe"]), 80)
        rates["atr14"] = _atr(rates)
        latest = rates.iloc[-1]
        if pd.isna(latest["atr14"]):
            continue
        buy = _position_side(client, position) > 0
        candidate = float(latest["close"] - record["trail_atr"] * latest["atr14"]) if buy else float(latest["close"] + record["trail_atr"] * latest["atr14"])
        candidate = max(candidate, float(position.price_open)) if buy else min(candidate, float(position.price_open))
        current = float(getattr(position, "sl", 0.0) or 0.0)
        improves = candidate > current if buy else (current == 0.0 or candidate < current)
        if improves:
            ok = _modify_stop(client, position, candidate)
            events.append({"ticket": int(position.ticket), "action": "trail", "ok": ok, "new_sl": candidate})
    return events


def _save_filled_swing_state(state: dict, result: dict, signal: SwingSignal) -> None:
    ticket = str(result.get("ticket", ""))
    request = result.get("request") or {}
    if not ticket or ticket == "0" or not request:
        return
    state.setdefault("swing_positions", {})[ticket] = {
        "opened": datetime.now(timezone.utc).isoformat(),
        "initial_risk_price": abs(float(request["price"]) - float(request["sl"])),
        "timeframe": signal.management_timeframe,
        "max_hold_bars": signal.max_hold_bars,
        "trail_atr": signal.trail_atr,
        "trail_start_r": signal.trail_start_r,
        "setup": signal.setup,
        "canonical_symbol": signal.canonical_symbol,
    }


def run_v11_multisymbol_cycle(
    client: Any,
    journal: Any,
    settings: Any,
    account: Any,
    risk_ok: bool,
    active: bool,
    base_config: Optional[V10MultiSymbolConfig] = None,
    swing_config: Optional[V11SwingConfig] = None,
) -> dict:
    """Run satellites and the V11 swing layer under shared risk controls."""
    base_config = base_config or V10MultiSymbolConfig.from_env()
    swing_config = swing_config or V11SwingConfig.from_env()
    store = AtomicStateStore(Path(swing_config.state_path))
    state = store.load()
    state.setdefault("swing_positions", {})

    management = manage_multisymbol_positions(client, base_config, state)
    swing_management = manage_swing_positions(client, state, base_config.max_slippage_points)
    positions = list(client.positions_get() or [])
    outcomes = []
    symbol_views = []

    for canonical in base_config.symbols:
        spec = base_config.spec(canonical)
        try:
            broker_symbol = state["symbol_map"].get(canonical)
            if not broker_symbol or client.symbol_info(broker_symbol) is None:
                broker_symbol = resolve_broker_symbol(client, canonical)
                state["symbol_map"][canonical] = broker_symbol

            satellite_intents = []
            if canonical == "GBPUSD":
                satellite_intents = [
                    intent for intent in _gbpusd_intents(client, broker_symbol, base_config)
                    if int(intent.magic) == int(os.getenv("GBPUSD_SATELLITE_MAGIC", "51002"))
                ]
            else:
                satellite = _v7_intent(client, canonical, broker_symbol, spec)
                satellite_intents = [satellite] if satellite is not None else []

            swing_signals = evaluate_swing_signals(client, canonical, broker_symbol)
            intents_with_signal = [(intent, None) for intent in satellite_intents] + [(signal.to_intent(), signal) for signal in swing_signals]
            symbol_views.append({
                "symbol": canonical,
                "broker_symbol": broker_symbol,
                "signals": len(intents_with_signal),
                "setups": [intent.setup for intent, _ in intents_with_signal],
            })

            for intent, swing_signal in intents_with_signal:
                marker = f"{intent.canonical_symbol}:{intent.setup}:{intent.signal_time.isoformat()}"
                if state["signals"].get(marker):
                    outcomes.append({"status": "SKIPPED", "reason": "duplicate_signal", "marker": marker})
                    continue
                state["signals"][marker] = datetime.now(timezone.utc).isoformat()
                if not risk_ok or not active:
                    outcomes.append({"status": "REJECTED", "reason": "global_risk_or_pause", "setup": intent.setup})
                    continue
                if swing_signal is not None:
                    swing_decision = swing_risk_gate(client, account, positions, swing_signal, swing_config)
                    if not swing_decision.allowed:
                        outcomes.append({"status": "REJECTED", "reason": swing_decision.reason, "setup": intent.setup})
                        continue
                result = execute_intent(
                    client=client, journal=journal, settings=settings, account=account,
                    positions=positions, intent=intent, config=base_config,
                )
                outcomes.append({"symbol": canonical, "setup": intent.setup, **result})
                if result.get("status") == "FILLED":
                    if swing_signal is not None:
                        _save_filled_swing_state(state, result, swing_signal)
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
        "strategy_version": "V11_MULTISYMBOL_SWING",
        "mode": _mode_value(settings),
        "symbols": symbol_views,
        "outcomes": outcomes,
        "management": management,
        "swing_management": swing_management,
        "open_positions": len(positions),
        "note": "V11 replaces the legacy GBPUSD swing entry and adds H4/H1 retest swing engines to all configured symbols.",
    }
