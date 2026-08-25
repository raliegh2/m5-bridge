"""Train US_INDEX_FORWARD_V4_H4 and compare it with V3 on a $5,000 account."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.us_index_forward_v3 import (
    LOCKED_CONFIG as V3_CONFIG,
    backtest_window as backtest_v3,
    load_artifact as load_v3,
)
from mt5_ai_bridge.us_index_forward_v4 import (
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
DEFAULT_DATA = ROOT / "research" / "data" / "US500_H4.csv"
D1_DATA = ROOT / "research" / "data" / "US500_D1.csv"
DEFAULT_ARTIFACT = ROOT / "research" / "us_index_forward_v4_model.json"
DEFAULT_RESULT = ROOT / "research" / "us_index_forward_v4_result.json"
V3_ARTIFACT = ROOT / "research" / "us_index_forward_v3_model.json"


def _cagr_pct(start_balance: float, end_balance: float, start_time: int, end_time: int) -> float:
    years = max((end_time - start_time) / (365.2425 * 86_400.0), 1e-9)
    if start_balance <= 0 or end_balance <= 0:
        return -100.0
    return ((end_balance / start_balance) ** (1.0 / years) - 1.0) * 100.0


def _ten_year_equivalent(cagr_pct: float) -> float:
    return ((1.0 + cagr_pct / 100.0) ** 10.0 - 1.0) * 100.0


def _ready(v4: dict, v3: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if float(v4["training_years"]) < 5.0:
        reasons.append("training span below five years")
    if float(v4["return_pct"]) <= float(v3["return_pct"]):
        reasons.append("V4 did not beat V3 net return")
    if float(v4["holdout_cagr_pct"]) < 4.0:
        reasons.append("V4 holdout CAGR below locked 4.0% growth floor")
    pf = v4["profit_factor"]
    pfv = float("inf") if pf == "inf" else float(pf)
    if pfv < 1.10:
        reasons.append("V4 net profit factor below 1.10")
    if float(v4["max_drawdown_pct"]) > 10.0:
        reasons.append("V4 max drawdown above 10%")
    if int(v4["trades"]) < 250:
        reasons.append("V4 has fewer than 250 post-cutoff trades")
    if float(v4["max_drawdown_pct"]) > max(8.0, float(v3["max_drawdown_pct"]) * 2.5):
        reasons.append("V4 drawdown expanded too far relative to V3")
    return not reasons, reasons


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--balance", type=float, default=5_000.0)
    args = parser.parse_args(argv)

    h4 = pd.read_csv(args.data)
    artifact = fit_model(h4, LOCKED_CONFIG)
    save_artifact(artifact, args.artifact)

    cutoff = int(pd.Timestamp(LOCKED_CONFIG.training_cutoff, tz="UTC").timestamp())
    h4_start = int(h4.loc[h4["time"] > cutoff, "time"].min())
    h4_end = int(h4["time"].max())

    d1 = pd.read_csv(D1_DATA)
    d1_start = int(d1.loc[d1["time"] > cutoff, "time"].min())
    d1_end = int(d1["time"].max())
    validation_start = max(h4_start, d1_start)
    validation_end = min(h4_end, d1_end)

    v4_gross = backtest_window(
        h4,
        artifact,
        LOCKED_CONFIG,
        args.balance,
        start_time=validation_start,
        end_time=validation_end,
        min_lot=0.1,
        lot_step=0.1,
    )
    v4_trade_log = v4_gross.pop("trade_log")
    v4, v4_trade_log, _ = _net_of_costs(v4_gross, v4_trade_log, args.balance, symbol="US500")
    v4_cagr = _cagr_pct(args.balance, float(v4["final_balance"]), validation_start, validation_end)
    v4.update({
        "training_start": artifact.training_start,
        "training_end": artifact.training_end,
        "training_years": artifact.training_years,
        "training_rows": artifact.training_rows,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "selected_candidate": artifact.candidate["name"],
        "selection_report": artifact.selection_report,
        "risk_percent_ceiling": LOCKED_CONFIG.risk_percent,
        "timeframe": LOCKED_CONFIG.timeframe,
        "holdout_cagr_pct": round(v4_cagr, 4),
        "ten_year_equivalent_return_pct": round(_ten_year_equivalent(v4_cagr), 4),
        "ten_year_equivalent_is_projection": True,
    })

    if not V3_ARTIFACT.exists():
        raise FileNotFoundError("V3 artifact required for locked comparison")
    v3_artifact = load_v3(V3_ARTIFACT)
    v3_gross = backtest_v3(
        d1,
        v3_artifact,
        V3_CONFIG,
        args.balance,
        start_time=validation_start,
        end_time=validation_end,
    )
    v3_trade_log = v3_gross.pop("trade_log")
    v3, _, _ = _net_of_costs(v3_gross, v3_trade_log, args.balance, symbol="US500")
    v3_cagr = _cagr_pct(args.balance, float(v3["final_balance"]), validation_start, validation_end)

    annual = _annual_returns(v4_trade_log, args.balance, validation_start, validation_end)
    monthly = _monthly_equity(v4_trade_log, args.balance, validation_start, validation_end)
    ready, blockers = _ready(v4, {**v3, "holdout_cagr_pct": v3_cagr})

    result = dict(v4)
    result["annual_returns"] = annual
    result["monthly_equity"] = monthly
    result["comparison_v3_5000"] = {
        "final_balance": v3["final_balance"],
        "net_profit": v3["net_profit"],
        "return_pct": v3["return_pct"],
        "holdout_cagr_pct": round(v3_cagr, 4),
        "max_drawdown_pct": v3["max_drawdown_pct"],
        "trades": v3["trades"],
        "win_rate": v3["win_rate"],
        "profit_factor": v3["profit_factor"],
    }
    result["forward_test_ready"] = ready
    result["forward_test_blockers"] = blockers
    result["execution_scope"] = "demo-forward-test-only"
    result["starting_account_model"] = args.balance
    result["notes"] = (
        "V4 uses H4 data and pre-2021-only candidate selection with a 1.00% maximum "
        "initial-stop risk. The 2021+ window is the locked comparison against V3. "
        "The ten_year_equivalent_return_pct field is only a mathematical projection "
        "from holdout CAGR, not a ten-year backtest. The repo does not contain five "
        "training years plus ten additional untouched US500 years. Results include "
        "the typical US500 transaction-cost model and are not guaranteed future returns."
    )

    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    ledger = args.result.with_name(args.result.stem + "_ledger.json")
    ledger.write_text(json.dumps(v4_trade_log, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    print(f"artifact: {args.artifact}")
    print(f"ledger:   {ledger}")
    return 0 if ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
