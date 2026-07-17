"""Download an expanded FXCM H1 bid/ask research universe.

The downloader is research-only. Unsupported FXCM symbols are recorded and skipped;
core five-symbol coverage is mandatory. Data is never sent to MT5 or a broker.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE = "https://candledata.fxcorporate.com/H1/{symbol}/{year}/{week}.csv.gz"
CORE_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
CANDIDATE_SYMBOLS = (
    *CORE_SYMBOLS,
    "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "EURJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "GBPCHF", "EURCHF", "GBPAUD", "GBPCAD",
    "EURAUD", "EURCAD", "AUDCAD", "AUDNZD", "NZDJPY",
    "XAUUSD", "XAGUSD", "US30", "SPX500", "NAS100", "GER30",
    "UK100", "USOil", "UKOil",
)
FROM = pd.Timestamp(os.getenv("FXCM_FROM", "2013-01-01T00:00:00Z"))
TO = pd.Timestamp(os.getenv("FXCM_TO", "2026-05-31T23:59:59Z"))
OUT = Path(os.getenv("FXCM_OUT", "research/fxcm_diversified_2013_2026_data"))
WORKERS = int(os.getenv("FXCM_WORKERS", "32"))
MIN_BARS = int(os.getenv("FXCM_MIN_BARS", "18000"))


def requested_symbols() -> tuple[str, ...]:
    value = os.getenv("FXCM_SYMBOLS", "").strip()
    if not value:
        return CANDIDATE_SYMBOLS
    return tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))


def weeks(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[int, int]]:
    days = pd.date_range(start.floor("D") - pd.Timedelta(days=7), end.ceil("D"), freq="D", tz="UTC")
    result: set[tuple[int, int]] = set()
    for day in days:
        iso = day.isocalendar()
        result.add((int(iso.year), int(iso.week)))
    return sorted(result)


def fetch(url: str) -> tuple[int, bytes]:
    error: Exception | None = None
    for attempt in range(1, 5):
        try:
            response = requests.get(
                url,
                timeout=(15, 60),
                headers={"User-Agent": "m5-bridge-v15-research/1.0"},
            )
            if response.status_code == 404:
                return 404, b""
            response.raise_for_status()
            return response.status_code, response.content
        except Exception as exc:  # pragma: no cover - network retry path
            error = exc
            time.sleep(attempt * 1.25)
    raise RuntimeError(f"Failed to download {url}: {error}")


def parse_content(content: bytes, source_url: str) -> pd.DataFrame:
    try:
        raw = gzip.decompress(content)
    except OSError:
        raw = content
    try:
        frame = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:
        raise RuntimeError(f"Unable to parse {source_url}: {exc}") from exc
    if frame.empty:
        return frame
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def identify_time_column(frame: pd.DataFrame) -> str:
    names = {str(column).lower(): str(column) for column in frame.columns}
    for candidate in ("datetime", "date", "timestamp", "time"):
        if candidate in names:
            return names[candidate]
    return str(frame.columns[0])


def identify_column(frame: pd.DataFrame, *candidates: str) -> str:
    names = {
        str(column).lower().replace("_", "").replace(" ", ""): str(column)
        for column in frame.columns
    }
    for candidate in candidates:
        key = candidate.lower().replace("_", "").replace(" ", "")
        if key in names:
            return names[key]
    raise RuntimeError(f"Missing expected column {candidates}; available={list(frame.columns)}")


def normalize(frames: list[pd.DataFrame], symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    merged = pd.concat(frames, ignore_index=True, sort=False)
    time_column = identify_time_column(merged)
    merged["time"] = pd.to_datetime(merged[time_column], utc=True, errors="coerce")
    merged = merged.dropna(subset=["time"])
    merged = merged[(merged["time"] >= FROM) & (merged["time"] <= TO)]
    merged = merged.sort_values("time").drop_duplicates("time").reset_index(drop=True)

    volume_column = None
    for candidate in ("TickQty", "tick_volume", "volume"):
        try:
            volume_column = identify_column(merged, candidate)
            break
        except RuntimeError:
            continue

    def side(prefix: str) -> pd.DataFrame:
        output = pd.DataFrame(
            {
                "time": merged["time"],
                "open": pd.to_numeric(merged[identify_column(merged, f"{prefix}Open")], errors="coerce"),
                "high": pd.to_numeric(merged[identify_column(merged, f"{prefix}High")], errors="coerce"),
                "low": pd.to_numeric(merged[identify_column(merged, f"{prefix}Low")], errors="coerce"),
                "close": pd.to_numeric(merged[identify_column(merged, f"{prefix}Close")], errors="coerce"),
                "tick_volume": (
                    pd.to_numeric(merged[volume_column], errors="coerce").fillna(0.0)
                    if volume_column else 0.0
                ),
            }
        )
        return output.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    bid = side("Bid")
    ask = side("Ask")
    if len(bid) < MIN_BARS or len(ask) < MIN_BARS:
        raise RuntimeError(f"{symbol}: insufficient H1 rows bid={len(bid)} ask={len(ask)} minimum={MIN_BARS}")
    quality = {
        "symbol": symbol,
        "bars": int(min(len(bid), len(ask))),
        "start": max(bid["time"].min(), ask["time"].min()).isoformat(),
        "end": min(bid["time"].max(), ask["time"].max()).isoformat(),
        "raw_columns": list(merged.columns),
    }
    return bid, ask, quality


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    requested_weeks = weeks(FROM, TO)
    symbols = requested_symbols()
    manifest: dict[str, Any] = {
        "provider": "FXCM official weekly candle archive",
        "endpoint_template": BASE,
        "timeframe": "H1",
        "from": FROM.isoformat(),
        "to": TO.isoformat(),
        "requested_symbols": list(symbols),
        "core_symbols": list(CORE_SYMBOLS),
        "minimum_bars": MIN_BARS,
        "available": {},
        "unavailable": {},
    }

    for symbol in symbols:
        frames: list[pd.DataFrame] = []
        successes = 0
        missing = 0
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(fetch, BASE.format(symbol=symbol, year=year, week=week)): (year, week)
                for year, week in requested_weeks
            }
            for future in as_completed(futures):
                year, week = futures[future]
                url = BASE.format(symbol=symbol, year=year, week=week)
                try:
                    status, content = future.result()
                    if status == 404 or not content:
                        missing += 1
                        continue
                    frame = parse_content(content, url)
                    if not frame.empty:
                        frames.append(frame)
                        successes += 1
                except Exception as exc:  # pragma: no cover - network error evidence
                    failures.append(f"{year}-W{week:02d}: {exc}")

        try:
            if not frames:
                raise RuntimeError("no weekly files returned")
            bid, ask, quality = normalize(frames, symbol)
            bid.to_csv(OUT / f"{symbol}_H1_bid.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
            ask.to_csv(OUT / f"{symbol}_H1_ask.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
            manifest["available"][symbol] = {
                **quality,
                "weekly_files_downloaded": successes,
                "weekly_files_missing": missing,
                "weekly_failures": failures,
            }
            print(f"AVAILABLE {symbol} bars={quality['bars']}", flush=True)
        except Exception as exc:
            manifest["unavailable"][symbol] = {
                "reason": str(exc),
                "weekly_files_downloaded": successes,
                "weekly_files_missing": missing,
                "weekly_failures": failures[:20],
            }
            print(f"UNAVAILABLE {symbol}: {exc}", flush=True)

    missing_core = sorted(set(CORE_SYMBOLS) - set(manifest["available"]))
    manifest["missing_core_symbols"] = missing_core
    manifest["available_count"] = len(manifest["available"])
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    if missing_core:
        raise RuntimeError(f"Missing mandatory core FXCM symbols: {missing_core}")
    if len(manifest["available"]) < 12:
        raise RuntimeError(f"Expanded universe too small: {len(manifest['available'])} available symbols")


if __name__ == "__main__":
    main()
