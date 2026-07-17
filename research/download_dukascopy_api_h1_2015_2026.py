"""Download Dukascopy H1 bid/ask candles through the documented Trading Tools API."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE = "https://freeserv.dukascopy.com/2.0/"
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
SIDES = {"bid": "B", "ask": "A"}
FROM = pd.Timestamp(os.getenv("DUKASCOPY_FROM", "2015-01-01T00:00:00Z"))
TO = pd.Timestamp(os.getenv("DUKASCOPY_TO", "2026-07-17T00:00:00Z"))
OUT = Path(os.getenv("DUKASCOPY_OUT", "research/dukascopy_2016_2026_data"))
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "m5-bridge-research/1.0"})


def decode_payload(response: requests.Response) -> Any:
    response.raise_for_status()
    text = response.text.strip()
    try:
        return response.json()
    except ValueError:
        match = re.search(r"^[^(]*\((.*)\)\s*;?\s*$", text, flags=re.S)
        if match:
            return json.loads(match.group(1))
        raise RuntimeError(
            f"Unexpected Dukascopy response ({response.status_code}, {response.headers.get('content-type')}): {text[:500]}"
        )


def request(params: dict[str, Any]) -> Any:
    error: Exception | None = None
    for attempt in range(1, 7):
        try:
            response = SESSION.get(BASE, params=params, timeout=(20, 90))
            return decode_payload(response)
        except Exception as exc:  # network/server retries are deliberate
            error = exc
            print(f"request attempt {attempt} failed: {exc}", flush=True)
            time.sleep(min(30, attempt * 4))
    raise RuntimeError(f"Dukascopy API request failed after retries: {error}")


def records_container(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "candles", "prices", "result", "items", "instruments"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        # Some API wrappers key records by timestamp or id.
        values = list(payload.values())
        if values and all(isinstance(value, (dict, list)) for value in values):
            return values
    raise RuntimeError(f"Unable to locate records in Dukascopy payload: {str(payload)[:600]}")


def instrument_map() -> dict[str, int]:
    payload = request(
        {
            "path": "api/instrumentList",
            "fields": "id,name,pipValue,nameLong",
        }
    )
    mapping: dict[str, int] = {}
    for row in records_container(payload):
        if isinstance(row, dict):
            name = str(row.get("name") or row.get("symbol") or row.get("ticker") or "")
            identifier = row.get("id") or row.get("instrumentId") or row.get("instrument")
        elif isinstance(row, list) and len(row) >= 2:
            identifier, name = row[0], str(row[1])
        else:
            continue
        normalized = re.sub(r"[^A-Z]", "", name.upper())
        if normalized in SYMBOLS and identifier is not None:
            mapping[normalized] = int(identifier)
    missing = sorted(set(SYMBOLS) - set(mapping))
    if missing:
        raise RuntimeError(f"Dukascopy instrument ids not found for {missing}; resolved={mapping}")
    return mapping


def timestamp(value: Any) -> pd.Timestamp:
    if isinstance(value, (int, float)):
        unit = "ms" if abs(float(value)) > 100_000_000_000 else "s"
        return pd.to_datetime(value, unit=unit, utc=True)
    return pd.to_datetime(value, utc=True)


def normalize(row: Any) -> dict[str, Any] | None:
    if isinstance(row, dict):
        raw_time = row.get("timestamp") or row.get("time") or row.get("date") or row.get("datetime")
        values = {
            "open": row.get("open") if row.get("open") is not None else row.get("o"),
            "high": row.get("high") if row.get("high") is not None else row.get("h"),
            "low": row.get("low") if row.get("low") is not None else row.get("l"),
            "close": row.get("close") if row.get("close") is not None else row.get("c"),
            "tick_volume": row.get("volume") or row.get("volumes") or row.get("tick_volume") or row.get("v") or 0,
        }
    elif isinstance(row, list) and len(row) >= 5:
        raw_time = row[0]
        values = {
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "tick_volume": row[5] if len(row) > 5 else 0,
        }
    else:
        return None
    if raw_time is None:
        return None
    try:
        result = {"time": timestamp(raw_time)}
        for key, value in values.items():
            result[key] = float(value)
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def intervals(start: pd.Timestamp, end: pd.Timestamp):
    cursor = start
    while cursor < end:
        chunk_end = min(end, cursor + pd.DateOffset(months=6))
        yield cursor, chunk_end
        cursor = chunk_end


def download(identifier: int, symbol: str, side_name: str, offer_side: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for start, end in intervals(FROM, TO):
        print(f"{symbol}/{side_name}: {start.date()} to {end.date()}", flush=True)
        payload = request(
            {
                "path": "api/historicalPrices",
                "instrument": identifier,
                "timeFrame": "1hour",
                "count": 5000,
                "start": int(start.timestamp() * 1000),
                "end": int(end.timestamp() * 1000),
                "dayStartTime": "UTC",
                "offerSide": offer_side,
            }
        )
        for raw in records_container(payload):
            item = normalize(raw)
            if item is not None:
                rows.append(item)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"{symbol}/{side_name}: no candles returned")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    frame = frame[(frame["time"] >= FROM) & (frame["time"] <= TO)]
    frame = frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if len(frame) < 50_000:
        raise RuntimeError(f"{symbol}/{side_name}: only {len(frame)} unique H1 bars returned")
    path = OUT / f"{symbol}_H1_{side_name}.csv"
    frame.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    return {
        "symbol": symbol,
        "side": side_name,
        "bars": int(len(frame)),
        "start": frame["time"].min().isoformat(),
        "end": frame["time"].max().isoformat(),
        "file": str(path),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    completed: list[dict[str, Any]] = []
    try:
        ids = instrument_map()
        print("resolved instruments", ids, flush=True)
        for symbol in SYMBOLS:
            for side_name, offer_side in SIDES.items():
                completed.append(download(ids[symbol], symbol, side_name, offer_side))
        manifest = {
            "provider": "Dukascopy Trading Tools API",
            "endpoint": BASE,
            "timeframe": "1hour",
            "from": FROM.isoformat(),
            "to": TO.isoformat(),
            "downloads": completed,
        }
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2))
    except Exception as exc:
        (OUT / "download_error.json").write_text(
            json.dumps(
                {
                    "message": str(exc),
                    "type": type(exc).__name__,
                    "completed": completed,
                    "time": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
