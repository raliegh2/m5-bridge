"""Validation-gated lower-timeframe swing family for V18.

This engine is intentionally distinct from the existing H4 breakout/retest
families. It uses completed M15 candles for entry, completed H1 candles for the
local trend, and completed D1 candles for the macro regime. It is disabled
unless a broker-data walk-forward report marks the symbol as validated.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LtfConfig:
    risk_percent: float = 0.10
    allowed_hours_utc: tuple[int, ...] = (9, 10, 11, 12, 13, 14, 15, 16)
    minimum_body_ratio: float = 0.35
    minimum_h1_adx: float = 18.0
    pullback_atr: float = 0.30
    stop_atr: float = 1.20
    target_r: float = 2.20
    break_even_r: float = 1.00
    maximum_m15_bars: int = 96


@dataclass(frozen=True)
class LtfDecision:
    allowed: bool
    reason: str
    side: int = 0
    risk_percent: float = 0.0


def _get(row: Any, name: str, default: float = 0.0) -> float:
    if isinstance(row, dict):
        return float(row.get(name, default) or default)
    return float(getattr(row, name, default) or default)


def load_validation_registry(path: str | Path) -> dict[str, Any]:
    registry_path = Path(path)
    if not registry_path.exists():
        return {"status": "MISSING", "symbols": {}}
    return json.loads(registry_path.read_text(encoding="utf-8"))


def symbol_is_validated(symbol: str, registry: dict[str, Any]) -> bool:
    record = registry.get("symbols", {}).get(symbol.upper(), {})
    return bool(record.get("admitted", False))


def evaluate_ltf_signal(
    *,
    symbol: str,
    signal_end: datetime,
    m15: Any,
    h1: Any,
    d1: Any,
    registry: dict[str, Any],
    config: LtfConfig = LtfConfig(),
) -> LtfDecision:
    """Evaluate one completed M15 candle with completed H1/D1 context."""
    if not symbol_is_validated(symbol, registry):
        return LtfDecision(False, f"{symbol} M15 family is not broker-data validated")
    if signal_end.hour not in config.allowed_hours_utc:
        return LtfDecision(False, "outside validated UTC session")

    m15_open = _get(m15, "open")
    m15_close = _get(m15, "close")
    m15_high = _get(m15, "high")
    m15_low = _get(m15, "low")
    m15_atr = _get(m15, "atr14")
    h1_close = _get(h1, "close")
    h1_ema20 = _get(h1, "ema20")
    h1_ema50 = _get(h1, "ema50")
    h1_adx = _get(h1, "adx14")
    d1_close = _get(d1, "close")
    d1_ema20 = _get(d1, "ema20")
    d1_ema50 = _get(d1, "ema50")

    candle_range = max(m15_high - m15_low, 0.0)
    if candle_range <= 0 or m15_atr <= 0:
        return LtfDecision(False, "invalid completed M15 candle")
    body_ratio = abs(m15_close - m15_open) / candle_range
    if body_ratio < config.minimum_body_ratio:
        return LtfDecision(False, "M15 body quality below threshold")
    if h1_adx < config.minimum_h1_adx:
        return LtfDecision(False, "H1 trend strength below threshold")

    long_regime = d1_close > d1_ema20 > d1_ema50 and h1_close > h1_ema20 > h1_ema50
    short_regime = d1_close < d1_ema20 < d1_ema50 and h1_close < h1_ema20 < h1_ema50
    long_pullback = m15_low <= h1_ema20 + config.pullback_atr * m15_atr and m15_close > h1_ema20
    short_pullback = m15_high >= h1_ema20 - config.pullback_atr * m15_atr and m15_close < h1_ema20

    if long_regime and long_pullback and m15_close > m15_open:
        return LtfDecision(True, "validated M15/H1/D1 continuation", 1, config.risk_percent)
    if short_regime and short_pullback and m15_close < m15_open:
        return LtfDecision(True, "validated M15/H1/D1 continuation", -1, config.risk_percent)
    return LtfDecision(False, "no aligned M15 pullback continuation")
