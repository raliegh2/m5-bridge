"""Download FXCM's official weekly H1 bid/ask candle files for five symbols."""
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
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
FROM = pd.Timestamp(os.getenv("FXCM_FROM", "2015-01-01T00:00:00Z"))
TO = pd.Timestamp(os.getenv("FXCM_TO", "2026-07-17T00:00:00Z"))
OUT = Path(os.getenv("FXCM_OUT", "research/fxcm_2016_2026_data"))
WORKERS = int(os.getenv("FXCM_WORKERS", "24"))


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
                headers={"User-Agent": "m5-bridge-research/1.0"},
            )
            if response.status_code == 404:
                return response.status_code, b""
            response.raise_for_status()
            return response.status_code, response.content
        except Exception as exc:
            error = exc
            time.sleep(attempt * 1.5)
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
    names = {str(column).lower().replace("_", "").replace(" ", ""): str(column) for column in frame.columns}
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
                "tick_volume": pd.to_numeric(merged[volume_column], errors="coerce").fillna(0.0) if volume_column else 0.0,
            }
        )
        return output.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    bid = side("Bid")
    ask = side("Ask")
    if len(bid) < 50_000 or len(ask) < 50_000:
        raise RuntimeError(f"{symbol}: insufficient H1 rows bid={len(bid)} ask={len(ask)}")
    quality = {
        "symbol": symbol,
        "bars": int(len(bid)),
        "start": bid["time"].min().isoformat(),
        "end": bid["time"].max().isoformat(),
        "raw_columns": list(merged.columns),
    }
    return bid, ask, quality


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    requested_weeks = weeks(FROM, TO)
    manifest: dict[str, Any] = {
        "provider": "FXCM weekly candle archive",
        "endpoint_template": BASE,
        "timeframe": "H1",
        "from": FROM.isoformat(),
        "to": TO.isoformat(),
        "symbols": {},
    }
    try:
        for symbol in SYMBOLS:
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
                    except Exception as exc:
                        failures.append(f"{year}-W{week:02d}: {exc}")
            if not frames:
                raise RuntimeError(f"{symbol}: no FXCM weekly files downloaded; failures={failures[:5]}")
            bid, ask, quality = normalize(frames, symbol)
            bid.to_csv(OUT / f"{symbol}_H1_bid.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
            ask.to_csv(OUT / f"{symbol}_H1_ask.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
            manifest["symbols"][symbol] = {
                **quality,
                "weekly_files_downloaded": successes,
                "weekly_files_missing": missing,
                "weekly_failures": failures,
            }
            print(symbol, json.dumps(manifest["symbols"][symbol], indent=2), flush=True)
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2))
    except Exception as exc:
        (OUT / "download_error.json").write_text(
            json.dumps(
                {
                    "message": str(exc),
                    "type": type(exc).__name__,
                    "manifest_so_far": manifest,
                    "time": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
