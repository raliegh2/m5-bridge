"""Multi-symbol, dual-engine breakout system (the validated V2 family).

This generalizes the single-symbol GBPUSD Breakout V2 engine into a small,
config-driven set of INDEPENDENT breakout engines, each pinned to the timeframe
where it actually backtested profitable after costs:

* SWING engine  -> H4 breakout confirmed by the D1 trend      (FX majors)
* INTRADAY engine -> M30 breakout confirmed by the H4 trend    (gold)

Validated composition (only engines with out-of-sample / decade-long proof):

    GBPUSD  swing (H4/D1)   +15.3%  PF 1.35  160 trades / 10.5 yrs  (anchor)
    XAUUSD  intraday (M30)  +17%    PF 1.52  132 trades, OOS +4.6%  (engine)

Everything else was rejected on out-of-sample or long-history data:
    AUDUSD / EURUSD swing   PF 0.84 over ~13 yrs (161/193 trades) -- NEGATIVE
    USDJPY / GBPJPY         both engines lost after costs
    FX intraday             lost on every FX pair (only gold has an intraday edge)

Each engine trades one position at a time. Risk is bounded three ways:
* per-trade fixed-fractional sizing (each engine's own risk %),
* a per-symbol daily-drawdown halt (a symbol pauses itself after a bad day),
* the shared account combined-risk ceiling (the universal cap across engines).

Completed candles only. No partial closes (preserves the positive payoff skew).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .enums import Mode, Signal
from .execution import pip_size, place_market_order
from .sizing import RiskConfig, risk_lot
from .trade_manager import close_position, modify_position_sl
# Reuse the validated indicator + candle helpers from the original engine so
# the maths is byte-identical to the proxy-tested GBPUSD path.
from .gbpusd_breakout_v2 import _ema, _atr, _adx, _rates, _initial_risk_price


_TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


@dataclass(frozen=True)
class EngineSpec:
    """One breakout engine pinned to a symbol + timeframe pair."""
    symbol: str
    kind: str            # "swing" | "intraday"  (display/label only)
    entry_tf: str        # breakout timeframe, e.g. "H4" or "M30"
    trend_tf: str        # regime timeframe, e.g. "D1" or "H4"
    magic: int
    risk_percent: float = 0.50
    channel_bars: int = 55
    adx_min: float = 15.0
    volume_ratio_min: float = 0.80
    stop_atr: float = 2.0
    target_r: float = 2.0
    trail_atr: float = 2.5
    trail_start_r: float = 1.0
    min_stop_pips: float = 20.0
    max_stop_pips: float = 150.0
    max_hold_bars: int = 90
    entry_hours_utc: tuple = (12, 16)

    @property
    def label(self) -> str:
        return f"Breakout {self.kind.title()} {self.entry_tf}"


# The validated set -- only engines with out-of-sample or decade-long proof.
# GBPUSD keeps the original engine's magic so its live history carries over.
#
# AUDUSD and EURUSD swing were REJECTED on ~13 years of H4 (161/193 trades,
# PF 0.84 each, negative in and out of sample). Their earlier 16-month positives
# were small-sample noise. JPY and FX-intraday were rejected too. Do not re-add
# without a fresh, multi-year, out-of-sample-positive backtest.
DEFAULT_ENGINES: tuple = (
    EngineSpec("GBPUSD", "swing", "H4", "D1", magic=260755),
    # Gold: intraday M30 breakout, H4 trend. Wide stop clamps because gold ATR
    # is large in price; session-hour entries (London + New York).
    EngineSpec("XAUUSD", "intraday", "M30", "H4", magic=260758,
               min_stop_pips=10.0, max_stop_pips=5000.0, max_hold_bars=48,
               entry_hours_utc=tuple(range(7, 18))),
)


def engines_for(settings) -> tuple:
    """The engine set to run.

    Honours the account's SYMBOLS list: an engine only runs if its symbol is in
    settings.symbols (so a user can trim the set from .env) -- but if SYMBOLS is
    empty/default we run the full validated composition.
    """
    configured = {s.upper() for s in getattr(settings, "symbols", ())}
    if not configured:
        return DEFAULT_ENGINES
    return tuple(e for e in DEFAULT_ENGINES if e.symbol.upper() in configured) \
        or DEFAULT_ENGINES


# In-memory guard: one entry per signal candle per engine (prevents re-entering
# the same completed-candle breakout on repeated loop iterations).
_LAST_SIGNAL_END: dict[int, str] = {}


@dataclass(frozen=True)
class _Setup:
    side: Signal
    signal_end: datetime
    atr_price: float
    reason: str


def evaluate_setup(client, spec: EngineSpec) -> Optional[_Setup]:
    """Latest completed entry-TF candle vs the completed trend-TF regime."""
    need = max(160, spec.channel_bars + 60)
    entry = _rates(client, spec.symbol, spec.entry_tf, need)
    trend = _rates(client, spec.symbol, spec.trend_tf, 120)
    if entry is None or trend is None or len(entry) <= spec.channel_bars:
        return None

    entry = entry.copy()
    entry["atr"] = _atr(entry, 14)
    entry["adx"] = _adx(entry, 14)
    entry["avg_tick_volume"] = entry["tick_volume"].rolling(20, min_periods=20).mean()
    trend = trend.copy()
    trend["ema20"] = _ema(trend["close"], 20)
    trend["ema50"] = _ema(trend["close"], 50)

    latest = entry.iloc[-1]
    reg = trend.iloc[-1]
    required = (latest["atr"], latest["adx"], latest["avg_tick_volume"],
                reg["ema20"], reg["ema50"])
    if any(pd.isna(v) for v in required):
        return None

    minutes = _TF_MINUTES.get(spec.entry_tf.upper(), 240)
    signal_end = latest["time"].to_pydatetime() + pd.Timedelta(minutes=minutes)
    if signal_end.hour not in spec.entry_hours_utc:
        return None

    prior = entry.iloc[-(spec.channel_bars + 1):-1]
    channel_high = float(prior["high"].max())
    channel_low = float(prior["low"].min())
    volume_ratio = float(latest["tick_volume"] / latest["avg_tick_volume"])

    long_regime = reg["ema20"] > reg["ema50"] and reg["close"] > reg["ema20"]
    short_regime = reg["ema20"] < reg["ema50"] and reg["close"] < reg["ema20"]
    common = (float(latest["adx"]) >= spec.adx_min
              and volume_ratio >= spec.volume_ratio_min)

    if common and long_regime and latest["close"] > channel_high:
        return _Setup(Signal.BUY, signal_end, float(latest["atr"]),
                      f"{spec.entry_tf} close broke {spec.channel_bars}-bar high; "
                      f"{spec.trend_tf} EMA20>EMA50; ADX={latest['adx']:.1f}; "
                      f"vol={volume_ratio:.2f}x")
    if common and short_regime and latest["close"] < channel_low:
        return _Setup(Signal.SELL, signal_end, float(latest["atr"]),
                      f"{spec.entry_tf} close broke {spec.channel_bars}-bar low; "
                      f"{spec.trend_tf} EMA20<EMA50; ADX={latest['adx']:.1f}; "
                      f"vol={volume_ratio:.2f}x")
    return None


def manage_positions(client, spec: EngineSpec) -> list:
    """Entry-TF ATR trailing + the max-hold time exit for this engine."""
    messages: list = []
    positions = [p for p in (client.positions_get(symbol=spec.symbol) or [])
                 if getattr(p, "magic", None) == spec.magic]
    if not positions:
        return messages
    entry = _rates(client, spec.symbol, spec.entry_tf, 250)
    if entry is None or len(entry) < 20:
        return messages
    entry["atr"] = _atr(entry, 14)
    current_atr = float(entry.iloc[-1]["atr"])
    if pd.isna(current_atr) or current_atr <= 0:
        return messages

    for position in positions:
        entered = pd.to_datetime(getattr(position, "time", 0), unit="s", utc=True)
        since = entry[entry["time"] >= entered]
        if len(since) >= spec.max_hold_bars:
            _, message = close_position(client, position.ticket)
            messages.append(message)
            continue
        risk_price = _initial_risk_price(position)
        px_open = float(getattr(position, "price_open", 0.0) or 0.0)
        current = float(getattr(position, "price_current", 0.0) or 0.0)
        if not risk_price or px_open <= 0 or current <= 0 or since.empty:
            continue
        is_buy = position.type == client.POSITION_TYPE_BUY
        favourable = current - px_open if is_buy else px_open - current
        if favourable < spec.trail_start_r * risk_price:
            continue
        if is_buy:
            candidate = float(since["high"].max()) - spec.trail_atr * current_atr
            if candidate >= current:
                continue
            sl = float(getattr(position, "sl", 0.0) or 0.0)
            if sl and candidate <= sl:
                continue
        else:
            candidate = float(since["low"].min()) + spec.trail_atr * current_atr
            if candidate <= current:
                continue
            sl = float(getattr(position, "sl", 0.0) or 0.0)
            if sl and candidate >= sl:
                continue
        _, message = modify_position_sl(client, position, round(candidate, 5))
        messages.append(message)
    return messages


def _realized_today(client, spec: EngineSpec) -> float:
    """Today's realized P&L for this symbol+engine, from MT5 deal history.

    Powers the per-symbol drawdown halt. Fails OPEN (returns 0.0) if history is
    unavailable, so a data hiccup never blocks trading -- the account-level and
    combined-risk caps still apply.
    """
    try:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                    microsecond=0)
        deals = client.history_deals_get(start, datetime.now(timezone.utc))
        if not deals:
            return 0.0
        return sum(float(getattr(d, "profit", 0.0) or 0.0)
                   for d in deals
                   if getattr(d, "symbol", "") == spec.symbol
                   and getattr(d, "magic", None) == spec.magic)
    except Exception:  # noqa: BLE001
        return 0.0


def _engine_row(spec: EngineSpec, setup: Optional[_Setup], note: str) -> dict:
    """A per-symbol dashboard row (matches the engines_by_symbol shape)."""
    ready = setup is not None
    return {
        "symbol": spec.symbol,
        "aligned": ready,
        "bias": setup.side.value if setup else "NONE",
        "trades": [spec.label],
        "regime": {},
        "timeframes": [],
        "engines": [{
            "name": spec.label,
            "ready": ready,
            "bias": setup.side.value if setup else "NONE",
            "confidence": 1.0 if ready else 0.0,
            "reason": setup.reason if setup else note,
            "enabled": True,
            "risk": round(float(spec.risk_percent), 3),
        }],
    }


def run_cycle(client, journal, settings, account, risk_ok: bool, active: bool,
              risk_scale: float = 1.0, engines: Optional[tuple] = None) -> tuple:
    """Manage all engines and open at most one validated setup per symbol.

    Returns (thinking, rows): a summary dict for the top panel and the
    per-symbol rows for the "all engines" dashboard panel.
    """
    engines = engines or engines_for(settings)
    balance = float(getattr(account, "balance", 0.0) or 0.0)
    positions = client.positions_get() or []
    by_magic = {e.magic: e for e in engines}

    def pos_risk(p) -> float:
        e = by_magic.get(getattr(p, "magic", None))
        return e.risk_percent if e else 0.0

    open_risk = sum(pos_risk(p) for p in positions)
    per_symbol_dd = float(getattr(settings, "per_symbol_dd_pct", 0.0) or 0.0)
    rows: list = []

    for spec in engines:
        for message in manage_positions(client, spec):
            journal.log_order(spec.symbol, "MANAGE", 0.0, None, None, None, None,
                              "UPDATED", message)

        setup = evaluate_setup(client, spec)
        note = (f"No validated {spec.entry_tf} breakout on the latest completed "
                f"candle.")
        rows.append(_engine_row(spec, setup, note))
        if setup is None:
            continue

        journal.log_signal(spec.symbol, setup.side.value, setup.reason,
                           {"time": setup.signal_end.isoformat(),
                            "atr": setup.atr_price}, setup=1, filtered=0)

        # --- gates -------------------------------------------------------
        if settings.mode is Mode.READ_ONLY or not risk_ok or not active:
            continue
        # one position per symbol/engine
        if [p for p in positions if getattr(p, "symbol", "") == spec.symbol
                and getattr(p, "magic", None) == spec.magic]:
            continue
        marker = setup.signal_end.isoformat()
        if _LAST_SIGNAL_END.get(spec.magic) == marker:
            continue
        # universal ceiling: combined open risk across ALL engines
        if open_risk + spec.risk_percent > settings.combined_risk_ceiling + 1e-9:
            continue
        # per-symbol drawdown halt
        if per_symbol_dd > 0 and _realized_today(client, spec) <= \
                -balance * per_symbol_dd / 100.0:
            journal.log_order(spec.symbol, setup.side.value, 0.0, None, None,
                              None, None, "SKIPPED",
                              f"[{spec.label}] per-symbol drawdown halt "
                              f"(> {per_symbol_dd:g}% today).")
            continue

        # --- size + place ------------------------------------------------
        pip = pip_size(client, spec.symbol) or 0.0001
        stop_pips = min(max((spec.stop_atr * setup.atr_price) / pip,
                            spec.min_stop_pips), spec.max_stop_pips)
        target_pips = spec.target_r * stop_pips
        eff_risk = min(spec.risk_percent * max(float(risk_scale), 0.0), 1.0)
        if eff_risk <= 0:
            continue
        risk_cfg = RiskConfig(enabled=True, risk_percent=eff_risk,
                              pip_value_per_lot=float(settings.pip_value_per_lot),
                              max_lot=float(settings.max_lot))
        volume = risk_lot(balance, stop_pips, risk_cfg)
        ok, message = place_market_order(client, spec.symbol, setup.side, volume,
                                         stop_pips, target_pips, magic=spec.magic,
                                         comment=spec.label)
        journal.log_order(spec.symbol, setup.side.value, volume, None, stop_pips,
                          target_pips, None, "FILLED" if ok else "REJECTED",
                          f"[{spec.label} risk={eff_risk:.2f}%] {message}")
        if ok:
            _LAST_SIGNAL_END[spec.magic] = marker
            open_risk += spec.risk_percent

    ready_rows = [r for r in rows if r["aligned"]]
    thinking = {
        "timeframes": [],
        "bias": ready_rows[0]["bias"] if ready_rows else "NONE",
        "aligned": bool(ready_rows),
        "setup_valid": bool(ready_rows),
        "note": ("Breakout setup ready: "
                 + ", ".join(r["symbol"] for r in ready_rows)
                 if ready_rows else
                 "All breakout engines armed; waiting for a completed-candle "
                 "breakout with trend, ADX and volume confirmation."),
        "engines": [r["engines"][0] for r in rows],
    }
    return thinking, rows
