"""V14.16 cost-efficient quality allocation.

The V14.15 reasoning decision remains the admission authority.  This layer may
raise a *fully approved* candidate to the existing 0.80% single-trade ceiling
only for frozen, cost-resilient profiles.  It never revives a shadow trade and
never overrides observation, probation, loss-pressure, expectancy or drawdown
reductions.

Historical profile authorization is used only by the exact replay.  Live risk
uplift additionally requires mature broker-net evidence for both the individual
engine and its symbol/mode sleeve.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .v14_13_cost_regime_profile import CostRegimeDecision, strict_retail_profile
from .v14_15_unified_reasoning import rolling_evidence

QUALITY_RISK_PERCENT = 0.80
FULL_STRENGTH_TOLERANCE = 1e-9

NON_UPLIFT_REGIMES = frozenset(
    {
        "OBSERVATION",
        "DUAL_ENGINE_PROBATION",
        "REASONING_REDUCED",
        "REASONING_DEFENSIVE",
        "SHADOW",
    }
)


@dataclass(frozen=True)
class QualityProfile:
    name: str
    maximum_cost_r: float
    risk_percent: float = QUALITY_RISK_PERCENT


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


def quality_profile(
    *,
    symbol: str,
    engine: str,
    setup: str,
    mode: str,
    side: str,
    entry_time: Any,
) -> QualityProfile | None:
    """Return the frozen profile selected before the V14.16 replay.

    These rules use only fields known when a signal is created.  GBP ICT is
    restricted to the already validated strict setup/time/side profile.  The
    AUDUSD ICT 10:00 UTC concentration is excluded.
    """
    symbol = str(symbol).upper()
    engine = str(engine)
    mode = str(mode).upper()
    stamp = _utc(entry_time)

    if mode == "V12":
        if engine == "GBPUSD_V10_PRECISION" and symbol == "GBPUSD":
            return QualityProfile("GBPUSD_V10_PRECISION_QUALITY", 0.10)
        if engine == "EURUSD_SWING_CORE" and symbol == "EURUSD":
            return QualityProfile("EURUSD_SWING_CORE_QUALITY", 0.10)
        if engine == "AUDUSD_TREND_PULLBACK" and symbol == "AUDUSD":
            return QualityProfile("AUDUSD_TREND_PULLBACK_QUALITY", 0.10)
        return None

    if mode != "ICT":
        return None

    if (
        symbol == "GBPUSD"
        and engine == "ICT_V14_3_GBPUSD"
        and strict_retail_profile(symbol, setup, side, stamp)
    ):
        return QualityProfile("GBPUSD_STRICT_ICT_QUALITY", 0.18)
    if symbol == "EURUSD" and engine == "EURUSD_ICT_LIQUIDITY":
        return QualityProfile("EURUSD_ICT_LIQUIDITY_QUALITY", 0.18)
    if (
        symbol == "AUDUSD"
        and engine == "AUDUSD_ICT_ASIA_LONDON"
        and stamp.hour != 10
    ):
        return QualityProfile("AUDUSD_ICT_EXCLUDE_10UTC_QUALITY", 0.18)
    return None


def quality_risk_target(
    *,
    symbol: str,
    engine: str,
    setup: str,
    mode: str,
    side: str,
    entry_time: Any,
    all_in_cost_r: float,
    nominal_risk_percent: float,
    current_risk_percent: float,
    current_decision: CostRegimeDecision,
    historical_profile_authorized: bool,
    live_evidence_authorized: bool = False,
) -> tuple[float | None, str]:
    """Return an uplift target without undoing any existing reduction."""
    if not current_decision.funded or current_decision.is_shadow:
        return None, "CURRENT_DECISION_NOT_FUNDED"
    if float(all_in_cost_r) <= 1e-12:
        return None, "ZERO_COST_PARITY_UNCHANGED"
    if str(current_decision.regime).upper() in NON_UPLIFT_REGIMES:
        return None, "REDUCED_OR_PROBATION_REGIME"

    nominal = max(0.0, float(nominal_risk_percent))
    current = max(0.0, float(current_risk_percent))
    if nominal <= 0 or current + FULL_STRENGTH_TOLERANCE < nominal:
        return None, "EXISTING_RISK_REDUCTION_PRESERVED"

    profile = quality_profile(
        symbol=symbol,
        engine=engine,
        setup=setup,
        mode=mode,
        side=side,
        entry_time=entry_time,
    )
    if profile is None:
        return None, "NO_FROZEN_QUALITY_PROFILE"
    if float(all_in_cost_r) > profile.maximum_cost_r + 1e-12:
        return None, "QUALITY_PROFILE_COST_LIMIT"
    if not historical_profile_authorized and not live_evidence_authorized:
        return None, "QUALITY_EVIDENCE_NOT_AUTHORIZED"

    target = min(QUALITY_RISK_PERCENT, max(current, profile.risk_percent))
    if target <= current + 1e-12:
        return None, "ALREADY_AT_QUALITY_TARGET"
    return target, profile.name


def live_quality_evidence(
    engine_results: Iterable[float],
    symbol_mode_results: Iterable[float],
    *,
    minimum_engine_trades: int = 12,
    minimum_symbol_mode_trades: int = 16,
) -> tuple[bool, str]:
    """Require mature, positive broker-net evidence before live uplift."""
    engine = rolling_evidence(engine_results, window=40)
    sleeve = rolling_evidence(symbol_mode_results, window=60)
    if engine.trades < minimum_engine_trades:
        return False, "ENGINE_SAMPLE_BELOW_12"
    if sleeve.trades < minimum_symbol_mode_trades:
        return False, "SYMBOL_MODE_SAMPLE_BELOW_16"
    if engine.mean_r < 0.10 or engine.profit_factor < 1.15:
        return False, "ENGINE_EDGE_NOT_CONFIRMED"
    if sleeve.mean_r < 0.10 or sleeve.profit_factor < 1.15:
        return False, "SYMBOL_MODE_EDGE_NOT_CONFIRMED"
    return True, "BROKER_NET_QUALITY_CONFIRMED"


def apply_quality_allocation(
    current: CostRegimeDecision,
    *,
    target_risk_percent: float | None,
    reason: str,
) -> CostRegimeDecision:
    if target_risk_percent is None:
        return current
    risk = min(QUALITY_RISK_PERCENT, max(0.0, float(target_risk_percent)))
    return CostRegimeDecision(
        funded=risk > 0,
        regime="QUALITY_ALLOCATED",
        risk_percent=risk,
        reason=f"{current.reason}; {reason}; capped at {QUALITY_RISK_PERCENT:.2f}%",
        all_in_cost_r=current.all_in_cost_r,
        target_r=current.target_r,
    )


def validate_quality_profiles() -> None:
    assert quality_profile(
        symbol="GBPUSD",
        engine="GBPUSD_V10_PRECISION",
        setup="PRIMARY_16UTC_BREAKOUT",
        mode="V12",
        side="BUY",
        entry_time="2026-07-18T16:00:00+00:00",
    )
    assert quality_profile(
        symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="breakout_15_fade",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-18T12:00:00+00:00",
    )
    assert quality_profile(
        symbol="AUDUSD",
        engine="AUDUSD_ICT_ASIA_LONDON",
        setup="audusd_ict_asia_london",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-18T10:00:00+00:00",
    ) is None


validate_quality_profiles()
