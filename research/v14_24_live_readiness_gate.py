"""Fail-closed live-readiness audit for the V14.24 historical candidate.

This does not optimize the strategy. It prevents aggregate R ledgers or a
reviewed historical window from being mistaken for execution-parity evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research.v14_24_fx_gold_profit_path as model
from research.v14_24_stress_test import transform


REQUIRED_EXECUTION_COLUMNS = {
    "entry_price",
    "exit_price",
    "stop_price",
    "target_price",
    "spread_cost",
    "commission",
    "swap",
    "slippage",
    "maximum_adverse_excursion_r",
}
GOLD_GENERATOR = model.ROOT / "research/v14_24_gold_daily_generator.py"


def _missing_execution_columns(path: Path) -> list[str]:
    columns = set(pd.read_csv(path, nrows=1).columns)
    return sorted(REQUIRED_EXECUTION_COLUMNS - columns)


def build_readiness_snapshot() -> dict[str, Any]:
    trades = model.load_trades(
        model.DEFAULT_SWINGS,
        model.DEFAULT_HISTORICAL_GOLD,
        model.DEFAULT_LATEST_GOLD,
        0.02,
    )
    cutoff = pd.Timestamp("2026-07-03", tz="UTC")
    fresh = [trade for trade in trades if trade.entry > cutoff]
    recent_start, recent_end = model.SPLITS["recent"]
    recent_extra_003 = model.replay_events(
        transform(trades, extra_cost_r=0.03),
        recent_start,
        recent_end,
    )
    recent_extra_005 = model.replay_events(
        transform(trades, extra_cost_r=0.05),
        recent_start,
        recent_end,
    )
    combined = model.replay_events(
        transform(
            trades,
            extra_cost_r=0.03,
            winner_multiplier=0.80,
            loss_multiplier=1.20,
        ),
        *model.SPLITS["full"],
    )
    missing = {
        "swing": _missing_execution_columns(model.DEFAULT_SWINGS),
        "historical_gold": _missing_execution_columns(
            model.DEFAULT_HISTORICAL_GOLD
        ),
        "latest_gold": _missing_execution_columns(model.DEFAULT_LATEST_GOLD),
    }
    checks = {
        "execution_price_and_cost_fields_present": not any(missing.values()),
        "gold_strategy_generator_present": GOLD_GENERATOR.is_file(),
        "fresh_untouched_forward_trades_present": len(fresh) > 0,
        "intratrade_drawdown_available": all(
            "maximum_adverse_excursion_r" not in values
            for values in missing.values()
        ),
        "recent_extra_003r_pf_at_least_1_10": (
            float(recent_extra_003.get("profit_factor") or 0.0) >= 1.10
        ),
        "recent_extra_005r_profitable": (
            float(recent_extra_005.get("return_percent") or 0.0) > 0.0
            and float(recent_extra_005.get("profit_factor") or 0.0) >= 1.10
        ),
        "combined_stress_drawdown_within_9_5": (
            float(combined["maximum_closed_drawdown_percent"]) <= 9.50
        ),
    }
    return {
        "status": "READY" if all(checks.values()) else "BLOCKED",
        "candidate": "V14.24_FX_GOLD_HISTORICAL",
        "checks": checks,
        "missing_execution_columns": missing,
        "fresh_trade_count_after_2026_07_03": len(fresh),
        "sensitivity": {
            "recent_extra_0_03r": recent_extra_003,
            "recent_extra_0_05r": recent_extra_005,
            "full_combined_payoff_stress": combined,
        },
        "promotion_rule": (
            "Do not use V14.24 to authorize broker transmission until every "
            "check passes on an untouched forward window."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = build_readiness_snapshot()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
