from datetime import datetime, timezone
import json

from mt5_ai_bridge.v14_21_demo_auto_execution import (
    V1421DemoAutoConfig,
    V1421DemoAutoExecutor,
)
from tests.test_v14_21_demo_auto_execution import FakeClient


def test_reconciled_close_is_written_with_broker_net_r(tmp_path):
    config = V1421DemoAutoConfig(
        execution_mode="AUTO",
        learning_auto=True,
        state_path=str(tmp_path / "state.json"),
        expected_login=12345,
        expected_server="UnitTest-Demo",
        demo_acknowledgement="DEMO_ONLY",
        learning_jsonl_path=str(tmp_path / "learning.jsonl"),
        learning_document_path=str(tmp_path / "learning.md"),
    )
    executor = V1421DemoAutoExecutor(FakeClient(), config)
    closed_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    executor.state.record_closed(
        {
            "ticket": 77,
            "signal_key": "EURUSD:test",
            "symbol": "EURUSD",
            "engine": "EURUSD_SWING_CORE",
            "setup": "H4_DONCHIAN_BREAKOUT",
            "side": "BUY",
            "mode": "V12",
            "risk_dollars": 10.0,
            "metadata": {"source": "closed_mt5_v12"},
        },
        15.0,
        closed_at,
    )
    executor._flush_learning_closes()
    rows = [
        json.loads(line)
        for line in (tmp_path / "learning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["event"] == "TRADE_CLOSED"
    assert rows[-1]["r_multiple"] == 1.5
    assert "Broker-net P/L: 15.0" in (
        tmp_path / "learning.md"
    ).read_text(encoding="utf-8")
