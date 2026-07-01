"""Export long GBPUSD MT5 history for V4 and Satellite V2 validation.

Run through the repository-root wrapper ``python export_gbpusd_history.py``
after MT5 is open and logged in. Credentials are read from .env and are never
printed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client

BARS_PER_YEAR = {
    "M15": 96 * 260,
    "M30": 48 * 260,
    "H1": 24 * 260,
    "H4": 6 * 260,
    "D1": 260,
}


def export_timeframe(client, symbol: str, timeframe: str, count: int,
                     output: Path) -> dict:
    raw = client.copy_rates_from_pos(symbol, timeframe, 0, count)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"No {timeframe} bars returned: {client.last_error()}")
    frame = pd.DataFrame(raw)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    preferred = [
        column for column in (
            "time", "open", "high", "low", "close", "tick_volume",
            "spread", "real_volume",
        ) if column in frame.columns
    ]
    frame = frame[preferred].sort_values("time").drop_duplicates("time")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return {
        "timeframe": timeframe,
        "bars": len(frame),
        "start": frame.time.min().isoformat(),
        "end": frame.time.max().isoformat(),
        "path": str(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="GBPUSD")
    parser.add_argument("--years", type=int, default=11)
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    settings = load_settings()
    if not settings.has_credentials:
        raise RuntimeError("MT5 credentials are missing from .env")
    client = create_client()
    if not client.initialize():
        raise RuntimeError(f"MT5 initialize failed: {client.last_error()}")
    try:
        if not client.login(settings.login, settings.password, settings.server):
            raise RuntimeError(f"MT5 login failed: {client.last_error()}")
        results = []
        for timeframe in ("M15", "M30", "H1", "H4", "D1"):
            count = int(BARS_PER_YEAR[timeframe] * args.years * 1.20)
            results.append(export_timeframe(
                client,
                args.symbol,
                timeframe,
                count,
                args.out_dir / f"{args.symbol}_{timeframe}.csv",
            ))
        print("Export complete:")
        for result in results:
            print(
                f"{result['timeframe']}: {result['bars']} bars | "
                f"{result['start']} -> {result['end']} | {result['path']}"
            )
        m15 = next(item for item in results if item["timeframe"] == "M15")
        actual_years = (
            pd.Timestamp(m15["end"]) - pd.Timestamp(m15["start"])
        ).days / 365.25
        if actual_years < min(args.years - 0.5, 9.5):
            print(
                "WARNING: MT5 returned less M15 history than requested. Increase "
                "Tools > Options > Charts > Max bars in chart, open GBPUSD M15, "
                "press Home repeatedly to load older bars, then rerun the export."
            )
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
