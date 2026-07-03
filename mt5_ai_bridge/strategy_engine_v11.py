"""Strategy Engine V11 intraday walk-forward profitability profile.

V11 builds on the V10 profitability allocation, but it deliberately avoids a
blanket risk increase. The profile adds a quality-scored admission layer,
setup-level attribution targets, walk-forward validation requirements and
bounded adaptive risk tiers. It is a research/demo configuration only and must
remain READ_ONLY until broker-native out-of-sample validation passes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class EngineRiskPolicy:
    """Risk policy for one named engine.

    ``base_risk_percent`` is used for normal valid setups. ``strong`` and
    ``exceptional`` tiers are only available when a setup-level quality score
    passes the thresholds configured on ``StrategyEngineV11Profile``.
    """

    engine: str
    base_risk_percent: float
    strong_risk_percent: float
    exceptional_risk_percent: float
    minimum_quality_score: float
    max_trades_per_day: int
    rationale: str

    def risk_for_quality(
        self,
        quality_score: float | None,
        *,
        strong_threshold: float,
        exceptional_threshold: float,
    ) -> float:
        if quality_score is None:
            return self.base_risk_percent
        if quality_score >= exceptional_threshold:
            return self.exceptional_risk_percent
        if quality_score >= strong_threshold:
            return self.strong_risk_percent
        return self.base_risk_percent


@dataclass(frozen=True)
class SetupDiagnostics:
    """Normalized setup-quality inputs in the 0..1 range unless noted.

    ``spread_pips`` and ``atr_pips`` are raw pip values; the quality model uses
    their ratio to penalize expensive low-volatility entries.
    """

    trend_strength: float
    ema_separation: float
    body_quality: float
    volume_confirmation: float
    pullback_quality: float
    session_range_quality: float
    spread_pips: float
    atr_pips: float
    overextension: float = 0.0


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(max(float(value), lower), upper)


def compute_quality_score(diagnostics: SetupDiagnostics) -> float:
    """Return a bounded 0..1 admission score for an intraday candidate.

    The score intentionally rewards repeatable market structure rather than a
    hard-coded hour list. Time windows can still be used as a coarse session
    filter, but this score should decide whether a candidate deserves standard,
    strong or exceptional risk.
    """

    spread_ratio = 1.0 if diagnostics.atr_pips <= 0 else diagnostics.spread_pips / diagnostics.atr_pips
    spread_penalty = _clamp(spread_ratio / 0.12)
    overextension_penalty = _clamp(diagnostics.overextension)
    weighted = (
        0.22 * _clamp(diagnostics.trend_strength)
        + 0.16 * _clamp(diagnostics.ema_separation)
        + 0.15 * _clamp(diagnostics.body_quality)
        + 0.13 * _clamp(diagnostics.volume_confirmation)
        + 0.14 * _clamp(diagnostics.pullback_quality)
        + 0.10 * _clamp(diagnostics.session_range_quality)
        + 0.10 * (1.0 - spread_penalty)
        - 0.12 * overextension_penalty
    )
    return round(_clamp(weighted), 4)


@dataclass(frozen=True)
class StrategyEngineV11Profile:
    """V11 research boundaries for the $50/week objective.

    The weekly target is stored as a research target only. It must not be used
    to force trades or increase size after losses.
    """

    initial_balance: float = 5_000.0
    target_weekly_profit_dollars: float = 50.0
    mode: str = "READ_ONLY"
    max_positions: int = 4
    max_open_risk_percent: float = 0.90
    aligned_gbp_cap_percent: float = 0.60
    mixed_gbp_cap_percent: float = 0.45
    daily_new_risk_percent: float = 0.75
    max_risk_per_trade_percent: float = 0.40
    quality_threshold: float = 0.62
    strong_quality_threshold: float = 0.74
    exceptional_quality_threshold: float = 0.84
    minimum_oos_profit_factor: float = 1.40
    minimum_walk_forward_pass_rate: float = 0.70
    minimum_setup_trades_for_promotion: int = 30
    realized_drawdown_limit_percent: float = 6.0
    pause_drawdown_percent: float = 10.0
    loss_cut_risk_multiplier: float = 0.50
    risk_policies: tuple[EngineRiskPolicy, ...] = (
        EngineRiskPolicy(
            engine="GBPUSD_SATELLITE_V3",
            base_risk_percent=0.30,
            strong_risk_percent=0.35,
            exceptional_risk_percent=0.40,
            minimum_quality_score=0.62,
            max_trades_per_day=2,
            rationale="Primary intraday profit engine; promoted only by quality score and forward evidence.",
        ),
        EngineRiskPolicy(
            engine="EURUSD_SATELLITE_V7",
            base_risk_percent=0.30,
            strong_risk_percent=0.35,
            exceptional_risk_percent=0.40,
            minimum_quality_score=0.64,
            max_trades_per_day=2,
            rationale="Non-GBP diversification engine with historically strong synchronized PF.",
        ),
        EngineRiskPolicy(
            engine="GBPJPY_SATELLITE_V7",
            base_risk_percent=0.25,
            strong_risk_percent=0.35,
            exceptional_risk_percent=0.40,
            minimum_quality_score=0.66,
            max_trades_per_day=1,
            rationale="Higher-volatility GBP exposure; stricter daily count and GBP caps required.",
        ),
        EngineRiskPolicy(
            engine="GBPUSD_SWING_V6",
            base_risk_percent=0.25,
            strong_risk_percent=0.35,
            exceptional_risk_percent=0.40,
            minimum_quality_score=0.70,
            max_trades_per_day=1,
            rationale="Low-frequency support engine; must not reserve capacity ahead of stronger intraday signals.",
        ),
    )

    def _policy_map(self) -> Mapping[str, EngineRiskPolicy]:
        return {policy.engine: policy for policy in self.risk_policies}

    def policy_for(self, engine: str) -> EngineRiskPolicy:
        normalized = engine.upper()
        aliases = {
            "GBPUSD_SATELLITE_V2": "GBPUSD_SATELLITE_V3",
            "GBPUSD_LONDON_PULLBACK_V2": "GBPUSD_SATELLITE_V3",
            "GBPUSD_NEW_YORK_RETEST_V2": "GBPUSD_SATELLITE_V3",
        }
        normalized = aliases.get(normalized, normalized)
        try:
            return self._policy_map()[normalized]
        except KeyError as exc:
            raise KeyError(f"Unknown V11 engine: {engine}") from exc

    def risk_for(self, engine: str, quality_score: float | None = None) -> float:
        policy = self.policy_for(engine)
        risk = policy.risk_for_quality(
            quality_score,
            strong_threshold=self.strong_quality_threshold,
            exceptional_threshold=self.exceptional_quality_threshold,
        )
        return min(risk, self.max_risk_per_trade_percent)

    def admit_quality(self, engine: str, quality_score: float) -> bool:
        policy = self.policy_for(engine)
        return quality_score >= max(self.quality_threshold, policy.minimum_quality_score)

    def validate(self) -> None:
        if self.mode != "READ_ONLY":
            raise ValueError("V11 must default to READ_ONLY")
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.target_weekly_profit_dollars <= 0:
            raise ValueError("target weekly profit must be positive")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least one")
        if not 0 < self.max_open_risk_percent <= 1.0:
            raise ValueError("max_open_risk_percent must stay within (0, 1]")
        if self.aligned_gbp_cap_percent > self.max_open_risk_percent:
            raise ValueError("aligned GBP cap cannot exceed total open-risk cap")
        if self.mixed_gbp_cap_percent > self.aligned_gbp_cap_percent:
            raise ValueError("mixed GBP cap cannot exceed aligned GBP cap")
        if self.daily_new_risk_percent > self.max_open_risk_percent:
            raise ValueError("daily new risk cannot exceed max open risk")
        if not 0 < self.loss_cut_risk_multiplier <= 1.0:
            raise ValueError("loss cut multiplier must be within (0, 1]")
        if not 0 < self.minimum_walk_forward_pass_rate <= 1.0:
            raise ValueError("walk-forward pass rate must be within (0, 1]")
        if not self.quality_threshold <= self.strong_quality_threshold <= self.exceptional_quality_threshold:
            raise ValueError("quality thresholds must be nondecreasing")
        names = [policy.engine for policy in self.risk_policies]
        if len(names) != len(set(names)):
            raise ValueError("risk policies must be unique")
        for policy in self.risk_policies:
            if policy.base_risk_percent <= 0:
                raise ValueError(f"{policy.engine} base risk must be positive")
            if policy.exceptional_risk_percent > self.max_risk_per_trade_percent:
                raise ValueError(f"{policy.engine} exceeds max per-trade risk")
            if not policy.base_risk_percent <= policy.strong_risk_percent <= policy.exceptional_risk_percent:
                raise ValueError(f"{policy.engine} risk tiers must be nondecreasing")
            if policy.minimum_quality_score < self.quality_threshold:
                raise ValueError(f"{policy.engine} minimum quality cannot undercut portfolio threshold")


V11_PROFILE = StrategyEngineV11Profile()
V11_PROFILE.validate()
