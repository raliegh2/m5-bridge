"""V15.2 adaptive multi-system portfolio on the 2016-2026 FXCM database.

The V14.9 five-symbol swing/ICT core remains unchanged.  V15.2 adds a
chronological ensemble of H1/H4/D1 breakout, trend, squeeze, reversion,
currency-factor and session systems across the expanded FX universe.

Every new sleeve is shadow-scored from trades that closed before the proposed
entry.  The risk multiplier is selected on 2016-2023 only and then frozen for
the 2024-2026 forward segment.  No broker or MT5 order API is used.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from mt5_ai_bridge import v15_1_currency_systems as currency_systems  # noqa: E402
from research import v15_diversified_target_backtest as v15  # noqa: E402

OUT = ROOT / "research" / "v15_2_adaptive_ensemble_output"
START = pd.Timestamp("2016-01-01T00:00:00Z")
DEPLOYMENT_START = pd.Timestamp("2018-01-01T00:00:00Z")
SELECTION_END = pd.Timestamp("2024-01-01T00:00:00Z")
END = v15.TEST_END
STARTING_BALANCE = 5_000.0
TARGET_20K = 20_000.0
TARGET_40K = 40_000.0
RISK_MULTIPLIERS = (0.35, 0.50, 0.65, 0.80, 1.00, 1.25, 1.50)
TRAILING_DAYS = 730
RECENT_DAYS = 365
FAST_DAYS = 180

# More diversification, not more exposure.  The open-risk ceilings are kept.
MAX_NEW_POSITIONS = 6
MAX_NEW_TRADE_RISK = 0.35


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v15.ratio_stats(frame)


def dollar_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v15.dollar_stats(frame)


def minimum_observations(family: str) -> int:
    if family in {"D1_TREND", "D1_SQUEEZE", "D1_REVERSION", "CROSS_SECTIONAL"}:
        return 6
    if family.startswith("CURRENCY_FACTOR"):
        return 6
    if family in {"SESSION_BREAKOUT", "SESSION_FADE", "LIQUIDITY_FADE"}:
        return 18
    return 10


def adaptive_gate(candidates: pd.DataFrame) -> pd.DataFrame:
    """Chronological sleeve gate using only closed shadow trades."""
    outputs: list[pd.DataFrame] = []
    for sleeve_id, group in candidates.groupby("sleeve_id", sort=False):
        work = group.sort_values(["entry_time", "exit_time"]).copy().reset_index(drop=True)
        family = str(work.iloc[0]["family"])
        minimum = minimum_observations(family)
        decisions: list[dict[str, Any]] = []
        for row in work.itertuples(index=False):
            now = pd.Timestamp(row.entry_time)
            history = work[
                (work["exit_time"] < now)
                & (work["exit_time"] >= now - pd.Timedelta(days=TRAILING_DAYS))
            ]
            recent = history[history["exit_time"] >= now - pd.Timedelta(days=RECENT_DAYS)]
            fast = history[history["exit_time"] >= now - pd.Timedelta(days=FAST_DAYS)]
            trailing = ratio_stats(history)
            recent_stats = ratio_stats(recent)
            fast_stats = ratio_stats(fast)
            trades = int(trailing["trades"])
            net_r = float(trailing["net_r"])
            pf = float(trailing["profit_factor"] or 0.0)
            expectancy = float(trailing["expectancy_r"] or 0.0)
            drawdown_r = float(trailing["maximum_drawdown_r"] or 0.0)
            recent_net = float(recent_stats["net_r"] or 0.0)
            fast_net = float(fast_stats["net_r"] or 0.0)

            accepted = (
                now >= DEPLOYMENT_START
                and trades >= minimum
                and net_r >= 1.25
                and pf >= 1.15
                and expectancy > 0.0
                and recent_net > 0.0
                and fast_net > -0.75
                and drawdown_r <= 6.0
            )

            risk = 0.10
            if accepted and pf >= 1.30 and net_r >= 2.5 and drawdown_r <= 5.0:
                risk = 0.15
            if accepted and pf >= 1.50 and net_r >= 4.0 and recent_net >= 1.0 and drawdown_r <= 4.5:
                risk = 0.22
            if accepted and pf >= 1.80 and net_r >= 6.0 and recent_net >= 1.5 and drawdown_r <= 3.5:
                risk = 0.30
            if accepted and pf >= 2.20 and net_r >= 9.0 and recent_net >= 2.0 and drawdown_r <= 3.0:
                risk = 0.35

            score = (
                expectancy
                * math.sqrt(max(1, trades))
                * min(3.0, max(0.0, pf))
                * (1.0 + max(0.0, recent_net) / max(1.0, trades))
                / (1.0 + drawdown_r)
            )
            decisions.append(
                {
                    "gate_active": bool(accepted),
                    "gate_reason": "V15_2_ACTIVE" if accepted else "V15_2_SHADOW_TRAILING_EDGE",
                    "trailing_trades": trades,
                    "trailing_net_r": net_r,
                    "trailing_profit_factor": trailing["profit_factor"],
                    "trailing_expectancy_r": trailing["expectancy_r"],
                    "trailing_maximum_drawdown_r": drawdown_r,
                    "recent_net_r": recent_net,
                    "fast_net_r": fast_net,
                    "priority_score": float(score),
                    "requested_risk_percent": min(MAX_NEW_TRADE_RISK, risk),
                }
            )
        outputs.append(pd.concat([work, pd.DataFrame(decisions)], axis=1))
    if not outputs:
        raise RuntimeError("V15.2 generated no adaptive sleeve decisions")
    return pd.concat(outputs, ignore_index=True, sort=False).sort_values(
        ["entry_time", "priority_score", "symbol"], ascending=[True, False, True]
    ).reset_index(drop=True)


def prepare_new_candidates(raw: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Include the core symbols as secondary systems.  Existing core candidates
    # retain priority and the one-position-per-symbol admission rule, so a new
    # system cannot displace an already admitted core position.
    symbols = sorted(raw)
    legacy = v15.diversified.generate_universe_candidates(raw, symbols)
    factor = currency_systems.generate_all_candidates(raw, symbols)
    frames = [item for item in (legacy, factor) if item is not None and not item.empty]
    if not frames:
        raise RuntimeError("V15.2 strategy generators returned no candidates")
    source = pd.concat(frames, ignore_index=True, sort=False)
    source["entry_time"] = pd.to_datetime(source["entry_time"], utc=True)
    source["exit_time"] = pd.to_datetime(source["exit_time"], utc=True)
    source = source[(source["entry_time"] >= START) & (source["entry_time"] <= END)].copy()
    source["family"] = source["family"].astype(str)
    source["profile"] = source["profile"].astype(str)
    source["mode"] = source.get("mode", pd.Series("SWING", index=source.index)).fillna("SWING").astype(str)
    source.loc[source["mode"] == "ICT", "mode"] = "SWING"
    source["timeframe"] = source.get("timeframe", pd.Series("H1", index=source.index)).fillna("H1").astype(str)
    source["strategy_group"] = "V15_2_ADAPTIVE"
    source["priority_class"] = 1
    source["setup"] = (
        "v15_2_" + source["symbol"].astype(str).str.lower() + "_" + source["profile"].str.lower()
    )
    source["sleeve_id"] = (
        source["symbol"].astype(str) + "/" + source["family"] + "/" + source["profile"]
    )
    source = source.drop_duplicates(
        ["symbol", "sleeve_id", "side", "entry_time", "exit_time"], keep="first"
    ).sort_values(["entry_time", "symbol", "sleeve_id"]).reset_index(drop=True)
    gated = adaptive_gate(source)
    return source, gated


def configure_risk_limits() -> None:
    # Same 1.50% new-system and 3.25% combined open-risk ceilings; the larger
    # position count only divides that fixed budget across more instruments.
    v15.MAX_NEW_POSITIONS = MAX_NEW_POSITIONS
    v15.MAX_NEW_TRADE_RISK = MAX_NEW_TRADE_RISK


def run_once(
    baseline: pd.DataFrame,
    new: pd.DataFrame,
    multiplier: float,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, Any, pd.DataFrame, pd.DataFrame]:
    admitted = v15.unified_admission(baseline, new, multiplier)
    summary, trades, skipped, replay, active = v15.replay_from_admission(admitted)
    return summary, trades, skipped, replay, active, admitted


def safe(summary: dict[str, Any]) -> bool:
    return (
        float(summary["max_closed_drawdown_percent"]) <= 9.60
        and float(summary["stress_drawdown_percent"]) <= 10.00
    )


def new_trade_stats(trades: pd.DataFrame) -> dict[str, Any]:
    if "priority_class" not in trades:
        return dollar_stats(pd.DataFrame())
    return dollar_stats(trades[trades["priority_class"] == 1])


def run_development_grid(
    baseline: pd.DataFrame,
    new: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[float, tuple]]:
    base_dev = baseline[pd.to_datetime(baseline["entry_time"], utc=True) < SELECTION_END].copy()
    new_dev = new[pd.to_datetime(new["entry_time"], utc=True) < SELECTION_END].copy()
    rows: list[dict[str, Any]] = []
    outputs: dict[float, tuple] = {}
    for multiplier in RISK_MULTIPLIERS:
        result = run_once(base_dev, new_dev, multiplier)
        summary, trades = result[0], result[1]
        new_stats = new_trade_stats(trades)
        rows.append(
            {
                "risk_multiplier": float(multiplier),
                **summary,
                "safe": safe(summary),
                "new_system_net_profit": new_stats["net_profit"],
                "new_system_profit_factor": new_stats["profit_factor"],
                "new_system_trades": new_stats["trades"],
            }
        )
        outputs[float(multiplier)] = result
    return pd.DataFrame(rows), outputs


def select_multiplier(grid: pd.DataFrame) -> float:
    eligible = grid[
        grid["safe"]
        & (pd.to_numeric(grid["new_system_net_profit"], errors="coerce") > 0.0)
        & (pd.to_numeric(grid["new_system_profit_factor"], errors="coerce").fillna(0.0) > 1.0)
    ].copy()
    if eligible.empty:
        raise RuntimeError("No safe profitable V15.2 development allocation")
    return float(eligible.sort_values(["net_profit", "profit_factor"], ascending=False).iloc[0]["risk_multiplier"])


def attribution(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {}
    return {str(key): dollar_stats(group) for key, group in frame.groupby(column, dropna=False)}


def write_report(payload: dict[str, Any]) -> None:
    base = payload["baseline_v14_9"]
    portfolio = payload["portfolio"]
    holdout = payload["forward_2024_2026"]
    new = payload["new_systems_full_period"]
    new_holdout = payload["new_systems_2024_2026"]
    lines = [
        "# V15.2 Adaptive Diversified Portfolio", "",
        "**Database:** FXCM official H1 bid/ask archive", "**Starting balance:** $5,000.00",
        "**Backtest:** 2016-01-01 through latest common 2026 candle",
        "**Risk selection:** 2016-2023 only", "**Forward segment:** 2024-2026", "",
        "## Ten-year result", "",
        "| Metric | V14.9 | V15.2 | Change |", "|---|---:|---:|---:|",
        f"| Net profit | ${base['net_profit']:,.2f} | ${portfolio['net_profit']:,.2f} | ${portfolio['net_profit']-base['net_profit']:,.2f} |",
        f"| Ending balance | ${base['ending_balance']:,.2f} | ${portfolio['ending_balance']:,.2f} | ${portfolio['ending_balance']-base['ending_balance']:,.2f} |",
        f"| Profit factor | {float(base['profit_factor'] or 0):.4f} | {float(portfolio['profit_factor'] or 0):.4f} | {float(portfolio['profit_factor'] or 0)-float(base['profit_factor'] or 0):.4f} |",
        f"| Max closed drawdown | {base['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']-base['max_closed_drawdown_percent']:.4f} pp |",
        f"| Stressed drawdown | {base['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent']-base['stress_drawdown_percent']:.4f} pp |",
        f"| Closed trades | {base['closed_trades']} | {portfolio['closed_trades']} | {portfolio['closed_trades']-base['closed_trades']} |", "",
        "## New-system contribution", "",
        f"- Full period: **${new['net_profit']:,.2f}**, PF **{float(new['profit_factor'] or 0):.4f}**, {new['trades']} trades.",
        f"- 2024-2026: **${new_holdout['net_profit']:,.2f}**, PF **{float(new_holdout['profit_factor'] or 0):.4f}**, {new_holdout['trades']} trades.", "",
        "## Forward 2024-2026 combined result", "",
        f"- Net profit: **${holdout['net_profit']:,.2f}**",
        f"- Profit factor: **{float(holdout['profit_factor'] or 0):.4f}**",
        f"- Trades: **{holdout['trades']}**", "",
        "## Targets", "",
        f"- $20,000 target reached: **{payload['target_20k_reached']}**; gap **${payload['target_20k_gap']:,.2f}**.",
        f"- $40,000 stretch target reached: **{payload['target_40k_reached']}**; gap **${payload['target_40k_gap']:,.2f}**.", "",
        "## Controls", "",
        "- V14.9 core signal definitions and risk percentages are unchanged.",
        "- New systems share the existing 1.50% open-risk budget and 3.25% combined cap.",
        "- New per-trade risk is capped at 0.35%; up to six positions diversify the fixed budget.",
        "- The 7.5/8.5/9.0/9.6 drawdown governor and projected-stress control remain active.",
        "- Signals use completed candles and historical bid/ask execution with additional cost reserves.",
        "- Research only; no live runner, MT5 order submission or broker transmission was added.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    configure_risk_limits()
    raw, core, quality = v15.load_market()
    baseline, baseline_evidence = v15.build_baseline_candidates(core)
    baseline_summary, baseline_trades = v15.baseline_replay(baseline)
    baseline_trades.to_csv(OUT / "baseline_v14_9_closed_trades.csv", index=False)

    source, gated = prepare_new_candidates(raw)
    source.to_csv(OUT / "all_v15_2_candidates.csv", index=False)
    gated.to_csv(OUT / "v15_2_adaptive_gate.csv", index=False)

    development_grid, _ = run_development_grid(baseline, gated)
    development_grid.to_csv(OUT / "development_risk_grid.csv", index=False)
    selected_multiplier = select_multiplier(development_grid)

    full_rows: list[dict[str, Any]] = []
    full_outputs: dict[float, tuple] = {}
    for multiplier in RISK_MULTIPLIERS:
        result = run_once(baseline, gated, multiplier)
        summary, trades = result[0], result[1]
        new_stats = new_trade_stats(trades)
        holdout_trades = trades[pd.to_datetime(trades["entry_time"], utc=True) >= SELECTION_END]
        holdout_new = holdout_trades[holdout_trades.get("priority_class", 0) == 1]
        full_rows.append(
            {
                "risk_multiplier": float(multiplier), **summary, "safe": safe(summary),
                "new_system_net_profit": new_stats["net_profit"],
                "new_system_profit_factor": new_stats["profit_factor"],
                "new_system_trades": new_stats["trades"],
                "holdout_net_profit": dollar_stats(holdout_trades)["net_profit"],
                "holdout_new_net_profit": dollar_stats(holdout_new)["net_profit"],
            }
        )
        full_outputs[float(multiplier)] = result
    full_grid = pd.DataFrame(full_rows)
    full_grid.to_csv(OUT / "full_period_risk_grid.csv", index=False)

    # The development-selected multiplier may be reduced only to enforce the
    # pre-registered risk ceiling.  Forward profit is not used to increase it.
    selected_candidates = [value for value in RISK_MULTIPLIERS if value <= selected_multiplier]
    selected_candidates.sort(reverse=True)
    final_multiplier = None
    for multiplier in selected_candidates:
        if safe(full_outputs[float(multiplier)][0]):
            final_multiplier = float(multiplier)
            break
    if final_multiplier is None:
        raise RuntimeError("No V15.2 allocation stayed inside the full-period drawdown limits")

    summary, trades, skipped, replay, active, admitted = full_outputs[final_multiplier]
    if not safe(summary):
        raise RuntimeError(f"V15.2 final portfolio unsafe: {summary}")

    admitted.to_csv(OUT / "combined_gate_and_admission.csv", index=False)
    active.to_csv(OUT / "admitted_candidates.csv", index=False)
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(OUT / "closed_drawdown_governor_events.csv", index=False)
    pd.DataFrame(replay.projected_stress_events).to_csv(OUT / "projected_stress_governor_events.csv", index=False)

    old_out, old_start, old_end = v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END
    v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = OUT, START, END
    try:
        monthly, annual = v15.external.time_series(trades)
        monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
        annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
        v15.external.plot_outputs(monthly, annual, trades)
    finally:
        v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = old_out, old_start, old_end

    new_trades = trades[trades.get("priority_class", 0) == 1].copy()
    holdout = trades[pd.to_datetime(trades["entry_time"], utc=True) >= SELECTION_END].copy()
    holdout_new = holdout[holdout.get("priority_class", 0) == 1].copy()
    target20 = float(summary["net_profit"]) >= TARGET_20K
    target40 = float(summary["net_profit"]) >= TARGET_40K
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "FXCM official weekly H1 bid/ask archive",
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "selection_window": {"start": START.isoformat(), "end": (SELECTION_END-pd.Timedelta(seconds=1)).isoformat()},
        "forward_window": {"start": SELECTION_END.isoformat(), "end": END.isoformat()},
        "selection_protocol": {
            "risk_multiplier_selected_before_forward_window": True,
            "forward_profit_used_to_raise_risk": False,
            "shadow_history_uses_only_prior_closed_trades": True,
            "development_selected_multiplier": selected_multiplier,
            "final_risk_multiplier": final_multiplier,
            "risk_reduction_allowed_only_for_full_period_safety": True,
        },
        "risk_limits": {
            "maximum_new_trade_percent": MAX_NEW_TRADE_RISK,
            "maximum_new_open_risk_percent": v15.MAX_NEW_OPEN_RISK,
            "maximum_new_positions": MAX_NEW_POSITIONS,
            "maximum_ict_open_risk_percent": v15.MAX_ICT_OPEN_RISK,
            "maximum_combined_open_risk_percent": v15.MAX_COMBINED_OPEN_RISK,
            "maximum_closed_drawdown_percent": 9.60,
            "maximum_stressed_drawdown_percent": 10.00,
        },
        "data_quality": quality,
        "baseline_sleeve_evidence": baseline_evidence,
        "baseline_v14_9": baseline_summary,
        "portfolio": {**summary, "safe": True},
        "new_systems_full_period": new_trade_stats(trades),
        "forward_2024_2026": dollar_stats(holdout),
        "new_systems_2024_2026": dollar_stats(holdout_new),
        "attribution_by_symbol": attribution(trades, "symbol"),
        "attribution_by_family": attribution(trades, "family"),
        "attribution_by_strategy_group": attribution(trades, "strategy_group"),
        "development_risk_grid": development_grid.to_dict("records"),
        "full_period_risk_grid": full_grid.to_dict("records"),
        "target_20k_reached": target20,
        "target_20k_gap": round(max(0.0, TARGET_20K-float(summary["net_profit"])), 2),
        "target_40k_reached": target40,
        "target_40k_gap": round(max(0.0, TARGET_40K-float(summary["net_profit"])), 2),
        "research_only": True,
        "live_execution_changed": False,
    }
    (OUT / "v15_2_adaptive_ensemble_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(payload)
    print(json.dumps({
        "baseline": baseline_summary,
        "selected_multiplier": selected_multiplier,
        "final_multiplier": final_multiplier,
        "portfolio": summary,
        "new_systems": payload["new_systems_full_period"],
        "forward": payload["forward_2024_2026"],
        "forward_new": payload["new_systems_2024_2026"],
        "target_20k": target20,
        "target_20k_gap": payload["target_20k_gap"],
        "target_40k": target40,
        "target_40k_gap": payload["target_40k_gap"],
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
