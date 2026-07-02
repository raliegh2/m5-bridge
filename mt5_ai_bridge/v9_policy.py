"""Operational safeguards for the Strategy Engine V9 candidate.

The module is independent from MetaTrader 5 so the same rules can be exercised
by the live controller, historical backtests, and unit tests.
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Generic, Iterable, TypeVar

T = TypeVar("T")
UTC = timezone.utc


@dataclass(frozen=True)
class V9Policy:
    allowed_gbpusd_satellite_hours_utc: frozenset[int] = frozenset(
        {7, 10, 11, 12, 14, 15, 16}
    )
    max_signal_age_seconds: int = 120
    event_blackout_before_minutes: int = 30
    event_blackout_after_minutes: int = 30
    evaluation_cache_seconds: float = 5.0
    max_spread_pips: float = 1.5

    def __post_init__(self) -> None:
        if not self.allowed_gbpusd_satellite_hours_utc:
            raise ValueError("At least one V9 entry hour is required")
        if any(hour < 0 or hour > 23 for hour in self.allowed_gbpusd_satellite_hours_utc):
            raise ValueError("Entry hours must be between 0 and 23 UTC")
        if self.max_signal_age_seconds < 0:
            raise ValueError("max_signal_age_seconds cannot be negative")
        if self.evaluation_cache_seconds < 0:
            raise ValueError("evaluation_cache_seconds cannot be negative")


DEFAULT_V9_POLICY = V9Policy()


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return value.astimezone(UTC)


def evaluate_entry_gate(
    *,
    engine: str,
    symbol: str,
    signal_end: datetime,
    now: datetime | None = None,
    spread_pips: float | None = None,
    event_calendar: "EventBlackoutCalendar | None" = None,
    policy: V9Policy = DEFAULT_V9_POLICY,
) -> GateDecision:
    signal_utc = ensure_utc(signal_end)
    now_utc = ensure_utc(now or datetime.now(UTC))
    if signal_utc > now_utc + timedelta(seconds=5):
        return GateDecision(False, "signal_from_future")
    if (now_utc - signal_utc).total_seconds() > policy.max_signal_age_seconds:
        return GateDecision(False, "stale_signal")
    if spread_pips is not None:
        if spread_pips < 0:
            return GateDecision(False, "invalid_spread")
        if spread_pips > policy.max_spread_pips:
            return GateDecision(False, "spread_too_wide")

    is_v9_satellite = (
        engine.upper() in {"GBPUSD_SATELLITE_V2", "GBPUSD_SATELLITE_V3"}
        and symbol.upper() == "GBPUSD"
    )
    if is_v9_satellite and signal_utc.hour not in policy.allowed_gbpusd_satellite_hours_utc:
        return GateDecision(False, "strategy_hour_filter")
    if event_calendar and event_calendar.is_blocked(
        symbol=symbol,
        at=signal_utc,
        before_minutes=policy.event_blackout_before_minutes,
        after_minutes=policy.event_blackout_after_minutes,
    ):
        return GateDecision(False, "high_impact_event_blackout")
    return GateDecision(True, "allowed")


@dataclass(frozen=True)
class CalendarEvent:
    time: datetime
    currency: str
    impact: str
    event: str


class EventBlackoutCalendar:
    def __init__(self, events: Iterable[CalendarEvent] = ()) -> None:
        self._events = tuple(sorted(events, key=lambda item: item.time))

    @classmethod
    def from_csv(cls, path: str | Path) -> "EventBlackoutCalendar":
        events: list[CalendarEvent] = []
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"time", "currency", "impact", "event"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"Event CSV missing columns: {sorted(missing)}")
            for row in reader:
                raw = (row.get("time") or "").strip()
                if not raw:
                    continue
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                events.append(CalendarEvent(
                    time=ensure_utc(parsed),
                    currency=(row.get("currency") or "").strip().upper(),
                    impact=(row.get("impact") or "").strip().lower(),
                    event=(row.get("event") or "").strip(),
                ))
        return cls(events)

    def is_blocked(self, *, symbol: str, at: datetime,
                   before_minutes: int, after_minutes: int) -> bool:
        at_utc = ensure_utc(at)
        currencies = {symbol[:3].upper(), symbol[3:6].upper()}
        for event in self._events:
            if event.impact != "high" or event.currency not in currencies:
                continue
            if (event.time - timedelta(minutes=before_minutes)
                    <= at_utc <= event.time + timedelta(minutes=after_minutes)):
                return True
        return False


class AtomicBarRegistry:
    """Persist completed-bar processing state using atomic replacement."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _key(engine: str, symbol: str) -> str:
        return f"{engine.upper()}::{symbol.upper()}"

    def already_processed(self, engine: str, symbol: str, bar_end: datetime) -> bool:
        target = ensure_utc(bar_end).isoformat()
        with self._lock:
            return self._load().get(self._key(engine, symbol)) == target

    def mark_processed(self, engine: str, symbol: str, bar_end: datetime) -> None:
        target = ensure_utc(bar_end).isoformat()
        with self._lock:
            payload = self._load()
            payload[self._key(engine, symbol)] = target
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)


@dataclass
class _CacheItem(Generic[T]):
    expires_at: float
    value: T


class EvaluationCache(Generic[T]):
    """Thread-safe TTL cache that avoids duplicate MT5 history requests."""

    def __init__(self, ttl_seconds: float = DEFAULT_V9_POLICY.evaluation_cache_seconds) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._items: dict[str, _CacheItem[T]] = {}
        self._lock = threading.RLock()

    def get_or_compute(self, key: str, factory: Callable[[], T]) -> T:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item and item.expires_at >= now:
                return item.value
        value = factory()
        with self._lock:
            self._items[key] = _CacheItem(now + self.ttl_seconds, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
