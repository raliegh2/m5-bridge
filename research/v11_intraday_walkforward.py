"""Walk-forward validation helper for the V11 intraday research branch.

This script evaluates already-generated trade ledgers. It does not discover
signals and it does not place orders. The required input CSV columns are:

    entry_time, engine, setup, profit_dollars

Optional columns:

    risk_dollars, r_multiple, symbol

Example:
    python research/v11_intraday_walkforward.py \
        --trades research/v10_ledger.csv \
        --out research/v11_walkforward_report.md
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


@dataclass(frozen=True)
class TradeRecord:
    entry_time: datetime
    engine: str
    setup: str
    profit_dollars: float
    risk_dollars: float | None = None
    r_multiple: float | None = None
    symbol: str | None = None


@dataclass(frozen=True)
class WindowMetrics:
    label: str
    start: str
    end: str
    trades: int
    net_profit: float
    profit_factor: float
    win_rate: float
    average_trade: float
    max_drawdown: float
    pass_gate: bool


def _parse_time(value: str) -> datetime:
    cleaned = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_trades(path: Path) -> list[TradeRecord]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"entry_time", "engine", "setup", "profit_dollars"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")
        trades = []
        for row in reader:
            profit = float(row["profit_dollars"])
            risk = row.get("risk_dollars") or ""
            r_multiple = row.get("r_multiple") or ""
            trades.append(
                TradeRecord(
                    entry_time=_parse_time(row["entry_time"]),
                    engine=row["engine"].strip().upper(),
                    setup=row["setup"].strip().upper(),
                    profit_dollars=profit,
                    risk_dollars=float(risk) if risk else None,
                    r_multiple=float(r_multiple) if r_multiple else None,
                    symbol=(row.get("symbol") or "").strip().upper() or None,
                )
            )
    return sorted(trades, key=lambda trade: trade.entry_time)


def profit_factor(values: list[float]) -> float:
    wins = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses == 0:
        return math.inf if wins > 0 else 0.0
    return wins / losses


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return abs(drawdown)


def metric_window(label: str, trades: list[TradeRecord], min_pf: float, min_trades: int) -> WindowMetrics:
    if not trades:
        return WindowMetrics(label, "", "", 0, 0.0, 0.0, 0.0, 0.0, 0.0, False)
    values = [trade.profit_dollars for trade in trades]
    pf = profit_factor(values)
    wins = [value for value in values if value > 0]
    pass_gate = len(values) >= min_trades and pf >= min_pf and sum(values) > 0
    return WindowMetrics(
        label=label,
        start=trades[0].entry_time.date().isoformat(),
        end=trades[-1].entry_time.date().isoformat(),
        trades=len(values),
        net_profit=round(sum(values), 2),
        profit_factor=round(pf, 4) if math.isfinite(pf) else 999.0,
        win_rate=round(len(wins) / len(values), 4),
        average_trade=round(mean(values), 2),
        max_drawdown=round(max_drawdown(values), 2),
        pass_gate=pass_gate,
    )


def chunk_by_count(trades: list[TradeRecord], windows: int) -> list[list[TradeRecord]]:
    if windows <= 0:
        raise ValueError("windows must be positive")
    if not trades:
        return []
    size = max(1, math.ceil(len(trades) / windows))
    return [trades[index:index + size] for index in range(0, len(trades), size)]


def grouped_summary(trades: list[TradeRecord], min_pf: float, min_trades: int) -> dict[str, WindowMetrics]:
    grouped: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        key = f"{trade.engine}:{trade.setup}"
        grouped.setdefault(key, []).append(trade)
    return {
        key: metric_window(key, value, min_pf=min_pf, min_trades=min_trades)
        for key, value in sorted(grouped.items())
    }


def build_report(trades: list[TradeRecord], windows: int, min_pf: float, min_trades: int, min_pass_rate: float) -> dict:
    overall = metric_window("overall", trades, min_pf=min_pf, min_trades=min_trades)
    chunks = chunk_by_count(trades, windows)
    wf_metrics = [
        metric_window(f"wf_{index + 1}", chunk, min_pf=min_pf, min_trades=max(5, min_trades // 2))
        for index, chunk in enumerate(chunks)
    ]
    pass_rate = sum(metric.pass_gate for metric in wf_metrics) / len(wf_metrics) if wf_metrics else 0.0
    return {
        "overall": asdict(overall),
        "walk_forward": [asdict(metric) for metric in wf_metrics],
        "setup_summary": {key: asdict(value) for key, value in grouped_summary(trades, min_pf, min_trades).items()},
        "pass_rate": round(pass_rate, 4),
        "minimum_required_pass_rate": min_pass_rate,
        "portfolio_pass_gate": bool(overall.pass_gate and pass_rate >= min_pass_rate),
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# V11 Intraday Walk-Forward Report",
        "",
        "Status: **RESEARCH OUTPUT — not a live-trading approval**",
        "",
        "## Overall",
        "",
        "| Trades | Net profit | Profit factor | Win rate | Average trade | Max DD | Pass |",
        "|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    overall = report["overall"]
    lines.append(
        f"| {overall['trades']} | ${overall['net_profit']:.2f} | {overall['profit_factor']:.4f} | "
        f"{overall['win_rate']:.2%} | ${overall['average_trade']:.2f} | ${overall['max_drawdown']:.2f} | "
        f"{'YES' if overall['pass_gate'] else 'NO'} |"
    )
    lines.extend([
        "",
        "## Walk-forward windows",
        "",
        "| Window | Start | End | Trades | Net profit | PF | Pass |",
        "|---|---|---|---:|---:|---:|:---:|",
    ])
    for metric in report["walk_forward"]:
        lines.append(
            f"| {metric['label']} | {metric['start']} | {metric['end']} | {metric['trades']} | "
            f"${metric['net_profit']:.2f} | {metric['profit_factor']:.4f} | "
            f"{'YES' if metric['pass_gate'] else 'NO'} |"
        )
    lines.extend([
        "",
        f"Walk-forward pass rate: **{report['pass_rate']:.2%}**",
        f"Required pass rate: **{report['minimum_required_pass_rate']:.2%}**",
        f"Portfolio pass gate: **{'YES' if report['portfolio_pass_gate'] else 'NO'}**",
        "",
        "## Setup attribution",
        "",
        "| Engine/setup | Trades | Net profit | PF | Avg trade | Pass |",
        "|---|---:|---:|---:|---:|:---:|",
    ])
    for key, metric in report["setup_summary"].items():
        lines.append(
            f"| {key} | {metric['trades']} | ${metric['net_profit']:.2f} | "
            f"{metric['profit_factor']:.4f} | ${metric['average_trade']:.2f} | "
            f"{'YES' if metric['pass_gate'] else 'NO'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V11 walk-forward checks on a trade ledger")
    parser.add_argument("--trades", required=True, type=Path)
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--min-pf", type=float, default=1.40)
    parser.add_argument("--min-trades", type=int, default=30)
    parser.add_argument("--min-pass-rate", type=float, default=0.70)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    trades = load_trades(args.trades)
    report = build_report(
        trades,
        windows=args.windows,
        min_pf=args.min_pf,
        min_trades=args.min_trades,
        min_pass_rate=args.min_pass_rate,
    )
    markdown = render_markdown(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
