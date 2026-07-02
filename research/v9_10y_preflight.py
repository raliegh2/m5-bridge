"""Strict data-coverage gate for the V9 ten-year backtest.

The command refuses to label a run ten-year when any active engine lacks the
necessary raw history. It also identifies duplicate timestamps, malformed OHLC
rows, and large weekday gaps before simulation begins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

UTC = timezone.utc


@dataclass(frozen=True)
class DatasetRequirement:
    key: str
    symbol: str
    timeframe: str
    maximum_weekday_gap_hours: float


REQUIREMENTS = (
    DatasetRequirement("gbpusd_m15", "GBPUSD", "M15", 8.0),
    DatasetRequirement("gbpusd_h4", "GBPUSD", "H4", 12.0),
    DatasetRequirement("gbpusd_d1", "GBPUSD", "D1", 72.0),
    DatasetRequirement("eurusd_m15", "EURUSD", "M15", 8.0),
    DatasetRequirement("gbpjpy_m15", "GBPJPY", "M15", 8.0),
)


@dataclass(frozen=True)
class DatasetAudit:
    key: str
    path: str
    exists: bool
    rows: int
    start: str | None
    end: str | None
    duplicate_timestamps: int
    invalid_ohlc_rows: int
    largest_weekday_gap_hours: float | None
    sha256: str | None
    covers_requested_window: bool
    valid: bool
    issues: tuple[str, ...]


def _parse_utc(value: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_ohlc(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=None, engine="python")
    frame = frame.rename(columns={
        "<DATE>": "date", "<TIME>": "clock", "<OPEN>": "open",
        "<HIGH>": "high", "<LOW>": "low", "<CLOSE>": "close",
        "<TICKVOL>": "tick_volume", "<SPREAD>": "spread",
    })
    if "time" in frame:
        frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    elif {"date", "clock"}.issubset(frame.columns):
        frame["time"] = pd.to_datetime(
            frame["date"].astype(str) + " " + frame["clock"].astype(str),
            utc=True, errors="coerce",
        )
    else:
        raise ValueError("CSV requires time or <DATE>/<TIME> columns")
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")
    return frame.sort_values("time").reset_index(drop=True)


def _largest_weekday_gap_hours(times: pd.Series) -> float:
    largest = 0.0
    previous = None
    for current in times.dropna():
        if previous is not None:
            cursor = previous
            business_hours = 0.0
            while cursor < current:
                next_cursor = min(cursor + pd.Timedelta(hours=1), current)
                if cursor.weekday() < 5:
                    business_hours += (next_cursor - cursor).total_seconds() / 3600.0
                cursor = next_cursor
            largest = max(largest, business_hours)
        previous = current
    return largest


def audit_dataset(requirement: DatasetRequirement, path: Path,
                  requested_start: pd.Timestamp,
                  requested_end: pd.Timestamp) -> DatasetAudit:
    if not path.exists():
        return DatasetAudit(
            requirement.key, str(path), False, 0, None, None, 0, 0, None,
            None, False, False, ("file_not_found",),
        )
    issues: list[str] = []
    try:
        frame = load_ohlc(path)
    except Exception as exc:
        return DatasetAudit(
            requirement.key, str(path), True, 0, None, None, 0, 0, None,
            _sha256(path), False, False, (f"parse_error:{exc}",),
        )
    if frame.empty:
        issues.append("empty_file")
    duplicates = int(frame["time"].duplicated().sum())
    if duplicates:
        issues.append("duplicate_timestamps")
    invalid = int((
        frame["time"].isna()
        | (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
    ).sum())
    if invalid:
        issues.append("invalid_ohlc")

    start = frame["time"].min() if len(frame) else pd.NaT
    end = frame["time"].max() if len(frame) else pd.NaT
    tolerance = pd.Timedelta(days=7)
    covers = bool(
        pd.notna(start) and pd.notna(end)
        and start <= requested_start + tolerance
        and end >= requested_end - tolerance
    )
    if not covers:
        issues.append("insufficient_date_coverage")
    gap = _largest_weekday_gap_hours(frame["time"])
    if gap > requirement.maximum_weekday_gap_hours:
        issues.append("large_weekday_gap")

    return DatasetAudit(
        requirement.key, str(path), True, int(len(frame)),
        start.isoformat() if pd.notna(start) else None,
        end.isoformat() if pd.notna(end) else None,
        duplicates, invalid, gap, _sha256(path), covers, not issues, tuple(issues),
    )


def run_preflight(files: dict[str, Path], requested_start: pd.Timestamp,
                  requested_end: pd.Timestamp) -> dict:
    audits = [
        audit_dataset(requirement, files[requirement.key], requested_start, requested_end)
        for requirement in REQUIREMENTS
    ]
    ready = all(audit.valid for audit in audits)
    return {
        "status": "READY" if ready else "BLOCKED_INSUFFICIENT_DATA",
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "ten_year_label_allowed": ready,
        "datasets": [asdict(audit) for audit in audits],
        "rule": "A ten-year result may only be emitted when every active engine has valid raw coverage.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-07-01T00:00:00+00:00")
    parser.add_argument("--end", default=datetime.now(UTC).isoformat())
    parser.add_argument("--gbpusd-m15", type=Path, required=True)
    parser.add_argument("--gbpusd-h4", type=Path, required=True)
    parser.add_argument("--gbpusd-d1", type=Path, required=True)
    parser.add_argument("--eurusd-m15", type=Path, required=True)
    parser.add_argument("--gbpjpy-m15", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("v9_10y_preflight.json"))
    args = parser.parse_args(argv)
    files = {
        "gbpusd_m15": args.gbpusd_m15,
        "gbpusd_h4": args.gbpusd_h4,
        "gbpusd_d1": args.gbpusd_d1,
        "eurusd_m15": args.eurusd_m15,
        "gbpjpy_m15": args.gbpjpy_m15,
    }
    payload = run_preflight(files, _parse_utc(args.start), _parse_utc(args.end))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["ten_year_label_allowed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
