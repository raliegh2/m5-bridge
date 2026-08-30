"""Deterministic and Monte Carlo stresses for the frozen V14.24 candidate."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd

import research.v14_24_fx_gold_profit_path as model

OUT = model.ROOT / "research/v14_24_stress_test"
FULL_START, FULL_END = model.SPLITS["full"]


def maximum_losing_streak(trades: list[model.Trade]) -> int:
    ordered = sorted(trades, key=lambda item: (item.exit, item.entry))
    longest = current = 0
    for trade in ordered:
        if trade.net_r < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def transform(
    trades: list[model.Trade],
    *,
    extra_cost_r: float = 0.0,
    winner_multiplier: float = 1.0,
    loss_multiplier: float = 1.0,
) -> list[model.Trade]:
    output = []
    for trade in trades:
        adjusted = (
            trade.net_r * winner_multiplier
            if trade.net_r > 0
            else trade.net_r * loss_multiplier
        ) - extra_cost_r
        output.append(replace(trade, net_r=adjusted))
    return output


def drop_best_winners(trades: list[model.Trade], fraction: float) -> list[model.Trade]:
    winners = sorted(
        (trade for trade in trades if trade.net_r > 0),
        key=lambda item: item.net_r,
        reverse=True,
    )
    remove = {id(item) for item in winners[: int(len(winners) * fraction)]}
    return [item for item in trades if id(item) not in remove]


def monte_carlo(
    trades: list[model.Trade],
    simulations: int = 5_000,
    seed: int = 1424,
) -> dict[str, float | int]:
    rng = np.random.default_rng(seed)
    full_start = pd.Timestamp(FULL_START, tz="UTC")
    full_end = pd.Timestamp(FULL_END, tz="UTC")
    eligible = [trade for trade in trades if full_start <= trade.entry < full_end]
    by_engine = {
        engine: np.asarray([item.net_r for item in eligible if item.engine == engine])
        for engine in model.ENGINE_RISK_PERCENT
    }
    endings = np.empty(simulations)
    drawdowns = np.empty(simulations)
    for simulation in range(simulations):
        outcomes: list[tuple[str, float]] = []
        for engine, values in by_engine.items():
            sampled = rng.choice(values, size=len(values), replace=True)
            outcomes.extend((engine, float(value)) for value in sampled)
        rng.shuffle(outcomes)
        balance = peak = model.STARTING_BALANCE
        maximum_drawdown = 0.0
        for engine, net_r in outcomes:
            drawdown = (peak - balance) / peak * 100.0 if peak else 0.0
            risk = model.governed_risk(model.ENGINE_RISK_PERCENT[engine], drawdown)
            balance *= 1.0 + risk / 100.0 * net_r
            peak = max(peak, balance)
            maximum_drawdown = max(
                maximum_drawdown,
                (peak - balance) / peak * 100.0 if peak else 0.0,
            )
        endings[simulation] = balance
        drawdowns[simulation] = maximum_drawdown
    return {
        "simulations": simulations,
        "ending_balance_p05": round(float(np.percentile(endings, 5)), 2),
        "ending_balance_median": round(float(np.median(endings)), 2),
        "ending_balance_p95": round(float(np.percentile(endings, 95)), 2),
        "drawdown_p50_percent": round(float(np.percentile(drawdowns, 50)), 4),
        "drawdown_p95_percent": round(float(np.percentile(drawdowns, 95)), 4),
        "drawdown_p99_percent": round(float(np.percentile(drawdowns, 99)), 4),
        "probability_losing_percent": round(float(np.mean(endings < model.STARTING_BALANCE) * 100), 4),
        "probability_drawdown_over_10_percent": round(float(np.mean(drawdowns > 10.0) * 100), 4),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = model.load_trades(
        model.DEFAULT_SWINGS,
        model.DEFAULT_HISTORICAL_GOLD,
        model.DEFAULT_LATEST_GOLD,
        0.02,
    )
    in_period = [
        item for item in trades
        if pd.Timestamp(FULL_START, tz="UTC") <= item.entry < pd.Timestamp(FULL_END, tz="UTC")
    ]
    scenarios = {
        "base_demo_cost": in_period,
        "extra_0_03R_each_trade": transform(in_period, extra_cost_r=0.03),
        "winners_haircut_20_percent": transform(in_period, winner_multiplier=0.80),
        "losses_inflated_20_percent": transform(in_period, loss_multiplier=1.20),
        "combined_payoff_stress": transform(
            in_period, extra_cost_r=0.03, winner_multiplier=0.80, loss_multiplier=1.20
        ),
        "miss_best_10_percent_of_winners": drop_best_winners(in_period, 0.10),
    }
    deterministic = {
        name: model.replay_events(sample, FULL_START, FULL_END)
        for name, sample in scenarios.items()
    }
    payload = {
        "status": "COMPLETED",
        "historical_trade_counts": {
            "trades_before_open_risk_admission": len(in_period),
            "wins": sum(item.net_r > 0 for item in in_period),
            "losses": sum(item.net_r < 0 for item in in_period),
            "win_rate_percent": round(sum(item.net_r > 0 for item in in_period) / len(in_period) * 100.0, 4),
            "win_loss_count_ratio": round(
                sum(item.net_r > 0 for item in in_period) / sum(item.net_r < 0 for item in in_period), 4
            ),
            "maximum_raw_losing_streak": maximum_losing_streak(in_period),
        },
        "deterministic": deterministic,
        "monte_carlo_trade_bootstrap": monte_carlo(in_period),
        "limitations": [
            "Monte Carlo resamples closed R outcomes and does not recreate gaps or tick-level path risk.",
            "Historical drawdown is closed-trade drawdown; live intratrade drawdown may be larger.",
            "The reviewed recent period is not a fresh untouched holdout.",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# V14.24 stress test",
        "",
        f"Raw outcomes: {payload['historical_trade_counts']['wins']} wins / "
        f"{payload['historical_trade_counts']['losses']} losses, "
        f"{payload['historical_trade_counts']['win_rate_percent']:.2f}% win rate.",
        f"Maximum raw losing streak: {payload['historical_trade_counts']['maximum_raw_losing_streak']} trades.",
        "",
        "| Stress | Ending balance | Return | PF | Closed DD |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, result in deterministic.items():
        lines.append(
            f"| {name.replace('_', ' ')} | ${result['ending_balance']:,.2f} | "
            f"{result['return_percent']:.2f}% | {result['profit_factor']:.3f} | "
            f"{result['maximum_closed_drawdown_percent']:.2f}% |"
        )
    mc = payload["monte_carlo_trade_bootstrap"]
    lines.extend([
        "",
        f"Monte Carlo ({mc['simulations']:,} stratified trade bootstraps): median ending "
        f"${mc['ending_balance_median']:,.2f}; 5th percentile ${mc['ending_balance_p05']:,.2f}; "
        f"95th-percentile drawdown {mc['drawdown_p95_percent']:.2f}%.",
        "",
        "These are model stresses, not guarantees or tick-level futures tests.",
    ])
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
