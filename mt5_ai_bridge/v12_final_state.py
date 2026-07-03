"""Persistent state for the final V12 demo strategy.

Stores daily/peak equity baselines, accepted-order keys, open-position risk, and
the adaptive guard for EURUSD retest and USDJPY breakout.  JSON persistence is
atomic so a restart cannot silently reset cooldowns or drawdown limits.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .v12_final_risk import ADAPTIVE_ENGINES, DISABLED_ENGINES, PROTECTED_ENGINES, OpenRisk


ROLLING_TRADES = 16
MINIMUM_TRADES = 12
FULL_PF = 1.08
FULL_NET_R = 0.0
REDUCED_PF = 0.95
REDUCED_NET_R = -1.0
REDUCED_MULTIPLIER = 0.60
COOLDOWN_DAYS = 45
PROBE_MULTIPLIER = 0.35


@dataclass
class EngineState:
    history_r: list[float] = field(default_factory=list)
    disabled_until: Optional[str] = None
    probe_in_flight: bool = False


@dataclass
class StoredPosition:
    ticket: int
    symbol: str
    engine: str
    side: str
    risk_percent: float

    def as_open_risk(self) -> OpenRisk:
        return OpenRisk(self.symbol, self.engine, self.side, self.risk_percent)


@dataclass
class PersistentState:
    day: Optional[str] = None
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    recent_orders: dict[str, str] = field(default_factory=dict)
    positions: dict[str, StoredPosition] = field(default_factory=dict)
    engines: dict[str, EngineState] = field(default_factory=dict)


class StateStore:
    def __init__(self, path: str = "v12_final_demo_state.json") -> None:
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> PersistentState:
        if not self.path.exists():
            return PersistentState()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        positions = {
            str(key): StoredPosition(**value)
            for key, value in raw.get("positions", {}).items()
        }
        engines = {
            str(key): EngineState(**value)
            for key, value in raw.get("engines", {}).items()
        }
        return PersistentState(
            day=raw.get("day"),
            day_start_equity=float(raw.get("day_start_equity", 0.0)),
            peak_equity=float(raw.get("peak_equity", 0.0)),
            recent_orders=dict(raw.get("recent_orders", {})),
            positions=positions,
            engines=engines,
        )

    def save(self) -> None:
        payload = {
            "day": self.state.day,
            "day_start_equity": self.state.day_start_equity,
            "peak_equity": self.state.peak_equity,
            "recent_orders": self.state.recent_orders,
            "positions": {key: asdict(value) for key, value in self.state.positions.items()},
            "engines": {key: asdict(value) for key, value in self.state.engines.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def update_equity(self, equity: float, now: Optional[datetime] = None) -> None:
        if equity <= 0:
            raise ValueError("Equity must be positive.")
        now = now or datetime.now(timezone.utc)
        today = now.astimezone(timezone.utc).date().isoformat()
        if self.state.day != today:
            self.state.day = today
            self.state.day_start_equity = equity
        if self.state.peak_equity <= 0:
            self.state.peak_equity = equity
        self.state.peak_equity = max(self.state.peak_equity, equity)
        self.prune_recent_orders(now)
        self.save()

    def prune_recent_orders(self, now: datetime, window_seconds: int = 300) -> None:
        cutoff = now.astimezone(timezone.utc) - timedelta(seconds=window_seconds)
        kept = {}
        for key, raw in self.state.recent_orders.items():
            try:
                stamp = datetime.fromisoformat(raw)
            except (TypeError, ValueError):
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp >= cutoff:
                kept[key] = stamp.astimezone(timezone.utc).isoformat()
        self.state.recent_orders = kept

    def register_order_key(self, key: str, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        self.state.recent_orders[key] = now.astimezone(timezone.utc).isoformat()
        self.save()

    def register_position(self, position: StoredPosition) -> None:
        self.state.positions[str(position.ticket)] = position
        self.save()

    def sync_open_tickets(self, tickets: set[int]) -> None:
        self.state.positions = {
            key: value for key, value in self.state.positions.items()
            if value.ticket in tickets
        }
        self.save()

    def open_risk(self) -> tuple[OpenRisk, ...]:
        return tuple(value.as_open_risk() for value in self.state.positions.values())

    def engine_state(self, engine: str) -> EngineState:
        return self.state.engines.setdefault(engine, EngineState())

    def guard_multiplier(self, engine: str, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        if engine in DISABLED_ENGINES:
            return 0.0
        if engine in PROTECTED_ENGINES:
            return 1.0
        if engine not in ADAPTIVE_ENGINES:
            return 0.0

        state = self.engine_state(engine)
        if state.probe_in_flight:
            return 0.0
        if state.disabled_until:
            until = datetime.fromisoformat(state.disabled_until)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if now < until:
                return 0.0
            return PROBE_MULTIPLIER

        values = state.history_r[-ROLLING_TRADES:]
        if len(values) < MINIMUM_TRADES:
            return 1.0
        pf = profit_factor(values)
        net_r = sum(values)
        if pf >= FULL_PF and net_r > FULL_NET_R:
            return 1.0
        if pf >= REDUCED_PF and net_r > REDUCED_NET_R:
            return REDUCED_MULTIPLIER

        state.disabled_until = (now + timedelta(days=COOLDOWN_DAYS)).isoformat()
        self.save()
        return 0.0

    def mark_order_opened(self, engine: str, multiplier: float) -> None:
        if engine not in ADAPTIVE_ENGINES:
            return
        state = self.engine_state(engine)
        if abs(multiplier - PROBE_MULTIPLIER) <= 1e-9:
            state.probe_in_flight = True
            state.disabled_until = None
            self.save()

    def record_trade_result(self, engine: str, r_multiple: float,
                            now: Optional[datetime] = None) -> None:
        if not math.isfinite(r_multiple):
            raise ValueError("R multiple must be finite.")
        now = now or datetime.now(timezone.utc)
        state = self.engine_state(engine)
        state.history_r.append(float(r_multiple))
        state.history_r = state.history_r[-64:]
        if state.probe_in_flight:
            state.probe_in_flight = False
            if r_multiple <= 0:
                state.disabled_until = (now + timedelta(days=COOLDOWN_DAYS)).isoformat()
            else:
                state.disabled_until = None
        self.save()


def profit_factor(values: list[float]) -> float:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    if gross_loss:
        return gross_profit / gross_loss
    return math.inf if gross_profit else 0.0
