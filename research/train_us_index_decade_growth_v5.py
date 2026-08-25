"""Select V5 pre-2016 and run one sealed 2016+ multi-index decade test."""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.us_index_decade_growth_v5 import (
    CANDIDATES,
    artifact_dict,
    backtest_portfolio,
    select_candidate_pre2016,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = {
    "VTI": ROOT / "research" / "data" / "VTI_D1.csv",
    "ONEQ": ROOT / "research" / "data" / "ONEQ_D1.csv",
    "IWM": ROOT / "research" / "data" / "IWM_D1.csv",
}
ARTIFACT = ROOT / "research" / "us_index_decade_growth_v5_model.json"
RESULT = ROOT / "research" / "us_index_decade_growth_v5_result.json"
LEDGER = ROOT / "research" / "us_index_decade_growth_v5_ledger.json"
STARTING_BALANCE = 5_000.0
TRAINING_CUTOFF = "2015-12-31"
HOLDOUT_START = "2016-01-01"
TARGET_CAGR = 10.0


def annual_returns(trades: list[dict], starting_balance: float, start_time: int, end_time: int) -> list[dict]:
    start_year = pd.to_datetime(start_time, unit="s", utc=True).year
    end_year = pd.to_datetime(end_time, unit="s", utc=True).year
    buckets: OrderedDict[int, list[dict]] = OrderedDict((y, []) for y in range(start_year, end_year + 1))
    for trade in trades:
        year = pd.to_datetime(int(trade["exit_time"]), unit="s", utc=True).year
        buckets.setdefault(year, []).append(trade)
    balance = float(starting_balance)
    output = []
    for year, items in buckets.items():
        start = balance
        profit = sum(float(x["profit"]) for x in items)
        balance += profit
        output.append({
            "year": year,
            "start_balance": round(start, 2),
            "profit": round(profit, 2),
            "end_balance": round(balance, 2),
            "return_pct": round((profit / start) * 100.0, 3) if start else 0.0,
            "trades": len(items),
            "period": "YTD" if year == end_year else "full_year",
        })
    return output


def main() -> int:
    bars = {symbol: pd.read_csv(path) for symbol, path in DATA.items()}
    selected, selection_report = select_candidate_pre2016(bars)

    cutoff = int(pd.Timestamp(TRAINING_CUTOFF, tz="UTC").timestamp())
    holdout_start = int(pd.Timestamp(HOLDOUT_START, tz="UTC").timestamp())
    common_end = min(int(df["time"].max()) for df in bars.values())
    common_start = max(int(df["time"].min()) for df in bars.values())
    training_years = (cutoff - common_start) / (365.2425 * 86_400.0)

    result = backtest_portfolio(
        bars,
        selected,
        STARTING_BALANCE,
        start_time=holdout_start,
        end_time=common_end,
    )
    ledger = result.pop("trade_log")
    years = float(result["holdout_years"])
    required_end = STARTING_BALANCE * (1.0 + TARGET_CAGR / 100.0) ** years
    pf = result["profit_factor"]
    pfv = float("inf") if pf == "inf" else float(pf)

    blockers = []
    if years < 10.0:
        blockers.append("sealed holdout is shorter than ten years")
    if float(result["cagr_pct"]) < TARGET_CAGR:
        blockers.append(f"CAGR below {TARGET_CAGR:.1f}% target")
    if float(result["max_drawdown_pct"]) > 20.0:
        blockers.append("max drawdown above 20% hard research limit")
    if pfv < 1.10:
        blockers.append("profit factor below 1.10")
    if int(result["trades"]) < 100:
        blockers.append("fewer than 100 trades in decade holdout")

    output = dict(result)
    output.update({
        "training_universe": list(DATA),
        "training_data_start": common_start,
        "training_cutoff": TRAINING_CUTOFF,
        "training_years": round(training_years, 4),
        "selected_candidate": selected.name,
        "candidate_parameters": artifact_dict(selected, selection_report)["candidate"],
        "selection_report": selection_report,
        "target_cagr_pct": TARGET_CAGR,
        "target_ending_balance_for_actual_holdout_years": round(required_end, 2),
        "ten_year_target_ending_balance_from_5000": round(STARTING_BALANCE * (1.10 ** 10), 2),
        "annual_returns": annual_returns(ledger, STARTING_BALANCE, int(result["start_time"]), int(result["end_time"])),
        "forward_test_ready": not blockers,
        "forward_test_blockers": blockers,
        "execution_scope": "research/demo-forward-test-only",
        "notes": (
            "Candidate family and selection use only pre-2016 VTI/ONEQ/IWM data and pre-2016 folds. "
            "The 2016+ common window is then evaluated once. VTI/ONEQ/IWM are long-history research "
            "proxies for eventual MES/MNQ/M2K demo execution; proxy results do not guarantee futures fills. "
            "Initial stop risk is capped at 1% of equity, only one position may be open, risk tapers after "
            "5% drawdown, and new risk stops at 20% drawdown."
        ),
    })

    ARTIFACT.write_text(json.dumps(artifact_dict(selected, selection_report), indent=2), encoding="utf-8")
    RESULT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    return 0 if not blockers else 3


if __name__ == "__main__":
    raise SystemExit(main())
