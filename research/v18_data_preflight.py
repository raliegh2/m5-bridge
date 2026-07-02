"""Strict coverage checks for the V18 broker-data backtest."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
TIMEFRAMES = ("M15", "H1", "H4", "D1")
UTC = timezone.utc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "time" not in frame.columns:
        raise ValueError("CSV must contain a time column")
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="raise")
    required = {"open", "high", "low", "close"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame["time"].duplicated().any():
        raise ValueError("duplicate timestamps present")
    invalid = (
        (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
    )
    if invalid.any():
        raise ValueError(f"invalid OHLC rows: {int(invalid.sum())}")
    return frame.sort_values("time").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir")
    parser.add_argument("--start", default="2016-07-01T00:00:00Z")
    parser.add_argument("--end", default=datetime.now(UTC).isoformat())
    parser.add_argument("--out", default="v18_data_status.json")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    requested_start = pd.Timestamp(args.start)
    requested_end = pd.Timestamp(args.end)
    records = []
    complete = True
    latest_common = None
    earliest_common = None
    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            path = data_dir / f"{symbol}_{timeframe}.csv"
            record = {
                "symbol": symbol,
                "timeframe": timeframe,
                "path": str(path),
                "exists": path.exists(),
                "rows": 0,
                "start": None,
                "end": None,
                "sha256": None,
                "valid": False,
                "issues": [],
            }
            if not path.exists():
                record["issues"].append("file_not_found")
                complete = False
                records.append(record)
                continue
            try:
                frame = read_frame(path)
                record["rows"] = len(frame)
                record["start"] = frame["time"].min().isoformat()
                record["end"] = frame["time"].max().isoformat()
                record["sha256"] = sha256(path)
                tolerance = pd.Timedelta(days=14 if timeframe == "D1" else 10)
                if frame["time"].min() > requested_start + tolerance:
                    record["issues"].append("start_coverage_incomplete")
                if frame["time"].max() < requested_end - tolerance:
                    record["issues"].append("end_coverage_incomplete")
                record["valid"] = not record["issues"]
                complete = complete and record["valid"]
                earliest_common = (
                    frame["time"].min()
                    if earliest_common is None
                    else max(earliest_common, frame["time"].min())
                )
                latest_common = (
                    frame["time"].max()
                    if latest_common is None
                    else min(latest_common, frame["time"].max())
                )
            except Exception as exc:
                record["issues"].append(f"parse_error:{exc}")
                complete = False
            records.append(record)
    payload = {
        "status": "READY" if complete else "BLOCKED_INSUFFICIENT_BROKER_DATA",
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "common_start": earliest_common.isoformat() if earliest_common is not None else None,
        "common_end": latest_common.isoformat() if latest_common is not None else None,
        "periods_requested": ["10y", "5y", "4y", "3y", "2y", "1y", "6m"],
        "complete": complete,
        "records": records,
        "rule": "No whole-system result is emitted unless every active symbol has valid M15/H1/H4/D1 broker coverage.",
    }
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
