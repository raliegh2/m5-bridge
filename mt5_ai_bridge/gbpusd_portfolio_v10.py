"""V10 portfolio adapter with GBPUSD swing precision timing.

The existing Portfolio V2 controller remains authoritative for position checks,
news blocking, order placement, state, and portfolio caps. This adapter injects
V9 Satellite V3 plus the V10 completed-H4 swing quality gate for the currently
live V4 primary and secondary breakout families.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import pandas as pd

from . import gbpusd_portfolio_v2 as portfolio_v2
from .gbpusd_satellite_v3 import evaluate_setup as evaluate_satellite_v3
from .gbpusd_swing_v10_precision import evaluate_swing_timing
from .gbpusd_v4 import evaluate_setup as evaluate_v4_setup
from .execution import pip_size
from .sizing import RiskConfig, risk_lot

_LOCK = threading.RLock()


@dataclass(frozen=True)
class PrecisionSwingSetup:
    side: object
    variant: str
    signal_end: object
    atr_price: float
    reason: str
    precision_grade: str
    precision_risk_percent: float


def evaluate_precision_v4_setup(client, symbol: str):
    setup, h4 = evaluate_v4_setup(client, symbol)
    if setup is None or h4 is None or h4.empty:
        return setup, h4

    h4 = h4.copy()
    h4["ema20_h4"] = h4["close"].ewm(
        span=20, adjust=False, min_periods=20
    ).mean()
    h4["ema50_h4"] = h4["close"].ewm(
        span=50, adjust=False, min_periods=50
    ).mean()
    row = h4.iloc[-1]
    atr_value = float(row["atr"])
    range_atr = (
        (float(row["high"]) - float(row["low"])) / atr_value
        if atr_value > 0 else 0.0
    )
    side_value = 1 if setup.side.value == "BUY" else -1
    decision = evaluate_swing_timing(
        setup=setup.variant,
        side=side_value,
        open_price=float(row["open"]),
        close_price=float(row["close"]),
        atr14=atr_value,
        volume_ratio=float(row["volume_ratio"]),
        range_atr=range_atr,
        atr_ratio=float(row["atr_ratio"]),
        ema20_h4=float(row["ema20_h4"]),
        ema50_h4=float(row["ema50_h4"]),
    )
    if not decision.allowed:
        return None, h4
    return PrecisionSwingSetup(
        side=setup.side,
        variant=setup.variant,
        signal_end=setup.signal_end,
        atr_price=setup.atr_price,
        reason=(
            f"{setup.reason} V10 grade={decision.grade}; "
            f"{decision.reason}"
        ),
        precision_grade=decision.grade,
        precision_risk_percent=decision.risk_percent,
    ), h4


def planned_precision_v4_order(
    client, settings, account, setup: PrecisionSwingSetup,
    effective_risk: float, params,
) -> dict:
    pip = pip_size(client, settings.symbol) or 0.0001
    stop_pips = min(
        max(params.stop_atr * setup.atr_price / pip, params.min_stop_pips),
        params.max_stop_pips,
    )
    target_pips = params.target_r * stop_pips
    risk_percent = setup.precision_risk_percent
    if effective_risk < params.risk_percent:
        risk_percent = min(risk_percent, effective_risk)
    volume = risk_lot(
        float(account.balance),
        stop_pips,
        RiskConfig(
            enabled=True,
            risk_percent=risk_percent,
            pip_value_per_lot=float(settings.pip_value_per_lot),
            max_lot=float(settings.max_lot),
        ),
    )
    actual_risk = stop_pips * float(settings.pip_value_per_lot) * volume
    return {
        "volume": volume,
        "stop_pips": stop_pips,
        "target_pips": target_pips,
        "risk_percent": actual_risk / float(account.balance) * 100,
        "precision_grade": setup.precision_grade,
    }


def run_portfolio_v10_cycle(*args, **kwargs):
    with _LOCK:
        previous_satellite = portfolio_v2.evaluate_satellite_setup
        previous_swing = portfolio_v2.evaluate_v4_setup
        previous_planner = portfolio_v2._planned_v4_order
        portfolio_v2.evaluate_satellite_setup = evaluate_satellite_v3
        portfolio_v2.evaluate_v4_setup = evaluate_precision_v4_setup
        portfolio_v2._planned_v4_order = planned_precision_v4_order
        try:
            result = portfolio_v2.run_portfolio_v2_cycle(*args, **kwargs)
            if isinstance(result, dict):
                result["strategy_version"] = "V10_SWING_PRECISION"
                result["swing_precision_scope"] = (
                    "Live primary/secondary V4 breakouts; pullback add-on remains research-only."
                )
            return result
        finally:
            portfolio_v2.evaluate_satellite_setup = previous_satellite
            portfolio_v2.evaluate_v4_setup = previous_swing
            portfolio_v2._planned_v4_order = previous_planner
