from __future__ import annotations

from mt5_ai_bridge.v14_21_demo_auto_execution import V1421DemoAutoConfig
from v14_21_demo_auto_runner import _startup_banner
from v14_3_satellite_bot_m1 import _restore_persistent_scan_time


def test_enabled_gold_banner_reports_active_and_auto_connected(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GOLD_ENGINE", "on")
    monkeypatch.setenv("GOLD_RISK_PERCENT", "0.25")
    _startup_banner(V1421DemoAutoConfig(), "http://127.0.0.1:8800/")
    text = capsys.readouterr().out
    assert "AUTO CONNECTED" in text
    assert "Gold trigger/context    : ACTIVE" in text
    assert "when enabled" not in text
    assert "SHADOW_ONLY" in text
    assert "200 closed outcomes required per bucket" in text


def test_disabled_gold_banner_is_explicit(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GOLD_ENGINE", "off")
    _startup_banner(V1421DemoAutoConfig(), "http://127.0.0.1:8800/")
    text = capsys.readouterr().out
    assert "Gold metals engine      : OFF" in text
    assert "Gold trigger/context    : DISABLED" in text


def test_futures_feed_banner_reports_missing_key(monkeypatch, capsys) -> None:
    monkeypatch.setenv("V14_25_FUTURES_ORDER_FLOW", "true")
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    _startup_banner(V1421DemoAutoConfig(), "http://127.0.0.1:8800/")
    text = capsys.readouterr().out
    assert "Centralized futures flow: API_KEY_REQUIRED" in text
    assert "shadow/forward-gated" in text


def test_persistent_gold_scan_time_is_restored_after_restart() -> None:
    diagnostics = {
        "scan_schedule": {
            "GOLD": {
                "trigger": "M30",
                "timeframes": ["M30", "H4"],
                "last_scan_at": None,
            }
        }
    }
    _restore_persistent_scan_time(
        diagnostics,
        "GOLD",
        {"recorded_at": "2026-07-24T01:00:05+00:00"},
    )
    assert (
        diagnostics["scan_schedule"]["GOLD"]["last_scan_at"]
        == "2026-07-24T01:00:05+00:00"
    )
