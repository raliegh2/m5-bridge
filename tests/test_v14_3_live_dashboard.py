from __future__ import annotations

import json
from types import SimpleNamespace

from mt5_ai_bridge.v14_3_live_dashboard import DASHBOARD_HTML, LiveDashboard
from v14_3_satellite_live_runner import (
    ENGINE_REGISTRY,
    ENGINE_SCAN_PROFILES,
    _decision_rationale,
    _engine_status,
    apply_engine_runtime_metadata,
)


def test_dashboard_snapshot_is_written_atomically(tmp_path) -> None:
    path = tmp_path / "dashboard.json"
    dashboard = LiveDashboard(path)
    dashboard.write({"runner_status": "RUNNING", "account": {"balance": 5000.0}})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["runner_status"] == "RUNNING"
    assert payload["account"]["balance"] == 5000.0
    assert not path.with_suffix(".json.tmp").exists()


def test_engine_registry_covers_four_funded_symbols_and_both_modes() -> None:
    assert {symbol for symbol, _mode, _engine in ENGINE_REGISTRY} == {
        "GBPUSD", "EURUSD", "GBPJPY", "AUDUSD"
    }
    assert {mode for _symbol, mode, _engine in ENGINE_REGISTRY} == {"V12", "ICT"}
    assert {engine for _symbol, _mode, engine in ENGINE_REGISTRY} <= set(
        ENGINE_SCAN_PROFILES
    )


def test_decision_rationale_is_rule_trace() -> None:
    signal = SimpleNamespace(
        engine="EURUSD_SWING_CORE",
        side="BUY",
        setup="H4_DONCHIAN_BREAKOUT",
        requested_risk_percent=0.25,
        stop_pips=30.0,
        target_pips=90.0,
        metadata={"source": "closed_mt5_v12", "timeframe": "H4"},
    )
    result = SimpleNamespace(code="READ_ONLY_PROPOSAL", message="No order sent")
    rationale = _decision_rationale(signal, result)
    assert "completed H4 candle" in rationale
    assert "READ_ONLY_PROPOSAL" in rationale
    assert "SL 30.0 pips" in rationale


def test_engine_status_marks_matching_candidate() -> None:
    signal = SimpleNamespace(engine="EURUSD_SWING_CORE")
    results = [{
        "signal": {"engine": "EURUSD_SWING_CORE"},
        "result": {"code": "ORDER_FILLED"},
    }]
    statuses = _engine_status(
        [signal],
        results,
        {"legacy_gbp_ict_provider": "PROVIDER_NOT_INSTALLED"},
    )
    eurusd = next(item for item in statuses if item["engine"] == "EURUSD_SWING_CORE")
    gbpusd_ict = next(item for item in statuses if item["engine"] == "ICT_V14_3_GBPUSD")
    assert eurusd["status"] == "SIGNAL"
    assert "ORDER_FILLED" in eurusd["rationale"]
    assert gbpusd_ict["status"] == "PROVIDER_WAIT"


def test_engine_status_preserves_latest_rejection_between_scans() -> None:
    decisions = [{
        "time": "2026-07-23T01:00:00+00:00",
        "engine": "EURUSD_SWING_CORE",
        "code": "V14_4_SPREAD_COST_GUARD",
        "rationale": "Spread cost exceeded the configured cap.",
    }]
    statuses = _engine_status(
        [],
        [],
        {"legacy_gbp_ict_provider": "READY"},
        decisions,
    )
    eurusd = next(
        item for item in statuses
        if item["engine"] == "EURUSD_SWING_CORE"
    )
    assert eurusd["status"] == "LAST_REJECTED"
    assert "V14_4_SPREAD_COST_GUARD" in eurusd["rationale"]


def test_engine_runtime_metadata_proves_scheduler_and_auto_wiring() -> None:
    engines = apply_engine_runtime_metadata(
        [{"engine": "EURUSD_ICT_LIQUIDITY", "symbol": "EURUSD"}],
        {
            "FX_PORTFOLIO": {
                "last_scan_at": "2026-07-23T22:00:00+00:00",
            }
        },
    )
    assert engines[0]["timeframes"] == ["H1", "H4", "D1"]
    assert engines[0]["trigger"] == "H1"
    assert engines[0]["automatic_runner_connected"] is True
    assert engines[0]["last_scan_at"] == "2026-07-23T22:00:00+00:00"


def test_dashboard_exposes_order_flow_and_scan_schedule() -> None:
    assert 'id="orderflow"' in DASHBOARD_HTML
    assert 'id="futuresflow"' in DASHBOARD_HTML
    assert 'id="flowforward"' in DASHBOARD_HTML
    assert 'id="schedule"' in DASHBOARD_HTML
    assert 'id="scanlatest"' in DASHBOARD_HTML
    assert 'id="scanaudit"' in DASHBOARD_HTML
    assert "AUTO CONNECTED" in DASHBOARD_HTML
