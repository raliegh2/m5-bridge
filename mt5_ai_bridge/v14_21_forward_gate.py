"""Objective forward-evidence gate for the frozen V14.21 portfolio."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from .v14_21_portfolio_manifest import (
    HISTORICAL_CUTOFF,
    MANIFEST_ID,
    manifest_sha256,
)


SCHEMA_VERSION = 1
MINIMUM_CALENDAR_DAYS = 56
MINIMUM_ACTIVE_WEEKS = 8
MINIMUM_ACCEPTED_TRADES = 200
MINIMUM_PROFIT_FACTOR = 1.10
MAXIMUM_DRAWDOWN_PERCENT = 9.50
MINIMUM_TRADES_PER_WEEK = 10.0
REQUIRED_COLUMNS = {
    "signal_time",
    "closed_at",
    "accepted",
    "risk_dollars",
    "pnl",
    "equity_before",
    "equity_after",
    "rule_violation",
    "future_data",
    "hard_stop_breach",
    "manifest_id",
    "manifest_sha256",
}


@dataclass(frozen=True)
class EvidenceValidation:
    passed: bool
    code: str
    message: str
    payload: dict[str, Any] | None = None


def _boolean(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin(
        {"1", "true", "yes", "on"}
    )


def _maximum_drawdown(equity: list[float]) -> float:
    peak = 0.0
    maximum = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak * 100.0)
    return maximum


def evaluate_forward_records(
    records: pd.DataFrame,
    *,
    phase: str = "SHADOW",
) -> dict[str, Any]:
    missing = REQUIRED_COLUMNS - set(records.columns)
    if missing:
        raise ValueError(f"Forward ledger missing columns: {sorted(missing)}")
    frame = records.copy()
    frame["signal_time"] = pd.to_datetime(
        frame["signal_time"], utc=True, errors="coerce", format="mixed"
    )
    frame["closed_at"] = pd.to_datetime(
        frame["closed_at"], utc=True, errors="coerce", format="mixed"
    )
    for column in ("risk_dollars", "pnl", "equity_before", "equity_after"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[list(REQUIRED_COLUMNS - {
        "accepted", "rule_violation", "future_data", "hard_stop_breach",
        "manifest_id", "manifest_sha256",
    })].isna().any().any():
        raise ValueError("Forward ledger contains missing or invalid numeric/time values")
    accepted = frame[_boolean(frame["accepted"])].copy()
    accepted = accepted.sort_values(["closed_at", "signal_time"])
    if accepted.empty:
        raise ValueError("Forward ledger has no accepted closed trades")
    if (accepted["risk_dollars"] <= 0).any():
        raise ValueError("Every accepted trade requires positive risk_dollars")

    risk_multiple = accepted["pnl"] / accepted["risk_dollars"]
    gross_profit = float(risk_multiple[risk_multiple > 0].sum())
    gross_loss = abs(float(risk_multiple[risk_multiple < 0].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    start = accepted["signal_time"].min()
    end = accepted["closed_at"].max()
    calendar_days = max(0.0, (end - start).total_seconds() / 86400.0)
    active_weeks = len({
        (stamp.isocalendar().year, stamp.isocalendar().week)
        for stamp in accepted["signal_time"]
    })
    trades_per_week = len(accepted) / active_weeks if active_weeks else 0.0
    equity = [float(accepted.iloc[0]["equity_before"])] + [
        float(value) for value in accepted["equity_after"]
    ]
    manifest_ok = bool(
        (accepted["manifest_id"].astype(str) == MANIFEST_ID).all()
        and (
            accepted["manifest_sha256"].astype(str)
            == manifest_sha256()
        ).all()
    )
    fresh = bool((accepted["signal_time"] > pd.Timestamp(HISTORICAL_CUTOFF)).all())
    not_future_dated = bool(
        end <= pd.Timestamp(datetime.now(timezone.utc)) + pd.Timedelta(minutes=5)
    )
    chronological = bool((accepted["closed_at"] >= accepted["signal_time"]).all())
    rule_violations = int(_boolean(frame["rule_violation"]).sum())
    future_data_uses = int(_boolean(frame["future_data"]).sum())
    hard_stop_breaches = int(_boolean(frame["hard_stop_breach"]).sum())
    metrics = {
        "accepted_trades": int(len(accepted)),
        "calendar_days": round(calendar_days, 4),
        "active_weeks": active_weeks,
        "average_weekly_accepted_trades": round(trades_per_week, 4),
        "profit_factor": round(profit_factor, 6) if profit_factor is not None else None,
        "net_r": round(float(risk_multiple.sum()), 6),
        "maximum_drawdown_percent": round(_maximum_drawdown(equity), 6),
        "rule_violations": rule_violations,
        "future_data_uses": future_data_uses,
        "hard_stop_breaches": hard_stop_breaches,
        "first_signal_at": start.isoformat(),
        "last_close_at": end.isoformat(),
    }
    checks = {
        "manifest_matches": manifest_ok,
        "fresh_after_historical_cutoff": fresh,
        "not_future_dated": not_future_dated,
        "chronological": chronological,
        "minimum_calendar_days": calendar_days >= MINIMUM_CALENDAR_DAYS,
        "minimum_active_weeks": active_weeks >= MINIMUM_ACTIVE_WEEKS,
        "minimum_accepted_trades": len(accepted) >= MINIMUM_ACCEPTED_TRADES,
        "minimum_weekly_frequency": trades_per_week >= MINIMUM_TRADES_PER_WEEK,
        "positive_net_r": metrics["net_r"] > 0,
        "minimum_profit_factor": (
            profit_factor is not None and profit_factor >= MINIMUM_PROFIT_FACTOR
        ),
        "maximum_drawdown": (
            metrics["maximum_drawdown_percent"] <= MAXIMUM_DRAWDOWN_PERCENT
        ),
        "zero_rule_violations": rule_violations == 0,
        "zero_future_data_uses": future_data_uses == 0,
        "zero_hard_stop_breaches": hard_stop_breaches == 0,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": str(phase).upper(),
        "manifest_id": MANIFEST_ID,
        "manifest_sha256": manifest_sha256(),
        "historical_cutoff": HISTORICAL_CUTOFF.isoformat(),
        "source_sha256": "",
        "source_path": "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "checks": checks,
        "passed": all(checks.values()),
    }


def evidence_from_csv(path: str | Path, *, phase: str = "SHADOW") -> dict[str, Any]:
    source = Path(path).resolve()
    payload = evaluate_forward_records(pd.read_csv(source), phase=phase)
    payload["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    payload["source_path"] = str(source)
    return payload


def write_evidence(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def validate_evidence_payload(
    payload: dict[str, Any],
    *,
    required_phase: str = "SHADOW",
) -> EvidenceValidation:
    if int(payload.get("schema_version", 0) or 0) != SCHEMA_VERSION:
        return EvidenceValidation(False, "FORWARD_EVIDENCE_SCHEMA", "Unsupported evidence schema")
    if str(payload.get("phase", "")).upper() != required_phase.upper():
        return EvidenceValidation(False, "FORWARD_EVIDENCE_PHASE", "Wrong forward-evidence phase")
    if payload.get("manifest_id") != MANIFEST_ID or payload.get("manifest_sha256") != manifest_sha256():
        return EvidenceValidation(False, "FORWARD_EVIDENCE_MANIFEST", "Evidence does not match the frozen portfolio")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("source_sha256", ""))):
        return EvidenceValidation(False, "FORWARD_EVIDENCE_SOURCE_HASH", "Evidence source hash is missing or invalid")
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    required_checks = {
        "manifest_matches",
        "fresh_after_historical_cutoff",
        "not_future_dated",
        "chronological",
        "minimum_calendar_days",
        "minimum_active_weeks",
        "minimum_accepted_trades",
        "minimum_weekly_frequency",
        "positive_net_r",
        "minimum_profit_factor",
        "maximum_drawdown",
        "zero_rule_violations",
        "zero_future_data_uses",
        "zero_hard_stop_breaches",
    }
    thresholds_ok = bool(
        int(metrics.get("accepted_trades", 0) or 0) >= MINIMUM_ACCEPTED_TRADES
        and float(metrics.get("calendar_days", 0.0) or 0.0) >= MINIMUM_CALENDAR_DAYS
        and int(metrics.get("active_weeks", 0) or 0) >= MINIMUM_ACTIVE_WEEKS
        and float(metrics.get("average_weekly_accepted_trades", 0.0) or 0.0) >= MINIMUM_TRADES_PER_WEEK
        and float(metrics.get("net_r", 0.0) or 0.0) > 0
        and float(metrics.get("profit_factor", 0.0) or 0.0) >= MINIMUM_PROFIT_FACTOR
        and float(
            metrics.get("maximum_drawdown_percent")
            if metrics.get("maximum_drawdown_percent") is not None
            else 100.0
        ) <= MAXIMUM_DRAWDOWN_PERCENT
        and int(metrics.get("rule_violations", 1) or 0) == 0
        and int(metrics.get("future_data_uses", 1) or 0) == 0
        and int(metrics.get("hard_stop_breaches", 1) or 0) == 0
    )
    if not required_checks.issubset(checks) or not all(bool(checks[key]) for key in required_checks):
        return EvidenceValidation(False, "FORWARD_EVIDENCE_CHECKS", "One or more forward gates failed", payload)
    if not thresholds_ok or not bool(payload.get("passed")):
        return EvidenceValidation(False, "FORWARD_EVIDENCE_THRESHOLDS", "Forward metrics do not pass locked thresholds", payload)
    return EvidenceValidation(True, "FORWARD_EVIDENCE_CONFIRMED", "Locked fresh forward evidence passed", payload)


def validate_evidence_file(
    path: str | Path,
    *,
    required_phase: str = "SHADOW",
) -> EvidenceValidation:
    if not str(path).strip():
        return EvidenceValidation(False, "FORWARD_EVIDENCE_REQUIRED", "Forward evidence path is not configured")
    source = Path(path)
    if not source.is_file():
        return EvidenceValidation(False, "FORWARD_EVIDENCE_NOT_FOUND", f"Forward evidence file not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return EvidenceValidation(False, "FORWARD_EVIDENCE_INVALID", f"Cannot read forward evidence: {exc}")
    if not isinstance(payload, dict):
        return EvidenceValidation(False, "FORWARD_EVIDENCE_INVALID", "Forward evidence must be a JSON object")
    validated = validate_evidence_payload(payload, required_phase=required_phase)
    if not validated.passed:
        return validated
    source_path = str(payload.get("source_path", "")).strip()
    if not source_path:
        return EvidenceValidation(
            False,
            "FORWARD_EVIDENCE_SOURCE_REQUIRED",
            "Forward evidence does not identify its source ledger",
            payload,
        )
    source = Path(source_path)
    if not source.is_file():
        return EvidenceValidation(
            False,
            "FORWARD_EVIDENCE_SOURCE_NOT_FOUND",
            f"Forward evidence source ledger not found: {source}",
            payload,
        )
    if hashlib.sha256(source.read_bytes()).hexdigest() != payload["source_sha256"]:
        return EvidenceValidation(
            False,
            "FORWARD_EVIDENCE_SOURCE_CHANGED",
            "Forward source ledger changed after evidence was generated",
            payload,
        )
    try:
        rebuilt = evidence_from_csv(source, phase=required_phase)
    except (OSError, ValueError, TypeError) as exc:
        return EvidenceValidation(
            False,
            "FORWARD_EVIDENCE_REBUILD_FAILED",
            f"Forward source ledger cannot be re-evaluated: {exc}",
            payload,
        )
    if (
        rebuilt["metrics"] != payload.get("metrics")
        or rebuilt["checks"] != payload.get("checks")
        or rebuilt["passed"] != payload.get("passed")
    ):
        return EvidenceValidation(
            False,
            "FORWARD_EVIDENCE_REBUILD_MISMATCH",
            "Stored evidence does not match a fresh evaluation of its source ledger",
            payload,
        )
    return validated


__all__ = [
    "EvidenceValidation",
    "evaluate_forward_records",
    "evidence_from_csv",
    "validate_evidence_file",
    "validate_evidence_payload",
    "write_evidence",
]
