"""V14.20 range anti-consensus integration.

The V14.19 mean-reversion family remains shadow-only.  V14.20 uses an active
range shadow signal only as a pre-entry context for the existing V12/ICT
candidate stream.  No range order is funded.

A principal candidate is shadowed only when all of the following are true:

* the active range shadow direction conflicts with the principal direction;
* transaction cost is non-zero (zero-cost parity remains exact);
* the same principal engine has at least ten already-closed conflict trades;
* the rolling 20-trade conflict mean is negative; and
* rolling conflict profit factor is below 0.80.

Only executed principal trades update the broker-net conflict evidence.  A
candidate shadowed by V14.20 cannot contaminate later evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import pandas as pd

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_17_cost_adjusted_consensus import RollingNetEvidence
from .v14_18_hierarchical_regime_meta import (
    HierarchicalRegimeMetaLabeler,
    decision_key,
)

ANTI_CONSENSUS_WINDOW = 20
MINIMUM_ENGINE_CONFLICT_TRADES = 10
MAXIMUM_ENGINE_CONFLICT_MEAN_R = 0.0
MAXIMUM_ENGINE_CONFLICT_PROFIT_FACTOR = 0.80

LIVE_MINIMUM_ENGINE_CONFLICT_TRADES = 20
RANGE_RELATIONS = frozenset({"ALIGNED", "CONFLICT", "UNAVAILABLE"})
ACTIONS = frozenset({"UNCHANGED", "SHADOW"})


@dataclass(frozen=True)
class RangeContext:
    relation: str
    signal_side: str | None = None
    signal_entry_time: datetime | None = None
    signal_exit_time: datetime | None = None
    signal_engine: str | None = None


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


class RangeSignalIndex:
    """Lookup active V14.19 shadow signals without using post-entry outcomes."""

    REQUIRED_COLUMNS = frozenset(
        {
            "symbol",
            "side",
            "entry_time",
            "exit_time",
            "signal_available_at",
        }
    )

    def __init__(self, source: pd.DataFrame | None) -> None:
        self.by_symbol: dict[str, pd.DataFrame] = {}
        if source is None or source.empty:
            return
        missing = self.REQUIRED_COLUMNS - set(source.columns)
        if missing:
            raise ValueError(f"Range source missing columns: {sorted(missing)}")
        work = source.copy()
        for column in ("entry_time", "exit_time", "signal_available_at"):
            work[column] = pd.to_datetime(work[column], utc=True, errors="raise")
        work["symbol"] = work["symbol"].astype(str).str.upper()
        work["side"] = work["side"].astype(str).str.upper()
        if "engine" not in work:
            work["engine"] = "V14_19_D1_RANGE_REVERSION_SHADOW"
        for symbol, group in work.groupby("symbol", sort=False):
            self.by_symbol[str(symbol)] = (
                group.sort_values(["entry_time", "exit_time"])
                .reset_index(drop=True)
            )

    def context(self, *, symbol: str, side: Any, entry_time: Any) -> RangeContext:
        symbol = str(symbol).upper()
        principal_side = str(side).upper()
        stamp = pd.Timestamp(_utc(entry_time))
        group = self.by_symbol.get(symbol)
        if group is None or group.empty:
            return RangeContext("UNAVAILABLE")
        active = group[
            (group["signal_available_at"] <= stamp)
            & (group["entry_time"] <= stamp)
            & (group["exit_time"] > stamp)
        ]
        if active.empty:
            return RangeContext("UNAVAILABLE")
        row = active.iloc[-1]
        signal_side = str(row["side"]).upper()
        relation = "ALIGNED" if signal_side == principal_side else "CONFLICT"
        return RangeContext(
            relation=relation,
            signal_side=signal_side,
            signal_entry_time=_utc(row["entry_time"]),
            signal_exit_time=_utc(row["exit_time"]),
            signal_engine=str(row.get("engine", "")),
        )


def conflict_shadow_authorized(
    *,
    current: CostRegimeDecision,
    all_in_cost_r: float,
    context: RangeContext,
    evidence: RollingNetEvidence,
) -> tuple[bool, str]:
    """Return the frozen historical anti-consensus action."""

    if not current.funded or current.is_shadow:
        return False, "PARENT_NOT_FUNDED"
    if float(all_in_cost_r) <= 1e-12:
        return False, "ZERO_COST_PARITY_UNCHANGED"
    if context.relation != "CONFLICT":
        return False, f"RANGE_{context.relation}"
    if evidence.trades < MINIMUM_ENGINE_CONFLICT_TRADES:
        return False, "ENGINE_CONFLICT_SAMPLE_BELOW_10"
    if evidence.mean_r >= MAXIMUM_ENGINE_CONFLICT_MEAN_R:
        return False, "ENGINE_CONFLICT_MEAN_NOT_NEGATIVE"
    if evidence.profit_factor >= MAXIMUM_ENGINE_CONFLICT_PROFIT_FACTOR:
        return False, "ENGINE_CONFLICT_PF_NOT_BELOW_0_80"
    return True, "MATURE_NEGATIVE_ENGINE_CONFLICT_CONTEXT"


def live_conflict_shadow_authorized(
    payload: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Require stricter reconciled evidence before live risk can be reduced."""

    if not payload:
        return False, "LIVE_RANGE_CONTEXT_MISSING"
    if not bool(payload.get("broker_reconciled", False)):
        return False, "LIVE_RANGE_CONTEXT_NOT_RECONCILED"
    if not bool(payload.get("chronological", False)):
        return False, "LIVE_RANGE_CONTEXT_NOT_CHRONOLOGICAL"
    if not bool(payload.get("range_feed_parity", False)):
        return False, "LIVE_RANGE_FEED_PARITY_NOT_CONFIRMED"
    if str(payload.get("relation", "")).upper() != "CONFLICT":
        return False, "LIVE_RANGE_RELATION_NOT_CONFLICT"
    trades = int(payload.get("trades", 0) or 0)
    mean_r = float(payload.get("mean_r", 0.0) or 0.0)
    profit_factor = float(payload.get("profit_factor", 99.0) or 99.0)
    if trades < LIVE_MINIMUM_ENGINE_CONFLICT_TRADES:
        return False, "LIVE_ENGINE_CONFLICT_SAMPLE_BELOW_20"
    if mean_r >= MAXIMUM_ENGINE_CONFLICT_MEAN_R:
        return False, "LIVE_ENGINE_CONFLICT_MEAN_NOT_NEGATIVE"
    if profit_factor >= MAXIMUM_ENGINE_CONFLICT_PROFIT_FACTOR:
        return False, "LIVE_ENGINE_CONFLICT_PF_NOT_BELOW_0_80"
    return True, "LIVE_RANGE_ANTI_CONSENSUS_CONFIRMED"


class RangeAntiConsensusController:
    """Wrap V14.18 with a no-uplift range-conflict loss filter."""

    def __init__(
        self,
        parent: HierarchicalRegimeMetaLabeler,
        range_source: pd.DataFrame | None,
    ) -> None:
        self.parent = parent
        self.range_index = RangeSignalIndex(range_source)
        self.evidence: dict[str, RollingNetEvidence] = {}
        self.events: list[dict[str, Any]] = []
        self.decisions: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        self.action_counts = {action: 0 for action in ACTIONS}
        self.relation_counts = {relation: 0 for relation in RANGE_RELATIONS}

    def _engine_evidence(self, engine: str) -> RollingNetEvidence:
        key = str(engine)
        if key not in self.evidence:
            self.evidence[key] = RollingNetEvidence(window=ANTI_CONSENSUS_WINDOW)
        return self.evidence[key]

    def record_closed(self, item: Mapping[str, Any]) -> None:
        """Record an executed close; shadowed candidates never reach this method."""

        self.parent.record_closed(item)
        key = decision_key(
            entry_time=item.get("entry_time"),
            symbol=str(item.get("symbol", "")),
            engine=str(item.get("engine", "")),
            setup=str(item.get("setup", "")),
            side=item.get("side", ""),
        )
        metadata = self.decisions.get(key, {})
        if metadata.get("v14_20_range_relation") != "CONFLICT":
            return
        self._engine_evidence(str(item.get("engine", ""))).add(
            float(item.get("r_multiple", 0.0) or 0.0)
        )

    def decision(self, **kwargs: Any) -> CostRegimeDecision:
        current = self.parent.decision(**kwargs)
        parent_event = dict(self.parent.events[-1])
        symbol = str(kwargs.get("symbol", ""))
        engine = str(kwargs.get("engine", ""))
        setup = str(kwargs.get("setup", ""))
        side = kwargs.get("side", "")
        entry_time = kwargs.get("entry_time")
        all_in_cost = float(kwargs.get("all_in_cost", 0.0) or 0.0)

        context = self.range_index.context(
            symbol=symbol,
            side=side,
            entry_time=entry_time,
        )
        evidence = self._engine_evidence(engine)
        authorized, reason = conflict_shadow_authorized(
            current=current,
            all_in_cost_r=all_in_cost,
            context=context,
            evidence=evidence,
        )
        if authorized:
            final = CostRegimeDecision(
                funded=False,
                regime="SHADOW",
                risk_percent=0.0,
                reason=(
                    f"{current.reason}; V14.20 RANGE_ANTI_CONSENSUS/SHADOW; "
                    f"engine_conflict_trades={evidence.trades}, "
                    f"mean={evidence.mean_r:.4f}R, "
                    f"pf={evidence.profit_factor:.4f}; {reason}"
                ),
                all_in_cost_r=current.all_in_cost_r,
                target_r=current.target_r,
            )
            action = "SHADOW"
        else:
            final = current
            action = "UNCHANGED"

        self.action_counts[action] += 1
        self.relation_counts[context.relation] += 1
        event = {
            **parent_event,
            "v14_20_range_relation": context.relation,
            "v14_20_range_signal_side": context.signal_side,
            "v14_20_range_signal_entry_time": (
                context.signal_entry_time.isoformat()
                if context.signal_entry_time is not None
                else None
            ),
            "v14_20_range_signal_exit_time": (
                context.signal_exit_time.isoformat()
                if context.signal_exit_time is not None
                else None
            ),
            "v14_20_range_signal_engine": context.signal_engine,
            "v14_20_action": action,
            "v14_20_reason": reason,
            "v14_20_parent_funded": current.funded,
            "v14_20_parent_risk_percent": current.risk_percent,
            "v14_20_final_risk_percent": final.risk_percent,
            "v14_20_engine_conflict_trades": evidence.trades,
            "v14_20_engine_conflict_mean_r": evidence.mean_r,
            "v14_20_engine_conflict_profit_factor": evidence.profit_factor,
        }
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
            "window": ANTI_CONSENSUS_WINDOW,
            "minimum_engine_conflict_trades": MINIMUM_ENGINE_CONFLICT_TRADES,
            "maximum_engine_conflict_mean_r": MAXIMUM_ENGINE_CONFLICT_MEAN_R,
            "maximum_engine_conflict_profit_factor": (
                MAXIMUM_ENGINE_CONFLICT_PROFIT_FACTOR
            ),
            "action_counts": dict(self.action_counts),
            "relation_counts": dict(self.relation_counts),
            "direct_range_risk_percent": 0.0,
            "risk_uplift_allowed": False,
            "events": len(self.events),
            "parent": self.parent.summary(),
        }


__all__ = [
    "ANTI_CONSENSUS_WINDOW",
    "MINIMUM_ENGINE_CONFLICT_TRADES",
    "MAXIMUM_ENGINE_CONFLICT_MEAN_R",
    "MAXIMUM_ENGINE_CONFLICT_PROFIT_FACTOR",
    "RangeContext",
    "RangeSignalIndex",
    "RangeAntiConsensusController",
    "conflict_shadow_authorized",
    "live_conflict_shadow_authorized",
]
