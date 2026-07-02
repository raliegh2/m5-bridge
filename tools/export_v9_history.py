"""Export and verify the raw history required for an honest V9 ten-year test.

Run this on the Windows machine where MetaTrader 5 is installed and logged in.
The exporter requests data in yearly chunks, de-duplicates timestamps, writes a
SHA-256 manifest, and fails if the requested coverage is not present.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

UTC = timezone.utc

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

TIMEFRAMES = {
    "M15": "TIMEFRAME_M15",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}
REQUIRED_EXPORTS = {
    "GBPUSD": ("M15", "H4", "D1"),
    "EURUSD": ("M15",),
    "GBPJPY": ("M15",),
}


@dataclass(frozen=True)
class ExportRecord:
    symbol: str
    timeframe: str
    requested_start: str
    requested_end: str
    actual_start: str | None
    actual_end: str | None
    rows: int
    duplicate_timestamps_removed: int
    output: str
    sha256: str | None
    complete: bool
    error: str | None = None


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _year_boundaries(start: datetime, end: datetime) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        next_year = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        boundary = min(next_year, end)
        yield cursor, boundary
        cursor = boundary


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timeframe_value(name: str):
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package is not installed")
    return getattr(mt5, TIMEFRAMES[name])


def export_one(symbol: str, timeframe: str, start: datetime, end: datetime,
               out_dir: Path, retries: int) -> ExportRecord:
    frames: list[pd.DataFrame] = []
    error: str | None = None
    for chunk_start, chunk_end in _year_boundaries(start, end):
        rates = None
        for attempt in range(retries + 1):
            rates = mt5.copy_rates_range(
                symbol, _timeframe_value(timeframe), chunk_start, chunk_end
            )
            if rates is not None:
                break
            error = f"copy_rates_range failed: {mt5.last_error()}"
            time.sleep(min(2 ** attempt, 8))
        if rates is None:
            return ExportRecord(
                symbol, timeframe, start.isoformat(), end.isoformat(),
                None, None, 0, 0, "", None, False, error,
            )
        if len(rates):
            frame = pd.DataFrame(rates)
            frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
            frames.append(frame)

    if not frames:
        return ExportRecord(
            symbol, timeframe, start.isoformat(), end.isoformat(),
            None, None, 0, 0, "", None, False, "No bars returned",
        )

    frame = pd.concat(frames, ignore_index=True).sort_values("time")
    before = len(frame)
    frame = frame.drop_duplicates("time", keep="last").reset_index(drop=True)
    duplicates = before - len(frame)
    output = out_dir / f"{symbol}_{timeframe}_{start.date()}_{end.date()}.csv"
    frame.to_csv(output, index=False)
    actual_start = frame["time"].min().to_pydatetime()
    actual_end = frame["time"].max().to_pydatetime()
    tolerance_seconds = 7 * 24 * 3600
    complete = (
        (actual_start - start).total_seconds() <= tolerance_seconds
        and (end - actual_end).total_seconds() <= tolerance_seconds
    )
    return ExportRecord(
        symbol=symbol,
        timeframe=timeframe,
        requested_start=start.isoformat(),
        requested_end=end.isoformat(),
        actual_start=actual_start.isoformat(),
        actual_end=actual_end.isoformat(),
        rows=int(len(frame)),
        duplicate_timestamps_removed=int(duplicates),
        output=str(output),
        sha256=_sha256(output),
        complete=complete,
        error=None if complete else "Broker history does not cover the requested window",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-07-01T00:00:00+00:00")
    parser.add_argument("--end", default=datetime.now(UTC).isoformat())
    parser.add_argument("--out", type=Path, default=Path("data/v9_10y"))
    parser.add_argument("--terminal-path", help="Optional terminal64.exe path")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args(argv)

    if mt5 is None:
        print("MetaTrader5 Python package is required on the MT5 host.", file=sys.stderr)
        return 2
    start, end = _parse_utc(args.start), _parse_utc(args.end)
    if end <= start:
        print("--end must be later than --start", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    initialize_kwargs = {"path": args.terminal_path} if args.terminal_path else {}
    if not mt5.initialize(**initialize_kwargs):
        print(f"MT5 initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 2

    records: list[ExportRecord] = []
    try:
        for symbol, timeframes in REQUIRED_EXPORTS.items():
            if not mt5.symbol_select(symbol, True):
                for timeframe in timeframes:
                    records.append(ExportRecord(
                        symbol, timeframe, start.isoformat(), end.isoformat(),
                        None, None, 0, 0, "", None, False,
                        f"symbol_select failed: {mt5.last_error()}",
                    ))
                continue
            for timeframe in timeframes:
                print(f"Exporting {symbol} {timeframe}...")
                records.append(export_one(
                    symbol, timeframe, start, end, args.out, args.retries
                ))
    finally:
        mt5.shutdown()

    payload = {
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "complete": all(record.complete for record in records),
        "exports": [asdict(record) for record in records],
    }
    (args.out / "manifest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
