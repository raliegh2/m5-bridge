"""Available-data estimate for V13 V12 Final + V11 Intraday research branch.

This script intentionally does not claim to be a raw execution backtest. The repo
currently has:

* V12 Final optimized five-symbol maximum-history results.
* V12 Final optimized one-year window results.
* V11 intraday-only available-data one-year replay estimate.

It does not currently have a merged chronological ledger of V12 and V11 accepted
and rejected candidates. Therefore this runner reports two available-data
estimates:

1. Maximum-history additive estimate: V12 max-history + V11 one-year intraday.
2. Rough one-year comparison: V12 one-year window + V11 one-year intraday.

The second line is closer in duration but still not a true same-ledger replay.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

STARTING_BALANCE = 5_000.0


@dataclass(frozen=True)
class Scenario:
    name: str
    basis: str
    net_profit: float
    trades: int | None
    profit_factor: float | None = None
    max_drawdown_percent: float | None = None
    stress_drawdown_percent: float | None = None

    @property
    def ending_balance(self) -> float:
        return STARTING_BALANCE + self.net_profit

    @property
    def return_percent(self) -> float:
        return self.net_profit / STARTING_BALANCE * 100.0


def increase(base: Scenario, combined_profit: float) -> dict:
    delta = combined_profit - base.net_profit
    return {
        "base": base.name,
        "combined_net_profit": round(combined_profit, 2),
        "combined_ending_balance": round(STARTING_BALANCE + combined_profit, 2),
        "combined_return_percent": round(combined_profit / STARTING_BALANCE * 100.0, 4),
        "profit_increase_dollars": round(delta, 2),
        "profit_increase_percent_vs_base": round(delta / base.net_profit * 100.0, 4),
    }


def build_payload() -> dict:
    v12_max = Scenario(
        name="V12 Final optimized maximum-history",
        basis="PR #33 final report; five-symbol optimized maximum-history result",
        net_profit=3_201.58,
        trades=918,
        profit_factor=1.606,
        max_drawdown_percent=4.93,
        stress_drawdown_percent=5.25,
    )
    v12_one_year = Scenario(
        name="V12 Final optimized one-year window",
        basis="PR #33 final report; optimized 1y window",
        net_profit=289.65,
        trades=None,
        profit_factor=1.485,
        max_drawdown_percent=3.79,
        stress_drawdown_percent=4.50,
    )
    v11_intraday = Scenario(
        name="V11 intraday-only available-data estimate",
        basis="PR #35 intraday-only available-data estimate; approximately one-year synchronized replay",
        net_profit=1_046.89,
        trades=132,
        profit_factor=None,
        max_drawdown_percent=None,
        stress_drawdown_percent=None,
    )

    combined_max = v12_max.net_profit + v11_intraday.net_profit
    combined_one_year = v12_one_year.net_profit + v11_intraday.net_profit

    return {
        "status": "AVAILABLE_DATA_RESEARCH_ESTIMATE_NOT_RAW_EXECUTION_BACKTEST",
        "starting_balance": STARTING_BALANCE,
        "branch": "v13-v12-final-plus-v11-intraday",
        "methodology": (
            "Add V11 intraday-only available-data profit to V12 Final reported results. "
            "This is a capacity-unadjusted estimate because no merged chronological "
            "candidate ledger is available in the repo."
        ),
        "scenarios": {
            "v12_max_history": asdict(v12_max) | {
                "ending_balance": round(v12_max.ending_balance, 2),
                "return_percent": round(v12_max.return_percent, 4),
            },
            "v12_one_year": asdict(v12_one_year) | {
                "ending_balance": round(v12_one_year.ending_balance, 2),
                "return_percent": round(v12_one_year.return_percent, 4),
            },
            "v11_intraday_one_year_estimate": asdict(v11_intraday) | {
                "ending_balance": round(v11_intraday.ending_balance, 2),
                "return_percent": round(v11_intraday.return_percent, 4),
            },
        },
        "combined_estimates": {
            "max_history_additive_upper_bound": increase(v12_max, combined_max) | {
                "note": "Uses V12 maximum-history result plus V11 approximately one-year intraday estimate; useful only as an upper-bound/additive research estimate."
            },
            "rough_one_year_comparison": increase(v12_one_year, combined_one_year) | {
                "note": "Closer in duration, but still not a same-ledger chronological replay."
            },
        },
        "required_real_backtest": [
            "Export or regenerate V12 Final accepted and rejected candidate ledgers.",
            "Export or regenerate V11 intraday accepted and rejected candidate ledgers for the same timestamps.",
            "Merge all candidates by entry_time and replay through the V12 Final/V13 risk governor.",
            "Measure accepted/rejected trades, opportunity cost, symbol caps, GBP caps, max positions, PF, drawdown and stress drawdown.",
        ],
        "decision": (
            "Available data suggests adding V11 intraday can increase gross research profit, "
            "but the exact increase cannot be approved until a merged chronological replay proves "
            "V11 does not consume capacity from stronger V12 trades."
        ),
    }


def main() -> int:
    payload = build_payload()
    out = Path(__file__).resolve().parents[1] / "research" / "v13_v12_plus_v11_intraday_backtest_results.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
