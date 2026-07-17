"""V14.9 walk-forward five-symbol swing + ICT research portfolio.

The failed V14.8 external replay is not repaired by tuning against its 2022-2026
holdout. V14.9 uses 2016-2018 only to select ten simple sleeve definitions,
2019-2020 for validation, 2021-03-05 for audit, and 2022-03-06 onward as the
untouched chronological test. Every candidate sleeve is continuously shadow
scored after modeled retail costs. Capital is allocated only when the sleeve's
own prior 365-day record has at least six closed observations, positive net R,
and profit factor of at least 1.00.

Research only. No MT5 connection, order transmission, merge, or deployment.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v14_8_dukascopy_2016_2026_backtest as external  # noqa: E402
from research import v14_8_strict_all_ten_20k as v148  # noqa: E402

SYMBOLS = v148.SYMBOLS
DATA = ROOT / "research" / "fxcm_2016_2026_data"
OUT = ROOT / "research" / "v14_9_fxcm_walk_forward_output"
TEST_START = pd.Timestamp("2016-01-01T00:00:00Z")
TRAIN_END = pd.Timestamp("2018-12-31T23:59:59Z")
VALIDATION_END = pd.Timestamp("2020-12-31T23:59:59Z")
AUDIT_END = pd.Timestamp("2022-03-05T23:59:59Z")
FRESH_START = pd.Timestamp("2022-03-06T00:00:00Z")
TEST_END = pd.Timestamp("2026-05-01T20:00:00Z")
PORTFOLIO_START = pd.Timestamp("2019-01-01T00:00:00Z")
TARGET_NET = 20_000.0
TRAILING_DAYS = 365
MIN_TRAILING_TRADES = 6
MIN_TRAILING_NET_R = 0.0
MIN_TRAILING_PROFIT_FACTOR = 1.0
MAX_COMBINED_OPEN_RISK = 3.25
MAX_ICT_OPEN_RISK = 1.75

SESSION_HOURS = {
    "ASIA": (0, 6),
    "LONDON": (6, 12),
    "NEW_YORK": (12, 18),
    "LATE": (18, 24),
}


@dataclass(frozen=True)
class WalkForwardSleeve:
    symbol: str
    mode: str
    profile: str
    setup: str
    risk_percent: float
    side: str | None = None
    hour: int | None = None
    session: str | None = None
    excluded_weekday: int | None = None


# These definitions were selected using only 2016-03-05/2022 pre-holdout blocks.
# The final risk levels preserve V14.8's 1.25% swing and 0.60% ICT ceilings.
SLEEVES = (
    WalkForwardSleeve("GBPUSD", "SWING", "SWING_PULLBACK_20", "v14_9_gbpusd_swing_buy_12", 1.20, side="BUY", hour=12),
    WalkForwardSleeve("GBPUSD", "ICT", "gu_london_15", "v14_9_gbpusd_ict_london_08", 0.50, hour=8),
    WalkForwardSleeve("EURUSD", "SWING", "SWING_PULLBACK_20", "v14_9_eurusd_swing_04_no_thursday", 1.00, hour=4, excluded_weekday=3),
    WalkForwardSleeve("EURUSD", "ICT", "eu_london_20", "v14_9_eurusd_ict_london_08", 0.60, hour=8),
    WalkForwardSleeve("GBPJPY", "SWING", "SWING_PULLBACK_RUNNER", "v14_9_gbpjpy_swing_sell_ny", 1.20, side="SELL", session="NEW_YORK"),
    WalkForwardSleeve("GBPJPY", "ICT", "ICT_BREAKOUT_H4", "v14_9_gbpjpy_ict_buy_12", 0.60, side="BUY", hour=12),
    WalkForwardSleeve("AUDUSD", "SWING", "SWING_PULLBACK_20", "v14_9_audusd_swing_sell_no_friday", 1.00, side="SELL", excluded_weekday=4),
    WalkForwardSleeve("AUDUSD", "ICT", "ICT_FAST_RECLAIM", "v14_9_audusd_ict_buy_14", 0.50, side="BUY", hour=14),
    WalkForwardSleeve("USDJPY", "SWING", "SWING_EMA_RECLAIM", "v14_9_usdjpy_swing_buy_04", 1.00, side="BUY", hour=4),
    WalkForwardSleeve("USDJPY", "ICT", "uj_london_25", "v14_9_usdjpy_ict_no_monday", 0.50, excluded_weekday=0),
)


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    values = pd.to_numeric(frame.get("r_multiple", pd.Series(dtype=float)), errors="coerce").dropna()
    if values.empty:
        return {"trades": 0, "net_r": 0.0, "expectancy_r": None, "profit_factor": None, "win_rate": None}
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else 99.0,
        "win_rate": round(float((values > 0).mean()), 6),
    }


def dollar_stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "net_profit": 0.0, "profit_factor": None, "win_rate": None}
    pnl = pd.to_numeric(frame["pnl"], errors="coerce").dropna()
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    return {
        "trades": int(len(pnl)),
        "net_profit": round(float(pnl.sum()), 2),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else 99.0,
        "win_rate": round(float((pnl > 0).mean()), 6),
    }


def filter_sleeve(source: pd.DataFrame, sleeve: WalkForwardSleeve) -> pd.DataFrame:
    selected = source[
        (source["symbol"].astype(str) == sleeve.symbol)
        & (source["mode"].astype(str) == sleeve.mode)
        & (source["profile"].fillna("NONE").astype(str) == sleeve.profile)
    ].copy()
    if sleeve.side is not None:
        selected = selected[selected["side"].astype(str).str.upper() == sleeve.side]
    if sleeve.hour is not None:
        selected = selected[selected["entry_time"].dt.hour == sleeve.hour]
    if sleeve.session is not None:
        low, high = SESSION_HOURS[sleeve.session]
        selected = selected[
            (selected["entry_time"].dt.hour >= low)
            & (selected["entry_time"].dt.hour < high)
        ]
    if sleeve.excluded_weekday is not None:
        selected = selected[selected["entry_time"].dt.weekday != sleeve.excluded_weekday]
    if selected.empty:
        raise RuntimeError(f"No external candidates for {sleeve}")
    selected = selected.sort_values(["entry_time", "exit_time", "side"]).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "mode", "profile", "side"]
    )
    selected["raw_r_multiple"] = pd.to_numeric(selected["r_multiple"], errors="coerce")
    selected["cost_r"] = pd.to_numeric(selected["selection_cost_r"], errors="coerce")
    selected["r_multiple"] = selected["raw_r_multiple"] - selected["cost_r"]
    selected["setup"] = sleeve.setup
    selected["risk_percent"] = float(sleeve.risk_percent)
    selected["sleeve_id"] = f"{sleeve.symbol}/{sleeve.mode}"
    return selected.reset_index(drop=True)


def development_blocks(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        "training": ratio_stats(frame[(frame["entry_time"] >= TEST_START) & (frame["entry_time"] <= TRAIN_END)]),
        "validation": ratio_stats(frame[(frame["entry_time"] > TRAIN_END) & (frame["entry_time"] <= VALIDATION_END)]),
        "audit": ratio_stats(frame[(frame["entry_time"] > VALIDATION_END) & (frame["entry_time"] <= AUDIT_END)]),
    }


def validate_development(sleeve: WalkForwardSleeve, evidence: dict[str, dict[str, Any]]) -> None:
    minimums = {"SWING": (12, 8, 5), "ICT": (18, 10, 6)}[sleeve.mode]
    for name, minimum in zip(("training", "validation", "audit"), minimums):
        stats = evidence[name]
        if int(stats["trades"]) < minimum:
            raise RuntimeError(f"{sleeve.setup} insufficient {name}: {stats}")
        if float(stats["net_r"]) <= 0.0:
            raise RuntimeError(f"{sleeve.setup} negative {name}: {stats}")
        if float(stats["profit_factor"] or 0.0) < 1.05:
            raise RuntimeError(f"{sleeve.setup} weak {name} PF: {stats}")


def apply_walk_forward_gate(frame: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for sleeve_id, group in frame.groupby("sleeve_id", sort=False):
        work = group.sort_values(["entry_time", "exit_time"]).copy().reset_index(drop=True)
        decisions: list[dict[str, Any]] = []
        for row in work.itertuples(index=False):
            now = pd.Timestamp(row.entry_time)
            history = work[
                (work["exit_time"] < now)
                & (work["exit_time"] >= now - pd.Timedelta(days=TRAILING_DAYS))
            ]
            stats = ratio_stats(history)
            accepted = (
                int(stats["trades"]) >= MIN_TRAILING_TRADES
                and float(stats["net_r"]) > MIN_TRAILING_NET_R
                and float(stats["profit_factor"] or 0.0) >= MIN_TRAILING_PROFIT_FACTOR
                and now >= PORTFOLIO_START
            )
            reason = "ACTIVE" if accepted else "SHADOW_TRAILING_EDGE_GATE"
            expectancy = float(stats["expectancy_r"] or 0.0)
            score = expectancy * math.sqrt(max(1, int(stats["trades"])))
            decisions.append(
                {
                    "gate_active": bool(accepted),
                    "gate_reason": reason,
                    "trailing_trades": int(stats["trades"]),
                    "trailing_net_r": float(stats["net_r"]),
                    "trailing_profit_factor": stats["profit_factor"],
                    "trailing_expectancy_r": stats["expectancy_r"],
                    "gate_score": score,
                }
            )
        decision_frame = pd.DataFrame(decisions)
        work = pd.concat([work.reset_index(drop=True), decision_frame], axis=1)
        outputs.append(work)
    return pd.concat(outputs, ignore_index=True, sort=False).sort_values(
        ["entry_time", "gate_score", "symbol"], ascending=[True, False, True]
    ).reset_index(drop=True)


def portfolio_admission(gated: pd.DataFrame) -> pd.DataFrame:
    active: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for row in gated.sort_values(["entry_time", "gate_score"], ascending=[True, False]).itertuples(index=False):
        now = pd.Timestamp(row.entry_time)
        active = [item for item in active if item["exit_time"] > now]
        accepted = bool(row.gate_active)
        reason = str(row.gate_reason)
        requested = float(row.risk_percent)
        if accepted and any(item["symbol"] == row.symbol for item in active):
            accepted, reason = False, "SYMBOL_OPEN_POSITION_LIMIT"
        total_open = sum(float(item["risk_percent"]) for item in active)
        ict_open = sum(float(item["risk_percent"]) for item in active if item["mode"] == "ICT")
        if accepted and total_open + requested > MAX_COMBINED_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_COMBINED_RISK_CAP"
        if accepted and row.mode == "ICT" and ict_open + requested > MAX_ICT_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_ICT_RISK_CAP"
        if accepted:
            active.append(
                {
                    "symbol": row.symbol,
                    "mode": row.mode,
                    "exit_time": pd.Timestamp(row.exit_time),
                    "risk_percent": requested,
                }
            )
        decisions.append({"portfolio_admitted": accepted, "portfolio_admission_reason": reason})
    return pd.concat([gated.reset_index(drop=True), pd.DataFrame(decisions)], axis=1)


def run_replay(swing: pd.DataFrame, ict: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, Any]:
    old_risk, old_guards = v148.install_profile(ict)
    replay = v148.ProjectedStressReplay(swing, ict)
    try:
        summary, trades, skipped = replay.run()
    finally:
        v148.restore_profile(old_risk, old_guards)
    return summary, trades, skipped, replay


def baseline_v148(source: pd.DataFrame) -> dict[str, Any]:
    swing_frames: list[pd.DataFrame] = []
    ict_frames: list[pd.DataFrame] = []
    for sleeve in v148.FROZEN_SLEEVES:
        try:
            frame = v148.materialize_sleeve(source, sleeve)
        except RuntimeError:
            continue
        frame = frame[(frame["entry_time"] >= PORTFOLIO_START) & (frame["entry_time"] <= TEST_END)]
        if not frame.empty:
            (swing_frames if sleeve.mode == "SWING" else ict_frames).append(frame)
    if not swing_frames or not ict_frames:
        return {"available": False}
    swing = pd.concat(swing_frames, ignore_index=True, sort=False)
    ict = pd.concat(ict_frames, ignore_index=True, sort=False)
    summary, trades, skipped, _ = run_replay(swing, ict)
    return {
        "available": True,
        "summary": summary,
        "trades": int(len(trades)),
        "skipped": int(len(skipped)),
    }


def write_report(payload: dict[str, Any]) -> None:
    portfolio = payload["portfolio"]
    baseline = payload["baseline_v14_8"]
    fresh = payload["fresh_2022_2026"]
    lines = [
        "# V14.9 FXCM Walk-Forward Portfolio",
        "",
        f"**External data:** {payload['provider']}",
        f"**Chart window:** {TEST_START.date()} through {TEST_END.date()}",
        f"**Capital deployment window:** {PORTFOLIO_START.date()} through {TEST_END.date()}",
        f"**Untouched chronological test:** {FRESH_START.date()} through {TEST_END.date()}",
        "**Starting balance:** $5,000.00",
        "",
        "## Portfolio result",
        "",
        "| Metric | V14.9 |",
        "|---|---:|",
        f"| Net profit after modeled retail costs | ${portfolio['net_profit']:,.2f} |",
        f"| Ending balance | ${portfolio['ending_balance']:,.2f} |",
        f"| Return | {portfolio['return_percent']:.2f}% |",
        f"| Profit factor | {float(portfolio['profit_factor'] or 0.0):.4f} |",
        f"| Maximum closed drawdown | {portfolio['max_closed_drawdown_percent']:.4f}% |",
        f"| Projected stressed drawdown | {portfolio['stress_drawdown_percent']:.4f}% |",
        f"| Closed trades | {portfolio['closed_trades']} |",
        f"| $20,000 target reached | {payload['target_reached']} |",
        "",
        "## Untouched 2022-2026 result",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Trades | {fresh['trades']} |",
        f"| Net profit | ${fresh['net_profit']:,.2f} |",
        f"| Profit factor | {float(fresh['profit_factor'] or 0.0):.4f} |",
        f"| Win rate | {float(fresh['win_rate'] or 0.0) * 100.0:.2f}% |",
        "",
        "## Improvement over frozen V14.8 on the same 2019-2026 deployment window",
        "",
    ]
    if baseline.get("available"):
        old = baseline["summary"]
        lines += [
            "| Metric | V14.8 frozen | V14.9 walk-forward | Change |",
            "|---|---:|---:|---:|",
            f"| Net profit | ${old['net_profit']:,.2f} | ${portfolio['net_profit']:,.2f} | ${portfolio['net_profit'] - old['net_profit']:,.2f} |",
            f"| Profit factor | {float(old['profit_factor'] or 0.0):.4f} | {float(portfolio['profit_factor'] or 0.0):.4f} | {float(portfolio['profit_factor'] or 0.0) - float(old['profit_factor'] or 0.0):.4f} |",
            f"| Max closed DD | {old['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent'] - old['max_closed_drawdown_percent']:.4f} pp |",
            f"| Closed trades | {old['closed_trades']} | {portfolio['closed_trades']} | {portfolio['closed_trades'] - old['closed_trades']} |",
        ]
    else:
        lines.append("The frozen V14.8 external baseline could not be reconstructed on this run.")
    lines += [
        "",
        "## Method",
        "",
        "- 2016-2018 is calibration and shadow-history collection; no portfolio capital is deployed during that interval.",
        "- Ten sleeve definitions are required to pass positive training, validation and audit blocks before the 2022 holdout.",
        "- Every sleeve remains shadow-scored, including while inactive.",
        "- A sleeve receives capital only when its prior 365-day after-cost record has at least six closed observations, positive net R and PF >= 1.00.",
        "- One position per symbol, 1.75% ICT open-risk cap and 3.25% combined open-risk cap are enforced before replay.",
        "- The existing 7.5/8.5/9.0/9.6 drawdown governor and projected-stress admission limit remain active.",
        "- H1 bid and ask candles come from FXCM's official weekly archive; H4 and D1 are resampled from H1 bid candles.",
        "- Costs are modeled using the existing 0.04R swing, 0.09R wide-ICT and 0.12R strategy-family ICT reserves.",
        "- This is bar-based research, not tick-level broker execution. Historical performance is not a guarantee.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    external.DATA = DATA
    external.OUT = OUT
    external.TEST_START = TEST_START
    external.TEST_END = TEST_END
    external.FRESH_START = FRESH_START

    market, quality = external.load_market()
    source = external.build_external_candidates(market)
    source.to_csv(OUT / "all_external_candidates.csv", index=False)

    sleeve_frames: list[pd.DataFrame] = []
    evidence_rows: list[dict[str, Any]] = []
    for sleeve in SLEEVES:
        frame = filter_sleeve(source, sleeve)
        evidence = development_blocks(frame)
        validate_development(sleeve, evidence)
        evidence_rows.append(
            {
                "symbol": sleeve.symbol,
                "mode": sleeve.mode,
                "profile": sleeve.profile,
                "setup": sleeve.setup,
                "risk_percent": sleeve.risk_percent,
                "specification": asdict(sleeve),
                "development_evidence": evidence,
                "fresh_shadow_evidence": ratio_stats(
                    frame[(frame["entry_time"] >= FRESH_START) & (frame["entry_time"] <= TEST_END)]
                ),
            }
        )
        sleeve_frames.append(frame)

    candidates = pd.concat(sleeve_frames, ignore_index=True, sort=False).sort_values(
        ["entry_time", "symbol", "mode", "setup"]
    )
    gated = apply_walk_forward_gate(candidates)
    admitted = portfolio_admission(gated)
    admitted.to_csv(OUT / "walk_forward_gate_and_admission.csv", index=False)
    active = admitted[admitted["portfolio_admitted"]].copy()
    active = active[(active["entry_time"] >= PORTFOLIO_START) & (active["entry_time"] <= TEST_END)]
    swing = active[active["mode"] == "SWING"].copy().sort_values(["entry_time", "symbol"])
    ict = active[active["mode"] == "ICT"].copy().sort_values(["entry_time", "symbol"])
    if swing.empty or ict.empty:
        raise RuntimeError("Walk-forward gate produced an empty swing or ICT portfolio")
    swing.to_csv(OUT / "admitted_swing_candidates.csv", index=False)
    ict.to_csv(OUT / "admitted_ict_candidates.csv", index=False)

    summary, trades, skipped, replay = run_replay(swing, ict)
    trades = external.enrich_closed_trades(trades, pd.concat([swing, ict], ignore_index=True, sort=False))
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(OUT / "closed_drawdown_governor_events.csv", index=False)
    pd.DataFrame(replay.projected_stress_events).to_csv(OUT / "projected_stress_governor_events.csv", index=False)

    monthly, annual = external.time_series(trades)
    monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
    annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
    external.plot_outputs(monthly, annual, trades)

    fresh_trades = trades[
        (pd.to_datetime(trades["entry_time"], utc=True) >= FRESH_START)
        & (pd.to_datetime(trades["entry_time"], utc=True) <= TEST_END)
    ].copy()
    baseline = baseline_v148(source)
    active_swing = sorted(trades[trades["engine_group"] == "V12"]["symbol"].unique().tolist())
    active_ict = sorted(trades[trades["engine_group"] == "ICT"]["symbol"].unique().tolist())
    safe = (
        float(summary["max_closed_drawdown_percent"]) <= 10.0
        and float(summary["stress_drawdown_percent"]) <= 10.0
    )
    payload = {
        "generated_at": datetime.now().isoformat(),
        "provider": "FXCM official weekly H1 candle archive",
        "window": {"start": TEST_START.isoformat(), "end": TEST_END.isoformat()},
        "portfolio_window": {"start": PORTFOLIO_START.isoformat(), "end": TEST_END.isoformat()},
        "development_protocol": {
            "training_end": TRAIN_END.isoformat(),
            "validation_end": VALIDATION_END.isoformat(),
            "audit_end": AUDIT_END.isoformat(),
            "fresh_start": FRESH_START.isoformat(),
            "holdout_not_used_for_sleeve_or_gate_selection": True,
        },
        "walk_forward_gate": {
            "lookback_days": TRAILING_DAYS,
            "minimum_closed_shadow_trades": MIN_TRAILING_TRADES,
            "minimum_net_r": MIN_TRAILING_NET_R,
            "minimum_profit_factor": MIN_TRAILING_PROFIT_FACTOR,
            "inactive_sleeves_continue_shadow_scoring": True,
        },
        "risk_limits": {
            "maximum_swing_trade_percent": 1.20,
            "maximum_ict_trade_percent": 0.60,
            "maximum_ict_open_risk_percent": MAX_ICT_OPEN_RISK,
            "maximum_combined_open_risk_percent": MAX_COMBINED_OPEN_RISK,
            "maximum_stressed_drawdown_percent": 10.0,
        },
        "data_quality": quality,
        "sleeves": evidence_rows,
        "coverage": {
            "candidate_swing_symbols": sorted(swing["symbol"].unique().tolist()),
            "candidate_ict_symbols": sorted(ict["symbol"].unique().tolist()),
            "executed_swing_symbols": active_swing,
            "executed_ict_symbols": active_ict,
            "all_five_symbols_have_swing_and_ict_candidates": set(swing["symbol"]) == set(SYMBOLS) and set(ict["symbol"]) == set(SYMBOLS),
        },
        "baseline_v14_8": baseline,
        "portfolio": {**summary, "safe": safe},
        "fresh_2022_2026": dollar_stats(fresh_trades),
        "attribution": v148.attribution(trades),
        "gate_counts": admitted.groupby(["sleeve_id", "portfolio_admission_reason"]).size().rename("count").reset_index().to_dict("records"),
        "total_modeled_fee_dollars": round(float(trades["modeled_fee_dollars"].sum()), 2),
        "target": {"net_profit": TARGET_NET, "ending_balance": v148.STARTING_BALANCE + TARGET_NET},
        "target_reached": float(summary["net_profit"]) >= TARGET_NET,
        "target_gap": round(max(0.0, TARGET_NET - float(summary["net_profit"])), 2),
        "monthly": monthly.assign(month=monthly["month"].astype(str)).to_dict("records"),
        "annual": annual.to_dict("records"),
    }
    (OUT / "v14_9_fxcm_walk_forward_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    pd.DataFrame(
        [
            {
                "symbol": item["symbol"],
                "mode": item["mode"],
                "profile": item["profile"],
                "setup": item["setup"],
                "risk_percent": item["risk_percent"],
                **{
                    f"{block}_{metric}": value
                    for block, block_values in item["development_evidence"].items()
                    for metric, value in block_values.items()
                },
                **{f"fresh_{metric}": value for metric, value in item["fresh_shadow_evidence"].items()},
            }
            for item in evidence_rows
        ]
    ).to_csv(OUT / "sleeve_evidence.csv", index=False)
    write_report(payload)
    print(json.dumps({
        "portfolio": payload["portfolio"],
        "fresh_2022_2026": payload["fresh_2022_2026"],
        "baseline_v14_8": payload["baseline_v14_8"],
        "coverage": payload["coverage"],
        "target_reached": payload["target_reached"],
        "target_gap": payload["target_gap"],
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
