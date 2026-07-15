"""Recreate the V14.3 ICT candidate trade stream.

This is the missing research-side generator for the V14.3 ICT satellite used by
the under-10 drawdown combined replay. It is intentionally research-only: it
never connects to MT5, never sends orders, and never modifies live bot state.

The script can work from either:

1. the canonical deduped V14.3 high-frequency liquidity-fade stream:
   research/v13_ict_high_activity_select_out/deduped_liquidity_fade_gap60.csv

2. the larger raw activity-signal exports:
   research/v13_ict_high_activity_out/GBPUSD_all_activity_signals.csv
   research/v13_ict_high_activity_out/GBPJPY_all_activity_signals.csv

From those inputs it recreates:
- the 11,649-row deduped gap-60 candidate stream; and
- the 4,303-row V14.3 under-10 selected ICT trade file.

It then runs the same no-same-trade-result-lookahead event replay used by the
V14.3 under-10 research profile:
- active ICT risk 0.45%;
- throttle to 0.05% once conservative combined DD proxy reaches 8.0%;
- hard skip if conservative combined DD proxy reaches 9.70%;
- max ICT open-risk cap 1.25%;
- V12 stress-DD reserve 5.25%.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANONICAL = ROOT / "research" / "v13_ict_high_activity_select_out" / "deduped_liquidity_fade_gap60.csv"
DEFAULT_RAW_DIR = ROOT / "research" / "v13_ict_high_activity_out"
DEFAULT_OUT = ROOT / "research" / "v14_3_under10_target_out"

SETUP_PRIORITY = {
    "sweep_reclaim_60": 0.0,
    "sweep_reclaim_30": 1.0,
    "sweep_reclaim_15": 2.0,
    "breakout_60_fade": 3.2,
    "breakout_30_fade": 4.2,
    "breakout_15_fade": 5.2,
}
SYMBOLS = ("GBPUSD", "GBPJPY")


@dataclass(frozen=True)
class V143Profile:
    starting_balance: float = 5000.0
    active_risk_percent: float = 0.45
    throttle_dd_percent: float = 8.0
    throttle_risk_percent: float = 0.05
    hard_dd_percent: float = 9.70
    max_ict_open_risk_percent: float = 1.25
    v12_stress_dd_reserve_percent: float = 5.25


def _safe_json(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _read_signal_file(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"entry_time", "exit_time", "r", "direction", "symbol", "setup"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
    frame = frame.copy()
    frame["entry_time"] = pd.to_datetime(frame["entry_time"])
    frame["exit_time"] = pd.to_datetime(frame["exit_time"])
    frame["r"] = pd.to_numeric(frame["r"], errors="coerce")
    frame["direction"] = pd.to_numeric(frame["direction"], errors="coerce").astype(int)
    frame["symbol"] = frame["symbol"].astype(str)
    frame["setup"] = frame["setup"].astype(str)
    return frame.dropna(subset=["entry_time", "exit_time", "r"])


def build_deduped_gap_stream(raw_dir: Path, gap_minutes: int = 60) -> pd.DataFrame:
    """Build the canonical gap-deduped liquidity-fade candidate stream.

    The raw files contain many overlapping variants. V14.3 keeps only the six
    liquidity-fade/sweep-reclaim setup families and then greedily keeps the first
    signal per symbol after a 60-minute gap. When several setup variants share
    the same timestamp, lower priority value wins; this reproduces the original
    V14.3 gap stream except for harmless floating-point string precision.
    """
    frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        path = raw_dir / f"{symbol}_all_activity_signals.csv"
        if not path.exists():
            raise FileNotFoundError(f"raw activity signal file not found: {path}")
        frame = _read_signal_file(path)
        frame = frame[frame["setup"].isin(SETUP_PRIORITY)].copy()
        frame["priority"] = frame["setup"].map(SETUP_PRIORITY).astype(float)
        frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.sort_values(["symbol", "entry_time", "priority"], ascending=[True, True, True])

    rows: list[dict] = []
    last_by_symbol: dict[str, pd.Timestamp] = {}
    min_gap = pd.Timedelta(minutes=gap_minutes)
    for row in raw.itertuples(index=False):
        symbol = str(row.symbol)
        entry_time = pd.Timestamp(row.entry_time)
        last = last_by_symbol.get(symbol)
        if last is None or entry_time - last >= min_gap:
            rows.append(
                {
                    "entry_time": entry_time,
                    "exit_time": pd.Timestamp(row.exit_time),
                    "r": float(row.r),
                    "direction": int(row.direction),
                    "symbol": symbol,
                    "setup": str(row.setup),
                    "priority": float(row.priority),
                }
            )
            last_by_symbol[symbol] = entry_time
    return pd.DataFrame(rows).sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)


def load_or_build_candidate_stream(candidate_stream: Path, raw_dir: Path) -> pd.DataFrame:
    if candidate_stream.exists():
        frame = _read_signal_file(candidate_stream)
        if "priority" not in frame.columns:
            frame["priority"] = frame["setup"].map(SETUP_PRIORITY).fillna(0.0).astype(float)
        return frame[["entry_time", "exit_time", "r", "direction", "symbol", "setup", "priority"]].copy()
    return build_deduped_gap_stream(raw_dir)


def apply_v14_3_filters(stream: pd.DataFrame) -> pd.DataFrame:
    """Apply the locked V14.3 edge filters."""
    frame = stream.copy()
    frame["hour"] = frame["entry_time"].dt.hour
    frame["dow"] = frame["entry_time"].dt.dayofweek

    is_gbpjpy_breakout_fade = (frame["symbol"] == "GBPJPY") & frame["setup"].isin(
        ["breakout_15_fade", "breakout_30_fade", "breakout_60_fade"]
    )
    is_gbpusd_sweep15 = (frame["symbol"] == "GBPUSD") & (frame["setup"] == "sweep_reclaim_15")
    is_tuesday = frame["dow"] == 1
    is_blocked_hour = frame["hour"].isin([7, 13])

    selected = frame[~(is_gbpjpy_breakout_fade | is_gbpusd_sweep15 | is_tuesday | is_blocked_hour)].copy()
    return selected.sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)


def replay_under10(selected: pd.DataFrame, profile: V143Profile) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Run no-same-trade-result-lookahead event replay for the ICT stream."""
    balance = peak = profile.starting_balance
    active: list[dict] = []
    accepted: list[dict] = []
    skipped: list[dict] = []
    events: list[dict] = []
    max_dd = 0.0
    max_open = 0.0

    def drawdown() -> float:
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - balance) / peak * 100.0)

    def close_due(now: pd.Timestamp) -> None:
        nonlocal balance, peak, max_dd
        due = sorted([item for item in active if item["exit_time"] <= now], key=lambda item: item["exit_time"])
        for item in due:
            pnl = float(item["risk_dollars"]) * float(item["r"])
            balance += pnl
            peak = max(peak, balance)
            post_dd = drawdown()
            max_dd = max(max_dd, post_dd)
            item["pnl"] = pnl
            item["post_exit_equity"] = balance
            item["post_exit_dd"] = post_dd
            accepted.append(item)
            active.remove(item)
            events.append(
                {
                    "time": item["exit_time"].isoformat(),
                    "event": "EXIT",
                    "trade_id": item["trade_id"],
                    "symbol": item["symbol"],
                    "setup": item["setup"],
                    "pnl": pnl,
                    "equity": balance,
                    "drawdown_percent": post_dd,
                    "open_risk_percent": sum(float(position["assigned_risk_percent"]) for position in active),
                }
            )

    for source_id, row in enumerate(selected.itertuples(index=False)):
        entry_time = pd.Timestamp(row.entry_time)
        close_due(entry_time)
        pre_dd = drawdown()
        pre_combined_proxy_dd = profile.v12_stress_dd_reserve_percent + pre_dd
        pre_open_risk = sum(float(position["assigned_risk_percent"]) for position in active)

        if pre_combined_proxy_dd >= profile.hard_dd_percent:
            skipped.append(
                {
                    **row._asdict(),
                    "skip_reason": "hard_dd",
                    "pre_equity": balance,
                    "pre_dd": pre_dd,
                    "pre_combined_proxy_dd": pre_combined_proxy_dd,
                    "pre_open_risk": pre_open_risk,
                }
            )
            continue

        assigned_risk = (
            profile.throttle_risk_percent
            if pre_combined_proxy_dd >= profile.throttle_dd_percent
            else profile.active_risk_percent
        )
        risk_reason = "dd_throttle" if pre_combined_proxy_dd >= profile.throttle_dd_percent else "active"

        if pre_open_risk + assigned_risk > profile.max_ict_open_risk_percent + 1e-12:
            skipped.append(
                {
                    **row._asdict(),
                    "skip_reason": "ict_open_risk_cap",
                    "assigned_risk_percent": assigned_risk,
                    "pre_equity": balance,
                    "pre_dd": pre_dd,
                    "pre_combined_proxy_dd": pre_combined_proxy_dd,
                    "pre_open_risk": pre_open_risk,
                }
            )
            continue

        risk_dollars = balance * assigned_risk / 100.0
        item = {
            "trade_id": int(source_id),
            "entry_time": entry_time,
            "exit_time": pd.Timestamp(row.exit_time),
            "symbol": str(row.symbol),
            "setup": str(row.setup),
            "r": float(row.r),
            "direction": int(row.direction),
            "priority": float(row.priority),
            "hour": int(row.hour),
            "dow": int(row.dow),
            "pre_equity": balance,
            "pre_hwm": peak,
            "pre_dd": pre_dd,
            "pre_combined_proxy_dd": pre_combined_proxy_dd,
            "pre_open_risk": pre_open_risk,
            "assigned_risk_percent": assigned_risk,
            "risk_dollars": risk_dollars,
            "risk_reason": risk_reason,
            "entry_decision_used_trade_result": False,
        }
        active.append(item)
        max_open = max(max_open, pre_open_risk + assigned_risk)
        events.append(
            {
                "time": entry_time.isoformat(),
                "event": "ENTRY",
                "trade_id": int(source_id),
                "symbol": str(row.symbol),
                "setup": str(row.setup),
                "assigned_risk_percent": assigned_risk,
                "risk_reason": risk_reason,
                "equity": balance,
                "drawdown_percent": pre_dd,
                "open_risk_percent": pre_open_risk + assigned_risk,
            }
        )

    close_due(pd.Timestamp.max)
    trades = pd.DataFrame(accepted).sort_values(["entry_time", "trade_id"]).reset_index(drop=True)
    skipped_frame = pd.DataFrame(skipped)
    events_frame = pd.DataFrame(events)

    if trades.empty:
        gross_win = gross_loss = 0.0
    else:
        pnl = trades["pnl"].astype(float)
        gross_win = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())

    summary = {
        "profile": asdict(profile),
        "source_signals_after_v14_3_filters": int(len(selected)),
        "accepted_trades": int(len(trades)),
        "skipped_trades": int(len(skipped_frame)),
        "starting_balance": profile.starting_balance,
        "ending_balance": balance,
        "net_result": balance - profile.starting_balance,
        "return_percent": (balance / profile.starting_balance - 1.0) * 100.0,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "profit_factor": gross_win / gross_loss if gross_loss else (math.inf if gross_win else 0.0),
        "max_ict_realized_dd_percent": max_dd,
        "max_combined_proxy_dd_percent": profile.v12_stress_dd_reserve_percent + max_dd,
        "max_ict_open_risk_percent": max_open,
        "risk_reason_counts": trades["risk_reason"].value_counts().to_dict() if not trades.empty else {},
        "skip_reason_counts": skipped_frame["skip_reason"].value_counts().to_dict() if not skipped_frame.empty else {},
    }
    return trades, skipped_frame, events_frame, summary


def write_outputs(out: Path, stream: pd.DataFrame, selected: pd.DataFrame, trades: pd.DataFrame, skipped: pd.DataFrame, events: pd.DataFrame, summary: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stream.to_csv(out / "deduped_liquidity_fade_gap60.csv", index=False)
    selected.to_csv(out / "v14_3_filtered_candidate_stream.csv", index=False)
    trades.to_csv(out / "selected_under10_target_trades.csv", index=False)
    skipped.to_csv(out / "selected_under10_target_skipped.csv", index=False)
    events.to_csv(out / "selected_under10_target_events.csv", index=False)
    if not trades.empty:
        trades.assign(year=pd.to_datetime(trades["entry_time"]).dt.year).groupby("year").agg(
            accepted_trades=("trade_id", "count"),
            avg_r=("r", "mean"),
            total_r=("r", "sum"),
            pnl=("pnl", "sum"),
            avg_risk_pct=("assigned_risk_percent", "mean"),
            max_post_exit_dd=("post_exit_dd", "max"),
        ).reset_index().to_csv(out / "selected_under10_target_by_year.csv", index=False)
        trades.groupby("risk_reason").agg(
            accepted_trades=("trade_id", "count"),
            pnl=("pnl", "sum"),
            avg_risk_pct=("assigned_risk_percent", "mean"),
            avg_r=("r", "mean"),
        ).reset_index().to_csv(out / "selected_under10_target_by_reason.csv", index=False)
    (out / "v14_3_candidate_generator_summary.json").write_text(
        json.dumps(summary, indent=2, default=_safe_json), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Recreate the V14.3 ICT candidate and selected trade stream")
    parser.add_argument("--candidate-stream", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--assert-known", action="store_true", help="Assert the known V14.3 under-10 target counts/results.")
    args = parser.parse_args()

    stream = load_or_build_candidate_stream(args.candidate_stream, args.raw_dir)
    selected = apply_v14_3_filters(stream)
    trades, skipped, events, summary = replay_under10(selected, V143Profile())
    write_outputs(args.out, stream, selected, trades, skipped, events, summary)

    if args.assert_known:
        if int(summary["source_signals_after_v14_3_filters"]) != 4303:
            raise AssertionError(f"expected 4303 filtered signals, got {summary['source_signals_after_v14_3_filters']}")
        if int(summary["accepted_trades"]) != 4303:
            raise AssertionError(f"expected 4303 accepted trades, got {summary['accepted_trades']}")
        if abs(float(summary["ending_balance"]) - 10208.812171316025) > 0.05:
            raise AssertionError(f"ending balance drifted: {summary['ending_balance']}")
        if abs(float(summary["max_combined_proxy_dd_percent"]) - 9.570988298980303) > 0.02:
            raise AssertionError(f"combined DD drifted: {summary['max_combined_proxy_dd_percent']}")

    print(json.dumps(summary, indent=2, default=_safe_json))


if __name__ == "__main__":
    main()
