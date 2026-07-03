"""Continuously log M15/M30 intraday candidates without sending orders."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from mt5_ai_bridge.app import connect  # noqa: E402
from mt5_ai_bridge.config import load_settings  # noqa: E402
from mt5_ai_bridge.mt5_client import create_client  # noqa: E402
from v12_intraday_paper_engine import latest_candidate  # noqa: E402

STATE_PATH = ROOT / "v12_intraday_paper_state.json"
LOG_PATH = ROOT / "v12_intraday_paper_signals.jsonl"


def _frame(rates) -> pd.DataFrame:
    frame = pd.DataFrame(rates)
    if frame.empty:
        return frame
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame[["time", "open", "high", "low", "close"]].dropna().sort_values(
        "time").drop_duplicates("time").reset_index(drop=True)


def _last_key() -> str:
    if not STATE_PATH.exists():
        return ""
    try:
        return str(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("last_key", ""))
    except (OSError, ValueError, TypeError):
        return ""


def scan_once(client) -> dict | None:
    # start=1 excludes the currently forming M5 candle.
    frame = _frame(client.copy_rates_from_pos("GBPUSD", "M5", 1, 3000))
    if len(frame) < 300:
        raise RuntimeError("Insufficient closed GBPUSD M5 history for paper engine.")
    candidate = latest_candidate(frame)
    if candidate is None:
        return None
    key = f"{candidate.engine}:{candidate.side}:{candidate.signal_time.isoformat()}"
    if key == _last_key():
        return None
    payload = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER_ONLY_NO_ORDER_SEND", "candidate": candidate.to_dict(),
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")
    STATE_PATH.write_text(json.dumps({"last_key": key}, indent=2) + "\n",
                          encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    client = create_client()
    connect(client, load_settings())
    print("M15/M30 intraday engine: PAPER ONLY; broker orders are disabled.")
    try:
        while True:
            try:
                if scan_once(client) is None:
                    print(f"{datetime.now(timezone.utc).isoformat()} no new paper signal")
            except Exception as exc:  # noqa: BLE001
                print(f"paper engine error: {type(exc).__name__}: {exc}")
            if args.once:
                break
            time.sleep(max(5, args.interval))
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
