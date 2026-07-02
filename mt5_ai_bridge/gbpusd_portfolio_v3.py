"""Compatibility controller that injects the V9 satellite gate into Portfolio V2.

Portfolio V2 remains the authoritative risk/order controller. This adapter swaps
only the satellite evaluator for the duration of a cycle. A lock keeps the
operation deterministic if a dashboard or scheduler invokes concurrent cycles.
"""
from __future__ import annotations

import threading

from . import gbpusd_portfolio_v2 as portfolio_v2
from .gbpusd_satellite_v3 import evaluate_setup as evaluate_satellite_v3

_LOCK = threading.RLock()


def run_portfolio_v3_cycle(*args, **kwargs):
    with _LOCK:
        previous = portfolio_v2.evaluate_satellite_setup
        portfolio_v2.evaluate_satellite_setup = evaluate_satellite_v3
        try:
            result = portfolio_v2.run_portfolio_v2_cycle(*args, **kwargs)
            if isinstance(result, dict):
                result.setdefault("strategy_version", "V9_CANDIDATE")
            return result
        finally:
            portfolio_v2.evaluate_satellite_setup = previous
