"""V14.15 unified reasoning for dual V12/ICT symbol coverage.

Every configured symbol keeps both a V12 and ICT engine family active.  The
existing V14.14 decision remains authoritative for validated trades.  When an
engine would otherwise be entirely shadowed, narrowly defined probation
profiles can receive bounded risk so that the live system can collect broker-net
evidence instead of permanently disabling the mode.

The layer is deterministic, uses only pre-entry fields, and never raises risk
above the frozen strategy allocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_14_extended_cost_profile import (
    ExtendedCostRegimeConfig,
    extended_cost_regime_decision,
)

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")

DUAL_ENGINE_REGISTRY: dict[str, dict[str, tuple[str, ...]]] = {
    "GBPUSD": {
        "V12": ("GBPUSD_V10_PRECISION", "GBPUSD_SWING_RETEST"),
        "ICT": ("ICT_V14_3_GBPUSD",),
    },
    "EURUSD": {
        "V12": ("EURUSD_SWING_CORE", "EURUSD_SWING_RETEST"),
        "ICT": ("EURUSD_ICT_LIQUIDITY",),
    },
    "GBPJPY": {
        "V12": ("GBPJPY_SWING_CORE",),
        "ICT": ("ICT_V14_3_GBPJPY",),
    },
    "AUDUSD": {
        "V12": ("AUDUSD_TREND_PULLBACK",),
        "ICT": ("AUDUSD_ICT_ASIA_LONDON",),
    },
    "USDJPY": {
        "V12": ("USDJPY_SAFE_HAVEN_BREAKOUT",),
        "ICT": ("USDJPY_ICT_SESSION_SWEEP",),
    },
}


@dataclass(frozen=True)
class ProbationProfile:
    name: str
    risk_cap_percent: float
    maximum_cost_r: float


@dataclass(frozen=True)
class RollingEvidence:
    trades: int
    mean_r: float
    profit_factor: float


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    elif hasattr(value, "to_pydatetime"):
        stamp = value.to_pydatetime()
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _normal_side(value: Any) -> str:
    side = str(value).upper()
    if side in {"1", "1.0", "LONG"}:
        return "BUY"
    if side in {"-1", "-1.0", "SHORT"}:
        return "SELL"
    return side


def probation_profile(
    *,
    symbol: str,
    engine: str,
    mode: str,
    side: str,
    entry_time: Any,
) -> ProbationProfile | None:
    """Return a frozen, pre-entry recovery profile for a shadowed mode."""
    symbol = str(symbol).upper()
    engine = str(engine)
    mode = str(mode).upper()
    side = _normal_side(side)
    stamp = _utc(entry_time)
    hour = stamp.hour
    weekday = stamp.weekday()

    # Small Friday retest sleeve: observation risk only because the sample is
    # limited even though its cost-adjusted aggregate is positive.
    if (
        symbol == "EURUSD"
        and mode == "V12"
        and engine == "EURUSD_SWING_RETEST"
        and weekday == 4
    ):
        return ProbationProfile("EURUSD_FRIDAY_RETEST", 0.025, 0.10)

    # USDJPY V12 recovery hours.  Risk is capped below the original 0.15% tier
    # until broker-net evidence confirms the historical subset.
    if (
        symbol == "USDJPY"
        and mode == "V12"
        and engine == "USDJPY_SAFE_HAVEN_BREAKOUT"
        and hour in {0, 16}
    ):
        return ProbationProfile("USDJPY_V12_00_16UTC", 0.050, 0.10)

    # EURUSD ICT has positive aggregate evidence at the extended 0.28R cost,
    # but receives probation risk above the V14.14 0.23R standard ceiling.
    if symbol == "EURUSD" and mode == "ICT" and engine == "EURUSD_ICT_LIQUIDITY":
        return ProbationProfile("EURUSD_ICT_EXTENDED_PROBATION", 0.100, 0.28)

    # AUDUSD hour 10 was the cost-negative concentration.  Other selected H1
    # entries remain available at probation risk through the extreme tier.
    if (
        symbol == "AUDUSD"
        and mode == "ICT"
        and engine == "AUDUSD_ICT_ASIA_LONDON"
        and hour != 10
    ):
        return ProbationProfile("AUDUSD_ICT_EXCLUDE_10UTC", 0.100, 0.28)

    # USDJPY ICT quality subset: buys plus the 16:00/18:00 UTC sell windows.
    if (
        symbol == "USDJPY"
        and mode == "ICT"
        and engine == "USDJPY_ICT_SESSION_SWEEP"
        and (side == "BUY" or hour in {16, 18})
    ):
        return ProbationProfile("USDJPY_ICT_BUY_OR_16_18UTC", 0.100, 0.28)

    return None


def unified_cost_reasoning_decision(
    *,
    symbol: str,
    engine: str,
    setup: str,
    mode: str,
    side: str,
    entry_time: Any,
    base_risk_percent: float,
    all_in_cost: float,
    target_r: float,
    config: ExtendedCostRegimeConfig,
) -> CostRegimeDecision:
    """Apply V14.14 first, then bounded dual-engine probation when eligible."""
    existing = extended_cost_regime_decision(
        symbol=symbol,
        engine=engine,
        setup=setup,
        mode=mode,
        side=side,
        entry_time=entry_time,
        base_risk_percent=base_risk_percent,
        all_in_cost=all_in_cost,
        target_r=target_r,
        config=config,
    )
    if existing.funded:
        return existing

    cost = max(0.0, float(all_in_cost))
    target = max(0.0, float(target_r))
    base = max(0.0, float(base_risk_percent))
    if base <= 0 or target <= 0:
        return existing
    if cost / target > config.maximum_cost_fraction_of_target + 1e-12:
        return existing

    profile = probation_profile(
        symbol=symbol,
        engine=engine,
        mode=mode,
        side=side,
        entry_time=entry_time,
    )
    if profile is None or cost > profile.maximum_cost_r + 1e-12:
        return existing

    risk = min(base, profile.risk_cap_percent)
    return CostRegimeDecision(
        funded=risk > 0,
        regime="DUAL_ENGINE_PROBATION",
        risk_percent=risk,
        reason=f"{profile.name}: bounded dual-engine evidence collection",
        all_in_cost_r=cost,
        target_r=target,
    )


def rolling_evidence(values: Iterable[float], window: int = 40) -> RollingEvidence:
    results = [float(value) for value in values][-max(1, int(window)) :]
    if not results:
        return RollingEvidence(0, 0.0, 1.0)
    gross_profit = sum(value for value in results if value > 0)
    gross_loss = -sum(value for value in results if value < 0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (3.0 if gross_profit > 0 else 1.0)
    return RollingEvidence(
        trades=len(results),
        mean_r=sum(results) / len(results),
        profit_factor=profit_factor,
    )


def evidence_multiplier(
    engine_results: Iterable[float],
    symbol_mode_results: Iterable[float],
    minimum_trades: int = 8,
) -> tuple[float, str]:
    """Return a conservative live multiplier using broker-net closed results."""
    engine = rolling_evidence(engine_results)
    symbol_mode = rolling_evidence(symbol_mode_results, window=60)
    mature = [item for item in (engine, symbol_mode) if item.trades >= minimum_trades]
    if not mature:
        return 1.0, "INSUFFICIENT_LIVE_SAMPLE"

    if any(item.mean_r <= -0.15 or item.profit_factor < 0.75 for item in mature):
        return 0.0, "LIVE_EDGE_FAILED"
    if any(item.mean_r <= -0.05 or item.profit_factor < 0.95 for item in mature):
        return 0.25, "LIVE_EDGE_DEFENSIVE"
    if all(item.mean_r >= 0.10 and item.profit_factor >= 1.15 for item in mature):
        return 1.0, "LIVE_EDGE_CONFIRMED"
    return 0.65, "LIVE_EDGE_MIXED"


def validate_dual_engine_registry() -> None:
    if set(DUAL_ENGINE_REGISTRY) != set(SYMBOLS):
        raise RuntimeError("Dual-engine registry does not cover all five symbols")
    for symbol, modes in DUAL_ENGINE_REGISTRY.items():
        if set(modes) != {"V12", "ICT"}:
            raise RuntimeError(f"{symbol} must contain both V12 and ICT modes")
        if not modes["V12"] or not modes["ICT"]:
            raise RuntimeError(f"{symbol} has an empty engine family")


validate_dual_engine_registry()
