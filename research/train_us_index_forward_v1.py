"""Train and evaluate US_INDEX_FORWARD_V1 on the committed US500 history."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.us_index_forward_v1 import (
    LOCKED_CONFIG,
    backtest_frozen,
    fit_model,
    save_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "research" / "data" / "US500_D1.csv"
DEFAULT_ARTIFACT = ROOT / "research" / "us_index_forward_v1_model.json"
DEFAULT_RESULT = ROOT / "research" / "us_index_forward_v1_result.json"


def _ready(summary: dict) -> tuple[bool, list[str]]:
    reasons = []
    if float(summary["training_years"]) < 5.0:
        reasons.append("training span below five years")
    if float(summary["return_pct"]) <= 0:
        reasons.append("post-cutoff return is not positive")
    pf = summary["profit_factor"]
    pf_value = float("inf") if pf == "inf" else float(pf)
    if pf_value < 1.0:
        reasons.append("post-cutoff profit factor below 1.0")
    if float(summary["max_drawdown_pct"]) > 10.0:
        reasons.append("post-cutoff max drawdown above 10%")
    if int(summary["trades"]) < 40:
        reasons.append("fewer than 40 post-cutoff trades")
    return not reasons, reasons


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--balance", type=float, default=10_000.0)
    args = parser.parse_args(argv)

    bars = pd.read_csv(args.data)
    artifact = fit_model(bars, LOCKED_CONFIG)
    save_artifact(artifact, args.artifact)

    full = backtest_frozen(bars, artifact, LOCKED_CONFIG, args.balance)
    trade_log = full.pop("trade_log")
    ready, reasons = _ready(full)
    full["forward_test_ready"] = ready
    full["forward_test_blockers"] = reasons
    full["execution_scope"] = "demo-forward-test-only"
    full["options_enabled"] = False
    full["notes"] = (
        "The artifact is fit only through 2020-12-31. All later bars are "
        "evaluated without refitting. Passing this gate authorizes demo forward "
        "testing only; it does not authorize funded/live trading."
    )

    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(full, indent=2), encoding="utf-8")
    ledger = args.result.with_name(args.result.stem + "_ledger.json")
    ledger.write_text(json.dumps(trade_log, indent=2), encoding="utf-8")

    print(json.dumps(full, indent=2))
    print(f"artifact: {args.artifact}")
    print(f"ledger:   {ledger}")
    return 0 if ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
