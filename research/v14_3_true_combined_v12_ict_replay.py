"""True event-based V12 + V14.3 ICT combined replay.

This runner consumes a generated V12 ledger plus the selected V14.3 under-10 ICT
candidate trade file and recomputes the combined portfolio chronologically.

Required first step:
    python research/v12_final_ledger_export.py

Then run, for example:
    python research/v14_3_true_combined_v12_ict_replay.py \
        --v12-ledger research/v12_final_ledger_output/v12_final_trade_ledger.csv \
        --ict-trades research/v14_3_under10_target_out/selected_under10_target_trades.csv

V12 trades are treated as the master protected engine and are always admitted
from the verified V12 ledger. ICT trades are treated as satellite candidates;
they are sized using only pre-entry combined equity, pre-entry combined
drawdown, and current ICT open-risk capacity.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "research" / "v14_3_true_combined_v12_ict_output"


def _safe_json(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _timestamp(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _drawdown(peak: float, equity: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100.0)


def _load_v12(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"V12 ledger not found: {path}. Run research/v12_final_ledger_export.py first."
        )
    frame = pd.read_csv(path)
    required = {"entry_time", "exit_time", "symbol", "engine", "side", "risk_percent", "r_multiple"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"V12 ledger missing required columns: {sorted(missing)}")
    frame = frame.copy()
    frame["entry_time"] = frame["entry_time"].map(_timestamp)
    frame["exit_time"] = frame["exit_time"].map(_timestamp)
    frame["risk_percent"] = frame["risk_percent"].astype(float)
    frame["r_multiple"] = frame["r_multiple"].astype(float)
    frame["engine_group"] = "V12"
    frame["setup"] = frame.get("setup", "V12_FINAL")
    return frame.sort_values(["entry_time", "engine", "setup"]).reset_index(drop=True)


def _load_ict(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"ICT trade file not found: {path}")
    frame = pd.read_csv(path)
    required = {"entry_time", "exit_time", "symbol", "setup", "r"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ICT trade file missing required columns: {sorted(missing)}")
    frame = frame.copy()
    frame["entry_time"] = frame["entry_time"].map(_timestamp)
    frame["exit_time"] = frame["exit_time"].map(_timestamp)
    frame["engine"] = "ICT_V14_3_UNDER10"
    frame["side"] = frame.get("direction", "")
    frame["r_multiple"] = frame["r"].astype(float)
    frame["engine_group"] = "ICT"
    return frame.sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)


def replay(
    v12: pd.DataFrame,
    ict: pd.DataFrame,
    starting_balance: float = 5000.0,
    ict_active_risk_percent: float = 0.45,
    ict_throttle_dd_percent: float = 8.0,
    ict_throttle_risk_percent: float = 0.05,
    ict_hard_dd_percent: float = 9.70,
    max_ict_open_risk_percent: float = 1.25,
    max_combined_open_risk_percent: float = 2.75,
) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay the portfolio in chronological entry/exit order.

    V12 ledger trades are admitted as master trades. ICT trades are candidates and
    can be skipped if the combined portfolio is already in hard drawdown or ICT
    open-risk capacity is crowded. The ICT trade result is never used before the
    trade is accepted and sized.
    """
    balance = peak = float(starting_balance)
    max_closed_dd = 0.0
    stress_dd = 0.0
    active: list[dict] = []
    closed: list[dict] = []
    skipped: list[dict] = []
    events: list[dict] = [{
        "time": min(v12["entry_time"].min(), ict["entry_time"].min()).isoformat(),
        "event": "START",
        "equity": balance,
        "balance": balance,
        "peak": peak,
        "drawdown_percent": 0.0,
        "open_risk_percent": 0.0,
    }]

    v12_iter = iter(v12.to_dict("records"))
    ict_iter = iter(ict.to_dict("records"))
    v12_next = next(v12_iter, None)
    ict_next = next(ict_iter, None)
    trade_id = 1

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
            dd_after = _drawdown(peak, balance)
            max_closed_dd = max(max_closed_dd, dd_after)
            active.remove(item)
            row = {
                **item,
                "pnl": pnl,
                "equity_before_exit": before,
                "equity_after_exit": balance,
                "drawdown_after_exit": dd_after,
            }
            closed.append(row)
            events.append({
                "time": item["exit_time"].isoformat(),
                "event": "EXIT",
                "trade_id": item["trade_id"],
                "engine_group": item["engine_group"],
                "engine": item["engine"],
                "symbol": item["symbol"],
                "pnl": pnl,
                "equity": balance,
                "balance": balance,
                "peak": peak,
                "drawdown_percent": dd_after,
                "open_risk_percent": sum(float(x["risk_percent"]) for x in active),
            })

    def admit(row: dict, group: str) -> None:
        nonlocal trade_id, stress_dd
        now = row["entry_time"]
        close_due(now)
        pre_dd = _drawdown(peak, balance)
        open_risk_total = sum(float(item["risk_percent"]) for item in active)
        ict_open_risk = sum(float(item["risk_percent"]) for item in active if item["engine_group"] == "ICT")

        if group == "V12":
            risk_percent = float(row["risk_percent"])
            reason = "v12_master_admitted"
        else:
            if pre_dd >= ict_hard_dd_percent:
                skipped.append({
                    **row,
                    "skip_reason": "ict_hard_dd",
                    "pre_equity": balance,
                    "pre_dd": pre_dd,
                    "pre_open_risk_total": open_risk_total,
                    "pre_ict_open_risk": ict_open_risk,
                })
                return
            risk_percent = ict_throttle_risk_percent if pre_dd >= ict_throttle_dd_percent else ict_active_risk_percent
            reason = "ict_dd_throttle" if pre_dd >= ict_throttle_dd_percent else "ict_active"
            if ict_open_risk + risk_percent > max_ict_open_risk_percent + 1e-12:
                skipped.append({
                    **row,
                    "skip_reason": "ict_open_risk_cap",
                    "assigned_risk_percent": risk_percent,
                    "pre_equity": balance,
                    "pre_dd": pre_dd,
                    "pre_open_risk_total": open_risk_total,
                    "pre_ict_open_risk": ict_open_risk,
                })
                return
            if open_risk_total + risk_percent > max_combined_open_risk_percent + 1e-12:
                skipped.append({
                    **row,
                    "skip_reason": "combined_open_risk_cap",
                    "assigned_risk_percent": risk_percent,
                    "pre_equity": balance,
                    "pre_dd": pre_dd,
                    "pre_open_risk_total": open_risk_total,
                    "pre_ict_open_risk": ict_open_risk,
                })
                return

        risk_dollars = balance * risk_percent / 100.0
        item = {
            "trade_id": trade_id,
            "engine_group": group,
            "engine": str(row.get("engine", group)),
            "symbol": str(row.get("symbol", "")),
            "setup": str(row.get("setup", "")),
            "side": str(row.get("side", "")),
            "entry_time": row["entry_time"],
            "exit_time": row["exit_time"],
            "risk_percent": risk_percent,
            "risk_dollars": risk_dollars,
            "r_multiple": float(row["r_multiple"]),
            "pre_equity": balance,
            "pre_drawdown_percent": pre_dd,
            "pre_open_risk_total": open_risk_total,
            "pre_ict_open_risk": ict_open_risk,
            "admission_reason": reason,
        }
        trade_id += 1
        active.append(item)
        stressed = balance - sum(float(position["risk_dollars"]) for position in active)
        stress_dd = max(stress_dd, _drawdown(peak, stressed))
        events.append({
            "time": now.isoformat(),
            "event": "ENTRY",
            "trade_id": item["trade_id"],
            "engine_group": group,
            "engine": item["engine"],
            "symbol": item["symbol"],
            "risk_percent": risk_percent,
            "equity": balance,
            "balance": balance,
            "peak": peak,
            "drawdown_percent": pre_dd,
            "open_risk_percent": sum(float(x["risk_percent"]) for x in active),
            "admission_reason": reason,
        })

    while v12_next is not None or ict_next is not None:
        if ict_next is None or (v12_next is not None and v12_next["entry_time"] <= ict_next["entry_time"]):
            admit(v12_next, "V12")
            v12_next = next(v12_iter, None)
        else:
            admit(ict_next, "ICT")
            ict_next = next(ict_iter, None)

    close_due(pd.Timestamp.max.tz_localize("UTC"))
    closed_frame = pd.DataFrame(closed).sort_values(["exit_time", "trade_id"]).reset_index(drop=True)
    skipped_frame = pd.DataFrame(skipped)
    events_frame = pd.DataFrame(events)

    if closed_frame.empty:
        gross_profit = gross_loss = 0.0
    else:
        pnl = closed_frame["pnl"].astype(float)
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())

    by_engine = {}
    if not closed_frame.empty:
        for name, grp in closed_frame.groupby("engine_group"):
            pnl = grp["pnl"].astype(float)
            gp = float(pnl[pnl > 0].sum())
            gl = float(-pnl[pnl < 0].sum())
            by_engine[name] = {
                "trades": int(len(grp)),
                "net": float(pnl.sum()),
                "gross_profit": gp,
                "gross_loss": gl,
                "profit_factor": gp / gl if gl else (math.inf if gp else 0.0),
            }

    summary = {
        "starting_balance": starting_balance,
        "ending_balance": balance,
        "net_profit": balance - starting_balance,
        "return_percent": (balance / starting_balance - 1.0) * 100.0,
        "closed_trades": int(len(closed_frame)),
        "skipped_ict_trades": int(len(skipped_frame)),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0),
        "max_closed_drawdown_percent": max_closed_dd,
        "stress_drawdown_percent": stress_dd,
        "settings": {
            "ict_active_risk_percent": ict_active_risk_percent,
            "ict_throttle_dd_percent": ict_throttle_dd_percent,
            "ict_throttle_risk_percent": ict_throttle_risk_percent,
            "ict_hard_dd_percent": ict_hard_dd_percent,
            "max_ict_open_risk_percent": max_ict_open_risk_percent,
            "max_combined_open_risk_percent": max_combined_open_risk_percent,
        },
        "by_engine_group": by_engine,
        "skip_reasons": skipped_frame["skip_reason"].value_counts().to_dict() if not skipped_frame.empty else {},
    }
    return summary, closed_frame, skipped_frame, events_frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Run true V12 + ICT event-based combined replay")
    parser.add_argument("--v12-ledger", type=Path, default=ROOT / "research" / "v12_final_ledger_output" / "v12_final_trade_ledger.csv")
    parser.add_argument("--ict-trades", type=Path, default=ROOT / "research" / "v14_3_under10_target_out" / "selected_under10_target_trades.csv")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--starting-balance", type=float, default=5000.0)
    parser.add_argument("--ict-active-risk", type=float, default=0.45)
    parser.add_argument("--ict-throttle-dd", type=float, default=8.0)
    parser.add_argument("--ict-throttle-risk", type=float, default=0.05)
    parser.add_argument("--ict-hard-dd", type=float, default=9.70)
    parser.add_argument("--max-ict-open-risk", type=float, default=1.25)
    parser.add_argument("--max-combined-open-risk", type=float, default=2.75)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    v12 = _load_v12(args.v12_ledger)
    ict = _load_ict(args.ict_trades)
    summary, closed, skipped, events = replay(
        v12=v12,
        ict=ict,
        starting_balance=args.starting_balance,
        ict_active_risk_percent=args.ict_active_risk,
        ict_throttle_dd_percent=args.ict_throttle_dd,
        ict_throttle_risk_percent=args.ict_throttle_risk,
        ict_hard_dd_percent=args.ict_hard_dd,
        max_ict_open_risk_percent=args.max_ict_open_risk,
        max_combined_open_risk_percent=args.max_combined_open_risk,
    )

    closed.to_csv(args.out / "true_combined_closed_trades.csv", index=False)
    skipped.to_csv(args.out / "true_combined_skipped_ict_trades.csv", index=False)
    events.to_csv(args.out / "true_combined_equity_events.csv", index=False)
    (args.out / "true_combined_summary.json").write_text(
        json.dumps(summary, indent=2, default=_safe_json), encoding="utf-8"
    )

    report = [
        "# V14.3 True Combined V12 + ICT Event-Based Replay",
        "",
        "Status: research-only true event-based replay using generated V12 ledger and ICT candidate trades.",
        "",
        "## Summary",
        "",
        f"- Starting balance: ${summary['starting_balance']:,.2f}",
        f"- Ending balance: ${summary['ending_balance']:,.2f}",
        f"- Net profit: ${summary['net_profit']:,.2f}",
        f"- Return: {summary['return_percent']:.2f}%",
        f"- Closed trades: {summary['closed_trades']}",
        f"- Skipped ICT trades: {summary['skipped_ict_trades']}",
        f"- Profit factor: {summary['profit_factor']:.3f}",
        f"- Max closed DD: {summary['max_closed_drawdown_percent']:.2f}%",
        f"- Stress DD: {summary['stress_drawdown_percent']:.2f}%",
        "",
        "## Engine-group result",
        "",
    ]
    for group, stats in summary["by_engine_group"].items():
        report.append(
            f"- {group}: trades={stats['trades']}, net=${stats['net']:,.2f}, PF={stats['profit_factor']:.3f}"
        )
    report.extend([
        "",
        "## Limitations",
        "",
        "- Research-only historical replay.",
        "- V12 price fields may be blank because the V12 ledger is R-multiple based.",
        "- Forward/paper validation is still required before any execution use.",
    ])
    (args.out / "V14_3_TRUE_COMBINED_V12_ICT_REPLAY_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=_safe_json))


if __name__ == "__main__":
    main()
