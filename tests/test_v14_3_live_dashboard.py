from __future__ import annotations

import json
from types import SimpleNamespace

from mt5_ai_bridge.v14_3_live_dashboard import LiveDashboard
from v14_3_satellite_live_runner import (
    ENGINE_REGISTRY,
    _decision_rationale,
    _engine_status,
)


def test_dashboard_snapshot_is_written_atomically(tmp_path) -> None:
    path = tmp_path / "dashboard.json"
    dashboard = LiveDashboard(path)
    dashboard.write({"runner_status": "RUNNING", "account": {"balance": 5000.0}})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["runner_status"] == "RUNNING"
    assert payload["account"]["balance"] == 5000.0
    assert not path.with_suffix(".json.tmp").exists()


def test_engine_registry_covers_all_five_symbols_and_both_modes() -> None:
    assert {symbol for symbol, _mode, _engine in ENGINE_REGISTRY} == {
        "GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"
    }
    assert {mode for _symbol, mode, _engine in ENGINE_REGISTRY} == {"V12", "ICT"}


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
