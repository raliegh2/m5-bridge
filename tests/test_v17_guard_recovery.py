from __future__ import annotations

import pandas as pd

from v17_guard import GuardConfig, recovery_decision, risk_multiplier
from v17_guard_recovery_runner import validated_policy


def losing_history():
    return {"EURUSD_SWING_BREAKOUT": [-1.0] * 24}


def test_legacy_guard_restarts_cooldown_without_mature_probe() -> None:
    engine = "EURUSD_SWING_BREAKOUT"
    now = pd.Timestamp("2026-01-31T00:00:00Z")
    disabled = {engine: pd.Timestamp("2026-01-30T00:00:00Z")}
    multiplier = risk_multiplier(engine, losing_history(), now, disabled)
    assert multiplier == 0.0
    assert disabled[engine] > now


def test_recovery_guard_issues_probe_after_expired_cooldown() -> None:
    engine = "EURUSD_SWING_BREAKOUT"
    now = pd.Timestamp("2026-01-31T00:00:00Z")
    disabled = {engine: pd.Timestamp("2026-01-30T00:00:00Z")}
    decision = recovery_decision(engine, losing_history(), now, disabled, {})
    assert decision.is_probe
    assert decision.multiplier == GuardConfig().probe_multiplier
    assert decision.reason == "recovery_probe"
    assert disabled[engine] == pd.Timestamp("2026-01-30T00:00:00Z")


def test_open_probe_blocks_additional_engine_signals() -> None:
    engine = "EURUSD_SWING_BREAKOUT"
    now = pd.Timestamp("2026-01-31T00:00:00Z")
    decision = recovery_decision(
        engine,
        losing_history(),
        now,
        {},
        {engine: now + pd.Timedelta(days=2)},
    )
    assert decision.multiplier == 0.0
    assert decision.reason == "probe_in_flight"


def test_precision_satellite_is_never_scaled_or_disabled() -> None:
    now = pd.Timestamp("2026-01-31T00:00:00Z")
    decision = recovery_decision(
        "GBPUSD_V10_PRECISION",
        {"GBPUSD_V10_PRECISION": [-1.0] * 50},
        now,
        {"GBPUSD_V10_PRECISION": now + pd.Timedelta(days=30)},
        {},
    )
    assert decision.multiplier == 1.0
    assert decision.reason == "precision_passthrough"


def test_policy_requires_profitable_validation_and_holdout() -> None:
    selection = {
        "EURUSD": {
            "status": "QUALIFIED",
            "validation": {"net_r": 2.0, "profit_factor": 1.1},
            "holdout": {"net_r": 1.0, "profit_factor": 1.05},
        },
        "GBPUSD": {
            "status": "QUALIFIED",
            "validation": {"net_r": 4.0, "profit_factor": 1.4},
            "holdout": {"net_r": -1.0, "profit_factor": 0.9},
        },
    }
    candidates = pd.DataFrame(
        [
            {"symbol": "EURUSD", "engine": "EURUSD_SWING_BREAKOUT"},
            {"symbol": "GBPUSD", "engine": "GBPUSD_SWING_BREAKOUT"},
            {"symbol": "GBPUSD", "engine": "GBPUSD_V10_PRECISION"},
        ]
    )
    assert validated_policy(selection, candidates) == {
        "EURUSD_SWING_BREAKOUT": 1.10
    }
