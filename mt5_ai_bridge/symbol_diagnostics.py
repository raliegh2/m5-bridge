"""Per-symbol diagnostics for the final five-symbol MT5 runner."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd

from .execution import pip_size
from .final_engine_registry import FINAL_SYMBOLS, engines_for_symbol


@dataclass(frozen=True)
class SymbolDiagnostic:
    symbol: str
    generated_at: str
    data_available: bool
    bars: Mapping[str, int]
    latest_closed_bar: Mapping[str, Optional[str]]
    candidates_generated: int
    recent_candidates: int
    engines: tuple[str, ...]
    risk_multipliers: Mapping[str, float]
    current_spread_pips: Optional[float]
    rejection_code: str
    rejection_message: str
    next_eligible_scan_time: str


def _utc(value: Optional[datetime] = None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("diagnostic time must be timezone-aware")
    return now.astimezone(timezone.utc)


def _next_hour(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _next_h4(now: datetime) -> datetime:
    base = now.replace(minute=0, second=0, microsecond=0)
    add = 4 - (base.hour % 4)
    return base + timedelta(hours=add)


def next_scan_time(symbol: str, now: Optional[datetime] = None) -> datetime:
    now_utc = _utc(now)
    symbol = symbol.upper()
    if symbol == "EURUSD":
        return _next_hour(now_utc)
    if symbol == "AUDUSD":
        base = now_utc.replace(minute=0, second=0, microsecond=0)
        for day_offset in range(0, 3):
            day = base + timedelta(days=day_offset)
            for hour in (4, 8):
                candidate = day.replace(hour=hour)
                if candidate > now_utc:
                    return candidate
    candidate = _next_h4(now_utc)
    if symbol == "GBPJPY":
        while not 7 <= candidate.hour < 20:
            if candidate.hour >= 20:
                candidate = (candidate + timedelta(days=1)).replace(hour=8)
            else:
                candidate = candidate.replace(hour=8)
    return candidate


def _latest(frame: pd.DataFrame) -> Optional[str]:
    if frame.empty:
        return None
    column = "end" if "end" in frame.columns else "time"
    value = pd.Timestamp(frame.iloc[-1][column])
    return value.isoformat()


def _spread(client: Any, symbol: str) -> Optional[float]:
    tick = client.symbol_info_tick(symbol)
    pip = pip_size(client, symbol)
    if tick is None or pip is None or pip <= 0:
        return None
    return (float(tick.ask) - float(tick.bid)) / float(pip)


def _risk_multipliers(adapter: Any, symbol: str, now: datetime) -> dict[str, float]:
    values: dict[str, float] = {}
    executor = adapter.executor
    for engine in engines_for_symbol(symbol):
        if symbol == "GBPJPY":
            open_count = len(executor._positions(symbol="GBPJPY"))
            decision = executor.gbpjpy_guard.decision(open_positions=open_count, now=now)
            values[engine.engine] = decision.risk_cap_percent if decision.ok else 0.0
        else:
            values[engine.engine] = float(executor.state.guard_multiplier(engine.engine, now))
    return values


def build_diagnostics(
    client: Any,
    adapter: Any,
    prepared: Mapping[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    candidates: pd.DataFrame,
    execution_results: Optional[Mapping[str, Mapping[str, str]]] = None,
    lookback_hours: int = 8,
    now: Optional[datetime] = None,
) -> list[SymbolDiagnostic]:
    now_utc = _utc(now)
    cutoff = pd.Timestamp(now_utc) - pd.Timedelta(hours=lookback_hours)
    execution_results = execution_results or {}
    output: list[SymbolDiagnostic] = []

    for symbol in FINAL_SYMBOLS:
        h1, h4, d1 = prepared.get(symbol, (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
        data_ok = min(len(h1), len(h4), len(d1)) >= 100
        symbol_candidates = (
            candidates[candidates["symbol"] == symbol]
            if not candidates.empty and "symbol" in candidates.columns
            else pd.DataFrame()
        )
        recent = (
            symbol_candidates[symbol_candidates["entry_time"] >= cutoff]
            if not symbol_candidates.empty
            else pd.DataFrame()
        )
        multipliers = _risk_multipliers(adapter, symbol, now_utc)
        result = execution_results.get(symbol)

        if result:
            code = str(result.get("code", "UNKNOWN_RESULT"))
            message = str(result.get("message", "Execution attempt completed."))
        elif not data_ok:
            code = "DATA_UNAVAILABLE"
            message = "One or more required H1/H4/D1 histories contain fewer than 100 closed bars."
        elif symbol == "GBPJPY" and not adapter.executor.gbpjpy_guard.in_session(now_utc):
            code = "GBPJPY_SESSION_BLOCK"
            message = "GBPJPY is outside its 07:00-20:00 UTC entry window."
        elif multipliers and all(value <= 0 for value in multipliers.values()):
            code = "ENGINE_GUARD_BLOCKED"
            message = "Every approved engine for this symbol is currently blocked by its risk guard."
        elif recent.empty:
            code = "NO_RECENT_CANDIDATE"
            message = "Data is available, but no approved setup triggered inside the scan lookback."
        else:
            code = "CANDIDATE_READY"
            message = "At least one approved candidate is available for execution gating."

        output.append(SymbolDiagnostic(
            symbol=symbol,
            generated_at=now_utc.isoformat(),
            data_available=data_ok,
            bars={"H1": len(h1), "H4": len(h4), "D1": len(d1)},
            latest_closed_bar={"H1": _latest(h1), "H4": _latest(h4), "D1": _latest(d1)},
            candidates_generated=len(symbol_candidates),
            recent_candidates=len(recent),
            engines=tuple(engine.engine for engine in engines_for_symbol(symbol)),
            risk_multipliers=multipliers,
            current_spread_pips=_spread(client, symbol),
            rejection_code=code,
            rejection_message=message,
            next_eligible_scan_time=next_scan_time(symbol, now_utc).isoformat(),
        ))
    return output


def write_diagnostics(path: Path, diagnostics: list[SymbolDiagnostic]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": [asdict(item) for item in diagnostics],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
