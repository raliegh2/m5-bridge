"""V10 multi-symbol live/demo controller.

The controller coordinates GBPUSD, EURUSD and GBPJPY under one account-level
risk budget. It intentionally defaults to READ_ONLY and uses completed candles
only. EURUSD/GBPJPY reuse the frozen Strategy Engine V7 rules; GBPUSD reuses the
V10 precision evaluators.

This module is broker-adapter code, not a guarantee of profitability. AUTO mode
must only be enabled after shadow and demo reconciliation.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SymbolSpec:
    canonical: str
    magic: int
    risk_percent: float
    max_spread_pips: float
    force_flat_hour_utc: int
    pip_size_fallback: float


@dataclass(frozen=True)
class V10MultiSymbolConfig:
    symbols: tuple[str, ...] = ("GBPUSD", "EURUSD", "GBPJPY")
    max_positions: int = 3
    max_open_risk_percent: float = 0.75
    aligned_gbp_cap_percent: float = 0.75
    mixed_gbp_cap_percent: float = 0.50
    max_slippage_points: int = 20
    state_path: str = "state/v10_multisymbol_state.json"
    specs: tuple[SymbolSpec, ...] = (
        SymbolSpec("GBPUSD", 51001, 0.30, 2.0, 20, 0.0001),
        SymbolSpec("EURUSD", 52001, 0.35, 1.5, 20, 0.0001),
        SymbolSpec("GBPJPY", 53001, 0.35, 3.5, 20, 0.01),
    )

    @classmethod
    def from_env(cls) -> "V10MultiSymbolConfig":
        symbols = tuple(
            item.strip().upper()
            for item in os.getenv("SYMBOLS", "GBPUSD,EURUSD,GBPJPY").split(",")
            if item.strip()
        )
        base = cls()
        risk = {
            "GBPUSD": float(os.getenv("GBPUSD_SATELLITE_RISK", "0.30")),
            "EURUSD": float(os.getenv("EURUSD_RISK", "0.35")),
            "GBPJPY": float(os.getenv("GBPJPY_RISK", "0.35")),
        }
        spread = {
            "GBPUSD": float(os.getenv("MAX_SPREAD_GBPUSD", "2.0")),
            "EURUSD": float(os.getenv("MAX_SPREAD_EURUSD", "1.5")),
            "GBPJPY": float(os.getenv("MAX_SPREAD_GBPJPY", "3.5")),
        }
        magic = {
            "GBPUSD": int(os.getenv("GBPUSD_MULTISYMBOL_MAGIC", "51001")),
            "EURUSD": int(os.getenv("EURUSD_MAGIC", "52001")),
            "GBPJPY": int(os.getenv("GBPJPY_MAGIC", "53001")),
        }
        fallbacks = {"GBPUSD": 0.0001, "EURUSD": 0.0001, "GBPJPY": 0.01}
        specs = tuple(
            SymbolSpec(
                symbol,
                magic[symbol],
                risk[symbol],
                spread[symbol],
                int(os.getenv(f"{symbol}_FORCE_FLAT_HOUR_UTC", "20")),
                fallbacks[symbol],
            )
            for symbol in symbols
        )
        config = cls(
            symbols=symbols,
            max_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
            max_open_risk_percent=float(os.getenv("MAX_OPEN_RISK_PERCENT", "0.75")),
            aligned_gbp_cap_percent=float(
                os.getenv("ALIGNED_GBP_RISK_CAP_PERCENT", "0.75")
            ),
            mixed_gbp_cap_percent=float(
                os.getenv("MIXED_GBP_RISK_CAP_PERCENT", "0.50")
            ),
            max_slippage_points=int(os.getenv("MAX_SLIPPAGE_POINTS", "20")),
            state_path=os.getenv(
                "V10_MULTISYMBOL_STATE_PATH", base.state_path
            ),
            specs=specs,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("At least one symbol is required")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("SYMBOLS must not contain duplicates")
        supported = {"GBPUSD", "EURUSD", "GBPJPY"}
        unknown = set(self.symbols) - supported
        if unknown:
            raise ValueError(f"Unsupported V10 symbols: {sorted(unknown)}")
        if not 0 < self.max_open_risk_percent <= 1.0:
            raise ValueError("MAX_OPEN_RISK_PERCENT must be within (0, 1]")
        if self.mixed_gbp_cap_percent > self.aligned_gbp_cap_percent:
            raise ValueError("Mixed GBP cap cannot exceed aligned GBP cap")
        if self.aligned_gbp_cap_percent > self.max_open_risk_percent:
            raise ValueError("Aligned GBP cap cannot exceed total open-risk cap")
        if self.max_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be positive")
        magics = [spec.magic for spec in self.specs]
        if len(set(magics)) != len(magics):
            raise ValueError("Each symbol must have a unique magic number")
        for spec in self.specs:
            if not 0 < spec.risk_percent <= self.max_open_risk_percent:
                raise ValueError(f"Invalid risk allocation for {spec.canonical}")
            if spec.max_spread_pips <= 0:
                raise ValueError(f"Invalid spread cap for {spec.canonical}")

    def spec(self, canonical: str) -> SymbolSpec:
        canonical = canonical.upper()
        for item in self.specs:
            if item.canonical == canonical:
                return item
        raise KeyError(canonical)


@dataclass(frozen=True)
class TradeIntent:
    canonical_symbol: str
    broker_symbol: str
    side: int
    setup: str
    signal_time: datetime
    risk_percent: float
    stop_atr: float
    target_r: float
    break_even_r: float
    max_hold_m15_bars: int
    atr_price: float
    reason: str
    magic: int
    force_flat_hour_utc: Optional[int] = None


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    estimated_new_risk_dollars: float
    open_risk_dollars: float


@dataclass
class AtomicStateStore:
    path: Path

    def load(self) -> dict:
        if not self.path.exists():
            return {"signals": {}, "positions": {}, "symbol_map": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"signals": {}, "positions": {}, "symbol_map": {}}
        payload.setdefault("signals", {})
        payload.setdefault("positions", {})
        payload.setdefault("symbol_map", {})
        return payload

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(self.path)


def _mode_value(settings: Any) -> str:
    mode = getattr(settings, "mode", "READ_ONLY")
    return str(getattr(mode, "value", mode)).upper()


def _side_value(signal: Any) -> int:
    value = str(getattr(signal, "value", signal)).upper()
    if value in {"BUY", "1"}:
        return 1
    if value in {"SELL", "-1"}:
        return -1
    raise ValueError(f"Unsupported side: {signal!r}")


def _pip_size(info: Any, fallback: float) -> float:
    point = float(getattr(info, "point", 0.0) or 0.0)
    digits = int(getattr(info, "digits", 0) or 0)
    if point > 0:
        return point * 10 if digits in {3, 5} else point
    return fallback


def resolve_broker_symbol(client: Any, canonical: str) -> str:
    """Resolve common broker suffixes/prefixes and select the symbol."""
    canonical = canonical.upper()
    direct = client.symbol_info(canonical)
    if direct is not None:
        if not bool(getattr(direct, "visible", True)):
            client.symbol_select(canonical, True)
        return canonical

    symbols = client.symbols_get() or []
    matches: list[str] = []
    for item in symbols:
        name = str(getattr(item, "name", item))
        compact = "".join(ch for ch in name.upper() if ch.isalpha())
        if canonical in compact:
            matches.append(name)
    if not matches:
        raise RuntimeError(f"Broker symbol not found for {canonical}")
    matches.sort(key=lambda name: (len(name), name))
    chosen = matches[0]
    if not client.symbol_select(chosen, True):
        raise RuntimeError(f"Unable to select broker symbol {chosen}")
    return chosen


def normalize_volume(info: Any, volume: float) -> float:
    minimum = float(getattr(info, "volume_min", 0.01) or 0.01)
    maximum = float(getattr(info, "volume_max", volume) or volume)
    step = float(getattr(info, "volume_step", 0.01) or 0.01)
    clipped = min(max(float(volume), minimum), maximum)
    steps = math.floor((clipped - minimum + 1e-12) / step)
    normalized = minimum + steps * step
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return round(min(max(normalized, minimum), maximum), decimals)


def spread_pips(client: Any, broker_symbol: str, fallback_pip: float) -> float:
    info = client.symbol_info(broker_symbol)
    tick = client.symbol_info_tick(broker_symbol)
    if info is None or tick is None:
        raise RuntimeError(f"No market information for {broker_symbol}")
    pip = _pip_size(info, fallback_pip)
    return (float(tick.ask) - float(tick.bid)) / pip


def _position_side(client: Any, position: Any) -> int:
    buy_type = getattr(client, "POSITION_TYPE_BUY", 0)
    return 1 if int(position.type) == int(buy_type) else -1


def _canonical_from_name(name: str) -> Optional[str]:
    compact = "".join(ch for ch in name.upper() if ch.isalpha())
    for symbol in ("GBPUSD", "EURUSD", "GBPJPY"):
        if symbol in compact:
            return symbol
    return None


def position_risk_dollars(client: Any, position: Any) -> float:
    stop = float(getattr(position, "sl", 0.0) or 0.0)
    entry = float(getattr(position, "price_open", 0.0) or 0.0)
    volume = float(getattr(position, "volume", 0.0) or 0.0)
    if not stop or not entry or not volume:
        return 0.0
    side = _position_side(client, position)
    order_type = (
        getattr(client, "ORDER_TYPE_BUY", 0)
        if side > 0
        else getattr(client, "ORDER_TYPE_SELL", 1)
    )
    try:
        result = client.order_calc_profit(
            order_type, position.symbol, volume, entry, stop
        )
        if result is not None:
            return abs(float(result))
    except Exception:
        pass
    info = client.symbol_info(position.symbol)
    if info is None:
        return 0.0
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    if tick_size <= 0 or tick_value <= 0:
        return 0.0
    return abs(entry - stop) / tick_size * tick_value * volume


class PortfolioRiskGate:
    def __init__(self, config: V10MultiSymbolConfig):
        self.config = config

    def evaluate(
        self,
        *,
        client: Any,
        account: Any,
        positions: Iterable[Any],
        canonical_symbol: str,
        side: int,
        new_risk_dollars: float,
    ) -> RiskDecision:
        positions = list(positions)
        balance = float(getattr(account, "balance", 0.0) or 0.0)
        if balance <= 0:
            return RiskDecision(False, "invalid_account_balance", 0.0, 0.0)
        open_risk = sum(position_risk_dollars(client, p) for p in positions)
        if len(positions) >= self.config.max_positions:
            return RiskDecision(False, "max_positions", new_risk_dollars, open_risk)
        if open_risk + new_risk_dollars > (
            balance * self.config.max_open_risk_percent / 100.0 + 1e-9
        ):
            return RiskDecision(False, "max_open_risk", new_risk_dollars, open_risk)

        if canonical_symbol.startswith("GBP"):
            gbp_positions = [
                p
                for p in positions
                if (_canonical_from_name(str(p.symbol)) or "").startswith("GBP")
            ]
            gbp_risk = sum(position_risk_dollars(client, p) for p in gbp_positions)
            sides = {_position_side(client, p) for p in gbp_positions}
            sides.add(int(side))
            mixed = len(sides) > 1
            cap = (
                self.config.mixed_gbp_cap_percent
                if mixed
                else self.config.aligned_gbp_cap_percent
            )
            if gbp_risk + new_risk_dollars > balance * cap / 100.0 + 1e-9:
                return RiskDecision(
                    False, "gbp_currency_risk_cap", new_risk_dollars, open_risk
                )
        return RiskDecision(True, "allowed", new_risk_dollars, open_risk)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=frame.index,
    )
    atr = _atr(frame, period)
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    average_loss = losses.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    return 100 - 100 / (1 + relative_strength)


def _rates(client: Any, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    raw = client.copy_rates_from_pos(symbol, timeframe, 1, count)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"No completed {timeframe} rates for {symbol}")
    frame = pd.DataFrame(raw)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").reset_index(drop=True)


def build_v7_feature_row(client: Any, broker_symbol: str) -> pd.Series:
    """Build the completed-candle feature row required by Strategy Engine V7."""
    m15 = _rates(client, broker_symbol, "M15", 1200)
    m30 = _rates(client, broker_symbol, "M30", 600)
    h1 = _rates(client, broker_symbol, "H1", 600)
    if len(m15) < 250 or len(m30) < 100 or len(h1) < 250:
        raise RuntimeError(f"Insufficient completed history for {broker_symbol}")

    m15["atr14"] = _atr(m15)
    m15["rsi14"] = _rsi(m15["close"])
    m15["body_ratio"] = (m15["close"] - m15["open"]).abs() / (
        m15["high"] - m15["low"]
    ).replace(0, np.nan)
    m15["vol_ratio"] = m15["tick_volume"] / m15["tick_volume"].rolling(
        20, min_periods=20
    ).mean()
    m15["range_atr"] = (m15["high"] - m15["low"]) / m15["atr14"]
    m15["end"] = m15["time"] + pd.Timedelta(minutes=15)

    m30["ema20_m30"] = _ema(m30["close"], 20)
    m30["ema50_m30"] = _ema(m30["close"], 50)
    m30["end"] = m30["time"] + pd.Timedelta(minutes=30)

    h1["ema20_h1"] = _ema(h1["close"], 20)
    h1["ema50_h1"] = _ema(h1["close"], 50)
    h1["ema20_slope_h1"] = h1["ema20_h1"].diff(3) / 3
    h1["adx14_h1"] = _adx(h1)
    h1["atr14_h1"] = _atr(h1)
    h1["atr_q55_h1"] = h1["atr14_h1"].rolling(252, min_periods=100).quantile(0.55)
    h1["atr_q60_h1"] = h1["atr14_h1"].rolling(252, min_periods=100).quantile(0.60)
    h1["close_h1"] = h1["close"]
    h1["end"] = h1["time"] + pd.Timedelta(hours=1)

    row = m15.iloc[-1].copy()
    signal_end = pd.Timestamp(row["end"])
    h1_row = h1[h1["end"] <= signal_end].iloc[-1]
    m30_history = m30[m30["end"] <= signal_end]
    m30_row = m30_history.iloc[-1]

    row["weekday"] = signal_end.weekday()
    row["hour"] = signal_end.hour + signal_end.minute / 60.0
    for name in (
        "ema20_h1",
        "ema50_h1",
        "ema20_slope_h1",
        "adx14_h1",
        "atr14_h1",
        "atr_q55_h1",
        "atr_q60_h1",
        "close_h1",
    ):
        row[name] = h1_row[name]
    row["ema20_m30"] = m30_row["ema20_m30"]
    row["ema50_m30"] = m30_row["ema50_m30"]
    recent_m30 = m30_history.tail(4)
    row["recent_m30_pullback_touch"] = bool(
        (
            (recent_m30["low"] <= recent_m30["ema20_m30"])
            & (recent_m30["high"] >= recent_m30["ema20_m30"])
        ).any()
    )

    completed = m15[m15["end"] <= signal_end].copy()
    current_date = signal_end.date()
    same_day = completed["end"].dt.date == current_date
    end_minutes = completed["end"].dt.hour * 60 + completed["end"].dt.minute
    asian = completed[same_day & (end_minutes <= 7 * 60)]
    if asian.empty:
        row["asian_high"] = np.nan
        row["asian_range"] = np.nan
        row["asian_med20"] = np.nan
        row["recent_asian_up_break"] = False
    else:
        row["asian_high"] = float(asian["high"].max())
        row["asian_range"] = float(asian["high"].max() - asian["low"].min())
        asian_minutes = completed["end"].dt.hour * 60 + completed["end"].dt.minute
        daily_asian = completed[asian_minutes <= 7 * 60].copy()
        daily_asian["date"] = daily_asian["end"].dt.date
        range_table = daily_asian.groupby("date").agg(
            session_high=("high", "max"), session_low=("low", "min")
        )
        ranges = range_table["session_high"] - range_table["session_low"]
        previous_ranges = ranges[ranges.index < current_date].tail(20)
        row["asian_med20"] = float(previous_ranges.median()) if len(previous_ranges) else np.nan
        recent = completed.tail(4)
        row["recent_asian_up_break"] = bool(
            (recent["close"] > row["asian_high"]).any()
        )

    london = completed[
        same_day
        & (completed["end"].dt.hour >= 7)
        & (completed["end"].dt.hour < 12)
    ]
    row["london_low"] = float(london["low"].min()) if len(london) else np.nan

    previous_dates = sorted(
        date for date in completed["end"].dt.date.unique() if date < current_date
    )
    if previous_dates:
        previous_day = completed[completed["end"].dt.date == previous_dates[-1]]
        row["prev_range"] = float(previous_day["high"].max() - previous_day["low"].min())
        row["prior_day_min_low"] = float(previous_day["low"].min())
    else:
        row["prev_range"] = np.nan
        row["prior_day_min_low"] = np.nan

    row["prior_2_low"] = float(completed["low"].iloc[-3:-1].min())
    row["prior_16_high"] = float(completed["high"].iloc[-17:-1].max())
    row["prior_16_low"] = float(completed["low"].iloc[-17:-1].min())
    return row


def _log_signal(journal: Any, intent: TradeIntent, status: str, reason: str) -> None:
    try:
        journal.log_signal(
            intent.broker_symbol,
            "BUY" if intent.side > 0 else "SELL",
            reason,
            {
                "strategy": "V10_MULTISYMBOL",
                "setup": intent.setup,
                "signal_time": intent.signal_time.isoformat(),
                "risk_percent": intent.risk_percent,
                "status": status,
            },
            setup=1,
            filtered=0 if status == "READY" else 1,
        )
    except Exception:
        pass


def _log_order(
    journal: Any,
    intent: TradeIntent,
    volume: float,
    stop_pips: float,
    target_pips: float,
    status: str,
    message: str,
) -> None:
    try:
        journal.log_order(
            intent.broker_symbol,
            "BUY" if intent.side > 0 else "SELL",
            volume,
            None,
            stop_pips,
            target_pips,
            None,
            status,
            f"[{intent.setup}] {message}",
        )
    except Exception:
        pass


def _risk_lot(client: Any, intent: TradeIntent, account: Any, entry: float, stop: float) -> float:
    info = client.symbol_info(intent.broker_symbol)
    risk_dollars = float(account.balance) * intent.risk_percent / 100.0
    distance = abs(entry - stop)
    if info is None or distance <= 0:
        return 0.0
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    if tick_size > 0 and tick_value > 0:
        raw = risk_dollars / ((distance / tick_size) * tick_value)
    else:
        order_type = (
            getattr(client, "ORDER_TYPE_BUY", 0)
            if intent.side > 0
            else getattr(client, "ORDER_TYPE_SELL", 1)
        )
        probe = 1.0
        try:
            one_lot_loss = abs(
                float(
                    client.order_calc_profit(
                        order_type, intent.broker_symbol, probe, entry, stop
                    )
                )
            )
        except Exception:
            one_lot_loss = 0.0
        raw = risk_dollars / one_lot_loss if one_lot_loss > 0 else 0.0
    return normalize_volume(info, raw) if raw > 0 else 0.0


def _stops(client: Any, intent: TradeIntent) -> tuple[float, float, float, float, float]:
    info = client.symbol_info(intent.broker_symbol)
    tick = client.symbol_info_tick(intent.broker_symbol)
    if info is None or tick is None:
        raise RuntimeError(f"No quote for {intent.broker_symbol}")
    entry = float(tick.ask if intent.side > 0 else tick.bid)
    stop_distance = intent.stop_atr * intent.atr_price
    minimum = float(getattr(info, "trade_stops_level", 0) or 0) * float(
        getattr(info, "point", 0.0) or 0.0
    )
    stop_distance = max(stop_distance, minimum)
    target_distance = stop_distance * intent.target_r
    stop = entry - stop_distance if intent.side > 0 else entry + stop_distance
    target = entry + target_distance if intent.side > 0 else entry - target_distance
    digits = int(getattr(info, "digits", 5) or 5)
    pip = _pip_size(info, 0.0001)
    return (
        entry,
        round(stop, digits),
        round(target, digits),
        stop_distance / pip,
        target_distance / pip,
    )


def _order_request(client: Any, intent: TradeIntent, volume: float, entry: float, stop: float, target: float) -> dict:
    request = {
        "action": getattr(client, "TRADE_ACTION_DEAL"),
        "symbol": intent.broker_symbol,
        "volume": volume,
        "type": (
            getattr(client, "ORDER_TYPE_BUY")
            if intent.side > 0
            else getattr(client, "ORDER_TYPE_SELL")
        ),
        "price": entry,
        "sl": stop,
        "tp": target,
        "deviation": int(os.getenv("MAX_SLIPPAGE_POINTS", "20")),
        "magic": intent.magic,
        "comment": f"V10 {intent.setup}"[:31],
        "type_time": getattr(client, "ORDER_TIME_GTC"),
    }
    info = client.symbol_info(intent.broker_symbol)
    filling = getattr(info, "filling_mode", None) if info is not None else None
    if filling is not None:
        request["type_filling"] = filling
    return request


def _check_retcode_ok(client: Any, result: Any) -> bool:
    if result is None:
        return False
    success_codes = {
        int(code)
        for code in (
            getattr(client, "TRADE_RETCODE_DONE", None),
            getattr(client, "TRADE_RETCODE_PLACED", None),
            getattr(client, "TRADE_RETCODE_DONE_PARTIAL", None),
        )
        if code is not None
    }
    return int(getattr(result, "retcode", -1)) in success_codes


def _order_check_ok(client: Any, result: Any) -> bool:
    if result is None:
        return False
    retcode = int(getattr(result, "retcode", -1))
    if retcode == 0:
        return True
    return _check_retcode_ok(client, result)


def execute_intent(
    *,
    client: Any,
    journal: Any,
    settings: Any,
    account: Any,
    positions: Iterable[Any],
    intent: TradeIntent,
    config: V10MultiSymbolConfig,
) -> dict:
    spec = config.spec(intent.canonical_symbol)
    current_spread = spread_pips(client, intent.broker_symbol, spec.pip_size_fallback)
    if current_spread > spec.max_spread_pips:
        _log_signal(journal, intent, "REJECTED", "spread_cap")
        return {"status": "REJECTED", "reason": "spread_cap", "spread": current_spread}
    entry, stop, target, stop_pips, target_pips = _stops(client, intent)
    volume = _risk_lot(client, intent, account, entry, stop)
    if volume <= 0:
        return {"status": "REJECTED", "reason": "invalid_volume"}
    estimated_risk = float(account.balance) * intent.risk_percent / 100.0
    risk_decision = PortfolioRiskGate(config).evaluate(
        client=client,
        account=account,
        positions=positions,
        canonical_symbol=intent.canonical_symbol,
        side=intent.side,
        new_risk_dollars=estimated_risk,
    )
    if not risk_decision.allowed:
        _log_signal(journal, intent, "REJECTED", risk_decision.reason)
        return {"status": "REJECTED", "reason": risk_decision.reason}

    mode = _mode_value(settings)
    _log_signal(journal, intent, "READY", intent.reason)
    if mode == "READ_ONLY":
        return {
            "status": "SHADOW",
            "symbol": intent.broker_symbol,
            "setup": intent.setup,
            "volume": volume,
            "entry": entry,
            "sl": stop,
            "tp": target,
            "risk_percent": intent.risk_percent,
        }
    if mode == "APPROVAL":
        answer = input(
            f"Approve {intent.setup} {'BUY' if intent.side > 0 else 'SELL'} "
            f"{volume} {intent.broker_symbol}? Type YES: "
        ).strip().upper()
        if answer != "YES":
            return {"status": "REJECTED", "reason": "operator_declined"}
    if mode not in {"APPROVAL", "AUTO"}:
        return {"status": "REJECTED", "reason": f"unsupported_mode:{mode}"}

    request = _order_request(client, intent, volume, entry, stop, target)
    check = client.order_check(request)
    if not _order_check_ok(client, check):
        message = f"order_check failed: {getattr(check, 'retcode', None)} {getattr(check, 'comment', '')}"
        _log_order(journal, intent, volume, stop_pips, target_pips, "REJECTED", message)
        return {"status": "REJECTED", "reason": "order_check", "message": message}
    result = client.order_send(request)
    if not _check_retcode_ok(client, result):
        message = f"order_send failed: {getattr(result, 'retcode', None)} {getattr(result, 'comment', '')}"
        _log_order(journal, intent, volume, stop_pips, target_pips, "REJECTED", message)
        return {"status": "REJECTED", "reason": "order_send", "message": message}
    ticket = int(getattr(result, "order", 0) or getattr(result, "deal", 0) or 0)
    _log_order(journal, intent, volume, stop_pips, target_pips, "FILLED", f"ticket={ticket}")
    return {"status": "FILLED", "ticket": ticket, "request": request}


def _v7_intent(client: Any, canonical: str, broker_symbol: str, spec: SymbolSpec) -> Optional[TradeIntent]:
    from .strategy_engine_v7 import V7Config, evaluate_eurusd_v7, evaluate_gbpjpy_v7

    row = build_v7_feature_row(client, broker_symbol)
    evaluator = evaluate_eurusd_v7 if canonical == "EURUSD" else evaluate_gbpjpy_v7
    signal = evaluator(row, V7Config(enabled=True, risk_percent=spec.risk_percent))
    if signal is None:
        return None
    return TradeIntent(
        canonical_symbol=canonical,
        broker_symbol=broker_symbol,
        side=_side_value(signal.side),
        setup=signal.setup,
        signal_time=signal.signal_time,
        risk_percent=signal.risk_percent,
        stop_atr=signal.stop_atr,
        target_r=signal.target_r,
        break_even_r=signal.break_even_r,
        max_hold_m15_bars=signal.max_hold_m15_bars,
        atr_price=float(row["atr14"]),
        reason=signal.reason,
        magic=spec.magic,
        force_flat_hour_utc=spec.force_flat_hour_utc,
    )


def _gbpusd_intents(client: Any, broker_symbol: str, config: V10MultiSymbolConfig) -> list[TradeIntent]:
    from .gbpusd_portfolio_v10 import evaluate_precision_v4_setup
    from .gbpusd_satellite_v3 import evaluate_setup as evaluate_satellite_v3
    from .gbpusd_v4 import LiveParams

    intents: list[TradeIntent] = []
    swing, _ = evaluate_precision_v4_setup(client, broker_symbol)
    if swing is not None:
        params = LiveParams()
        intents.append(
            TradeIntent(
                canonical_symbol="GBPUSD",
                broker_symbol=broker_symbol,
                side=_side_value(swing.side),
                setup=swing.variant,
                signal_time=swing.signal_end,
                risk_percent=(
                    float(os.getenv("GBPUSD_SWING_RISK_A", "0.40"))
                    if str(swing.precision_grade).upper() == "A"
                    else float(os.getenv("GBPUSD_SWING_RISK_B", "0.15"))
                ),
                stop_atr=params.stop_atr,
                target_r=params.target_r,
                break_even_r=params.partial_r,
                max_hold_m15_bars=params.max_h4_bars * 16,
                atr_price=float(swing.atr_price),
                reason=swing.reason,
                magic=int(os.getenv("GBPUSD_SWING_MAGIC", "51001")),
                force_flat_hour_utc=None,
            )
        )
    satellite, _ = evaluate_satellite_v3(client, broker_symbol)
    if satellite is not None:
        intents.append(
            TradeIntent(
                canonical_symbol="GBPUSD",
                broker_symbol=broker_symbol,
                side=_side_value(satellite.side),
                setup=satellite.name,
                signal_time=satellite.signal_end,
                risk_percent=config.spec("GBPUSD").risk_percent,
                stop_atr=satellite.stop_atr,
                target_r=satellite.target_r,
                break_even_r=satellite.break_even_r,
                max_hold_m15_bars=satellite.max_hold_m15_bars,
                atr_price=float(satellite.atr_price),
                reason=satellite.reason,
                magic=int(os.getenv("GBPUSD_SATELLITE_MAGIC", "51002")),
                force_flat_hour_utc=20,
            )
        )
    return intents


def _close_position(client: Any, position: Any, deviation: int) -> tuple[bool, str]:
    tick = client.symbol_info_tick(position.symbol)
    if tick is None:
        return False, "missing_tick"
    is_buy = _position_side(client, position) > 0
    request = {
        "action": getattr(client, "TRADE_ACTION_DEAL"),
        "position": int(position.ticket),
        "symbol": position.symbol,
        "volume": float(position.volume),
        "type": getattr(client, "ORDER_TYPE_SELL") if is_buy else getattr(client, "ORDER_TYPE_BUY"),
        "price": float(tick.bid if is_buy else tick.ask),
        "deviation": deviation,
        "magic": int(getattr(position, "magic", 0) or 0),
        "comment": "V10 time/flat exit",
        "type_time": getattr(client, "ORDER_TIME_GTC"),
    }
    result = client.order_send(request)
    return _check_retcode_ok(client, result), str(getattr(result, "comment", ""))


def _modify_break_even(client: Any, position: Any) -> bool:
    request = {
        "action": getattr(client, "TRADE_ACTION_SLTP"),
        "position": int(position.ticket),
        "symbol": position.symbol,
        "sl": float(position.price_open),
        "tp": float(getattr(position, "tp", 0.0) or 0.0),
    }
    return _check_retcode_ok(client, client.order_send(request))


def manage_multisymbol_positions(
    client: Any,
    config: V10MultiSymbolConfig,
    state: dict,
    now: Optional[datetime] = None,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    managed_magics = {spec.magic for spec in config.specs} | {
        int(os.getenv("GBPUSD_SWING_MAGIC", "51001")),
        int(os.getenv("GBPUSD_SATELLITE_MAGIC", "51002")),
    }
    positions = [
        p for p in (client.positions_get() or [])
        if int(getattr(p, "magic", 0) or 0) in managed_magics
    ]
    active = {str(p.ticket) for p in positions}
    state["positions"] = {
        key: value for key, value in state.get("positions", {}).items() if key in active
    }
    events: list[dict] = []
    for position in positions:
        key = str(position.ticket)
        canonical = _canonical_from_name(str(position.symbol))
        if canonical is None:
            continue
        record = state["positions"].setdefault(
            key,
            {
                "opened": datetime.fromtimestamp(int(position.time), tz=timezone.utc).isoformat(),
                "initial_risk_price": abs(float(position.price_open) - float(position.sl)),
                "break_even_r": 1.0,
                "max_hold_m15_bars": (
                    72 * 16
                    if int(getattr(position, "magic", 0) or 0)
                    == int(os.getenv("GBPUSD_SWING_MAGIC", "51001"))
                    else 48
                ),
                "force_flat_hour_utc": (
                    None
                    if int(getattr(position, "magic", 0) or 0)
                    == int(os.getenv("GBPUSD_SWING_MAGIC", "51001"))
                    else config.spec(canonical).force_flat_hour_utc
                ),
                "break_even_done": False,
            },
        )
        opened = datetime.fromisoformat(record["opened"])
        maximum_minutes = int(record["max_hold_m15_bars"]) * 15
        force_flat_hour = record.get("force_flat_hour_utc")
        force_flat_due = bool(
            force_flat_hour is not None
            and (now.date() > opened.date() or now.hour >= int(force_flat_hour))
        )
        if force_flat_due or (now - opened).total_seconds() >= maximum_minutes * 60:
            ok, message = _close_position(client, position, config.max_slippage_points)
            events.append({"ticket": int(position.ticket), "action": "close", "ok": ok, "message": message})
            continue
        risk_price = float(record.get("initial_risk_price", 0.0) or 0.0)
        if risk_price <= 0 or record.get("break_even_done"):
            continue
        favorable = (
            float(position.price_current) - float(position.price_open)
            if _position_side(client, position) > 0
            else float(position.price_open) - float(position.price_current)
        )
        if favorable >= float(record.get("break_even_r", 1.0)) * risk_price:
            ok = _modify_break_even(client, position)
            if ok:
                record["break_even_done"] = True
            events.append({"ticket": int(position.ticket), "action": "break_even", "ok": ok})
    return events


def run_v10_multisymbol_cycle(
    client: Any,
    journal: Any,
    settings: Any,
    account: Any,
    risk_ok: bool,
    active: bool,
    config: Optional[V10MultiSymbolConfig] = None,
) -> dict:
    """Evaluate and optionally execute all V10 symbols in one shared cycle."""
    config = config or V10MultiSymbolConfig.from_env()
    store = AtomicStateStore(Path(config.state_path))
    state = store.load()
    management = manage_multisymbol_positions(client, config, state)
    positions = list(client.positions_get() or [])
    outcomes: list[dict] = []
    symbol_views: list[dict] = []

    for canonical in config.symbols:
        spec = config.spec(canonical)
        try:
            broker_symbol = state["symbol_map"].get(canonical)
            if not broker_symbol or client.symbol_info(broker_symbol) is None:
                broker_symbol = resolve_broker_symbol(client, canonical)
                state["symbol_map"][canonical] = broker_symbol
            intents = (
                _gbpusd_intents(client, broker_symbol, config)
                if canonical == "GBPUSD"
                else [intent] if (intent := _v7_intent(client, canonical, broker_symbol, spec)) else []
            )
            symbol_views.append({
                "symbol": canonical,
                "broker_symbol": broker_symbol,
                "signals": len(intents),
                "setups": [item.setup for item in intents],
            })
            for intent in intents:
                marker = f"{intent.canonical_symbol}:{intent.setup}:{intent.signal_time.isoformat()}"
                if state["signals"].get(marker):
                    outcomes.append({"status": "SKIPPED", "reason": "duplicate_signal", "marker": marker})
                    continue
                state["signals"][marker] = datetime.now(timezone.utc).isoformat()
                if not risk_ok or not active:
                    _log_signal(journal, intent, "REJECTED", "global_risk_or_pause")
                    outcomes.append({"status": "REJECTED", "reason": "global_risk_or_pause", "setup": intent.setup})
                    continue
                result = execute_intent(
                    client=client,
                    journal=journal,
                    settings=settings,
                    account=account,
                    positions=positions,
                    intent=intent,
                    config=config,
                )
                outcomes.append({"symbol": canonical, "setup": intent.setup, **result})
                if result.get("status") == "FILLED":
                    ticket = str(result.get("ticket", ""))
                    if ticket and ticket != "0":
                        state["positions"][ticket] = {
                            "opened": datetime.now(timezone.utc).isoformat(),
                            "initial_risk_price": abs(
                                float(result["request"]["price"])
                                - float(result["request"]["sl"])
                            ),
                            "break_even_r": intent.break_even_r,
                            "max_hold_m15_bars": intent.max_hold_m15_bars,
                            "force_flat_hour_utc": intent.force_flat_hour_utc,
                            "break_even_done": False,
                            "setup": intent.setup,
                            "canonical_symbol": intent.canonical_symbol,
                        }
                    positions = list(client.positions_get() or [])
        except Exception as exc:
            symbol_views.append({"symbol": canonical, "error": str(exc), "signals": 0})

    # Retain only recent signal markers to avoid unbounded state growth.
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
    state["signals"] = {
        key: value
        for key, value in state["signals"].items()
        if pd.Timestamp(value) >= cutoff
    }
    store.save(state)
    return {
        "strategy_version": "V10_MULTISYMBOL_LIVE_CANDIDATE",
        "mode": _mode_value(settings),
        "symbols": symbol_views,
        "outcomes": outcomes,
        "management": management,
        "open_positions": len(positions),
        "note": "All symbols share the V10 account-risk gate. Keep READ_ONLY until reconciliation passes.",
    }
