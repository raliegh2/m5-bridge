"""Event-correct research replay for the V14.24 FX + Gold profit path.

The candidate keeps only engines with positive cost-adjusted evidence across
the reviewed development, validation, and recent regimes. Profits are realized
at exit (not while iterating entries), concurrent open risk is capped, and the
existing parity drawdown governor is applied at each new entry.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import heapq
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research/v14_24_fx_gold_profit_path"
DATA = OUT / "data"
DEFAULT_SWINGS = DATA / "regenerated_swing_trades.csv"
DEFAULT_HISTORICAL_GOLD = DATA / "xauusd_daily_2015_2024_ledger.csv"
DEFAULT_LATEST_GOLD = DATA / "xauusd_daily_2025_2026_ledger.csv"

STARTING_BALANCE = 5_000.0
MAX_OPEN_RISK_PERCENT = 3.25
OBSERVATION_RISK_PERCENT = 0.025
ENGINE_RISK_PERCENT = {
    "GBPUSD_V10_PRECISION": 0.70,
    "GBPJPY_SWING_CORE": 0.65,
    "GOLD_DAILY_TREND": 0.50,
}
GOVERNOR = ((9.6, 0.0), (9.0, 0.50), (8.5, 0.82), (7.5, 0.98))
SPLITS = {
    "development": ("2016-07-03", "2021-01-01"),
    "validation": ("2021-01-01", "2023-01-01"),
    "recent": ("2023-01-01", "2026-08-01"),
    "full": ("2016-07-03", "2026-08-01"),
}


@dataclass(frozen=True)
class Trade:
    entry: pd.Timestamp
    exit: pd.Timestamp
    engine: str
    symbol: str
    net_r: float


def governed_risk(risk: float, drawdown_percent: float) -> float:
    for threshold, multiplier in GOVERNOR:
        if drawdown_percent >= threshold:
            return risk * multiplier if multiplier > 0 else min(
                risk, OBSERVATION_RISK_PERCENT
            )
    return risk


def load_trades(
    swing_path: Path,
    historical_gold_path: Path,
    latest_gold_path: Path,
    fx_cost_r: float,
) -> list[Trade]:
    swings = pd.read_csv(swing_path)
    swings["entry_time"] = pd.to_datetime(swings["entry_time"], utc=True)
    swings["exit_time"] = pd.to_datetime(swings["exit_time"], utc=True)
    swings = swings[swings["engine"].isin(
        ("GBPUSD_V10_PRECISION", "GBPJPY_SWING_CORE")
    )]
    trades = [
        Trade(
            row.entry_time, row.exit_time, str(row.engine), str(row.symbol),
            float(row.r_multiple) - fx_cost_r,
        )
        for row in swings.itertuples(index=False)
    ]

    historical = pd.read_csv(historical_gold_path)
    historical["entry_time"] = pd.to_datetime(historical["entry_time"], utc=True)
    historical["exit_time"] = pd.to_datetime(historical["exit_time"], utc=True)
    latest = pd.read_csv(latest_gold_path)
    latest["entry_time"] = pd.to_datetime(latest["entry_time"], utc=True)
    latest["exit_time"] = pd.to_datetime(latest["exit_time"], utc=True)
    for frame in (historical, latest):
        for row in frame.itertuples(index=False):
            trades.append(
                Trade(
                    pd.Timestamp(row.entry_time), pd.Timestamp(row.exit_time),
                    "GOLD_DAILY_TREND", "XAUUSD", float(row.net_r),
                )
            )
    return sorted(trades, key=lambda item: (item.entry, item.exit, item.engine))


def replay_events(
    trades: Iterable[Trade],
    start: str,
    end: str,
    *,
    apply_governor: bool = True,
) -> dict[str, float | int | dict[str, float]]:
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    selected = [item for item in trades if start_ts <= item.entry < end_ts]
    balance = peak = STARTING_BALANCE
    maximum_drawdown = 0.0
    gross_profit = gross_loss = 0.0
    opened = skipped = sequence = wins = losses = breakeven = 0
    current_losing_streak = maximum_losing_streak = 0
    by_engine: dict[str, float] = {}
    # exit, sequence, risk dollars, R, engine
    positions: list[tuple[pd.Timestamp, int, float, float, str]] = []

    def settle(until: pd.Timestamp) -> None:
        nonlocal balance, peak, maximum_drawdown, gross_profit, gross_loss
        nonlocal wins, losses, breakeven
        nonlocal current_losing_streak, maximum_losing_streak
        while positions and positions[0][0] <= until:
            _exit, _seq, risk_dollars, net_r, engine = heapq.heappop(positions)
            pnl = risk_dollars * net_r
            balance += pnl
            by_engine[engine] = by_engine.get(engine, 0.0) + pnl
            gross_profit += max(pnl, 0.0)
            gross_loss += max(-pnl, 0.0)
            if pnl > 0:
                wins += 1
                current_losing_streak = 0
            elif pnl < 0:
                losses += 1
                current_losing_streak += 1
                maximum_losing_streak = max(maximum_losing_streak, current_losing_streak)
            else:
                breakeven += 1
                current_losing_streak = 0
            peak = max(peak, balance)
            maximum_drawdown = max(
                maximum_drawdown,
                (peak - balance) / peak * 100.0 if peak else 0.0,
            )

    for trade in selected:
        settle(trade.entry)
        base_risk = ENGINE_RISK_PERCENT[trade.engine]
        current_drawdown = (peak - balance) / peak * 100.0 if peak else 0.0
        risk = governed_risk(base_risk, current_drawdown) if apply_governor else base_risk
        open_risk = sum(item[2] for item in positions) / balance * 100.0
        if open_risk + risk > MAX_OPEN_RISK_PERCENT + 1e-9:
            skipped += 1
            continue
        sequence += 1
        heapq.heappush(
            positions,
            (trade.exit, sequence, balance * risk / 100.0, trade.net_r, trade.engine),
        )
        opened += 1
    settle(pd.Timestamp.max.tz_localize("UTC"))
    years = max((end_ts - start_ts).days / 365.25, 1e-9)
    return {
        "trades": opened,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate_percent": round(wins / opened * 100.0, 4) if opened else 0.0,
        "win_loss_count_ratio": round(wins / losses, 4) if losses else None,
        "average_win_loss_payoff_ratio": round(
            (gross_profit / wins) / (gross_loss / losses), 4
        ) if wins and losses else None,
        "maximum_losing_streak": maximum_losing_streak,
        "skipped_open_risk": skipped,
        "starting_balance": STARTING_BALANCE,
        "ending_balance": round(balance, 2),
        "net_profit": round(balance - STARTING_BALANCE, 2),
        "return_percent": round((balance / STARTING_BALANCE - 1.0) * 100.0, 4),
        "cagr_percent": round(
            ((balance / STARTING_BALANCE) ** (1.0 / years) - 1.0) * 100.0, 4
        ),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "maximum_closed_drawdown_percent": round(maximum_drawdown, 4),
        "net_by_engine": {
            key: round(value, 2)
            for key, value in sorted(by_engine.items(), key=lambda item: -item[1])
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--swings", type=Path, default=DEFAULT_SWINGS)
    parser.add_argument("--historical-gold", type=Path, default=DEFAULT_HISTORICAL_GOLD)
    parser.add_argument("--latest-gold", type=Path, default=DEFAULT_LATEST_GOLD)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    scenarios = {}
    for cost_name, fx_cost_r in (("demo", 0.02), ("retail_stress", 0.03)):
        trades = load_trades(
            args.swings, args.historical_gold, args.latest_gold, fx_cost_r
        )
        scenarios[cost_name] = {
            name: replay_events(trades, start, end)
            for name, (start, end) in SPLITS.items()
        }
    payload = {
        "status": "HISTORICAL_CANDIDATE_FORWARD_TEST_REQUIRED",
        "symbols": ["GBPUSD", "GBPJPY", "XAUUSD"],
        "engine_risk_percent": ENGINE_RISK_PERCENT,
        "maximum_open_risk_percent": MAX_OPEN_RISK_PERCENT,
        "drawdown_governor": GOVERNOR,
        "scenarios": scenarios,
        "selection_warning": (
            "The recent period has been reviewed during prior research; it is not a fresh "
            "untouched holdout. Freeze this profile and use new demo-forward trades next."
        ),
        "is_profit_guarantee": False,
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    full = scenarios["demo"]["full"]
    recent = scenarios["demo"]["recent"]
    stress = scenarios["retail_stress"]["full"]
    lines = [
        "# V14.24 FX + Gold profit path",
        "",
        "Selected sleeves: GBPUSD V10, GBPJPY Swing Core, and Gold daily trend.",
        "Profits are realized chronologically at exit and concurrent open risk is capped.",
        "",
        "| Scenario | Trades | Ending balance | Return | CAGR | PF | Closed DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Demo-cost full | {full['trades']} | ${full['ending_balance']:,.2f} | {full['return_percent']:.2f}% | {full['cagr_percent']:.2f}% | {full['profit_factor']:.3f} | {full['maximum_closed_drawdown_percent']:.2f}% |",
        f"| Demo-cost 2023-2026 | {recent['trades']} | ${recent['ending_balance']:,.2f} | {recent['return_percent']:.2f}% | {recent['cagr_percent']:.2f}% | {recent['profit_factor']:.3f} | {recent['maximum_closed_drawdown_percent']:.2f}% |",
        f"| Retail-stress full | {stress['trades']} | ${stress['ending_balance']:,.2f} | {stress['return_percent']:.2f}% | {stress['cagr_percent']:.2f}% | {stress['profit_factor']:.3f} | {stress['maximum_closed_drawdown_percent']:.2f}% |",
        "",
        "Historical closed-trade drawdown is not the same as guaranteed live or intratrade drawdown.",
        "The profile must remain demo/shadow-only until genuinely new forward trades pass.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
