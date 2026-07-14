"""Approved engine registry for the final five-symbol demo portfolio.

Only engines backed by the frozen V12 research replay are executable here.
V11 intraday engines are intentionally excluded until a merged chronological
replay and broker-native forward validation exist.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class FinalEngine:
    symbol: str
    engine: str
    setups: tuple[str, ...]
    base_risk_percent: tuple[float, ...]
    timeframe: str
    adaptive: bool
    evidence: str


FINAL_ENGINES: Mapping[str, FinalEngine] = {
    "GBPUSD_V10_PRECISION": FinalEngine(
        symbol="GBPUSD",
        engine="GBPUSD_V10_PRECISION",
        setups=(
            "PRIMARY_16UTC_BREAKOUT",
            "SECONDARY_12UTC_BREAKOUT",
            "GBPUSD_SWING_V5_PULLBACK_ADDON",
        ),
        base_risk_percent=(0.20, 0.40, 0.50),
        timeframe="H4",
        adaptive=False,
        evidence="V12 final maximum-history replay",
    ),
    "GBPUSD_SWING_RETEST": FinalEngine(
        symbol="GBPUSD",
        engine="GBPUSD_SWING_RETEST",
        setups=("H4_BREAKOUT_RETEST",),
        base_risk_percent=(0.15,),
        timeframe="H4",
        adaptive=False,
        evidence="V12 final maximum-history replay",
    ),
    "EURUSD_SWING_CORE": FinalEngine(
        symbol="EURUSD",
        engine="EURUSD_SWING_CORE",
        setups=("H4_DONCHIAN_BREAKOUT",),
        base_risk_percent=(0.25,),
        timeframe="H4",
        adaptive=False,
        evidence="V12 final maximum-history replay",
    ),
    "EURUSD_SWING_RETEST": FinalEngine(
        symbol="EURUSD",
        engine="EURUSD_SWING_RETEST",
        setups=("H1_BREAKOUT_RETEST",),
        base_risk_percent=(0.10,),
        timeframe="H1",
        adaptive=True,
        evidence="V12 final maximum-history replay with adaptive guard",
    ),
    "GBPJPY_SWING_CORE": FinalEngine(
        symbol="GBPJPY",
        engine="GBPJPY_SWING_CORE",
        setups=("H4_DONCHIAN_BREAKOUT",),
        base_risk_percent=(0.15,),
        timeframe="H4",
        adaptive=True,
        evidence="V12 final replay plus V14.7 GBPJPY guard validation",
    ),
    "AUDUSD_TREND_PULLBACK": FinalEngine(
        symbol="AUDUSD",
        engine="AUDUSD_TREND_PULLBACK",
        setups=("D1_H4_EMA_PULLBACK_04_08UTC",),
        base_risk_percent=(0.25,),
        timeframe="H4",
        adaptive=False,
        evidence="V12 validated-assets replay",
    ),
    "USDJPY_SAFE_HAVEN_BREAKOUT": FinalEngine(
        symbol="USDJPY",
        engine="USDJPY_SAFE_HAVEN_BREAKOUT",
        setups=("D1_H4_40BAR_BREAKOUT",),
        base_risk_percent=(0.25,),
        timeframe="H4",
        adaptive=True,
        evidence="V12 validated-assets replay with adaptive guard",
    ),
}

FINAL_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")


def engines_for_symbol(symbol: str) -> tuple[FinalEngine, ...]:
    value = symbol.upper()
    return tuple(engine for engine in FINAL_ENGINES.values() if engine.symbol == value)


def registry_summary() -> dict:
    return {
        "symbols": list(FINAL_SYMBOLS),
        "engine_count": len(FINAL_ENGINES),
        "engines": {name: asdict(engine) for name, engine in FINAL_ENGINES.items()},
        "excluded": {
            "V11_INTRADAY": (
                "Not executable in the final registry: standalone research exists, "
                "but no merged chronological five-symbol replay or broker-native "
                "forward validation has approved it for execution."
            )
        },
    }
