from datetime import datetime, timezone

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_21_portfolio_manifest import (
    MANIFEST_ID,
    manifest_sha256,
    signal_guard,
)


def candidate(**overrides):
    values = {
        "symbol": "EURUSD",
        "broker_symbol": "EURUSD",
        "engine": "EURUSD_SWING_CORE",
        "setup": "H4_DONCHIAN_BREAKOUT",
        "mode": "V12",
        "side": "BUY",
        "signal_time": datetime(2026, 7, 15, tzinfo=timezone.utc),
        "requested_risk_percent": 0.55,
        "stop_pips": 20.0,
        "target_pips": 60.0,
        "metadata": {},
    }
    values.update(overrides)
    return LiveSignal(**values)


def test_manifest_identity_is_stable_and_nonempty():
    assert MANIFEST_ID == "V14_21_FORWARD_CANDIDATE_2026_07_03"
    assert len(manifest_sha256()) == 64
    assert manifest_sha256() == manifest_sha256()


def test_locked_signal_is_accepted():
    assert signal_guard(candidate(), transmitting=True) is None


def test_unknown_or_mismatched_signal_fails_closed():
    assert signal_guard(
        candidate(engine="USDJPY_SAFE_HAVEN_BREAKOUT", symbol="USDJPY"),
        transmitting=False,
    )[0] == "PORTFOLIO_ENGINE_NOT_LOCKED"
    assert signal_guard(candidate(mode="ICT"), transmitting=False)[0] == (
        "PORTFOLIO_MODE_MISMATCH"
    )
    assert signal_guard(candidate(symbol="GBPUSD"), transmitting=False)[0] == (
        "PORTFOLIO_SYMBOL_MISMATCH"
    )


def test_unvalidated_gold_can_shadow_but_not_transmit():
    gold = candidate(
        symbol="XAUUSD",
        broker_symbol="XAUUSD",
        engine="GOLD_INTRADAY_M30",
        mode="GOLD",
        requested_risk_percent=0.25,
    )
    assert signal_guard(gold, transmitting=False) is None
    assert signal_guard(gold, transmitting=True)[0] == (
        "PORTFOLIO_ENGINE_SHADOW_ONLY"
    )
