"""V14.17 cost-adjusted consensus and contextual admission controls.

V14.17 is stacked on V14.16. It does not add a new signal family, raise any
risk ceiling, or alter exits. The layer uses only information available before
entry plus results from trades that have already closed:

* contextual net expectancy is tracked by symbol, engine, setup, direction,
  UTC hour, session and parent cost regime;
* V12 candidates with mature negative engine/direction evidence are dynamically
  demoted rather than promoted or revived;
* the latest opposite-engine signal on the same symbol is classified as
  aligned, conflicting or unavailable;
* same-currency directional exposure is capped before portfolio admission.

The exact historical replay can use closed replay outcomes. Live use must be
fed reconciled broker-net evidence and remains stricter than research replay.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .v14_13_cost_regime_profile import CostRegimeDecision
from .v14_15_unified_reasoning import unified_cost_reasoning_decision

CONTEXT_WINDOW = 80
MIN_CONTEXT_TRADES = 20
NEGATIVE_MEAN_R = -0.05
NEGATIVE_PROFIT_FACTOR = 0.95
CONTEXT_DEMOTION_MULTIPLIER = 0.50
CONFLICT_DEMOTION_MULTIPLIER = 0.90
MINIMUM_RISK_PERCENT = 0.025
CORRELATED_CURRENCY_RISK_CAP_PERCENT = 2.40
CONSENSUS_LOOKBACK_HOURS = 24 * 30
LIVE_MIN_CONTEXT_TRADES = 30
LIVE_MIN_SYMBOL_MODE_TRADES = 40


@dataclass
class RollingNetEvidence:
    """Bounded, chronological broker-net R evidence."""

    window: int = CONTEXT_WINDOW
    values: deque[float] = field(default_factory=deque)
    total: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.values, deque):
            self.values = deque(self.values)
        self.values = deque(self.values, maxlen=max(1, int(self.window)))
        if self.values:
            snapshot = list(self.values)
            self.total = float(sum(snapshot))
            self.gross_profit = float(sum(value for value in snapshot if value > 0))
            self.gross_loss = float(-sum(value for value in snapshot if value < 0))

    def add(self, value: float) -> None:
        value = float(value)
        if len(self.values) == self.values.maxlen:
            removed = float(self.values[0])
            self.total -= removed
            if removed > 0:
                self.gross_profit -= removed
            elif removed < 0:
                self.gross_loss -= -removed
        self.values.append(value)
        self.total += value
        if value > 0:
            self.gross_profit += value
        elif value < 0:
            self.gross_loss += -value

    @property
    def trades(self) -> int:
        return len(self.values)

    @property
    def mean_r(self) -> float:
        return self.total / self.trades if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss > 1e-12:
            return self.gross_profit / self.gross_loss
        return 99.0 if self.gross_profit > 0 else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "trades": self.trades,
            "mean_r": self.mean_r,
            "profit_factor": self.profit_factor,
        }


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


def normalized_direction(value: Any) -> int:
    text = str(value).strip().upper()
    if text in {"BUY", "LONG", "1", "+1", "1.0"}:
        return 1
    if text in {"SELL", "SHORT", "-1", "-1.0"}:
        return -1
    return 0


def utc_session(hour: int) -> str:
    hour = int(hour) % 24
    if hour < 7:
        return "ASIA"
    if hour < 12:
        return "LONDON"
    if hour < 17:
        return "NEW_YORK"
    return "LATE"


def currency_exposure(symbol: str, direction: int, risk_percent: float) -> dict[str, float]:
    symbol = str(symbol).upper()
    if len(symbol) != 6 or direction == 0:
        return {}
    risk = max(0.0, float(risk_percent))
    return {
        symbol[:3]: float(direction) * risk,
        symbol[3:]: -float(direction) * risk,
    }


def context_keys(
    *,
    symbol: str,
    engine: str,
    setup: str,
    mode: str,
    side: Any,
    entry_time: Any,
    regime: str,
) -> dict[str, tuple[Any, ...]]:
    stamp = _utc(entry_time)
    direction = normalized_direction(side)
    return {
        "symbol": ("symbol", str(symbol).upper(), str(mode).upper()),
        "engine": ("engine", str(engine)),
        "setup": ("setup", str(engine), str(setup)),
        "direction": ("direction", str(engine), direction),
        "hour": ("hour", str(symbol).upper(), str(mode).upper(), stamp.hour),
        "session": (
            "session",
            str(symbol).upper(),
            str(mode).upper(),
            utc_session(stamp.hour),
        ),
        "regime": ("regime", str(regime).upper()),
    }


def contextual_demotion_authorized(evidence: RollingNetEvidence) -> bool:
    return (
        evidence.trades >= MIN_CONTEXT_TRADES
        and evidence.mean_r < NEGATIVE_MEAN_R
        and evidence.profit_factor < NEGATIVE_PROFIT_FACTOR
    )


def live_context_evidence_authorized(
    context_payload: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Require reconciled, mature broker-net context before live V14.17 action."""
    if not context_payload:
        return False, "LIVE_CONTEXT_EVIDENCE_MISSING"
    if not bool(context_payload.get("broker_reconciled", False)):
        return False, "LIVE_CONTEXT_NOT_BROKER_RECONCILED"
    direction = context_payload.get("direction", {})
    sleeve = context_payload.get("symbol_mode", {})
    if int(direction.get("trades", 0) or 0) < LIVE_MIN_CONTEXT_TRADES:
        return False, "LIVE_DIRECTION_SAMPLE_BELOW_30"
    if int(sleeve.get("trades", 0) or 0) < LIVE_MIN_SYMBOL_MODE_TRADES:
        return False, "LIVE_SYMBOL_MODE_SAMPLE_BELOW_40"
    return True, "LIVE_CONTEXT_BROKER_NET_CONFIRMED"


class CostAdjustedConsensusController:
    """Stateful chronological overlay used by the V14.17 replay."""

    def __init__(
        self,
        replay: Any | None = None,
        *,
        parent_decision: Callable[..., CostRegimeDecision] = unified_cost_reasoning_decision,
    ) -> None:
        self.replay = replay
        self.parent_decision = parent_decision
        self.evidence: dict[tuple[Any, ...], RollingNetEvidence] = {}
        self.latest_signal: dict[tuple[str, str], tuple[datetime, int]] = {}
        self.events: list[dict[str, Any]] = []
        self.action_counts: dict[str, int] = {}
        self.consensus_counts: dict[str, int] = {
            "ALIGNED": 0,
            "CONFLICT": 0,
            "UNAVAILABLE": 0,
        }

    def _evidence(self, key: tuple[Any, ...]) -> RollingNetEvidence:
        if key not in self.evidence:
            self.evidence[key] = RollingNetEvidence()
        return self.evidence[key]

    def _count(self, name: str) -> None:
        self.action_counts[name] = self.action_counts.get(name, 0) + 1

    def record_closed(self, item: Mapping[str, Any]) -> None:
        keys = context_keys(
            symbol=str(item.get("symbol", "")),
            engine=str(item.get("engine", "")),
            setup=str(item.get("setup", "")),
            mode=str(item.get("engine_group", item.get("mode", ""))),
            side=item.get("side", ""),
            entry_time=item.get("entry_time"),
            regime=str(item.get("cost_regime", "UNKNOWN")),
        )
        net_r = float(item.get("r_multiple", 0.0) or 0.0)
        for key in keys.values():
            self._evidence(key).add(net_r)

    def _consensus(
        self,
        *,
        symbol: str,
        mode: str,
        side: Any,
        entry_time: Any,
    ) -> str:
        mode = str(mode).upper()
        other = "ICT" if mode == "V12" else "V12"
        previous = self.latest_signal.get((str(symbol).upper(), other))
        direction = normalized_direction(side)
        if previous is None or direction == 0:
            return "UNAVAILABLE"
        age_hours = (_utc(entry_time) - previous[0]).total_seconds() / 3600.0
        if age_hours < 0 or age_hours > CONSENSUS_LOOKBACK_HOURS:
            return "UNAVAILABLE"
        return "ALIGNED" if direction == previous[1] else "CONFLICT"

    def _record_signal(
        self,
        *,
        symbol: str,
        mode: str,
        side: Any,
        entry_time: Any,
        funded: bool,
    ) -> None:
        direction = normalized_direction(side)
        if funded and direction:
            self.latest_signal[(str(symbol).upper(), str(mode).upper())] = (
                _utc(entry_time),
                direction,
            )

    def _correlation_adjusted_risk(
        self,
        *,
        symbol: str,
        side: Any,
        requested_risk_percent: float,
    ) -> tuple[float, float]:
        requested = max(0.0, float(requested_risk_percent))
        direction = normalized_direction(side)
        candidate = currency_exposure(symbol, direction, requested)
        if not candidate or self.replay is None:
            return requested, 0.0

        active_exposure: dict[str, float] = {}
        for item in getattr(self.replay, "active", []):
            item_risk = float(item.get("risk_percent", 0.0) or 0.0)
            for currency, exposure in currency_exposure(
                str(item.get("symbol", "")),
                normalized_direction(item.get("side", "")),
                item_risk,
            ).items():
                active_exposure[currency] = active_exposure.get(currency, 0.0) + exposure

        excess = 0.0
        for currency, exposure in candidate.items():
            projected = abs(active_exposure.get(currency, 0.0) + exposure)
            excess = max(
                excess,
                projected - CORRELATED_CURRENCY_RISK_CAP_PERCENT,
            )
        if excess <= 1e-12:
            return requested, 0.0
        approved = max(MINIMUM_RISK_PERCENT, requested - excess)
        return min(requested, approved), excess

    def _context_snapshot(
        self,
        *,
        symbol: str,
        engine: str,
        setup: str,
        mode: str,
        side: Any,
        entry_time: Any,
        regime: str,
    ) -> tuple[dict[str, tuple[Any, ...]], dict[str, dict[str, float | int]]]:
        keys = context_keys(
            symbol=symbol,
            engine=engine,
            setup=setup,
            mode=mode,
            side=side,
            entry_time=entry_time,
            regime=regime,
        )
        snapshot = {name: self._evidence(key).as_dict() for name, key in keys.items()}
        return keys, snapshot

    def decision(self, **kwargs: Any) -> CostRegimeDecision:
        current = self.parent_decision(**kwargs)
        symbol = str(kwargs.get("symbol", ""))
        engine = str(kwargs.get("engine", ""))
        setup = str(kwargs.get("setup", ""))
        mode = str(kwargs.get("mode", "")).upper()
        side = kwargs.get("side", "")
        entry_time = kwargs.get("entry_time")
        all_in_cost = float(kwargs.get("all_in_cost", 0.0) or 0.0)

        _, snapshot = self._context_snapshot(
            symbol=symbol,
            engine=engine,
            setup=setup,
            mode=mode,
            side=side,
            entry_time=entry_time,
            regime=current.regime,
        )
        consensus = self._consensus(
            symbol=symbol,
            mode=mode,
            side=side,
            entry_time=entry_time,
        )
        self.consensus_counts[consensus] += 1

        action = "PARENT_DECISION_RETAINED"
        risk = float(current.risk_percent)
        reason_parts = [str(current.reason)]

        if current.funded and not current.is_shadow and all_in_cost > 1e-12:
            direction_evidence = self._evidence(
                context_keys(
                    symbol=symbol,
                    engine=engine,
                    setup=setup,
                    mode=mode,
                    side=side,
                    entry_time=entry_time,
                    regime=current.regime,
                )["direction"]
            )
            if mode == "V12" and contextual_demotion_authorized(direction_evidence):
                multiplier = CONTEXT_DEMOTION_MULTIPLIER
                if consensus == "CONFLICT":
                    multiplier *= CONFLICT_DEMOTION_MULTIPLIER
                risk = max(MINIMUM_RISK_PERCENT, risk * multiplier)
                action = "CONTEXT_DEMOTED"
                reason_parts.append(
                    "V14.17 prior-closed V12 direction evidence "
                    f"n={direction_evidence.trades}, mean={direction_evidence.mean_r:.4f}R, "
                    f"PF={direction_evidence.profit_factor:.4f}; consensus={consensus}; "
                    f"multiplier={multiplier:.4f}"
                )
                self._count(action)

            adjusted, excess = self._correlation_adjusted_risk(
                symbol=symbol,
                side=side,
                requested_risk_percent=risk,
            )
            if adjusted < risk - 1e-12:
                risk = adjusted
                action = (
                    "CONTEXT_AND_CORRELATION_DEMOTED"
                    if action == "CONTEXT_DEMOTED"
                    else "CORRELATION_CAPPED"
                )
                reason_parts.append(
                    "V14.17 correlation-aware currency exposure cap "
                    f"{CORRELATED_CURRENCY_RISK_CAP_PERCENT:.2f}% reduced risk by "
                    f"{excess:.4f}%"
                )
                self._count("CORRELATION_CAPPED")

        funded = current.funded and risk > 0.0
        regime = current.regime
        if risk < float(current.risk_percent) - 1e-12:
            # This inherited regime is explicitly recognized by V14.16 as a
            # non-uplift state, so a contextual/correlation reduction cannot be
            # reversed by the later quality-allocation layer.
            regime = "REASONING_REDUCED"

        final = CostRegimeDecision(
            funded=funded,
            regime=regime,
            risk_percent=max(0.0, risk),
            reason="; ".join(reason_parts),
            all_in_cost_r=current.all_in_cost_r,
            target_r=current.target_r,
        )
        self._record_signal(
            symbol=symbol,
            mode=mode,
            side=side,
            entry_time=entry_time,
            funded=final.funded,
        )
        event: dict[str, Any] = {
            "entry_time": _utc(entry_time).isoformat(),
            "symbol": symbol,
            "engine": engine,
            "setup": setup,
            "mode": mode,
            "side": side,
            "v14_17_action": action,
            "v14_17_consensus": consensus,
            "v14_17_parent_regime": current.regime,
            "v14_17_parent_risk_percent": current.risk_percent,
            "v14_17_final_regime": final.regime,
            "v14_17_final_risk_percent": final.risk_percent,
        }
        for name, values in snapshot.items():
            event[f"v14_17_{name}_trades"] = values["trades"]
            event[f"v14_17_{name}_mean_r"] = values["mean_r"]
            event[f"v14_17_{name}_profit_factor"] = values["profit_factor"]
        self.events.append(event)
        return final

    def summary(self) -> dict[str, Any]:
        return {
            "context_window": CONTEXT_WINDOW,
            "minimum_context_trades": MIN_CONTEXT_TRADES,
            "negative_mean_r": NEGATIVE_MEAN_R,
            "negative_profit_factor": NEGATIVE_PROFIT_FACTOR,
            "context_demotion_multiplier": CONTEXT_DEMOTION_MULTIPLIER,
            "correlated_currency_risk_cap_percent": (
                CORRELATED_CURRENCY_RISK_CAP_PERCENT
            ),
            "action_counts": dict(self.action_counts),
            "consensus_counts": dict(self.consensus_counts),
            "events": len(self.events),
        }


__all__ = [
    "CONTEXT_WINDOW",
    "MIN_CONTEXT_TRADES",
    "NEGATIVE_MEAN_R",
    "NEGATIVE_PROFIT_FACTOR",
    "CONTEXT_DEMOTION_MULTIPLIER",
    "CORRELATED_CURRENCY_RISK_CAP_PERCENT",
    "CostAdjustedConsensusController",
    "RollingNetEvidence",
    "context_keys",
    "contextual_demotion_authorized",
    "currency_exposure",
    "live_context_evidence_authorized",
    "normalized_direction",
    "utc_session",
]
