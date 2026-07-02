"""Export broker-native bars for the V18 five-symbol portfolio.

Run this on the Windows machine where MetaTrader 5 is installed, open and
logged into the intended demo broker. The script resolves broker suffixes,
exports M15/H1/H4/D1 bars in yearly chunks, de-duplicates timestamps, and
writes a SHA-256 manifest. No trading order is sent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - only installed on the MT5 host
    mt5 = None

UTC = timezone.utc
CANONICAL_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
TIMEFRAMES = {
    "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


@dataclass(frozen=True)
class ExportRecord:
    canonical_symbol: str
    broker_symbol: str | None
    timeframe: str
    requested_start: str
    requested_end: str
    actual_start: str | None
    actual_end: str | None
    rows: int
    output: str
    sha256: str | None
    complete: bool
    error: str | None = None


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_text(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalpha())


def resolve_symbol(canonical: str) -> str | None:
    exact = mt5.symbol_info(canonical)
    if exact is not None:
        mt5.symbol_select(canonical, True)
        return canonical
    matches = []
    for item in mt5.symbols_get() or ():
        text = canonical_text(item.name)
        if canonical in text:
            score = (0 if text.startswith(canonical) else 1, len(item.name), item.name)
            matches.append((score, item.name))
    if not matches:
        return None
    matches.sort()
    selected = matches[0][1]
    return selected if mt5.symbol_select(selected, True) else None


def boundaries(start: datetime, end: datetime):
    cursor = start
    while cursor < end:
        next_year = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        boundary = min(next_year, end)
        yield cursor, boundary
        cursor = boundary


def export_one(canonical: str, broker_symbol: str | None, timeframe: str,
               start: datetime, end: datetime, output_dir: Path,
               retries: int) -> ExportRecord:
    output = output_dir / f"{canonical}_{timeframe}.csv"
    if broker_symbol is None:
        return ExportRecord(canonical, None, timeframe, start.isoformat(),
                            end.isoformat(), None, None, 0, str(output), None,
                            False, "broker symbol could not be resolved")
    frames = []
    for chunk_start, chunk_end in boundaries(start, end):
        rates = None
        for attempt in range(retries + 1):
            rates = mt5.copy_rates_range(
                broker_symbol,
                getattr(mt5, TIMEFRAMES[timeframe]),
                chunk_start,
                chunk_end,
            )
            if rates is not None:
                break
            time.sleep(min(2 ** attempt, 8))
        if rates is None:
            return ExportRecord(canonical, broker_symbol, timeframe,
                                start.isoformat(), end.isoformat(), None, None,
                                0, str(output), None, False,
                                f"copy_rates_range failed: {mt5.last_error()}")
        if len(rates):
            frames.append(pd.DataFrame(rates))
    if not frames:
        return ExportRecord(canonical, broker_symbol, timeframe,
                            start.isoformat(), end.isoformat(), None, None, 0,
                            str(output), None, False, "no bars returned")
    frame = pd.concat(frames, ignore_index=True)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame = frame.sort_values("time").drop_duplicates("time", keep="last")
    frame.to_csv(output, index=False)
    actual_start = frame["time"].min().isoformat()
    actual_end = frame["time"].max().isoformat()
    tolerance = {
        "M15": pd.Timedelta(days=7),
        "H1": pd.Timedelta(days=7),
        "H4": pd.Timedelta(days=10),
        "D1": pd.Timedelta(days=14),
    }[timeframe]
    complete = (
        frame["time"].min() <= pd.Timestamp(start) + tolerance
        and frame["time"].max() >= pd.Timestamp(end) - tolerance
    )
    return ExportRecord(canonical, broker_symbol, timeframe,
                        start.isoformat(), end.isoformat(), actual_start,
                        actual_end, len(frame), str(output), sha256(output),
                        bool(complete), None if complete else "coverage incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-07-01T00:00:00Z")
    parser.add_argument("--end", default=datetime.now(UTC).isoformat())
    parser.add_argument("--out", default="v18_broker_history")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    if mt5 is None:
        raise SystemExit("MetaTrader5 is not installed in this Python environment")
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")
    try:
        start, end = parse_utc(args.start), parse_utc(args.end)
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        records = []
        resolved = {symbol: resolve_symbol(symbol) for symbol in CANONICAL_SYMBOLS}
        for symbol in CANONICAL_SYMBOLS:
            for timeframe in TIMEFRAMES:
                records.append(export_one(symbol, resolved[symbol], timeframe,
                                          start, end, output_dir, args.retries))
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "terminal_company": getattr(terminal, "company", None),
            "terminal_name": getattr(terminal, "name", None),
            "account_server": getattr(account, "server", None),
            "account_login": getattr(account, "login", None),
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "resolved_symbols": resolved,
            "complete": all(record.complete for record in records),
            "records": [asdict(record) for record in records],
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2))
        return 0 if manifest["complete"] else 2
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
