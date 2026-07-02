from datetime import datetime, timedelta, timezone

from mt5_ai_bridge.v9_policy import (
    AtomicBarRegistry,
    CalendarEvent,
    EventBlackoutCalendar,
    EvaluationCache,
    evaluate_entry_gate,
)

UTC = timezone.utc


def test_v9_hour_gate():
    allowed = evaluate_entry_gate(
        engine="GBPUSD_SATELLITE_V3",
        symbol="GBPUSD",
        signal_end=datetime(2026, 1, 5, 10, 15, tzinfo=UTC),
        now=datetime(2026, 1, 5, 10, 16, tzinfo=UTC),
    )
    blocked = evaluate_entry_gate(
        engine="GBPUSD_SATELLITE_V3",
        symbol="GBPUSD",
        signal_end=datetime(2026, 1, 5, 9, 15, tzinfo=UTC),
        now=datetime(2026, 1, 5, 9, 16, tzinfo=UTC),
    )
    assert allowed.allowed
    assert not blocked.allowed
    assert blocked.reason == "strategy_hour_filter"


def test_event_blackout_blocks_relevant_currency():
    event_time = datetime(2026, 1, 28, 16, 0, tzinfo=UTC)
    calendar = EventBlackoutCalendar([
        CalendarEvent(event_time, "USD", "high", "FOMC")
    ])
    decision = evaluate_entry_gate(
        engine="GBPUSD_SATELLITE_V3",
        symbol="GBPUSD",
        signal_end=event_time - timedelta(minutes=10),
        now=event_time - timedelta(minutes=9),
        event_calendar=calendar,
    )
    assert not decision.allowed
    assert decision.reason == "high_impact_event_blackout"


def test_atomic_registry_and_cache(tmp_path):
    registry = AtomicBarRegistry(tmp_path / "bars.json")
    bar = datetime(2026, 1, 5, 10, 15, tzinfo=UTC)
    assert not registry.already_processed("engine", "GBPUSD", bar)
    registry.mark_processed("engine", "GBPUSD", bar)
    assert registry.already_processed("engine", "GBPUSD", bar)

    calls = {"count": 0}
    cache = EvaluationCache[int](ttl_seconds=60)

    def compute():
        calls["count"] += 1
        return 42

    assert cache.get_or_compute("key", compute) == 42
    assert cache.get_or_compute("key", compute) == 42
    assert calls["count"] == 1
