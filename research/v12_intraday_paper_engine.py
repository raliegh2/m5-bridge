"""Paper-forward engine for the closest M15/M30 research candidate.

This module intentionally has no broker execution dependency. It produces a
standardized candidate that can be observed alongside the final engines while
new out-of-sample evidence accumulates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from v12_intraday_m15_m30_backtest import IntradayParams, prepare_signals


PAPER_PARAMS = IntradayParams(
    family="TREND_REENTRY_ONLY", adx_min=24.0, stop_atr=1.5,
    reward_risk=2.0, max_hold_m5_bars=24, risk_percent=0.25,
)


@dataclass(frozen=True)
class IntradayPaperCandidate:
    symbol: str
    engine: str
    setup: str
    side: str
    signal_time: datetime
    stop_pips: float
    target_pips: float
    risk_percent: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def latest_candidate(m5: pd.DataFrame,
                     params: IntradayParams = PAPER_PARAMS) -> Optional[IntradayPaperCandidate]:
    signals = prepare_signals(m5, params)
    if signals.empty:
        return None
    row = signals.iloc[-1]
    latest_complete = m5["time"].max() + pd.Timedelta(minutes=5)
    if row["bar_end"] < latest_complete - pd.Timedelta(minutes=20):
        return None
    stop_pips = float(row["atr14"] * params.stop_atr / 0.0001)
    side = "BUY" if int(row["side"]) > 0 else "SELL"
    return IntradayPaperCandidate(
        symbol="GBPUSD", engine="GBPUSD_M15_M30_REENTRY_PAPER",
        setup=str(row["setup"]), side=side,
        signal_time=row["bar_end"].to_pydatetime(), stop_pips=stop_pips,
        target_pips=stop_pips * params.reward_risk,
        risk_percent=params.risk_percent,
        reason=(f"M15 {side.lower()} re-entry with aligned M30 EMA20/EMA50 "
                f"and ADX >= {params.adx_min:g}"),
    )
