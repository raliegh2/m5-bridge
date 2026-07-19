"""V14.18 hierarchical, regime-aware meta-labeler.

V14.18 is stacked on V14.17.  It does not generate signals, change exits, add
risk or revive a shadow candidate.  The layer assigns each candidate a market
regime and a hierarchical meta-label using only information available before
entry plus broker-net R results from trades that have already closed.

The first stable policy is deliberately conservative:

* every candidate is classified as TREND, RANGE, TRANSITION or DISLOCATED;
* a sequential empirical-Bayes posterior is calculated across broad-to-local
  evidence nodes;
* FULL is the default and no label may increase inherited risk;
* non-FULL labels are activated only for V12 candidates already reduced by
  V14.17 and carrying mature, materially negative direction evidence;
* ICT/range candidates remain FULL while the framework establishes stability;
* the proposed range mean-reversion engine is not implemented in V14.18.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import prod
from typing import Any, Mapping

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_17_cost_adjusted_consensus import (
    CostAdjustedConsensusController,
    RollingNetEvidence,
    normalized_direction,
    utc_session,
)

META_WINDOW = 100
HIERARCHY_PRIOR_STRENGTH = 24.0
MINIMUM_RISK_PERCENT = 0.025
HIERARCHICAL_POSITIVE_OVERRIDE_R = 0.75

FULL_MULTIPLIER = 1.00
REDUCED_MULTIPLIER = 0.50
OBSERVATION_MULTIPLIER = 0.25
SHADOW_MULTIPLIER = 0.00

SHADOW_MIN_TRADES = 20
SHADOW_MAX_MEAN_R = -0.20
SHADOW_MAX_PROFIT_FACTOR = 0.80
OBSERVATION_MIN_TRADES = 24
OBSERVATION_MAX_MEAN_R = -0.16
OBSERVATION_MAX_PROFIT_FACTOR = 0.85
REDUCED_MIN_TRADES = 30
REDUCED_MAX_MEAN_R = -0.12
REDUCED_MAX_PROFIT_FACTOR = 0.90

MARKET_REGIMES = frozenset({"TREND", "RANGE", "TRANSITION", "DISLOCATED"})
META_LABELS = frozenset({"FULL", "REDUCED", "OBSERVATION", "SHADOW"})

LIVE_MIN_DIRECTION_TRADES = 40
LIVE_MIN_ENGINE_TRADES = 50
LIVE_MIN_SYMBOL_MODE_TRADES = 60


@dataclass(frozen=True)
class HierarchicalPosterior:
    score_r: float
    confidence: float
    mature_negative_nodes: int
    node_count: int
    effective_trades: int
    nodes: dict[str, dict[str, float | int]]


@dataclass(frozen=True)
class MetaLabelDecision:
    label: str
    multiplier: float
    reason: str


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


def decision_key(
    *,
    entry_time: Any,
    symbol: str,
    engine: str,
    setup: str,
    side: Any,
) -> tuple[str, str, str, str, str]:
    return (
        _utc(entry_time).isoformat(),
        str(symbol).upper(),
        str(engine),
        str(setup),
        str(side).upper(),
    )


def classify_market_regime(
    *,
    mode: str,
    engine: str,
    setup: str,
    consensus: str,
    parent_regime: str,
) -> str:
    """Classify the candidate using fields available before entry.

    The first V14.18 classifier is structural rather than price-fitted.  ICT
    liquidity and sweep engines are RANGE candidates, V12 directional engines
    are TREND candidates, retest/fade or cross-engine conflict is TRANSITION,
    and an inherited defensive state is DISLOCATED.  Price-derived regime
    features can be added later only after feed-parity evidence exists.
    """
    mode = str(mode).upper()
    engine_text = str(engine).upper()
    setup_text = str(setup).upper()
    consensus = str(consensus).upper()
    parent_regime = str(parent_regime).upper()

    if parent_regime == "REASONING_DEFENSIVE":
        return "DISLOCATED"
    if consensus == "CONFLICT":
        return "TRANSITION"
    if "RETEST" in engine_text or "RETEST" in setup_text or "FADE" in setup_text:
        return "TRANSITION"
    if mode == "ICT":
        return "RANGE"
    return "TREND"


def hierarchy_keys(
    *,
    symbol: str,
    engine: str,
    setup: str,
    mode: str,
    side: Any,
    entry_time: Any,
    market_regime: str,
) -> list[tuple[str, tuple[Any, ...]]]:
    stamp = _utc(entry_time)
    direction = normalized_direction(side)
    symbol = str(symbol).upper()
    mode = str(mode).upper()
    regime = str(market_regime).upper()
    return [
        ("global", ("global",)),
        ("mode", ("mode", mode)),
        ("market_regime", ("market_regime", mode, regime)),
        ("symbol_mode", ("symbol_mode", symbol, mode)),
        ("engine_regime", ("engine_regime", str(engine), regime)),
        ("setup_regime", ("setup_regime", str(engine), str(setup), regime)),
        ("direction_regime", ("direction_regime", str(engine), direction, regime)),
        ("session_regime", ("session_regime", symbol, mode, utc_session(stamp.hour), regime)),
        ("hour_regime", ("hour_regime", symbol, mode, stamp.hour, regime)),
    ]


def hierarchical_posterior(
    nodes: list[tuple[str, RollingNetEvidence]],
    *,
    prior_strength: float = HIERARCHY_PRIOR_STRENGTH,
) -> HierarchicalPosterior:
    """Sequentially shrink local net-R evidence toward broader parent nodes."""
    score = 0.0
    reliabilities: list[float] = []
    mature_negative = 0
    effective_trades = 0
    snapshot: dict[str, dict[str, float | int]] = {}

    for name, evidence in nodes:
        n = int(evidence.trades)
        mean_r = float(evidence.mean_r)
        profit_factor = float(evidence.profit_factor)
        if n > 0:
            score = (n * mean_r + float(prior_strength) * score) / (
                n + float(prior_strength)
            )
            reliability = n / (n + float(prior_strength))
            reliabilities.append(reliability)
            effective_trades = max(effective_trades, n)
            if n >= 20 and mean_r < 0.0 and profit_factor < 1.0:
                mature_negative += 1
        snapshot[name] = {
            "trades": n,
            "mean_r": mean_r,
            "profit_factor": profit_factor,
        }

    confidence = 1.0 - prod(1.0 - value for value in reliabilities) if reliabilities else 0.0
    return HierarchicalPosterior(
        score_r=float(score),
        confidence=float(confidence),
        mature_negative_nodes=int(mature_negative),
        node_count=len(nodes),
        effective_trades=int(effective_trades),
        nodes=snapshot,
    )


def meta_label_from_evidence(
    *,
    current: CostRegimeDecision,
    mode: str,
    all_in_cost_r: float,
    market_regime: str,
    posterior: HierarchicalPosterior,
    direction_evidence: Mapping[str, Any],
) -> MetaLabelDecision:
    """Return a no-uplift meta-label using frozen V14.18 thresholds."""
    if not current.funded or current.is_shadow:
        return MetaLabelDecision("SHADOW", SHADOW_MULTIPLIER, "PARENT_NOT_FUNDED")
    if float(all_in_cost_r) <= 1e-12:
        return MetaLabelDecision("FULL", FULL_MULTIPLIER, "ZERO_COST_PARITY_UNCHANGED")
    if str(mode).upper() != "V12":
        return MetaLabelDecision("FULL", FULL_MULTIPLIER, "RANGE_ICT_POLICY_FROZEN_FULL")
    if str(current.regime).upper() != "REASONING_REDUCED":
        return MetaLabelDecision("FULL", FULL_MULTIPLIER, "V14_17_NOT_ALREADY_REDUCED")
    if str(market_regime).upper() not in {"TREND", "TRANSITION"}:
        return MetaLabelDecision("FULL", FULL_MULTIPLIER, "REGIME_NOT_ACTIVE_FOR_META_REDUCTION")
    if posterior.score_r >= HIERARCHICAL_POSITIVE_OVERRIDE_R:
        return MetaLabelDecision("FULL", FULL_MULTIPLIER, "STRONG_POSITIVE_HIERARCHY_OVERRIDE")

    trades = int(direction_evidence.get("trades", 0) or 0)
    mean_r = float(direction_evidence.get("mean_r", 0.0) or 0.0)
    profit_factor = float(direction_evidence.get("profit_factor", 0.0) or 0.0)

    if (
        trades >= SHADOW_MIN_TRADES
        and mean_r < SHADOW_MAX_MEAN_R
        and profit_factor < SHADOW_MAX_PROFIT_FACTOR
    ):
        return MetaLabelDecision(
            "SHADOW",
            SHADOW_MULTIPLIER,
            "MATURE_SEVERE_NEGATIVE_DIRECTION_CONTEXT",
        )
    if (
        trades >= OBSERVATION_MIN_TRADES
        and mean_r < OBSERVATION_MAX_MEAN_R
        and profit_factor < OBSERVATION_MAX_PROFIT_FACTOR
    ):
        return MetaLabelDecision(
            "OBSERVATION",
            OBSERVATION_MULTIPLIER,
            "MATURE_NEGATIVE_DIRECTION_CONTEXT",
        )
    if (
        trades >= REDUCED_MIN_TRADES
        and mean_r < REDUCED_MAX_MEAN_R
        and profit_factor < REDUCED_MAX_PROFIT_FACTOR
    ):
        return MetaLabelDecision(
            "REDUCED",
            REDUCED_MULTIPLIER,
            "WEAK_DIRECTION_CONTEXT",
        )
    return MetaLabelDecision("FULL", FULL_MULTIPLIER, "META_EVIDENCE_NOT_DECISIVE")


def live_hierarchy_authorized(payload: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Require mature reconciled live evidence before any non-FULL label."""
    if not payload:
        return False, "LIVE_HIERARCHY_MISSING"
    if not bool(payload.get("broker_reconciled", False)):
        return False, "LIVE_HIERARCHY_NOT_RECONCILED"
    if not bool(payload.get("chronological", False)):
        return False, "LIVE_HIERARCHY_NOT_CHRONOLOGICAL"
    direction = payload.get("direction", {})
    engine = payload.get("engine", {})
    symbol_mode = payload.get("symbol_mode", {})
    if int(direction.get("trades", 0) or 0) < LIVE_MIN_DIRECTION_TRADES:
        return False, "LIVE_DIRECTION_SAMPLE_BELOW_40"
    if int(engine.get("trades", 0) or 0) < LIVE_MIN_ENGINE_TRADES:
        return False, "LIVE_ENGINE_SAMPLE_BELOW_50"
    if int(symbol_mode.get("trades", 0) or 0) < LIVE_MIN_SYMBOL_MODE_TRADES:
        return False, "LIVE_SYMBOL_MODE_SAMPLE_BELOW_60"
    return True, "LIVE_HIERARCHY_BROKER_NET_CONFIRMED"


class HierarchicalRegimeMetaLabeler:
    """Chronological V14.18 controller wrapped around the V14.17 controller."""

    def __init__(
        self,
        parent: CostAdjustedConsensusController,
        replay: Any | None = None,
    ) -> None:
        self.parent = parent
        self.replay = replay
        self.evidence: dict[tuple[Any, ...], RollingNetEvidence] = {}
        self.events: list[dict[str, Any]] = []
        self.label_counts: dict[str, int] = {label: 0 for label in META_LABELS}
        self.regime_counts: dict[str, int] = {regime: 0 for regime in MARKET_REGIMES}
        self.decisions: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    def _evidence(self, key: tuple[Any, ...]) -> RollingNetEvidence:
        if key not in self.evidence:
            self.evidence[key] = RollingNetEvidence(window=META_WINDOW)
        return self.evidence[key]

    def _posterior(self, **values: Any) -> HierarchicalPosterior:
        nodes = [
            (name, self._evidence(key))
            for name, key in hierarchy_keys(**values)
        ]
        return hierarchical_posterior(nodes)

    def record_closed(self, item: Mapping[str, Any]) -> None:
        """Update both parent and meta evidence only after a trade has closed."""
        self.parent.record_closed(item)
        key = decision_key(
            entry_time=item.get("entry_time"),
            symbol=str(item.get("symbol", "")),
            engine=str(item.get("engine", "")),
            setup=str(item.get("setup", "")),
            side=item.get("side", ""),
        )
        metadata = self.decisions.get(key, {})
        mode = str(item.get("engine_group", item.get("mode", ""))).upper()
        market_regime = str(metadata.get("v14_18_market_regime", ""))
        if market_regime not in MARKET_REGIMES:
            market_regime = classify_market_regime(
                mode=mode,
                engine=str(item.get("engine", "")),
                setup=str(item.get("setup", "")),
                consensus="UNAVAILABLE",
                parent_regime=str(item.get("cost_regime", "UNKNOWN")),
            )
        net_r = float(item.get("r_multiple", 0.0) or 0.0)
        for _, evidence_key in hierarchy_keys(
            symbol=str(item.get("symbol", "")),
            engine=str(item.get("engine", "")),
            setup=str(item.get("setup", "")),
            mode=mode,
            side=item.get("side", ""),
            entry_time=item.get("entry_time"),
            market_regime=market_regime,
        ):
            self._evidence(evidence_key).add(net_r)

    def decision(self, **kwargs: Any) -> CostRegimeDecision:
        current = self.parent.decision(**kwargs)
        parent_event = dict(self.parent.events[-1])
        symbol = str(kwargs.get("symbol", ""))
        engine = str(kwargs.get("engine", ""))
        setup = str(kwargs.get("setup", ""))
        mode = str(kwargs.get("mode", "")).upper()
        side = kwargs.get("side", "")
        entry_time = kwargs.get("entry_time")
        all_in_cost = float(kwargs.get("all_in_cost", 0.0) or 0.0)
        consensus = str(parent_event.get("v14_17_consensus", "UNAVAILABLE"))

        market_regime = classify_market_regime(
            mode=mode,
            engine=engine,
            setup=setup,
            consensus=consensus,
            parent_regime=current.regime,
        )
        posterior = self._posterior(
            symbol=symbol,
            engine=engine,
            setup=setup,
            mode=mode,
            side=side,
            entry_time=entry_time,
            market_regime=market_regime,
        )
        direction_evidence = {
            "trades": parent_event.get("v14_17_direction_trades", 0),
            "mean_r": parent_event.get("v14_17_direction_mean_r", 0.0),
            "profit_factor": parent_event.get("v14_17_direction_profit_factor", 0.0),
        }
        meta = meta_label_from_evidence(
            current=current,
            mode=mode,
            all_in_cost_r=all_in_cost,
            market_regime=market_regime,
            posterior=posterior,
            direction_evidence=direction_evidence,
        )

        requested = max(0.0, float(current.risk_percent))
        if meta.label == "SHADOW":
            risk = 0.0
        else:
            risk = min(
                requested,
                max(MINIMUM_RISK_PERCENT, requested * float(meta.multiplier)),
            )
        funded = current.funded and risk > 0.0
        regime = current.regime
        if meta.label == "SHADOW":
            regime = "SHADOW"
        elif risk < requested - 1e-12:
            regime = "REASONING_REDUCED"

        final = CostRegimeDecision(
            funded=funded,
            regime=regime,
            risk_percent=risk,
            reason=(
                f"{current.reason}; V14.18 {market_regime}/{meta.label}; "
                f"hierarchy={posterior.score_r:.4f}R, confidence={posterior.confidence:.4f}; "
                f"{meta.reason}"
            ),
            all_in_cost_r=current.all_in_cost_r,
            target_r=current.target_r,
        )
        self.label_counts[meta.label] += 1
        self.regime_counts[market_regime] += 1

        event = {
            **parent_event,
            "v14_18_market_regime": market_regime,
            "v14_18_meta_label": meta.label,
            "v14_18_meta_reason": meta.reason,
            "v14_18_risk_multiplier": meta.multiplier,
            "v14_18_parent_risk_percent": current.risk_percent,
            "v14_18_final_risk_percent": final.risk_percent,
            "v14_18_hierarchical_score_r": posterior.score_r,
            "v14_18_hierarchical_confidence": posterior.confidence,
            "v14_18_mature_negative_nodes": posterior.mature_negative_nodes,
            "v14_18_effective_trades": posterior.effective_trades,
        }
        for name, values in posterior.nodes.items():
            event[f"v14_18_{name}_trades"] = values["trades"]
            event[f"v14_18_{name}_mean_r"] = values["mean_r"]
            event[f"v14_18_{name}_profit_factor"] = values["profit_factor"]
        self.events.append(event)
        key = decision_key(
            entry_time=entry_time,
            symbol=symbol,
            engine=engine,
            setup=setup,
            side=side,
        )
        self.decisions[key] = event
        return final

    def metadata_for(self, item: Mapping[str, Any]) -> dict[str, Any]:
        return dict(
            self.decisions.get(
                decision_key(
                    entry_time=item.get("entry_time"),
                    symbol=str(item.get("symbol", "")),
                    engine=str(item.get("engine", "")),
                    setup=str(item.get("setup", "")),
                    side=item.get("side", ""),
                ),
                {},
            )
        )

    def summary(self) -> dict[str, Any]:
        return {
            "meta_window": META_WINDOW,
            "hierarchy_prior_strength": HIERARCHY_PRIOR_STRENGTH,
            "positive_hierarchy_override_r": HIERARCHICAL_POSITIVE_OVERRIDE_R,
            "label_counts": dict(self.label_counts),
            "market_regime_counts": dict(self.regime_counts),
            "range_ict_active_reduction": False,
            "range_mean_reversion_engine_implemented": False,
            "events": len(self.events),
            "parent": self.parent.summary(),
        }


__all__ = [
    "FULL_MULTIPLIER",
    "REDUCED_MULTIPLIER",
    "OBSERVATION_MULTIPLIER",
    "SHADOW_MULTIPLIER",
    "HierarchicalPosterior",
    "MetaLabelDecision",
    "HierarchicalRegimeMetaLabeler",
    "classify_market_regime",
    "decision_key",
    "hierarchical_posterior",
    "hierarchy_keys",
    "live_hierarchy_authorized",
    "meta_label_from_evidence",
]
