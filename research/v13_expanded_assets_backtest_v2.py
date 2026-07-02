"""Second-stage V13 research replay.

Tests AUDUSD and USDCAD independently on an early development segment and a
later out-of-sample segment. Only a commodity strategy that passes the frozen
validation gate is admitted to the synchronized portfolio. USDJPY is evaluated
under the same rule. This prevents a weak new pair from being included merely
to increase trade count.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import v13_expanded_assets_backtest as base

OUT = base.ROOT / "research" / "v13_output_v2"
OUT.mkdir(parents=True, exist_ok=True)
base.BASKETS["USDCAD"] = {"COMMODITY_BLOCK"}


def passes(report: dict) -> bool:
    validation = report["validation"]
    return (
        int(validation["trades"]) >= 60
        and float(validation["net_r"]) > 0.0
        and float(validation["profit_factor"]) >= 1.10
    )


def main() -> None:
    research_symbols = ("AUDUSD", "USDCAD", "USDJPY")
    core_symbols = ("GBPUSD", "EURUSD", "GBPJPY")
    prepared = {symbol: base.prepare(symbol) for symbol in core_symbols + research_symbols}

    selected_params = {}
    validation = {}
    for symbol in research_symbols:
        h1, h4, _ = prepared[symbol]
        params, report = base.choose_params(symbol, h1, h4)
        selected_params[symbol] = params
        validation[symbol] = report
        report["passed"] = passes(report)

    passing_commodities = [
        symbol for symbol in ("AUDUSD", "USDCAD") if validation[symbol]["passed"]
    ]
    selected_commodity = max(
        passing_commodities,
        key=lambda symbol: (
            validation[symbol]["validation"]["net_r"],
            validation[symbol]["validation"]["profit_factor"],
        ),
        default=None,
    )
    selected_safe_haven = "USDJPY" if validation["USDJPY"]["passed"] else None
    admitted = [symbol for symbol in (selected_commodity, selected_safe_haven) if symbol]

    candidates = [base.gbpusd_precision_candidates(prepared["GBPUSD"][1])]
    existing_params = {
        "EURUSD": base.StrategyParams(55, 20.0, 1.25, 3.0, 2.5, 24, 0.20),
        "GBPJPY": base.StrategyParams(55, 20.0, 1.25, 3.0, 2.5, 24, 0.20),
    }
    for symbol, params in existing_params.items():
        h1, h4, _ = prepared[symbol]
        candidates.append(base.generic_candidates(symbol, h1, h4, params))
    for symbol in admitted:
        h1, h4, _ = prepared[symbol]
        candidates.append(base.generic_candidates(symbol, h1, h4, selected_params[symbol]))

    all_candidates = pd.concat(candidates, ignore_index=True).sort_values("entry_time")
    all_candidates.to_csv(OUT / "all_candidates.csv", index=False)
    active_symbols = core_symbols + tuple(admitted)
    common_end = min(prepared[symbol][1]["time"].max() for symbol in active_symbols)
    common_start = max(prepared[symbol][1]["time"].min() for symbol in active_symbols)

    results = {
        "data_source": base.DATA_URL,
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "validation_gate": {
            "minimum_validation_trades": 60,
            "minimum_validation_profit_factor": 1.10,
            "minimum_validation_net_r": 0.0,
        },
        "selected_commodity": selected_commodity,
        "selected_safe_haven": selected_safe_haven,
        "admitted_new_symbols": admitted,
        "selected_parameters": {
            symbol: asdict(selected_params[symbol]) for symbol in research_symbols
        },
        "validation": validation,
        "portfolio_config": asdict(base.PortfolioConfig()),
        "windows": {},
    }
    for years in (10, 5, 3, 2):
        start = max(common_start, common_end - pd.DateOffset(years=years))
        summary, accepted, rejected = base.replay(
            all_candidates, start, common_end, base.PortfolioConfig()
        )
        results["windows"][str(years)] = summary
        accepted.to_csv(OUT / f"accepted_{years}y.csv", index=False)
        rejected.to_csv(OUT / f"rejected_{years}y.csv", index=False)
    (OUT / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
