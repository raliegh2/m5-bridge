"""Frozen Strategy Engine V8 portfolio profile.

This module records the engine/risk contract that produced the synchronized
one-year V8 result. It deliberately keeps strategy identity separate from the
live broker adapter so that the same limits can be used by the replay tool,
unit tests, and a future multi-symbol execution loop.

The profile is intentionally conservative: it ports the tested GBPUSD Swing V6
runner into the V8 portfolio instead of silently re-optimizing it. Parameter
changes require a new profile/version and a fresh validation run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence


EURUSD_SATELLITE_V7 = "EURUSD_SATELLITE_V7"
GBPJPY_SATELLITE_V7 = "GBPJPY_SATELLITE_V7"
GBPUSD_SATELLITE_V2 = "GBPUSD_SATELLITE_V2"
GBPUSD_SWING_V6 = "GBPUSD_SWING_V6"


@dataclass(frozen=True)
class ExitProfile:
    """Trade-management contract for an engine."""

    atr_stop_multiple: float | None = None
    min_stop_pips: float | None = None
    max_stop_pips: float | None = None
    partial_fraction: float | None = None
    partial_at_r: float | None = None
    move_to_break_even_after_partial: bool = False
    final_target_r: float | None = None
    trailing_atr_multiple: float | None = None
    maximum_hold_bars: int | None = None
    maximum_hold_timeframe: str | None = None


@dataclass(frozen=True)
class EngineSpec:
    """Immutable identity and risk settings for one V8 engine."""

    name: str
    symbol: str
    risk_percent: float
    entry_timeframe: str
    observation_timeframes: tuple[str, ...] = ()
    anchor_timeframes: tuple[str, ...] = ()
    max_positions: int = 1
    session: str = "engine-defined"
    exit_profile: ExitProfile = field(default_factory=ExitProfile)

    def __post_init__(self) -> None:
        if self.risk_percent <= 0:
            raise ValueError("risk_percent must be positive")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least one")


@dataclass(frozen=True)
class PortfolioRules:
    """Shared portfolio controls used by the synchronized V8 replay."""

    initial_balance: float = 5_000.0
    max_positions: int = 3
    max_open_risk_percent: float = 0.75
    gbp_aligned_cap_percent: float = 0.75
    gbp_mixed_cap_percent: float = 0.50
    daily_loss_limit: float = 250.0
    weekly_loss_percent: float = 4.0
    total_loss_limit: float = 500.0
    drawdown_throttle_percent: float = 6.0
    drawdown_pause_percent: float = 10.0
    allow_aligned_gbpusd_swing_and_satellite: bool = True


@dataclass(frozen=True)
class OpenRisk:
    """Minimal open-position view needed for admission control."""

    engine: str
    symbol: str
    side: int
    risk_percent: float

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be -1 (short) or 1 (long)")
        if self.risk_percent <= 0:
            raise ValueError("risk_percent must be positive")


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    projected_open_risk_percent: float


GBPUSD_SWING_EXIT = ExitProfile(
    atr_stop_multiple=1.5,
    min_stop_pips=20.0,
    max_stop_pips=150.0,
    partial_fraction=0.50,
    partial_at_r=1.0,
    move_to_break_even_after_partial=True,
    final_target_r=3.0,
    trailing_atr_multiple=2.5,
    maximum_hold_bars=72,
    maximum_hold_timeframe="H4",
)


ENGINE_SPECS: Mapping[str, EngineSpec] = {
    EURUSD_SATELLITE_V7: EngineSpec(
        name=EURUSD_SATELLITE_V7,
        symbol="EURUSD",
        risk_percent=0.25,
        entry_timeframe="M15",
        anchor_timeframes=("H1",),
        session="London/New York",
    ),
    GBPJPY_SATELLITE_V7: EngineSpec(
        name=GBPJPY_SATELLITE_V7,
        symbol="GBPJPY",
        risk_percent=0.25,
        entry_timeframe="M15",
        anchor_timeframes=("H1",),
        session="London/New York",
    ),
    GBPUSD_SATELLITE_V2: EngineSpec(
        name=GBPUSD_SATELLITE_V2,
        symbol="GBPUSD",
        risk_percent=0.25,
        entry_timeframe="M15",
        anchor_timeframes=("M30",),
        session="London/New York",
    ),
    GBPUSD_SWING_V6: EngineSpec(
        name=GBPUSD_SWING_V6,
        symbol="GBPUSD",
        risk_percent=0.50,
        entry_timeframe="M30",
        observation_timeframes=("M1", "M5"),
        anchor_timeframes=("H4", "D1"),
        # The historical V6 replay contains one active core runner at a time.
        # A second swing entry must not be enabled until it is separately tested.
        max_positions=1,
        session="London/New York; no thin-session initiation",
        exit_profile=GBPUSD_SWING_EXIT,
    ),
}

DEFAULT_RULES = PortfolioRules()


def engine_spec(name: str) -> EngineSpec:
    """Return a frozen engine specification or raise a useful error."""

    try:
        return ENGINE_SPECS[name]
    except KeyError as exc:
        known = ", ".join(sorted(ENGINE_SPECS))
        raise KeyError(f"Unknown V8 engine {name!r}. Known engines: {known}") from exc


def _is_gbp_exposure(position: OpenRisk) -> bool:
    return "GBP" in position.symbol.upper()


def _is_gbpusd_pair(position: OpenRisk) -> bool:
    return position.symbol.upper() == "GBPUSD"


def _engine_position_count(open_positions: Iterable[OpenRisk], engine: str) -> int:
    return sum(1 for position in open_positions if position.engine == engine)


def evaluate_candidate(
    open_positions: Sequence[OpenRisk],
    candidate: OpenRisk,
    rules: PortfolioRules = DEFAULT_RULES,
) -> RiskDecision:
    """Apply V8's shared position and currency-risk admission controls.

    Risk percentages are expressed in account-percent units: ``0.25`` means
    0.25% of current balance, not 25%.
    """

    spec = engine_spec(candidate.engine)
    if candidate.symbol.upper() != spec.symbol:
        return RiskDecision(False, "engine_symbol_mismatch", sum_risk(open_positions))

    if _engine_position_count(open_positions, candidate.engine) >= spec.max_positions:
        return RiskDecision(False, "engine_position_limit", sum_risk(open_positions))

    if len(open_positions) >= rules.max_positions:
        return RiskDecision(False, "max_positions", sum_risk(open_positions))

    projected = sum_risk(open_positions) + candidate.risk_percent
    if projected > rules.max_open_risk_percent + 1e-12:
        return RiskDecision(False, "max_open_risk", projected)

    gbp_positions = [position for position in open_positions if _is_gbp_exposure(position)]
    if _is_gbp_exposure(candidate):
        projected_gbp = sum(position.risk_percent for position in gbp_positions) + candidate.risk_percent
        sides = {position.side for position in gbp_positions}
        sides.add(candidate.side)
        mixed = len(sides) > 1
        cap = rules.gbp_mixed_cap_percent if mixed else rules.gbp_aligned_cap_percent
        if projected_gbp > cap + 1e-12:
            return RiskDecision(False, "gbp_currency_risk_cap", projected)

        if _is_gbpusd_pair(candidate):
            gbpusd_open = [position for position in gbp_positions if _is_gbpusd_pair(position)]
            has_swing = any(position.engine == GBPUSD_SWING_V6 for position in gbpusd_open)
            has_satellite = any(position.engine == GBPUSD_SATELLITE_V2 for position in gbpusd_open)
            candidate_is_swing = candidate.engine == GBPUSD_SWING_V6
            candidate_is_satellite = candidate.engine == GBPUSD_SATELLITE_V2
            pairing = (has_swing and candidate_is_satellite) or (
                has_satellite and candidate_is_swing
            )
            if pairing and not rules.allow_aligned_gbpusd_swing_and_satellite:
                return RiskDecision(False, "gbpusd_cross_engine_pairing_disabled", projected)

    return RiskDecision(True, "allowed", projected)


def sum_risk(open_positions: Iterable[OpenRisk]) -> float:
    return float(sum(position.risk_percent for position in open_positions))
