from __future__ import annotations

import pandas as pd

from v12_plus_validated_assets_backtest import (
    GuardConfig,
    NEW_SYMBOLS,
    ORIGINAL_CAPS,
    PRECISION_ENGINE,
    _guard_decision,
    _position_reason,
    _proxy,
    _symbol_cap,
)


def test_precision_engine_bypasses_adaptive_guard() -> None:
    decision = _guard_decision(
        PRECISION_ENGINE,
        {PRECISION_ENGINE: [-1.0] * 20},
        pd.Timestamp("2022-01-01", tz="UTC"),
        {},
        {},
        GuardConfig(),
    )
    assert decision.multiplier == 1.0
    assert decision.reason == "precision_passthrough"


def test_mature_engine_gets_one_recovery_probe_after_cooldown() -> None:
    now = pd.Timestamp("2022-01-01", tz="UTC")
    disabled = {"AUDUSD_TREND_PULLBACK": now - pd.Timedelta(seconds=1)}
    probes = {}
    decision = _guard_decision(
        "AUDUSD_TREND_PULLBACK", {}, now, disabled, probes, GuardConfig()
    )
    assert decision.is_probe
    assert decision.multiplier == 0.5
    assert decision.reason == "recovery_probe"


def test_new_symbols_keep_quarter_percent_symbol_cap() -> None:
    for symbol in NEW_SYMBOLS:
        assert _symbol_cap(symbol, f"{symbol}_ENGINE", ORIGINAL_CAPS) == 0.25


def test_second_new_symbol_position_same_symbol_is_rejected() -> None:
    active = [
        {
            "symbol": "AUDUSD",
            "engine": "AUDUSD_TREND_PULLBACK",
            "side": 1,
            "risk_percent": 0.25,
        }
    ]
    incoming = _proxy(
        {
            "symbol": "AUDUSD",
            "engine": "AUDUSD_TREND_PULLBACK",
            "side": 1,
            "risk_percent": 0.25,
        }
    )
    assert _position_reason(active, incoming, ORIGINAL_CAPS) == "symbol_cap"
