"""Why an entry was blocked -- every reason, not just the first one.

``RiskGuardedClient.can_open_new_trade`` answers a yes/no question and returns
the first gate that said no. That is right for execution and useless for
diagnosis: when the bot places no trades for a day you learn that the entry
interval was active, and nothing about the four other gates that were also
shut. Worse, the answer only exists at the instant an order is attempted, so
by the time you look, it is gone.

This module holds the gate logic as a pure function over
``(state, config, symbol, volume, now)`` and returns the standing of *every*
gate. :class:`~mt5_ai_bridge.session_guard.RiskGuardedClient` consumes it for
its yes/no decision, so there is exactly one definition of each rule and the
diagnostic can never drift from what actually runs.

:class:`RejectionLedger` accumulates rejections across a session so
"why am I not trading?" has a durable answer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

__all__ = [
    "GateStatus",
    "EntryDiagnosis",
    "evaluate_entry_gates",
    "RejectionLedger",
    "GATE_NAMES",
]

_EPS = 1e-9

GATE_NAMES = (
    "daily_lock",
    "loss_cooldown",
    "volume_positive",
    "minimum_lot",
    "maximum_lot",
    "daily_trade_limit",
    "symbol_trade_limit",
    "entry_interval",
)


@dataclass(frozen=True)
class GateStatus:
    """One gate's standing. ``blocking`` is True when it would refuse entry."""

    name: str
    blocking: bool
    reason: str = ""
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EntryDiagnosis:
    """Every gate's standing for one hypothetical entry."""

    symbol: str
    volume: float
    gates: tuple[GateStatus, ...]

    @property
    def blocking(self) -> tuple[GateStatus, ...]:
        return tuple(g for g in self.gates if g.blocking)

    @property
    def allowed(self) -> bool:
        return not self.blocking

    @property
    def first_reason(self) -> str:
        """The reason the live guard would report (first blocking gate)."""
        blocking = self.blocking
        return blocking[0].reason if blocking else "session risk checks passed"

    def summary(self) -> str:
        if self.allowed:
            return f"{self.symbol} {self.volume:g}: ALLOWED (all gates open)"
        lines = [f"{self.symbol} {self.volume:g}: BLOCKED by "
                 f"{len(self.blocking)} of {len(self.gates)} gates"]
        for g in self.gates:
            mark = "BLOCK" if g.blocking else "  ok "
            lines.append(f"  [{mark}] {g.name}"
                         + (f" -- {g.reason}" if g.blocking else ""))
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "volume": self.volume,
            "allowed": self.allowed,
            "first_reason": self.first_reason,
            "gates": [{"name": g.name, "blocking": g.blocking,
                       "reason": g.reason, "detail": dict(g.detail)}
                      for g in self.gates],
        }


def _open(name: str, **detail) -> GateStatus:
    return GateStatus(name=name, blocking=False, detail=detail)


def _shut(name: str, reason: str, **detail) -> GateStatus:
    return GateStatus(name=name, blocking=True, reason=reason, detail=detail)


def evaluate_entry_gates(state: dict, cfg: Any, symbol: str,
                         requested_volume: float, now_ts: float
                         ) -> EntryDiagnosis:
    """Evaluate every session gate without short-circuiting.

    ``state`` is the guard's persisted state dict and ``cfg`` a
    :class:`~mt5_ai_bridge.session_guard.SessionGuardConfig`. Nothing is
    mutated -- the caller owns side effects such as clearing an expired
    cooldown.

    Gate order matches the live guard, so ``first_reason`` reproduces exactly
    what ``can_open_new_trade`` reports.
    """
    state = state or {}
    symbol = str(symbol or "")
    volume = float(requested_volume or 0.0)
    gates: List[GateStatus] = []

    # 1. Daily lock -- set by the loss, giveback or loss-streak breakers.
    if state.get("daily_lock"):
        gates.append(_shut("daily_lock",
                           state.get("lock_reason") or "daily trading lock active",
                           lock_reason=state.get("lock_reason")))
    else:
        gates.append(_open("daily_lock"))

    # 2. Loss cooldown.
    cooldown_until = float(state.get("cooldown_until") or 0.0)
    if cooldown_until > now_ts:
        remaining = max(1, int((cooldown_until - now_ts + 59) // 60))
        gates.append(_shut(
            "loss_cooldown",
            f"loss cooldown active for approximately {remaining} more minute(s)",
            minutes_remaining=remaining, cooldown_until=cooldown_until))
    else:
        gates.append(_open("loss_cooldown", cooldown_until=cooldown_until))

    # 3-5. Volume bounds.
    if volume <= 0:
        gates.append(_shut("volume_positive",
                           "requested volume is not positive", volume=volume))
    else:
        gates.append(_open("volume_positive", volume=volume))

    minimum_lot = float(getattr(cfg, "minimum_lot", 0.0) or 0.0)
    if minimum_lot > 0 and volume + _EPS < minimum_lot:
        gates.append(_shut(
            "minimum_lot",
            f"requested volume {volume:g} is below "
            f"SESSION_MINIMUM_LOT {minimum_lot:g}",
            volume=volume, minimum_lot=minimum_lot))
    else:
        gates.append(_open("minimum_lot", minimum_lot=minimum_lot))

    maximum_lot = float(getattr(cfg, "maximum_lot", 0.0) or 0.0)
    if maximum_lot > 0 and volume > maximum_lot + _EPS:
        gates.append(_shut(
            "maximum_lot",
            f"requested volume {volume:g} exceeds "
            f"SESSION_MAXIMUM_LOT {maximum_lot:g}",
            volume=volume, maximum_lot=maximum_lot))
    else:
        gates.append(_open("maximum_lot", maximum_lot=maximum_lot))

    # 6-8. Frequency limits, only when the trade limiter is enabled.
    if getattr(cfg, "enable_trade_limit", False):
        total = int(state.get("trades_today") or 0)
        max_per_day = int(getattr(cfg, "max_trades_per_day", 0) or 0)
        if max_per_day > 0 and total >= max_per_day:
            gates.append(_shut(
                "daily_trade_limit",
                f"daily trade limit reached ({total}/{max_per_day})",
                trades_today=total, limit=max_per_day))
        else:
            gates.append(_open("daily_trade_limit",
                               trades_today=total, limit=max_per_day))

        per_symbol = dict(state.get("trades_by_symbol") or {})
        sym_count = int(per_symbol.get(symbol.upper(), 0))
        max_per_symbol = int(
            getattr(cfg, "max_trades_per_symbol_per_day", 0) or 0)
        if max_per_symbol > 0 and sym_count >= max_per_symbol:
            gates.append(_shut(
                "symbol_trade_limit",
                f"{symbol} daily trade limit reached "
                f"({sym_count}/{max_per_symbol})",
                symbol_trades=sym_count, limit=max_per_symbol))
        else:
            gates.append(_open("symbol_trade_limit",
                               symbol_trades=sym_count, limit=max_per_symbol))

        last_entry = float(state.get("last_entry_time") or 0.0)
        wait_seconds = int(
            getattr(cfg, "minimum_minutes_between_entries", 0) or 0) * 60
        if wait_seconds > 0 and last_entry > 0 \
                and now_ts - last_entry < wait_seconds:
            remaining = max(
                1, int((wait_seconds - (now_ts - last_entry) + 59) // 60))
            gates.append(_shut(
                "entry_interval",
                f"minimum entry interval active for approximately "
                f"{remaining} more minute(s)",
                minutes_remaining=remaining, last_entry_time=last_entry))
        else:
            gates.append(_open("entry_interval", last_entry_time=last_entry))
    else:
        for name in ("daily_trade_limit", "symbol_trade_limit",
                     "entry_interval"):
            gates.append(_open(name, disabled=True))

    return EntryDiagnosis(symbol=symbol, volume=volume, gates=tuple(gates))


class RejectionLedger:
    """Counts why entries were refused, so a quiet day can be explained.

    The live guard logs one line per rejection and moves on. Aggregating them
    turns "the bot did nothing today" into "94% of attempts hit the entry
    interval", which points at a setting rather than at the strategy.
    """

    def __init__(self, capacity: int = 500) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self._by_gate: Counter = Counter()
        self._by_symbol: Counter = Counter()
        self._recent: List[dict] = []
        self._attempts = 0
        self._allowed = 0

    def record(self, diagnosis: EntryDiagnosis,
               timestamp: Optional[float] = None) -> None:
        self._attempts += 1
        if diagnosis.allowed:
            self._allowed += 1
        else:
            for gate in diagnosis.blocking:
                self._by_gate[gate.name] += 1
            self._by_symbol[diagnosis.symbol.upper()] += 1
        entry = dict(diagnosis.as_dict())
        entry["timestamp"] = timestamp
        self._recent.append(entry)
        if len(self._recent) > self.capacity:
            del self._recent[:len(self._recent) - self.capacity]

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def allowed(self) -> int:
        return self._allowed

    @property
    def rejected(self) -> int:
        return self._attempts - self._allowed

    @property
    def recent(self) -> tuple[dict, ...]:
        return tuple(self._recent)

    def by_gate(self) -> dict:
        """Rejection counts per gate, most frequent first."""
        return dict(self._by_gate.most_common())

    def by_symbol(self) -> dict:
        return dict(self._by_symbol.most_common())

    def dominant_gate(self) -> Optional[str]:
        """The gate responsible for the most rejections, if any."""
        top = self._by_gate.most_common(1)
        return top[0][0] if top else None

    def report(self) -> str:
        if not self._attempts:
            return "No entry attempts recorded."
        lines = [f"Entry attempts: {self._attempts}  "
                 f"allowed: {self._allowed}  rejected: {self.rejected}"]
        if self.rejected:
            lines.append("Blocking gates (an attempt can hit several):")
            for name, count in self._by_gate.most_common():
                pct = 100.0 * count / self.rejected
                lines.append(f"  {name:<20} {count:>5}  ({pct:5.1f}% of rejections)")
            lines.append("By symbol:")
            for sym, count in self._by_symbol.most_common():
                lines.append(f"  {sym:<20} {count:>5}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "attempts": self._attempts,
            "allowed": self._allowed,
            "rejected": self.rejected,
            "by_gate": self.by_gate(),
            "by_symbol": self.by_symbol(),
            "dominant_gate": self.dominant_gate(),
        }


def main(argv=None) -> int:
    """Explain, against a persisted guard state, why an entry would be refused.

        python -m mt5_ai_bridge.entry_diagnostics --symbol GBPUSD --volume 0.1
    """
    import argparse
    import json
    import time
    from pathlib import Path

    # Deferred: session_guard imports this module, so importing it at module
    # scope would be circular.
    from .session_guard import SessionGuardConfig

    p = argparse.ArgumentParser(
        prog="mt5_ai_bridge.entry_diagnostics",
        description="Show why a live entry would be blocked.")
    p.add_argument("--state", default="session_guard_state.json",
                   help="Path to the guard's persisted state JSON")
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--volume", type=float, default=0.10)
    p.add_argument("--now", type=float, default=None,
                   help="Unix timestamp to evaluate at (default: now)")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args(argv)

    path = Path(args.state)
    if not path.exists():
        print(f"No guard state at {path}. The bot has not run, or "
              f"SESSION_STATE_PATH points elsewhere.")
        return 2
    # utf-8-sig: state files hand-edited on Windows often carry a BOM.
    state = json.loads(path.read_text(encoding="utf-8-sig") or "{}")

    # Config comes from the same environment the bot would read.
    cfg = SessionGuardConfig.from_settings(
        type("S", (), {"max_trades_per_day": 8, "db_path": ""})())
    now = args.now if args.now is not None else time.time()

    diagnosis = evaluate_entry_gates(state, cfg, args.symbol, args.volume, now)
    if args.json:
        print(json.dumps(diagnosis.as_dict(), indent=2))
    else:
        print(f"State: {path}")
        print(diagnosis.summary())
    return 0 if diagnosis.allowed else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
