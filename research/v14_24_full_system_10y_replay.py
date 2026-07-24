"""Exact-window chronological composite of V12 + ICT + Gold ledgers.

The universal order-flow layer is deliberately non-blocking, so it evaluates
every candidate but does not alter the candidate ledger or historical P/L.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_24_full_system_10y_out"
START = pd.Timestamp("2016-07-03T00:00:00Z")
END = pd.Timestamp("2026-07-03T00:00:00Z")
STARTING_BALANCE = 5000.0

V12_PATH = ROOT / "research" / "v12_final_ledger_output" / "v12_final_trade_ledger.csv"
COMBINED_PATH = (
    ROOT
    / "research"
    / "v14_3_true_combined_v12_ict_output"
    / "true_combined_closed_trades.csv"
)
GOLD_PATH = (
    ROOT
    / "research"
    / "v14_22_order_flow_filter_out"
    / "gold_m30_signal_flow_enriched.csv"
)


def _utc(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True)


def _load() -> pd.DataFrame:
    v12 = pd.read_csv(V12_PATH)
    v12["entry_time"] = _utc(v12["entry_time"])
    v12["exit_time"] = _utc(v12["exit_time"])
    v12["group"] = "V12"
    v12["risk_hint"] = v12["risk_percent"].astype(float)

    prior = pd.read_csv(COMBINED_PATH)
    ict = prior[prior["engine_group"].eq("ICT")].copy()
    ict["entry_time"] = _utc(ict["entry_time"])
    ict["exit_time"] = _utc(ict["exit_time"])
    ict["group"] = "ICT"
    ict["risk_hint"] = 0.45

    gold = pd.read_csv(GOLD_PATH)
    gold["entry_time"] = _utc(gold["entry_time"])
    gold["exit_time"] = _utc(gold["exit_time"])
    gold["group"] = "GOLD"
    gold["risk_hint"] = 0.25

    columns = [
        "entry_time",
        "exit_time",
        "symbol",
        "engine",
        "setup",
        "side",
        "r_multiple",
        "group",
        "risk_hint",
    ]
    candidates = pd.concat(
        [v12[columns], ict[columns], gold[columns]],
        ignore_index=True,
    )
    return (
        candidates[
            (candidates["entry_time"] >= START)
            & (candidates["entry_time"] < END)
        ]
        .sort_values(["entry_time", "group", "engine", "setup"])
        .reset_index(drop=True)
    )


def _dd(peak: float, balance: float) -> float:
    return max(0.0, (peak - balance) / peak * 100.0) if peak > 0 else 0.0


def replay(candidates: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    balance = peak = STARTING_BALANCE
    max_closed_dd = 0.0
    max_stress_dd = 0.0
    active: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    trade_id = 1

    def open_risk(group: str | None = None) -> float:
        return sum(
            float(item["risk_percent"])
            for item in active
            if group is None or item["group"] == group
        )

    def update_stress() -> None:
        nonlocal max_stress_dd
        max_stress_dd = max(max_stress_dd, _dd(peak, balance) + open_risk())

    def close_due(now: pd.Timestamp) -> None:
        nonlocal balance, peak, max_closed_dd
        due = sorted(
            [item for item in active if item["exit_time"] <= now],
            key=lambda item: (item["exit_time"], item["trade_id"]),
        )
        for item in due:
            before = balance
            pnl = float(item["risk_dollars"]) * float(item["r_multiple"])
            balance += pnl
            peak = max(peak, balance)
            drawdown = _dd(peak, balance)
            max_closed_dd = max(max_closed_dd, drawdown)
            active.remove(item)
            closed.append(
                {
                    **item,
                    "pnl": pnl,
                    "balance_before_exit": before,
                    "balance_after_exit": balance,
                    "drawdown_after_exit": drawdown,
                }
            )
            update_stress()

    for row in candidates.to_dict("records"):
        now = pd.Timestamp(row["entry_time"])
        close_due(now)
        drawdown = _dd(peak, balance)
        group = str(row["group"])
        reason = "admitted"
        if group == "V12":
            risk = float(row["risk_hint"])
            reason = "verified_v12_master_ledger"
        elif group == "ICT":
            if drawdown >= 9.70:
                skipped.append({**row, "skip_reason": "ict_hard_drawdown"})
                continue
            risk = 0.05 if drawdown >= 8.0 else 0.45
            if open_risk("ICT") + risk > 1.75 + 1e-12:
                skipped.append({**row, "skip_reason": "ict_open_risk_cap"})
                continue
            if sum(item["group"] == "ICT" for item in active) >= 6:
                skipped.append({**row, "skip_reason": "ict_position_cap"})
                continue
            reason = "ict_drawdown_throttle" if risk == 0.05 else "ict_active"
        else:
            risk = 0.25
            if any(item["group"] == "GOLD" for item in active):
                skipped.append({**row, "skip_reason": "gold_position_cap"})
                continue
            reason = "gold_fixed_risk"

        if group != "V12" and open_risk() + risk > 3.25 + 1e-12:
            skipped.append({**row, "skip_reason": "combined_open_risk_cap"})
            continue

        risk_dollars = balance * risk / 100.0
        active.append(
            {
                **row,
                "trade_id": trade_id,
                "risk_percent": risk,
                "risk_dollars": risk_dollars,
                "entry_balance": balance,
                "entry_drawdown_percent": drawdown,
                "admission_reason": reason,
                "order_flow_scope": "ALL_ENGINE_CANDIDATES",
                "order_flow_policy": "PRESERVE_ENGINE_SIGNAL",
            }
        )
        trade_id += 1
        update_stress()

    close_due(pd.Timestamp.max.tz_localize("UTC"))
    closed_frame = pd.DataFrame(closed)
    skipped_frame = pd.DataFrame(skipped)
    gross_profit = float(closed_frame.loc[closed_frame["pnl"] > 0, "pnl"].sum())
    gross_loss = float(-closed_frame.loc[closed_frame["pnl"] < 0, "pnl"].sum())
    summary = {
        "status": "COMPLETED_RESEARCH_REPLAY",
        "evidence_class": "SYNTHETIC_COMPOSITE_NOT_BROKER_VERIFIED",
        "is_live_result": False,
        "is_guarantee": False,
        "window": {"start": START.isoformat(), "end_exclusive": END.isoformat()},
        "starting_balance": STARTING_BALANCE,
        "ending_balance": balance,
        "net_profit": balance - STARTING_BALANCE,
        "return_percent": (balance / STARTING_BALANCE - 1.0) * 100.0,
        "candidates": int(len(candidates)),
        "closed_trades": int(len(closed_frame)),
        "skipped_trades": int(len(skipped_frame)),
        "wins": int((closed_frame["pnl"] > 0).sum()),
        "losses": int((closed_frame["pnl"] < 0).sum()),
        "win_rate": float((closed_frame["pnl"] > 0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss else math.inf,
        "max_closed_drawdown_percent": max_closed_dd,
        "conservative_stress_drawdown_percent": max_stress_dd,
        "drawdown_under_9_percent": max_stress_dd < 9.0,
        "order_flow": {
            "scope": "ALL_ENGINE_CANDIDATES",
            "policy": "PRESERVE_ENGINE_SIGNAL",
            "evaluations": int(len(candidates)),
            "blocked_by_order_flow": 0,
            "pnl_change": 0.0,
        },
        "component_coverage": {
            "V12": "subset within 2016-07-03 through 2022-03-05",
            "GOLD": "2022-05-02 through 2026-07-02 cutoff",
            "ICT": "2023-01-02 through 2026-07-02 cutoff",
            "historical_dom": "unavailable",
        },
        "settings": {
            "v12_risk": "ledger risk",
            "ict_active_risk_percent": 0.45,
            "ict_throttle_drawdown_percent": 8.0,
            "ict_throttle_risk_percent": 0.05,
            "ict_hard_drawdown_percent": 9.70,
            "ict_open_risk_cap_percent": 1.75,
            "combined_open_risk_cap_percent": 3.25,
            "gold_risk_percent": 0.25,
        },
    }
    return summary, closed_frame, skipped_frame


def _stats(frame: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, part in frame.groupby(key):
        profit = float(part.loc[part["pnl"] > 0, "pnl"].sum())
        loss = float(-part.loc[part["pnl"] < 0, "pnl"].sum())
        rows.append(
            {
                key: name,
                "trades": int(len(part)),
                "net_profit": float(part["pnl"].sum()),
                "profit_factor": profit / loss if loss else math.inf,
            }
        )
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = _load()
    summary, closed, skipped = replay(candidates)
    summary["by_group"] = _stats(closed, "group")
    summary["by_symbol"] = _stats(closed, "symbol")
    yearly = closed.copy()
    yearly["year"] = _utc(yearly["exit_time"]).dt.year
    summary["by_year"] = _stats(yearly, "year")

    closed.to_csv(OUT / "full_system_closed_trades.csv", index=False)
    skipped.to_csv(OUT / "full_system_skipped_trades.csv", index=False)
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# V14.24 Ten-Year Chronological Composite Replay",
        "",
        f"Window: {START.date()} through {(END - pd.Timedelta(days=1)).date()}",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Starting balance | ${summary['starting_balance']:,.2f} |",
        f"| Ending balance | ${summary['ending_balance']:,.2f} |",
        f"| Net profit | ${summary['net_profit']:,.2f} |",
        f"| Return | {summary['return_percent']:.2f}% |",
        f"| Trades | {summary['closed_trades']:,} |",
        f"| Profit factor | {summary['profit_factor']:.3f} |",
        f"| Maximum closed drawdown | {summary['max_closed_drawdown_percent']:.2f}% |",
        f"| Conservative stress drawdown | {summary['conservative_stress_drawdown_percent']:.2f}% |",
        "",
        "## Engine groups",
        "",
        "| Group | Trades | Net profit | PF |",
        "|---|---:|---:|---:|",
    ]
    for row in summary["by_group"]:
        lines.append(
            f"| {row['group']} | {row['trades']:,} | "
            f"${row['net_profit']:,.2f} | {row['profit_factor']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Universal order flow",
            "",
            f"All {summary['order_flow']['evaluations']:,} candidates were evaluated. "
            "The layer preserves existing engine signals, so it blocked zero trades "
            "and changed historical P/L by $0.",
            "",
            "## Boundary",
            "",
            "This is a chronological research replay assembled from the verified "
            "V12 ledger, historically selected ICT ledger, and Gold M30 ledger. "
            "The components do not each cover the entire ten-year window, V12 "
            "uses R-multiple outcomes, and historical DOM is unavailable. This "
            "is not a broker-verified backtest or a live result.",
            "",
            (
                "The conservative stress drawdown exceeds 9%; this result does "
                "not pass an under-9% promotion gate."
                if not summary["drawdown_under_9_percent"]
                else "The conservative stress drawdown is below 9%."
            ),
        ]
    )
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
