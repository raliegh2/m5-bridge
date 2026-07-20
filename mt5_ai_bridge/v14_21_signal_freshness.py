"""Fresh-candle boundary for incremental GBP ICT scans.

The recovered GBP provider intentionally returns a recent recovery window so a
runner restart can discover signals that formed while it was offline. The
full startup/H1 scan retains that recovery behavior. Incremental M1 scans,
however, should execute and display only candidates formed on the latest
completed M1 candle; otherwise the same historical candidate appears as a new
"signal this scan" on every minute until the recovery window expires.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .v14_3_live_execution import LiveSignal
from .v14_3_live_signals import (
    load_legacy_gbp_ict_signals as _load_legacy_gbp_ict_signals,
)

GBP_ICT_SYMBOLS = ("GBPUSD", "GBPJPY")


def _latest_completed_m1_time(
    client: Any,
    broker_symbol: str,
) -> pd.Timestamp | None:
    rates = client.copy_rates_from_pos(broker_symbol, "M1", 1, 1)
    if rates is None or len(rates) < 1:
        return None
    row = rates[0]
    try:
        epoch = int(row["time"])
    except (KeyError, TypeError, ValueError, IndexError):
        epoch = int(getattr(row, "time", 0) or 0)
    if epoch <= 0:
        return None
    return pd.Timestamp(datetime.fromtimestamp(epoch, tz=timezone.utc))


def load_current_m1_gbp_ict_signals(
    client: Any,
    broker_map: dict[str, str],
) -> tuple[list[LiveSignal], str]:
    """Return only GBP ICT signals from the newest completed M1 candle.

    The underlying provider still owns the strategy and its recovery window.
    This wrapper narrows only the incremental per-minute runner scan. It never
    changes prices, setup rules, risk, or the startup recovery scan.
    """

    signals, status = _load_legacy_gbp_ict_signals(client, broker_map)
    latest = {
        symbol: _latest_completed_m1_time(client, broker_map[symbol])
        for symbol in GBP_ICT_SYMBOLS
        if symbol in broker_map
    }

    current: list[LiveSignal] = []
    for signal in signals:
        symbol = str(signal.symbol).upper()
        boundary = latest.get(symbol)
        if boundary is None:
            continue
        signal_time = pd.Timestamp(signal.signal_time)
        if signal_time.tzinfo is None:
            signal_time = signal_time.tz_localize("UTC")
        else:
            signal_time = signal_time.tz_convert("UTC")
        if signal_time.floor("min") == boundary.floor("min"):
            current.append(signal)
    return current, status


__all__ = [
    "GBP_ICT_SYMBOLS",
    "load_current_m1_gbp_ict_signals",
]
