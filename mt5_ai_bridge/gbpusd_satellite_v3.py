"""V9 candidate wrapper around the tested GBPUSD Satellite V2 engine.

Signal generation and trade management remain unchanged. V3 adds only the
frozen UTC hour gate, stale-signal/spread/event checks, and a short TTL cache to
reduce repeated MT5 history requests in fast polling loops.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import gbpusd_satellite_v2 as v2
from .v9_policy import (
    DEFAULT_V9_POLICY,
    EvaluationCache,
    EventBlackoutCalendar,
    V9Policy,
    evaluate_entry_gate,
)

MAGIC = 260732
COMMENT = "GBPUSD Satellite V3"
SatelliteV3Params = v2.SatelliteV2Params
SatelliteV3Setup = v2.SatelliteV2Setup

_CACHE: EvaluationCache[tuple[Optional[SatelliteV3Setup], dict]] = EvaluationCache(
    float(os.getenv("V9_EVAL_CACHE_SECONDS", DEFAULT_V9_POLICY.evaluation_cache_seconds))
)


def _calendar_from_environment() -> EventBlackoutCalendar | None:
    raw = os.getenv("V9_EVENT_CALENDAR", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return EventBlackoutCalendar.from_csv(path) if path.exists() else None


def evaluate_setup(
    client,
    symbol: str,
    params: SatelliteV3Params = SatelliteV3Params(),
    *,
    policy: V9Policy = DEFAULT_V9_POLICY,
    now_utc: datetime | None = None,
) -> tuple[Optional[SatelliteV3Setup], dict]:
    now = now_utc or datetime.now(timezone.utc)
    interval = max(1.0, policy.evaluation_cache_seconds or 1.0)
    cache_bucket = int(now.timestamp() // interval)
    key = f"{id(client)}:{symbol.upper()}:{cache_bucket}"
    setup, diagnostics = _CACHE.get_or_compute(
        key, lambda: v2.evaluate_setup(client, symbol, params)
    )
    diagnostics = dict(diagnostics)
    diagnostics["engine"] = "GBPUSD_SATELLITE_V3"
    diagnostics["v9_allowed_hours_utc"] = sorted(
        policy.allowed_gbpusd_satellite_hours_utc
    )
    if setup is None:
        return None, diagnostics

    try:
        spread = v2.current_spread_pips(client, symbol)
    except Exception:
        spread = None
    decision = evaluate_entry_gate(
        engine="GBPUSD_SATELLITE_V3",
        symbol=symbol,
        signal_end=setup.signal_end,
        now=now,
        spread_pips=spread,
        event_calendar=_calendar_from_environment(),
        policy=policy,
    )
    diagnostics["v9_gate"] = decision.reason
    if not decision.allowed:
        diagnostics.update({
            "setup": None,
            "m15_signal": "WAIT",
            "reason": f"V9 rejected the V2 setup: {decision.reason}.",
        })
        return None, diagnostics
    return setup, diagnostics


manage_positions = v2.manage_positions
news_blocked = v2.news_blocked
current_spread_pips = v2.current_spread_pips
risk_capped_lot = v2.risk_capped_lot
setup_stop_target_pips = v2.setup_stop_target_pips
