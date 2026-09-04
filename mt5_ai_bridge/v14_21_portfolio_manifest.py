"""Frozen identity for the V14.21 forward-validation portfolio.

Historical results and forward evidence are meaningful only when they refer to
the same signals that can reach ``order_send``.  This manifest is deliberately
code-owned: environment variables cannot silently add an engine or symbol.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


MANIFEST_ID = "V14_21_FORWARD_CANDIDATE_2026_07_03"
HISTORICAL_CUTOFF = datetime(2026, 7, 3, tzinfo=timezone.utc)


@dataclass(frozen=True)
class EngineRule:
    mode: str
    symbols: tuple[str, ...]
    maximum_risk_percent: float = 0.80
    transmission_allowed: bool = True


ENGINE_RULES: dict[str, EngineRule] = {
    "GBPUSD_V10_PRECISION": EngineRule("V12", ("GBPUSD",)),
    "GBPUSD_SWING_RETEST": EngineRule("V12", ("GBPUSD",)),
    "EURUSD_SWING_CORE": EngineRule("V12", ("EURUSD",)),
    "EURUSD_SWING_RETEST": EngineRule("V12", ("EURUSD",)),
    "GBPJPY_SWING_CORE": EngineRule("V12", ("GBPJPY",)),
    "AUDUSD_TREND_PULLBACK": EngineRule("V12", ("AUDUSD",)),
    "ICT_V14_3_GBPUSD": EngineRule("ICT", ("GBPUSD",)),
    "ICT_V14_3_GBPJPY": EngineRule("ICT", ("GBPJPY",)),
    "EURUSD_ICT_LIQUIDITY": EngineRule("ICT", ("EURUSD",)),
    "AUDUSD_ICT_ASIA_LONDON": EngineRule("ICT", ("AUDUSD",)),
    # This M30 engine is not the GOLD_DAILY_TREND sleeve in the V14.24 ledger.
    # It can collect shadow proposals but cannot transmit until it has its own
    # reproducible backtest and locked forward evidence.
    "GOLD_INTRADAY_M30": EngineRule(
        "GOLD",
        ("XAUUSD",),
        maximum_risk_percent=0.25,
        transmission_allowed=False,
    ),
}


def manifest_payload() -> dict[str, Any]:
    return {
        "manifest_id": MANIFEST_ID,
        "historical_cutoff": HISTORICAL_CUTOFF.isoformat(),
        "engines": {
            name: asdict(rule)
            for name, rule in sorted(ENGINE_RULES.items())
        },
    }


def manifest_sha256() -> str:
    encoded = json.dumps(
        manifest_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def signal_guard(
    signal: Any,
    *,
    transmitting: bool,
) -> tuple[str, str] | None:
    rule = ENGINE_RULES.get(str(signal.engine))
    if rule is None:
        return (
            "PORTFOLIO_ENGINE_NOT_LOCKED",
            f"Engine {signal.engine!r} is not in frozen manifest {MANIFEST_ID}",
        )
    if str(signal.mode).upper() != rule.mode:
        return (
            "PORTFOLIO_MODE_MISMATCH",
            f"{signal.engine} must run in {rule.mode} mode",
        )
    if str(signal.symbol).upper() not in rule.symbols:
        return (
            "PORTFOLIO_SYMBOL_MISMATCH",
            f"{signal.engine} is not approved for {signal.symbol}",
        )
    requested = float(signal.requested_risk_percent)
    if requested <= 0 or requested > rule.maximum_risk_percent + 1e-12:
        return (
            "PORTFOLIO_RISK_MISMATCH",
            f"{signal.engine} requested {requested:.3f}% above its locked "
            f"{rule.maximum_risk_percent:.3f}% ceiling",
        )
    if transmitting and not rule.transmission_allowed:
        return (
            "PORTFOLIO_ENGINE_SHADOW_ONLY",
            f"{signal.engine} lacks engine-specific execution-parity evidence",
        )
    return None


__all__ = [
    "ENGINE_RULES",
    "HISTORICAL_CUTOFF",
    "MANIFEST_ID",
    "EngineRule",
    "manifest_payload",
    "manifest_sha256",
    "signal_guard",
]
