from datetime import timedelta
import json

import pandas as pd
import pytest

from mt5_ai_bridge.v14_21_forward_gate import (
    evaluate_forward_records,
    validate_evidence_payload,
)
from mt5_ai_bridge.v14_21_portfolio_manifest import (
    HISTORICAL_CUTOFF,
    MANIFEST_ID,
    manifest_sha256,
)


def passing_records() -> pd.DataFrame:
    rows = []
    equity = 5000.0
    for index in range(200):
        signal_time = HISTORICAL_CUTOFF + timedelta(
            days=1 + index * 60 / 199,
        )
        risk = 10.0
        pnl = 15.0 if index % 2 == 0 else -10.0
        before = equity
        equity += pnl
        rows.append({
            "signal_time": signal_time.isoformat(),
            "closed_at": (signal_time + timedelta(hours=1)).isoformat(),
            "accepted": True,
            "risk_dollars": risk,
            "pnl": pnl,
            "equity_before": before,
            "equity_after": equity,
            "rule_violation": False,
            "future_data": False,
            "hard_stop_breach": False,
            "manifest_id": MANIFEST_ID,
            "manifest_sha256": manifest_sha256(),
        })
    return pd.DataFrame(rows)


def test_fresh_forward_ledger_passes_locked_gate():
    payload = evaluate_forward_records(passing_records())
    payload["source_sha256"] = "a" * 64
    assert payload["passed"] is True
    assert validate_evidence_payload(payload).passed is True


def test_reviewed_history_cannot_be_reused_as_forward_evidence():
    records = passing_records()
    records.loc[0, "signal_time"] = (
        HISTORICAL_CUTOFF - timedelta(days=1)
    ).isoformat()
    payload = evaluate_forward_records(records)
    assert payload["checks"]["fresh_after_historical_cutoff"] is False
    assert payload["passed"] is False


def test_future_dated_records_cannot_be_used_as_forward_evidence():
    records = passing_records()
    records["signal_time"] = (
        pd.to_datetime(records["signal_time"], format="mixed")
        + timedelta(days=3650)
    )
    records["closed_at"] = (
        pd.to_datetime(records["closed_at"], format="mixed")
        + timedelta(days=3650)
    )
    payload = evaluate_forward_records(records)
    assert payload["checks"]["not_future_dated"] is False
    assert payload["passed"] is False


def test_manifest_mismatch_and_rule_violation_fail_closed():
    records = passing_records()
    records.loc[0, "manifest_id"] = "ANOTHER_STRATEGY"
    records.loc[1, "rule_violation"] = True
    payload = evaluate_forward_records(records)
    assert payload["checks"]["manifest_matches"] is False
    assert payload["checks"]["zero_rule_violations"] is False


def test_forward_ledger_requires_equity_and_risk_fields():
    with pytest.raises(ValueError, match="missing columns"):
        evaluate_forward_records(passing_records().drop(columns=["equity_after"]))
