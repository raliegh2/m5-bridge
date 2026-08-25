"""Train US_INDEX_FORWARD_V3 and compare it with V2 on a $5,000 account."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.us_index_forward_v2 import (
    LOCKED_CONFIG as V2_CONFIG,
    backtest_window as backtest_v2,
    load_artifact as load_v2,
)
from mt5_ai_bridge.us_index_forward_v3 import (
    LOCKED_CONFIG,
    backtest_window,
    fit_model,
    save_artifact,
)
from research.train_us_index_forward_v2 import (
    _annual_returns,
    _monthly_equity,
    _net_of_costs,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "research" / "data" / "US500_D1.csv"
DEFAULT_ARTIFACT = ROOT / "research" / "us_index_forward_v3_model.json"
DEFAULT_RESULT = ROOT / "research" / "us_index_forward_v3_result.json"
V2_ARTIFACT = ROOT / "research" / "us_index_forward_v2_model.json"


def _ready(v3: dict, v2: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if float(v3["training_years"]) < 5.0:
        reasons.append("training span below five years")
    if float(v3["return_pct"]) <= float(v2["return_pct"]):
        reasons.append("V3 did not beat V2 net return")
    if float(v3["profit_factor"]) < 1.05:
        reasons.append("V3 net profit factor below 1.05")
    if float(v3["max_drawdown_pct"]) > 7.5:
        reasons.append("V3 max drawdown above 7.5%")
    minimum_trades = max(100, math.ceil(int(v2["trades"]) * 1.40))
    if int(v3["trades"]) < minimum_trades:
        reasons.append(f"V3 trade count below required {minimum_trades}")
    if float(v3["max_drawdown_pct"]) > max(5.0, float(v2["max_drawdown_pct"]) * 2.5):
        reasons.append("V3 drawdown expanded too far relative to V2")
    return not reasons, reasons


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--balance", type=float, default=5_000.0)
    args = parser.parse_args(argv)

    bars = pd.read_csv(args.data)
    artifact = fit_model(bars, LOCKED_CONFIG)
    save_artifact(artifact, args.artifact)

    cutoff = int(pd.Timestamp(LOCKED_CONFIG.training_cutoff, tz="UTC").timestamp())
    validation_start = int(bars.loc[bars["time"] > cutoff, "time"].min())
    validation_end = int(bars["time"].max())

    v3_gross = backtest_window(
        bars, artifact, LOCKED_CONFIG, args.balance,
        start_time=validation_start, end_time=validation_end,
    )
    v3_trade_log = v3_gross.pop("trade_log")
    v3, v3_trade_log, _ = _net_of_costs(v3_gross, v3_trade_log, args.balance)
    v3.update({
        "training_start": artifact.training_start,
        "training_end": artifact.training_end,
        "training_years": artifact.training_years,
        "training_rows": artifact.training_rows,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "selected_candidate": artifact.candidate["name"],
        "selection_report": artifact.selection_report,
        "risk_percent_ceiling": LOCKED_CONFIG.risk_percent,
    })

    if not V2_ARTIFACT.exists():
        raise FileNotFoundError("V2 artifact required for locked comparison")
    v2_artifact = load_v2(V2_ARTIFACT)
    v2_gross = backtest_v2(
        bars, v2_artifact, V2_CONFIG, args.balance,
        start_time=validation_start, end_time=validation_end,
    )
    v2_trade_log = v2_gross.pop("trade_log")
    v2, _, _ = _net_of_costs(v2_gross, v2_trade_log, args.balance)

    annual = _annual_returns(v3_trade_log, args.balance, validation_start, validation_end)
    monthly = _monthly_equity(v3_trade_log, args.balance, validation_start, validation_end)
    ready, blockers = _ready(v3, v2)

    result = dict(v3)
    result["annual_returns"] = annual
    result["monthly_equity"] = monthly
    result["comparison_v2_5000"] = {
        "final_balance": v2["final_balance"],
        "net_profit": v2["net_profit"],
        "return_pct": v2["return_pct"],
        "max_drawdown_pct": v2["max_drawdown_pct"],
        "trades": v2["trades"],
        "win_rate": v2["win_rate"],
        "profit_factor": v2["profit_factor"],
    }
    result["forward_test_ready"] = ready
    result["forward_test_blockers"] = blockers
    result["execution_scope"] = "demo-forward-test-only"
    result["starting_account_model"] = args.balance
    result["notes"] = (
        "V3 candidate selection used only pre-2021 walk-forward folds. The 2021+ "
        "window remained sealed until this final V2-vs-V3 comparison. Risk is "
        "capped at 1.00% at the initial stop and remains subject to the existing "
        "drawdown governor. Results are historical research estimates after the "
        "repo's typical US500 cost model, not guaranteed future returns."
    )

    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    ledger = args.result.with_name(args.result.stem + "_ledger.json")
    ledger.write_text(json.dumps(v3_trade_log, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    print(f"artifact: {args.artifact}")
    print(f"ledger:   {ledger}")
    return 0 if ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
