"""Centralized CME futures order flow for spot-FX/Gold confirmation.

Databento's CME Globex MBP-10 stream supplies ten levels of exchange order-book
depth.  The adapter is fail-open and telemetry-only: missing credentials,
subscriptions, packages or stale data never stop the existing MT5 strategy.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any


FUTURES_SYMBOLS = {
    "GBPUSD": ("6B.v.0",),
    "EURUSD": ("6E.v.0",),
    "AUDUSD": ("6A.v.0",),
    "GBPJPY": ("6B.v.0", "6J.v.0"),
    "XAUUSD": ("GC.v.0",),
}


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").rstrip("\x00")
    return str(value or "").rstrip("\x00")


def _level_size(level: Any, name: str) -> float:
    if isinstance(level, dict):
        return float(level.get(name, 0.0) or 0.0)
    return float(getattr(level, name, 0.0) or 0.0)


def mbp10_book_imbalance(record: Any) -> tuple[float | None, int]:
    """Return top-ten bid/ask depth imbalance in [-1, 1]."""
    levels = list(getattr(record, "levels", []) or [])[:10]
    bid = sum(_level_size(level, "bid_sz") for level in levels)
    ask = sum(_level_size(level, "ask_sz") for level in levels)
    total = bid + ask
    return ((bid - ask) / total if total > 0 else None), len(levels)


@dataclass
class _Latest:
    imbalance: float
    levels: int
    updated_monotonic: float
    ts_event: int
    events: int


class DatabentoFuturesOrderFlow:
    """Background MBP-10 cache using volume-ranked continuous futures."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str,
        dataset: str = "GLBX.MDP3",
        maximum_age_seconds: float = 10.0,
    ) -> None:
        self.enabled = bool(enabled)
        self.api_key = str(api_key)
        self.dataset = str(dataset)
        self.maximum_age_seconds = max(1.0, float(maximum_age_seconds))
        self._lock = threading.Lock()
        self._latest: dict[str, _Latest] = {}
        self._instrument_symbols: dict[int, str] = {}
        self._thread: threading.Thread | None = None
        self._client: Any | None = None
        self._status = "DISABLED" if not enabled else "STARTING"
        self._error: str | None = None

    @classmethod
    def from_env(cls) -> "DatabentoFuturesOrderFlow":
        enabled = os.getenv(
            "V14_25_FUTURES_ORDER_FLOW", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            enabled=enabled,
            api_key=os.getenv("DATABENTO_API_KEY", "").strip(),
            dataset=os.getenv(
                "V14_25_DATABENTO_DATASET", "GLBX.MDP3"
            ).strip(),
            maximum_age_seconds=float(
                os.getenv("V14_25_FUTURES_MAX_AGE_SECONDS", "10")
            ),
        )

    def start(self) -> None:
        if not self.enabled:
            return
        if not self.api_key:
            self._status = "API_KEY_REQUIRED"
            return
        try:
            import databento  # type: ignore
        except ImportError:
            self._status = "PACKAGE_REQUIRED"
            self._error = "Install the official 'databento' Python package."
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            args=(databento,),
            name="databento-futures-order-flow",
            daemon=True,
        )
        self._thread.start()

    def _run(self, databento: Any) -> None:
        try:
            client = databento.Live(key=self.api_key)
            self._client = client
            client.subscribe(
                dataset=self.dataset,
                schema="mbp-10",
                symbols=sorted({
                    symbol
                    for values in FUTURES_SYMBOLS.values()
                    for symbol in values
                }),
                stype_in="continuous",
            )
            client.add_callback(self._on_record, self._on_error)
            self._status = "CONNECTING"
            client.start()
            self._status = "STREAMING"
            client.block_for_close()
        except Exception as exc:  # noqa: BLE001 - optional feed fails open
            self._status = "ERROR"
            self._error = f"{type(exc).__name__}: {exc}"

    def _on_error(self, exc: Exception) -> None:
        self._status = "ERROR"
        self._error = f"{type(exc).__name__}: {exc}"

    def _on_record(self, record: Any) -> None:
        input_symbol = _text(getattr(record, "stype_in_symbol", ""))
        instrument_id = int(getattr(record, "instrument_id", 0) or 0)
        if input_symbol and instrument_id:
            with self._lock:
                self._instrument_symbols[instrument_id] = input_symbol
            return

        imbalance, levels = mbp10_book_imbalance(record)
        if imbalance is None or instrument_id <= 0:
            return
        with self._lock:
            symbol = self._instrument_symbols.get(instrument_id)
            if not symbol:
                return
            previous = self._latest.get(symbol)
            self._latest[symbol] = _Latest(
                imbalance=float(imbalance),
                levels=levels,
                updated_monotonic=time.monotonic(),
                ts_event=int(getattr(record, "ts_event", 0) or 0),
                events=(previous.events + 1 if previous else 1),
            )

    def _one(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._latest.get(symbol)
        if value is None:
            return None
        age = max(0.0, time.monotonic() - value.updated_monotonic)
        return {
            "futures_symbol": symbol,
            "imbalance": value.imbalance,
            "levels": value.levels,
            "age_seconds": round(age, 3),
            "event_count": value.events,
            "ts_event": value.ts_event,
            "fresh": age <= self.maximum_age_seconds,
        }

    def reading(self, spot_symbol: str) -> dict[str, Any]:
        canonical = str(spot_symbol).upper()
        proxies = FUTURES_SYMBOLS.get(canonical)
        base = {
            "provider": "DATABENTO_CME_MBP10",
            "dataset": self.dataset,
            "spot_symbol": canonical,
            "centralized": True,
            "status": self._status,
            "error": self._error,
        }
        if not proxies:
            return {**base, "state": "UNSUPPORTED", "imbalance": None}
        values = [self._one(symbol) for symbol in proxies]
        if any(value is None for value in values):
            return {
                **base,
                "state": "WAITING_FOR_DEPTH",
                "imbalance": None,
                "proxies": [value for value in values if value is not None],
            }
        ready = [value for value in values if value is not None]
        if not all(bool(value["fresh"]) for value in ready):
            return {
                **base,
                "state": "STALE",
                "imbalance": None,
                "proxies": ready,
            }
        if canonical == "GBPJPY":
            # GBPJPY rises with GBP/USD strength and falls with JPY/USD strength.
            imbalance = (
                float(ready[0]["imbalance"])
                - float(ready[1]["imbalance"])
            ) / 2.0
        else:
            imbalance = float(ready[0]["imbalance"])
        return {
            **base,
            "state": "READY",
            "imbalance": round(imbalance, 6),
            "event_count": min(int(value["event_count"]) for value in ready),
            "levels": sum(int(value["levels"]) for value in ready),
            "proxies": ready,
        }

    def snapshot(self) -> list[dict[str, Any]]:
        return [self.reading(symbol) for symbol in FUTURES_SYMBOLS]


__all__ = [
    "DatabentoFuturesOrderFlow",
    "FUTURES_SYMBOLS",
    "mbp10_book_imbalance",
]
