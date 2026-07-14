from datetime import datetime, timedelta, timezone

from mt5_ai_bridge.gbpjpy_guard import GBPJPYGuardStore


UTC = timezone.utc


def test_gbpjpy_guard_reduces_risk_without_resetting_after_one_win(tmp_path):
    path = tmp_path / "guard.json"
    guard = GBPJPYGuardStore(str(path))
    start = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    ready = guard.decision(now=start)
    assert ready.ok
    assert ready.risk_cap_percent == 0.20

    guard.record_result(-1.0, now=start + timedelta(hours=1))
    reduced = guard.decision(now=start + timedelta(hours=1, minutes=1))
    assert reduced.ok
    assert reduced.code == "GBPJPY_REDUCED_RISK"
    assert reduced.risk_cap_percent == 0.10

    guard.record_result(0.5, now=start + timedelta(hours=2))
    still_reduced = guard.decision(now=start + timedelta(hours=2, minutes=1))
    assert still_reduced.ok
    assert still_reduced.risk_cap_percent == 0.10
    assert guard.state.loss_pressure == 0.5

    restored = GBPJPYGuardStore(str(path))
    assert restored.state.loss_pressure == 0.5
    assert restored.decision(now=start + timedelta(hours=2, minutes=2)).risk_cap_percent == 0.10


def test_gbpjpy_guard_blocks_stacking_and_stops_after_two_losses(tmp_path):
    guard = GBPJPYGuardStore(str(tmp_path / "guard.json"))
    start = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)

    stacked = guard.decision(open_positions=1, now=start)
    assert not stacked.ok
    assert stacked.code == "GBPJPY_ONE_POSITION_LIMIT"

    guard.record_result(-0.7, now=start + timedelta(hours=1))
    guard.record_result(-0.6, now=start + timedelta(hours=2))
    stopped = guard.decision(now=start + timedelta(hours=2, minutes=1))
    assert not stopped.ok
    assert stopped.code == "GBPJPY_DAILY_STOP"

    next_day = guard.decision(now=start + timedelta(days=1, hours=5))
    assert next_day.ok
    assert next_day.risk_cap_percent == 0.20


def test_rolling_net_loss_starts_four_hour_cooldown(tmp_path):
    guard = GBPJPYGuardStore(str(tmp_path / "guard.json"))
    start = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)

    guard.record_result(-1.2, now=start)
    # A small win does not erase the rolling loss condition.
    guard.record_result(0.1, now=start + timedelta(minutes=30))
    guard.record_result(-1.0, now=start + timedelta(hours=1))

    blocked = guard.decision(now=start + timedelta(hours=1, minutes=1))
    assert not blocked.ok
    assert blocked.code in {"GBPJPY_DAILY_STOP", "GBPJPY_COOLDOWN"}
