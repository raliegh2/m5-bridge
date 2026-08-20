"""Export the deepest history this terminal will serve, for honest validation.

`research/V15_EDGE_INVESTIGATION.md` concluded that the binding constraint on
proving or disproving an edge is data: the repo held eight months of GBPUSD M5
and nothing else, so no ten-year claim in `research/` could be reproduced.

This script fixes that. It asks MetaTrader 5 for the maximum depth it has on
each requested symbol/timeframe, writes one CSV per pair, and records a manifest
of exactly what was obtained -- including what was NOT available, so a gap is
visible rather than assumed.

It never sends an order. It only calls initialize / symbol_select /
copy_rates_from_pos.

Run with MetaTrader 5 installed and logged in:

    python tools/export_validation_history.py
    python tools/export_validation_history.py --symbols GBPUSD EURUSD --out research/data

Tip: in MT5 set Tools -> Options -> Charts -> "Max bars in chart" to Unlimited,
otherwise the terminal caps the depth it serves.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "research" / "data"

DEFAULT_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY", "XAUUSD")
DEFAULT_TIMEFRAMES = ("D1", "H4", "H1")

# Generous request counts; the terminal returns only what it actually holds.
BARS_REQUEST = {"D1": 20_000, "H4": 120_000, "H1": 400_000,
                "M30": 500_000, "M15": 500_000, "M5": 500_000}
COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread"]


def _timeframes(mt5):
    return {"D1": mt5.TIMEFRAME_D1, "H4": mt5.TIMEFRAME_H4,
            "H1": mt5.TIMEFRAME_H1, "M30": mt5.TIMEFRAME_M30,
            "M15": mt5.TIMEFRAME_M15, "M5": mt5.TIMEFRAME_M5}


def _frame(raw) -> pd.DataFrame:
    frame = pd.DataFrame(raw)
    keep = [c for c in COLUMNS if c in frame.columns]
    return frame[keep].sort_values("time").drop_duplicates("time")


def export(mt5, symbol: str, tf_name: str, tf_const, out_dir: Path,
           retries: int = 2) -> dict:
    """Pull one symbol/timeframe, retrying while the terminal backfills."""
    raw = None
    for attempt in range(retries + 1):
        raw = mt5.copy_rates_from_pos(symbol, tf_const, 0,
                                      BARS_REQUEST.get(tf_name, 100_000))
        if raw is not None and len(raw):
            break
        # A freshly selected symbol often serves nothing until the terminal
        # has fetched it; give it a moment rather than reporting a false gap.
        if attempt < retries:
            time.sleep(2.0)
    if raw is None or len(raw) == 0:
        return {"bars": 0, "available": False,
                "note": "terminal served no bars for this symbol/timeframe"}

    frame = _frame(raw)
    path = out_dir / f"{symbol}_{tf_name}.csv"
    frame.to_csv(path, index=False)
    t0 = datetime.fromtimestamp(int(frame.time.min()), tz=timezone.utc)
    t1 = datetime.fromtimestamp(int(frame.time.max()), tz=timezone.utc)
    return {
        "bars": int(len(frame)),
        "available": True,
        "start": t0.isoformat(),
        "end": t1.isoformat(),
        "years": round((t1 - t0).days / 365.25, 2),
        "file": path.name,
        "bytes": path.stat().st_size,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    p.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    args = p.parse_args(argv)

    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("MetaTrader5 is not installed. This export requires Windows "
              "with the MT5 terminal.")
        return 2

    from dotenv import load_dotenv
    load_dotenv()

    if not mt5.initialize():
        import os
        login = os.getenv("MT5_LOGIN") or os.getenv("LOGIN")
        password = os.getenv("MT5_PASSWORD") or os.getenv("PASSWORD")
        server = os.getenv("MT5_SERVER") or os.getenv("SERVER")
        if not (login and password and server):
            print(f"MT5 initialize failed: {mt5.last_error()}")
            print("Open MetaTrader 5 and log in, or set MT5_LOGIN / "
                  "MT5_PASSWORD / MT5_SERVER in .env")
            return 2
        if not mt5.initialize(login=int(login), password=password,
                              server=server):
            print(f"MT5 login failed: {mt5.last_error()}")
            return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tf_map = _timeframes(mt5)

    account = mt5.account_info()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "server": getattr(account, "server", None) if account else None,
        "is_demo": (getattr(account, "trade_mode", None) == 0
                    if account else None),
        "symbols": {},
    }

    print(f"Server: {manifest['server']}  demo={manifest['is_demo']}")
    print(f"Output: {out_dir}\n")
    print(f"{'symbol':<9}{'tf':<5}{'bars':>9}  {'range':<25}{'span':>7}")
    print("-" * 56)

    try:
        for symbol in args.symbols:
            selected = mt5.symbol_select(symbol, True)
            manifest["symbols"][symbol] = {"selected": bool(selected),
                                           "timeframes": {}}
            if not selected:
                print(f"{symbol:<9}{'-':<5}{'':>9}  not offered by this broker")
                continue
            for tf_name in args.timeframes:
                tf_const = tf_map.get(tf_name.upper())
                if tf_const is None:
                    print(f"unknown timeframe {tf_name}, skipping")
                    continue
                info = export(mt5, symbol, tf_name.upper(), tf_const, out_dir)
                manifest["symbols"][symbol]["timeframes"][tf_name.upper()] = info
                if info["available"]:
                    print(f"{symbol:<9}{tf_name.upper():<5}{info['bars']:>9}  "
                          f"{info['start'][:10]} -> {info['end'][:10]}   "
                          f"{info['years']:>5.1f}y")
                else:
                    print(f"{symbol:<9}{tf_name.upper():<5}{0:>9}  "
                          f"no history served")
    finally:
        mt5.shutdown()

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    got = [(s, tf, i) for s, d in manifest["symbols"].items()
           for tf, i in d.get("timeframes", {}).items() if i["available"]]
    missing = [s for s, d in manifest["symbols"].items() if not d["selected"]]

    print(f"\nWrote {len(got)} file(s) and {manifest_path.name}")
    if missing:
        print(f"NOT offered by this broker: {', '.join(missing)}")
        print("Those symbols cannot be validated here. Any research report "
              "claiming results for them is unreproducible on this account.")
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())
