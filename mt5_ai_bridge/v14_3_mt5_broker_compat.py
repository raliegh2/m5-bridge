"""Broker compatibility adapter for the V14.3 MT5 live runner.

MetaTrader exposes ``symbol_info().filling_mode`` as a SYMBOL_FILLING_* flag
mask, while an order request requires one ORDER_FILLING_* enum value. Passing
the mask directly can produce retcode 10030 (invalid/unsupported filling mode),
especially when a symbol supports both FOK and IOC and the mask equals 3.

Some demo servers also expose bar/tick epoch values shifted by a whole-hour
server offset. The adapter detects only a stable near-whole-hour drift and
normalizes copied bar and live tick timestamps back to UTC. It does not alter
prices, volumes, signal logic, or risk controls.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a project dependency
    np = None  # type: ignore[assignment]


SYMBOL_FILLING_FOK_FLAG = 1
SYMBOL_FILLING_IOC_FLAG = 2
SYMBOL_FILLING_BOC_FLAG = 4


@dataclass(frozen=True)
class BrokerCompatibilityDiagnostics:
    symbol: str
    raw_filling_flags: int
    selected_order_filling: int
    detected_clock_offset_seconds: int


class _SymbolInfoView:
    """Delegate all symbol fields while replacing only ``filling_mode``."""

    def __init__(self, raw: Any, filling_mode: int) -> None:
        self._raw = raw
        self.filling_mode = int(filling_mode)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class _TickInfoView:
    """Delegate tick fields while normalizing only epoch timestamps to UTC."""

    def __init__(self, raw: Any, offset_seconds: int) -> None:
        self._raw = raw
        raw_time = int(getattr(raw, "time", 0) or 0)
        raw_time_msc = int(getattr(raw, "time_msc", 0) or 0)
        self.time = raw_time - int(offset_seconds) if raw_time > 0 else raw_time
        self.time_msc = (
            raw_time_msc - int(offset_seconds) * 1000
            if raw_time_msc > 0
            else raw_time_msc
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class MT5BrokerCompatibilityClient:
    """Transparent client proxy with safe filling-policy and UTC normalization."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._clock_offsets: dict[str, int] = {}
        self._filling_diagnostics: dict[str, BrokerCompatibilityDiagnostics] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def _select_order_filling(self, info: Any) -> int:
        flags = int(getattr(info, "filling_mode", 0) or 0)
        # ORDER_FILLING_* enum values are not the same as SYMBOL_FILLING_* flags.
        order_ioc = int(getattr(self._client, "ORDER_FILLING_IOC", 1))
        order_fok = int(getattr(self._client, "ORDER_FILLING_FOK", 0))
        order_return = int(getattr(self._client, "ORDER_FILLING_RETURN", 2))

        # IOC permits partial execution and is the safest market-order choice
        # when the broker advertises it. Otherwise use FOK when supported.
        if flags & SYMBOL_FILLING_IOC_FLAG:
            return order_ioc
        if flags & SYMBOL_FILLING_FOK_FLAG:
            return order_fok

        # RETURN is invalid under Market Execution. MetaTrader's conventional
        # execution enum is 2 for market execution; use RETURN only otherwise.
        trade_exemode = int(getattr(info, "trade_exemode", -1) or -1)
        market_execution = int(
            getattr(self._client, "SYMBOL_TRADE_EXECUTION_MARKET", 2)
        )
        if trade_exemode != market_execution:
            return order_return
        return order_fok

    def symbol_info(self, symbol: str) -> Any:
        raw = self._client.symbol_info(symbol)
        if raw is None:
            return None
        selected = self._select_order_filling(raw)
        offset = self._clock_offsets.get(symbol, 0)
        self._filling_diagnostics[symbol] = BrokerCompatibilityDiagnostics(
            symbol=symbol,
            raw_filling_flags=int(getattr(raw, "filling_mode", 0) or 0),
            selected_order_filling=selected,
            detected_clock_offset_seconds=offset,
        )
        return _SymbolInfoView(raw, selected)

    @staticmethod
    def _whole_hour_offset(raw_drift_seconds: float) -> int:
        """Accept only plausible near-whole-hour server offsets.

        Small clock skew is ignored. Large stale-market drifts and non-hour
        offsets are also ignored so the adapter fails closed rather than
        rewriting timestamps speculatively.
        """
        nearest_hour = int(round(raw_drift_seconds / 3600.0)) * 3600
        if abs(nearest_hour) < 1800 or abs(nearest_hour) > 14 * 3600:
            return 0
        if abs(raw_drift_seconds - nearest_hour) > 10 * 60:
            return 0
        return nearest_hour

    def _clock_offset(self, symbol: str) -> int:
        if symbol in self._clock_offsets:
            return self._clock_offsets[symbol]
        tick = self._client.symbol_info_tick(symbol)
        tick_time = int(getattr(tick, "time", 0) or 0) if tick is not None else 0
        if tick_time <= 0:
            offset = 0
        else:
            offset = self._whole_hour_offset(float(tick_time) - time.time())
        self._clock_offsets[symbol] = offset
        diagnostic = self._filling_diagnostics.get(symbol)
        if diagnostic is not None:
            self._filling_diagnostics[symbol] = BrokerCompatibilityDiagnostics(
                symbol=symbol,
                raw_filling_flags=diagnostic.raw_filling_flags,
                selected_order_filling=diagnostic.selected_order_filling,
                detected_clock_offset_seconds=offset,
            )
        return offset

    def symbol_info_tick(self, symbol: str) -> Any:
        """Return the broker tick with any stable server offset removed."""
        raw = self._client.symbol_info_tick(symbol)
        if raw is None:
            return None
        return _TickInfoView(raw, self._clock_offset(symbol))

    @staticmethod
    def _shift_rates(rates: Any, offset: int) -> Any:
        if rates is None or offset == 0:
            return rates
        if np is not None and isinstance(rates, np.ndarray):
            names = rates.dtype.names or ()
            if "time" not in names:
                return rates
            shifted = rates.copy()
            shifted["time"] = shifted["time"] - int(offset)
            return shifted
        if isinstance(rates, list):
            shifted_rows: list[Any] = []
            for row in rates:
                if isinstance(row, dict) and "time" in row:
                    copy = dict(row)
                    copy["time"] = int(copy["time"]) - int(offset)
                    shifted_rows.append(copy)
                else:
                    shifted_rows.append(row)
            return shifted_rows
        return rates

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: Any,
        start_pos: int,
        count: int,
    ) -> Any:
        rates = self._client.copy_rates_from_pos(
            symbol,
            timeframe,
            start_pos,
            count,
        )
        return self._shift_rates(rates, self._clock_offset(symbol))

    @staticmethod
    def _shift_ticks(ticks: Any, offset: int) -> Any:
        if ticks is None or offset == 0:
            return ticks
        if np is not None and isinstance(ticks, np.ndarray):
            names = ticks.dtype.names or ()
            shifted = ticks.copy()
            if "time" in names:
                shifted["time"] = shifted["time"] - int(offset)
            if "time_msc" in names:
                shifted["time_msc"] = shifted["time_msc"] - int(offset) * 1000
            return shifted
        if isinstance(ticks, list):
            rows: list[Any] = []
            for row in ticks:
                if not isinstance(row, dict):
                    rows.append(row)
                    continue
                copy = dict(row)
                if copy.get("time"):
                    copy["time"] = int(copy["time"]) - int(offset)
                if copy.get("time_msc"):
                    copy["time_msc"] = int(copy["time_msc"]) - int(offset) * 1000
                rows.append(copy)
            return rows
        return ticks

    def copy_ticks_from(
        self,
        symbol: str,
        date_from: Any,
        count: int,
        flags: int,
    ) -> Any:
        offset = self._clock_offset(symbol)
        broker_from = (
            date_from + timedelta(seconds=offset) if offset else date_from
        )
        ticks = self._client.copy_ticks_from(
            symbol,
            broker_from,
            count,
            flags,
        )
        return self._shift_ticks(ticks, offset)

    def compatibility_diagnostics(self) -> dict[str, dict[str, int | str]]:
        return {
            symbol: {
                "symbol": item.symbol,
                "raw_filling_flags": item.raw_filling_flags,
                "selected_order_filling": item.selected_order_filling,
                "detected_clock_offset_seconds": item.detected_clock_offset_seconds,
            }
            for symbol, item in sorted(self._filling_diagnostics.items())
        }
