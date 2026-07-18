"""V14.12 historical retail-cost and live-promotion replay.

Replays the repository's combined V12 + ICT closed-trade ledger under:

* the V14.5.2 static cost-robust allocation; and
* the V14.12 live policy, where setup and symbol risk tiers use only closed
  prior trades after fixed retail-cost reserves.

The replay processes exits before later entries, applies the 3.25% combined
open-risk limit and the 7.5/8.5/9.0/9.6 drawdown governor, and starts each test
window with empty live evidence. This is an R-multiple historical test, not a
promise of live profitability or a tick-level reconstruction.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mt5_ai_bridge.v14_3_research_parity_execution import (
    PARITY_DRAWDOWN_GOVERNOR,
    PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
)
from mt5_ai_bridge.v14_5_2_profit_filter_profile import v14_5_2_risk_percent
from mt5_ai_bridge.v14_12_net_positive_guard import (
    NetPositiveGuardConfig,
    apply_net_positive_tier,
    net_positive_tier,
)
from research import v14_5_cost_robust_backtest as source

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_12_live_net_positive_output"
STARTING_BALANCE = 5_000.0
OBSERVATION_RISK = 0.025
COSTS_R = {"V12": 0.03, "ICT": 0.13}
MODELS = ("v14_5_2_static", "v14_12_net_positive")


@dataclass
class ActiveTrade:
    trade_id: int
    engine_group: str
    engine: str
    symbol: str
    setup: str
    entry: datetime
    exit: datetime
    raw_r: float
    cost_r: float
    net_r: float
    static_risk_percent: float
    tier: str
    risk_percent: float
    risk_dollars: float


def static_risk(trade: dict[str, Any]) -> float:
    group = str(trade["engine_group"]).upper()
    if group == "ICT":
        return OBSERVATION_RISK
    return float(
        v14_5_2_risk_percent(
            str(trade["engine"]),
            "V12",
            trade["entry"],
        )
    )


def statistics(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "trades": 0,
            "net_profit": 0.0,
            "profit_factor": None,
            "win_rate": None,
        }
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = -sum(value for value in values if value < 0)
    return {
        "trades": len(values),
        "net_profit": round(sum(values), 2),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else 99.0,
        "win_rate": round(sum(value > 0 for value in values) / len(values), 6),
    }


def replay(
    trades: list[dict[str, Any]],
    model: str,
    start: datetime,
    end: datetime,
    config: NetPositiveGuardConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    balance = STARTING_BALANCE
    peak = balance
    max_closed_dd = 0.0
    max_stress_dd = 0.0
    active: list[ActiveTrade] = []
    closed: list[dict[str, Any]] = []
    setup_history: dict[str, list[float]] = defaultdict(list)
    symbol_history: dict[str, list[float]] = defaultdict(list)
    skipped_open_risk = 0
    skipped_drawdown = 0
    tier_counts: dict[str, int] = defaultdict(int)
    trade_id = 0

    def close_due(now: datetime) -> None:
        nonlocal balance, peak, max_closed_dd, active
        due = sorted(
            [item for item in active if item.exit <= now],
            key=lambda item: (item.exit, item.trade_id),
        )
        if not due:
            return
        due_ids = {item.trade_id for item in due}
        active = [item for item in active if item.trade_id not in due_ids]
        for item in due:
            pnl = item.risk_dollars * item.net_r
            balance += pnl
            peak = max(peak, balance)
            closed_dd = (peak - balance) / peak * 100.0 if peak > 0 else 0.0
            max_closed_dd = max(max_closed_dd, closed_dd)
            setup_history[f"{item.symbol}/{item.setup}"].append(item.net_r)
            symbol_history[item.symbol].append(item.net_r)
            closed.append(
                {
                    **asdict(item),
                    "pnl": round(pnl, 8),
                    "balance_after": round(balance, 8),
                    "closed_drawdown_percent": round(closed_dd, 8),
                    "modeled_cost_dollars": round(item.risk_dollars * item.cost_r, 8),
                }
            )

    selected = [
        trade
        for trade in trades
        if start <= trade["entry"] <= end
    ]
    selected.sort(key=lambda item: (item["entry"], item["exit"], item["engine"]))

    for trade in selected:
        close_due(trade["entry"])
        drawdown = (peak - balance) / peak * 100.0 if peak > 0 else 0.0
        base_risk = static_risk(trade)
        setup_key = f"{str(trade['symbol']).upper()}/{trade['setup']}"
        symbol = str(trade["symbol"]).upper()

        if model == "v14_12_net_positive":
            tier = net_positive_tier(
                setup_history[setup_key],
                symbol_history[symbol],
                config,
            )
            requested = apply_net_positive_tier(base_risk, tier, config)
        elif model == "v14_5_2_static":
            tier = "STATIC"
            requested = base_risk
        else:
            raise ValueError(model)

        governed = PARITY_DRAWDOWN_GOVERNOR.apply(requested, drawdown)
        if governed <= 0.0:
            skipped_drawdown += 1
            continue
        open_risk = sum(item.risk_percent for item in active)
        if open_risk + governed > PARITY_MAX_COMBINED_OPEN_RISK_PERCENT + 1e-12:
            skipped_open_risk += 1
            continue

        group = str(trade["engine_group"]).upper()
        cost_r = float(COSTS_R[group])
        net_r = float(trade["r"]) - cost_r
        risk_dollars = balance * governed / 100.0
        active.append(
            ActiveTrade(
                trade_id=trade_id,
                engine_group=group,
                engine=str(trade["engine"]),
                symbol=symbol,
                setup=str(trade["setup"]),
                entry=trade["entry"],
                exit=trade["exit"],
                raw_r=float(trade["r"]),
                cost_r=cost_r,
                net_r=net_r,
                static_risk_percent=base_risk,
                tier=tier,
                risk_percent=governed,
                risk_dollars=risk_dollars,
            )
        )
        tier_counts[tier] += 1
        trade_id += 1
        stressed_equity = balance - sum(item.risk_dollars for item in active)
        stress_dd = (peak - stressed_equity) / peak * 100.0 if peak > 0 else 0.0
        max_stress_dd = max(max_stress_dd, stress_dd)

    close_due(datetime.max.replace(tzinfo=timezone.utc))
    pnl = [float(item["pnl"]) for item in closed]
    by_symbol = {
        symbol: statistics([float(item["pnl"]) for item in closed if item["symbol"] == symbol])
        for symbol in sorted({item["symbol"] for item in closed})
    }
    by_engine = {
        engine: statistics([float(item["pnl"]) for item in closed if item["engine"] == engine])
        for engine in sorted({item["engine"] for item in closed})
    }
    summary = {
        "model": model,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "starting_balance": STARTING_BALANCE,
        "ending_balance": round(balance, 2),
        **statistics(pnl),
        "return_percent": round((balance / STARTING_BALANCE - 1.0) * 100.0, 4),
        "max_closed_drawdown_percent": round(max_closed_dd, 6),
        "stress_drawdown_percent": round(max_stress_dd, 6),
        "skipped_open_risk": skipped_open_risk,
        "skipped_drawdown": skipped_drawdown,
        "tier_counts": dict(sorted(tier_counts.items())),
        "modeled_cost_dollars": round(sum(float(item["modeled_cost_dollars"]) for item in closed), 2),
        "by_symbol": by_symbol,
        "by_engine": by_engine,
    }
    return summary, closed


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.12 Net-Positive After-Cost Replay",
        "",
        "**Starting balance:** $5,000.00",
        "**Modeled retail costs:** 0.03R per V12 trade and 0.13R per ICT trade",
        "**Risk controls:** V14.5.2 static allocation, 3.25% combined open-risk cap, 7.5/8.5/9.0/9.6 drawdown governor",
        "",
        "## Exact ten-year comparison",
        "",
        "| Model | Net profit | Ending balance | PF | Max closed DD | Stress DD | Trades |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in MODELS:
        result = payload["exact_ten_year"][key]
        lines.append(
            f"| {key} | ${result['net_profit']:,.2f} | ${result['ending_balance']:,.2f} | "
            f"{float(result['profit_factor'] or 0.0):.4f} | "
            f"{result['max_closed_drawdown_percent']:.4f}% | "
            f"{result['stress_drawdown_percent']:.4f}% | {result['trades']} |"
        )
    adaptive = payload["exact_ten_year"]["v14_12_net_positive"]
    lines += [
        "",
        "## V14.12 exact-ten-year symbol attribution",
        "",
        "| Symbol | Net profit | PF | Trades |",
        "|---|---:|---:|---:|",
    ]
    for symbol, result in adaptive["by_symbol"].items():
        lines.append(
            f"| {symbol} | ${result['net_profit']:,.2f} | "
            f"{float(result['profit_factor'] or 0.0):.4f} | {result['trades']} |"
        )
    lines += [
        "",
        "## Live-policy interpretation",
        "",
        "- Historical V14.5.2 evidence defines the maximum permitted allocation; V14.12 never increases it.",
        "- Setup and symbol evidence begins empty at the start of each replay window.",
        "- Full risk is earned only from previously closed, after-cost results.",
        "- ICT remains at the 0.025% observation tier because its short-stop stream did not reliably clear retail costs.",
        "- The live executor additionally rejects entries whose current spread plus commission/slippage/swap reserve consumes too much of the stop or target.",
        "",
        "## Limitations",
        "",
        "This is an R-multiple replay with fixed retail-cost reserves, not a tick-level MT5 simulation. Live spreads, slippage, gaps, swaps, rejected orders and market-regime changes can produce materially different results. Historical profitability does not guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = source.load_trades()
    if not trades:
        raise RuntimeError("Combined V12/ICT ledger is empty")
    first = min(item["entry"] for item in trades)
    last = max(item["exit"] for item in trades)
    exact_start = last - timedelta(days=3652)
    config = NetPositiveGuardConfig()

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "live_entrypoint_changed": True,
        "source": str(source.COMBINED.relative_to(ROOT)),
        "costs_r": COSTS_R,
        "net_positive_config": asdict(config),
        "full_history": {},
        "exact_ten_year": {},
    }
    closed_outputs: dict[str, list[dict[str, Any]]] = {}
    for window_name, start in (("full_history", first), ("exact_ten_year", exact_start)):
        for model in MODELS:
            summary, closed = replay(trades, model, start, last, config)
            payload[window_name][model] = summary
            closed_outputs[f"{window_name}_{model}"] = closed

    exact = payload["exact_ten_year"]["v14_12_net_positive"]
    payload["historically_profitable_after_retail_costs"] = bool(
        float(exact["net_profit"]) > 0.0
        and float(exact["profit_factor"] or 0.0) > 1.0
    )
    payload["within_drawdown_limits"] = bool(
        float(exact["max_closed_drawdown_percent"]) <= 9.60
        and float(exact["stress_drawdown_percent"]) <= 10.00
    )
    payload["promotion_claim"] = (
        "Historical after-cost validation only; demo forward evidence is still required."
    )

    (OUT / "v14_12_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    for name, closed in closed_outputs.items():
        (OUT / f"{name}_closed_trades.json").write_text(
            json.dumps(closed, indent=2, default=str), encoding="utf-8"
        )
    write_report(payload)
    print(json.dumps({
        "exact_ten_year": payload["exact_ten_year"],
        "historically_profitable_after_retail_costs": payload["historically_profitable_after_retail_costs"],
        "within_drawdown_limits": payload["within_drawdown_limits"],
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
