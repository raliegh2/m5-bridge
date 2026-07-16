"""Export the full V14.6 research dataset from the local MT5 terminal.

This produces every input needed to rebuild the 10-year benchmark with
continuous coverage (the current repo has V12 swing data only to 2022-03
and ICT data only from 2023-01):

* D1 / H4 / H1 bars, maximum available depth, for all five portfolio
  symbols (drives V12 swing signal regeneration 2013 -> today).
* M1 bars, maximum available depth, for GBPUSD and GBPJPY (drives the
  wider-stop ICT engine rebuild).

Run with MetaTrader 5 open and logged in:

    python tools\v14_6_export_full_history.py

or double-click "Export V14-6 Research Data.bat".

Tip: in MT5 go to Tools -> Options -> Charts and set "Max bars in chart"
to Unlimited first, otherwise the terminal may cap the depth it serves.
M1 exports are chunked, so a deep M1 history can take a few minutes and
several hundred MB.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "research" / "data_v14_6"

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
SWING_TIMEFRAMES = ("D1", "H4", "H1")
M1_SYMBOLS = ("GBPUSD", "GBPJPY")

# Generous request counts; MT5 returns what it actually has.
BARS_REQUEST = {"D1": 4000, "H4": 24000, "H1": 96000}
M1_YEARS = 11
COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread"]


def _frame(raw) -> pd.DataFrame:
    frame = pd.DataFrame(raw)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    keep = [column for column in COLUMNS if column in frame.columns]
    return frame[keep].sort_values("time").drop_duplicates("time")


def export_swing(client, symbol: str, timeframe: str) -> dict | None:
    raw = client.copy_rates_from_pos(symbol, timeframe, 0, BARS_REQUEST[timeframe])
    if raw is None or len(raw) == 0:
        return None
    frame = _frame(raw)
    path = OUT_DIR / f"{symbol}_{timeframe}.csv"
    frame.to_csv(path, index=False)
    return {
        "bars": len(frame),
        "start": frame.time.min().isoformat(),
        "end": frame.time.max().isoformat(),
        "file": path.name,
    }


def export_m1(client, symbol: str) -> dict | None:
    """Chunk M1 by bar position to dodge terminal request caps."""
    max_bars = int(370_000 * M1_YEARS)  # ~370k M1 bars per year
    chunk_size = 100_000
    chunks = []
    position = 0
    while position < max_bars:
        raw = client.copy_rates_from_pos(symbol, "M1", position, chunk_size)
        if raw is None or len(raw) == 0:
            break
        chunks.append(_frame(raw))
        if len(raw) < chunk_size:
            break
        position += chunk_size
    if not chunks:
        return None
    frame = (
        pd.concat(chunks, ignore_index=True)
        .sort_values("time")
        .drop_duplicates("time")
    )
    path = OUT_DIR / f"{symbol}_M1.csv"
    frame.to_csv(path, index=False)
    return {
        "bars": len(frame),
        "start": frame.time.min().isoformat(),
        "end": frame.time.max().isoformat(),
        "file": path.name,
    }


def main() -> int:
    settings = load_settings()
    client = create_client()
    connect(client, settings)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict] = {"generated_at": datetime.now(timezone.utc).isoformat()}
    try:
        for symbol in SYMBOLS:
            manifest[symbol] = {}
            for timeframe in SWING_TIMEFRAMES:
                info = export_swing(client, symbol, timeframe)
                manifest[symbol][timeframe] = info
                print(f"{symbol} {timeframe}: "
                      + (f"{info['bars']} bars {info['start'][:10]} -> {info['end'][:10]}"
                         if info else "NO DATA"))
        for symbol in M1_SYMBOLS:
            print(f"{symbol} M1: exporting in chunks "
                  f"(up to {M1_YEARS} years, please wait)...")
            info = export_m1(client, symbol)
            manifest[symbol]["M1"] = info
            print(f"{symbol} M1: "
                  + (f"{info['bars']} bars {info['start'][:10]} -> {info['end'][:10]}"
                     if info else "NO DATA"))
    finally:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nManifest written to {OUT_DIR / 'manifest.json'}")
    print("Next: rerun the V14.5/V14.6 research with continuous 10-year coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
