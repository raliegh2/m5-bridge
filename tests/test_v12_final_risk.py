from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mt5_ai_bridge.v12_final_risk import (
    BacktestExactLimits,
    OpenRisk,
    OrderIntent,
    PortfolioSnapshot,
    validate_order,
)
from mt5_ai_bridge.v12_final_state import StateStore


def intent(**overrides):
    data = dict(
        symbol="AUDUSD",
        engine="AUDUSD_TREND_PULLBACK",
        setup="D1_H4_EMA_PULLBACK_04_08UTC",
        side="BUY",
        requested_risk_percent=0.25,
        guard_multiplier=1.0,
        stop_pips=50.0,
        volume=0.025,
        pip_value_per_lot=10.0,
        spread_pips=1.0,
        order_key="unique",
    )
    data.update(overrides)
    return OrderIntent(**data)


def snapshot(**overrides):
    data = dict(
        balance=5000.0,
        equity=5000.0,
        day_start_equity=5000.0,
        peak_equity=5000.0,
        open_risk=(),
        recent_order_keys=frozenset(),
        is_demo_account=True,
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    data.update(overrides)
    return PortfolioSnapshot(**data)


def test_valid_audusd_order_passes() -> None:
    result = validate_order(intent(), snapshot())
    assert result.ok
    assert result.code == "APPROVED"
    assert result.expected_risk_percent == 0.25


def test_disabled_engines_are_rejected() -> None:
    result = validate_order(
        intent(
            symbol="GBPUSD",
            engine="GBPUSD_SWING_CORE",
            setup="H4_DONCHIAN_BREAKOUT",
            requested_risk_percent=0.20,
        ),
        snapshot(),
    )
    assert not result.ok
    assert result.code == "ENGINE_DISABLED"


def test_non_demo_account_is_rejected() -> None:
    result = validate_order(intent(), snapshot(is_demo_account=False))
    assert not result.ok
    assert result.code == "DEMO_ONLY"


def test_actual_broker_risk_cannot_exceed_profile() -> None:
    result = validate_order(intent(volume=0.04), snapshot())
    assert not result.ok
    assert result.code == "ACTUAL_RISK_TOO_HIGH"


def test_total_open_risk_is_hard_capped_at_1_5_percent() -> None:
    open_risk = (
        OpenRisk("GBPUSD", "GBPUSD_V10_PRECISION", "BUY", 0.50),
        OpenRisk("EURUSD", "EURUSD_SWING_CORE", "BUY", 0.25),
        OpenRisk("GBPJPY", "GBPJPY_SWING_CORE", "BUY", 0.15),
        OpenRisk("USDJPY", "USDJPY_SAFE_HAVEN_BREAKOUT", "BUY", 0.25),
        OpenRisk("GBPUSD", "GBPUSD_SWING_RETEST", "BUY", 0.15),
    )
    result = validate_order(intent(), snapshot(open_risk=open_risk))
    assert not result.ok
    assert result.code in {"MAX_POSITIONS", "MAX_OPEN_RISK"}


def test_new_symbol_cap_blocks_second_audusd_trade() -> None:
    result = validate_order(
        intent(),
        snapshot(open_risk=(OpenRisk("AUDUSD", "AUDUSD_TREND_PULLBACK", "BUY", 0.25),)),
    )
    assert not result.ok
    assert result.code == "SYMBOL_CAP"


def test_mixed_gbp_exposure_uses_lower_cap() -> None:
    result = validate_order(
        intent(
            symbol="GBPUSD",
            engine="GBPUSD_V10_PRECISION",
            setup="PRIMARY_16UTC_BREAKOUT",
            side="SELL",
            requested_risk_percent=0.50,
            stop_pips=50.0,
            volume=0.05,
        ),
        snapshot(open_risk=(OpenRisk("GBPJPY", "GBPJPY_SWING_CORE", "BUY", 0.15),)),
    )
    assert result.ok  # exactly 0.65%, the mixed-GBP ceiling

    blocked = validate_order(
        intent(
            symbol="GBPUSD",
            engine="GBPUSD_V10_PRECISION",
            setup="PRIMARY_16UTC_BREAKOUT",
            side="SELL",
            requested_risk_percent=0.50,
            stop_pips=50.0,
            volume=0.05,
        ),
        snapshot(open_risk=(OpenRisk("GBPJPY", "GBPJPY_SWING_CORE", "BUY", 0.20),)),
    )
    assert not blocked.ok
    assert blocked.code == "GBP_CORRELATION_CAP"


def test_daily_and_total_drawdown_stops() -> None:
    daily = validate_order(intent(), snapshot(equity=4920.0))
    assert not daily.ok
    assert daily.code == "DAILY_STOP"

    total = validate_order(
        intent(),
        snapshot(equity=4800.0, day_start_equity=4800.0, peak_equity=5100.0),
    )
    assert not total.ok
    assert total.code == "TOTAL_STOP"


def test_adaptive_guard_reduces_cools_and_probes(tmp_path) -> None:
    store = StateStore(str(tmp_path / "state.json"))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Thin but acceptable history -> 0.60x.
    for value in [1.0, -1.0] * 6:
        store.record_trade_result("USDJPY_SAFE_HAVEN_BREAKOUT", value, now)
    assert store.guard_multiplier("USDJPY_SAFE_HAVEN_BREAKOUT", now) == 0.60

    # Deteriorating rolling history starts a 45-day cooldown.
    state = store.engine_state("USDJPY_SAFE_HAVEN_BREAKOUT")
    state.history_r = [-1.0] * 16
    state.disabled_until = None
    store.save()
    assert store.guard_multiplier("USDJPY_SAFE_HAVEN_BREAKOUT", now) == 0.0
    assert store.guard_multiplier("USDJPY_SAFE_HAVEN_BREAKOUT", now + timedelta(days=46)) == 0.35

    store.mark_order_opened("USDJPY_SAFE_HAVEN_BREAKOUT", 0.35)
    assert store.guard_multiplier("USDJPY_SAFE_HAVEN_BREAKOUT", now + timedelta(days=46)) == 0.0
    store.record_trade_result("USDJPY_SAFE_HAVEN_BREAKOUT", 1.0, now + timedelta(days=47))
    assert not store.engine_state("USDJPY_SAFE_HAVEN_BREAKOUT").probe_in_flight


def test_state_survives_restart(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.update_equity(5000.0, datetime(2026, 1, 1, tzinfo=timezone.utc))
    store.register_order_key("abc", datetime(2026, 1, 1, tzinfo=timezone.utc))

    restored = StateStore(str(path))
    assert restored.state.day_start_equity == 5000.0
    assert "abc" in restored.state.recent_orders
