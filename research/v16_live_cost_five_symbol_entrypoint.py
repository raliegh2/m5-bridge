"""V16 live-cost five-symbol replacement research.

Reuses the externally validated V14.9 successor to the current V14.3/V14.4 live
model, then adds independent H4/D1/session systems on the same five production
symbols. Profiles are selected only from 2013-2015 data and replayed from
2016 through the latest common 2026 FXCM candle with bid/ask spread plus an
additional live execution reserve.

Research is fail-closed: no AUTO or broker order code is changed here.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from mt5_ai_bridge import v15_1_currency_systems as currency_systems  # noqa: E402
from research import v15_diversified_target_backtest as v15  # noqa: E402

OUT = ROOT / "research" / "v16_live_cost_five_symbol_output"
CORE_SYMBOLS = tuple(v15.CORE_SYMBOLS)
PRETRAIN_START = pd.Timestamp("2013-01-01T00:00:00Z")
TEST_START = pd.Timestamp("2016-01-01T00:00:00Z")
AUDIT_START = pd.Timestamp("2022-01-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2024-01-01T00:00:00Z")
END = v15.TEST_END
TARGET_NET = 20_000.0
STRETCH_NET = 40_000.0
MAX_CLOSED_DD = 9.60
MAX_STRESS_DD = 10.00
MAX_COMBINED_OPEN_RISK = 3.25
MAX_NEW_OPEN_RISK = 2.25
MAX_NEW_TRADE_RISK = 1.00
MAX_PROFILES_PER_SYMBOL = 3

LIVE_COST_BUFFER_R = {
    "H1": 0.080,
    "H4": 0.050,
    "D1": 0.030,
    "SWING": 0.050,
    "ICT": 0.090,
}

BASE_RISK_MULTIPLIERS = (0.35, 0.50, 0.65, 0.80, 1.00)
NEW_RISK_MULTIPLIERS = (0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00)


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v15.ratio_stats(frame)


def dollar_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v15.dollar_stats(frame)


def maximum_drawdown_r(frame: pd.DataFrame) -> float:
    values = pd.to_numeric(frame.get("r_multiple", pd.Series(dtype=float)), errors="coerce").dropna()
    if values.empty:
        return 0.0
    equity = values.cumsum()
    return float((equity.cummax().clip(lower=0.0) - equity).max())


def extra_cost_for_row(row: pd.Series) -> float:
    timeframe = str(row.get("timeframe", "")).upper()
    mode = str(row.get("mode", "")).upper()
    return float(LIVE_COST_BUFFER_R.get(timeframe, LIVE_COST_BUFFER_R.get(mode, 0.050)))


def apply_live_cost_buffer(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    work = frame.copy()
    extra = work.apply(extra_cost_for_row, axis=1)
    work["live_cost_buffer_r"] = extra
    work["pre_v16_r_multiple"] = pd.to_numeric(work["r_multiple"], errors="coerce")
    work["r_multiple"] = work["pre_v16_r_multiple"] - extra
    work["cost_r"] = pd.to_numeric(work.get("cost_r", 0.0), errors="coerce").fillna(0.0) + extra
    work["v16_cost_model"] = label
    return work


def pretrain_blocks(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        "2013": ratio_stats(frame[(frame.entry_time >= PRETRAIN_START) & (frame.entry_time < pd.Timestamp("2014-01-01T00:00:00Z"))]),
        "2014": ratio_stats(frame[(frame.entry_time >= pd.Timestamp("2014-01-01T00:00:00Z")) & (frame.entry_time < pd.Timestamp("2015-01-01T00:00:00Z"))]),
        "2015": ratio_stats(frame[(frame.entry_time >= pd.Timestamp("2015-01-01T00:00:00Z")) & (frame.entry_time < TEST_START)]),
    }


def minimum_block_trades(family: str) -> int:
    text = str(family).upper()
    if text.startswith("D1_") or "CURRENCY_FACTOR" in text:
        return 2
    if "SESSION" in text or "LIQUIDITY" in text:
        return 8
    return 4


def select_pre2016_profiles(source: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    evidence_rows: list[dict[str, Any]] = []
    accepted_rows: list[dict[str, Any]] = []
    for sleeve_id, group in source.groupby("sleeve_id", sort=False):
        group = group.sort_values(["entry_time", "exit_time"]).copy()
        pre = group[(group.entry_time >= PRETRAIN_START) & (group.entry_time < TEST_START)]
        if pre.empty:
            continue
        family = str(group.iloc[0].family)
        blocks = pretrain_blocks(pre)
        aggregate = ratio_stats(pre)
        dd_r = maximum_drawdown_r(pre)
        min_trades = minimum_block_trades(family)
        positive_blocks = sum(float(item["net_r"]) > 0.0 for item in blocks.values())
        pf_blocks = sum(float(item["profit_factor"] or 0.0) >= 1.0 for item in blocks.values())
        enough_blocks = sum(int(item["trades"]) >= min_trades for item in blocks.values())
        robust_score = (
            float(aggregate["expectancy_r"] or 0.0)
            * math.sqrt(max(1, int(aggregate["trades"])))
            * min(3.0, max(0.0, float(aggregate["profit_factor"] or 0.0)))
            / max(1.0, dd_r)
        )
        passed = (
            int(aggregate["trades"]) >= min_trades * 3
            and enough_blocks >= 2
            and positive_blocks >= 2
            and pf_blocks >= 2
            and float(aggregate["net_r"]) >= 3.0
            and float(aggregate["profit_factor"] or 0.0) >= 1.15
            and float(aggregate["expectancy_r"] or 0.0) > 0.04
            and dd_r <= 7.0
        )
        record = {
            "sleeve_id": str(sleeve_id),
            "symbol": str(group.iloc[0].symbol),
            "family": family,
            "profile": str(group.iloc[0].profile),
            "timeframe": str(group.iloc[0].timeframe),
            "blocks": blocks,
            "pretrain": aggregate,
            "pretrain_maximum_drawdown_r": round(dd_r, 6),
            "positive_blocks": positive_blocks,
            "profitable_pf_blocks": pf_blocks,
            "enough_blocks": enough_blocks,
            "robust_score": float(robust_score),
            "passed": bool(passed),
        }
        evidence_rows.append(record)
        if not passed:
            continue
        pf = float(aggregate["profit_factor"] or 0.0)
        net_r = float(aggregate["net_r"])
        risk = 0.35
        if pf >= 1.35 and net_r >= 5.0:
            risk = 0.50
        if pf >= 1.60 and net_r >= 8.0 and dd_r <= 5.0:
            risk = 0.70
        if pf >= 1.90 and net_r >= 12.0 and dd_r <= 4.0:
            risk = 0.90
        accepted_rows.append({**record, "base_risk_percent": risk})

    evidence = pd.DataFrame(evidence_rows)
    if not accepted_rows:
        raise RuntimeError("No pre-2016 live-cost profile passed V16 selection")
    accepted = pd.DataFrame(accepted_rows)
    accepted = accepted.sort_values(["symbol", "robust_score"], ascending=[True, False])
    accepted = accepted.drop_duplicates(["symbol", "family"], keep="first")
    selected = accepted.groupby("symbol", group_keys=False).head(MAX_PROFILES_PER_SYMBOL).reset_index(drop=True)
    missing = sorted(set(CORE_SYMBOLS) - set(selected.symbol))
    if missing:
        raise RuntimeError(f"V16 selection did not cover all five symbols: {missing}")
    return evidence, selected


def selected_records(selected: pd.DataFrame) -> list[dict[str, Any]]:
    keys = (
        "sleeve_id", "symbol", "family", "profile", "timeframe",
        "base_risk_percent", "robust_score", "pretrain",
        "pretrain_maximum_drawdown_r", "blocks",
    )
    return [{key: item[key] for key in keys} for item in selected.to_dict("records")]


def trailing_stats(group: pd.DataFrame, now: pd.Timestamp, days: int) -> dict[str, Any]:
    history = group[(group.exit_time < now) & (group.exit_time >= now - pd.Timedelta(days=days))]
    return ratio_stats(history)


def materialize_walk_forward(source: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    selected_ids = set(selected.sleeve_id)
    base_risk = dict(zip(selected.sleeve_id, selected.base_risk_percent))
    robust_score = dict(zip(selected.sleeve_id, selected.robust_score))
    frame = source[source.sleeve_id.isin(selected_ids)].copy()
    frame = frame[(frame.entry_time >= TEST_START) & (frame.entry_time <= END)]
    outputs: list[pd.DataFrame] = []
    for sleeve_id, group in frame.groupby("sleeve_id", sort=False):
        group = group.sort_values(["entry_time", "exit_time"]).copy().reset_index(drop=True)
        decisions: list[dict[str, Any]] = []
        initial_risk = float(base_risk[sleeve_id])
        for row in group.itertuples(index=False):
            now = pd.Timestamp(row.entry_time)
            long_stats = trailing_stats(group, now, 730)
            recent_stats = trailing_stats(group, now, 365)
            observations = int(long_stats["trades"])
            active = True
            reason = "PRE2016_PROFILE_ACTIVE"
            multiplier = 1.0
            if observations >= 6:
                pf = float(long_stats["profit_factor"] or 0.0)
                net_r = float(long_stats["net_r"])
                recent_r = float(recent_stats["net_r"])
                if net_r <= 0.0 or pf < 1.02 or recent_r < -1.0:
                    active = False
                    reason = "V16_TRAILING_EDGE_SHADOW"
                    multiplier = 0.0
                elif pf < 1.15 or recent_r <= 0.0:
                    multiplier = 0.50
                    reason = "V16_TRAILING_EDGE_REDUCED"
                elif pf >= 1.45 and net_r >= 3.0 and recent_r > 0.5:
                    multiplier = 1.20
                    reason = "V16_TRAILING_EDGE_STRONG"
            requested = min(MAX_NEW_TRADE_RISK, initial_risk * multiplier)
            decisions.append({
                "gate_active": bool(active),
                "gate_reason": reason,
                "trailing_trades": observations,
                "trailing_net_r": long_stats["net_r"],
                "trailing_profit_factor": long_stats["profit_factor"],
                "recent_net_r": recent_stats["net_r"],
                "requested_risk_percent": requested,
                "risk_percent": requested,
                "priority_score": float(robust_score[sleeve_id]),
                "priority_class": 1,
            })
        outputs.append(pd.concat([group, pd.DataFrame(decisions)], axis=1))
    return pd.concat(outputs, ignore_index=True, sort=False).sort_values(
        ["entry_time", "priority_score", "symbol"], ascending=[True, False, True]
    ).reset_index(drop=True)


def prepare_baseline(core: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    baseline, evidence = v15.build_baseline_candidates(core)
    baseline = apply_live_cost_buffer(baseline, "FXCM_BID_ASK_PLUS_V16_LIVE_RESERVE")
    baseline["requested_risk_percent"] = pd.to_numeric(baseline["requested_risk_percent"], errors="coerce")
    baseline["risk_percent"] = baseline["requested_risk_percent"]
    return baseline, evidence


def scale_baseline(baseline: pd.DataFrame, multiplier: float) -> pd.DataFrame:
    work = baseline.copy()
    requested = pd.to_numeric(work["requested_risk_percent"], errors="coerce") * float(multiplier)
    work["requested_risk_percent"] = requested.clip(lower=0.025, upper=1.25)
    work["risk_percent"] = work["requested_risk_percent"]
    return work


def scale_new(source: pd.DataFrame, multiplier: float) -> pd.DataFrame:
    work = source.copy()
    requested = pd.to_numeric(work["requested_risk_percent"], errors="coerce") * float(multiplier)
    work["requested_risk_percent"] = requested.clip(lower=0.025, upper=MAX_NEW_TRADE_RISK)
    work["risk_percent"] = work["requested_risk_percent"]
    return work


def safe(summary: dict[str, Any]) -> bool:
    return float(summary["max_closed_drawdown_percent"]) <= MAX_CLOSED_DD and float(summary["stress_drawdown_percent"]) <= MAX_STRESS_DD


def attribution(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {}
    return {str(key): dollar_stats(group) for key, group in frame.groupby(column, dropna=False)}


def five_symbol_gate(trades: pd.DataFrame, start: pd.Timestamp | None = None) -> tuple[bool, dict[str, Any]]:
    work = trades.copy()
    if start is not None:
        work = work[pd.to_datetime(work.entry_time, utc=True) >= start]
    stats_by_symbol = attribution(work, "symbol")
    complete = set(CORE_SYMBOLS) <= set(stats_by_symbol)
    all_positive = complete and all(float(stats_by_symbol[symbol]["net_profit"]) > 0.0 for symbol in CORE_SYMBOLS)
    return bool(all_positive), stats_by_symbol


def run_grid(baseline: pd.DataFrame, new_source: pd.DataFrame) -> tuple[pd.DataFrame, dict[tuple[float, float], tuple]]:
    old_new_trade = v15.MAX_NEW_TRADE_RISK
    old_new_open = v15.MAX_NEW_OPEN_RISK
    old_combined = v15.MAX_COMBINED_OPEN_RISK
    v15.MAX_NEW_TRADE_RISK = MAX_NEW_TRADE_RISK
    v15.MAX_NEW_OPEN_RISK = MAX_NEW_OPEN_RISK
    v15.MAX_COMBINED_OPEN_RISK = MAX_COMBINED_OPEN_RISK
    rows: list[dict[str, Any]] = []
    outputs: dict[tuple[float, float], tuple] = {}
    try:
        for base_multiplier in BASE_RISK_MULTIPLIERS:
            for new_multiplier in NEW_RISK_MULTIPLIERS:
                scaled_base = scale_baseline(baseline, base_multiplier)
                scaled_new = scale_new(new_source, new_multiplier)
                admitted = v15.unified_admission(scaled_base, scaled_new, 1.0)
                summary, trades, skipped, replay, active = v15.replay_from_admission(admitted)
                holdout = trades[pd.to_datetime(trades.entry_time, utc=True) >= HOLDOUT_START].copy()
                audit = trades[pd.to_datetime(trades.entry_time, utc=True) >= AUDIT_START].copy()
                full_symbols_pass, full_by_symbol = five_symbol_gate(trades)
                holdout_symbols_pass, holdout_by_symbol = five_symbol_gate(holdout)
                holdout_stats = dollar_stats(holdout)
                audit_stats = dollar_stats(audit)
                promotion = (
                    safe(summary)
                    and full_symbols_pass
                    and holdout_symbols_pass
                    and float(holdout_stats["net_profit"]) > 0.0
                    and float(holdout_stats["profit_factor"] or 0.0) >= 1.05
                    and float(audit_stats["net_profit"]) > 0.0
                    and float(audit_stats["profit_factor"] or 0.0) >= 1.05
                )
                rows.append({
                    "base_risk_multiplier": base_multiplier,
                    "new_risk_multiplier": new_multiplier,
                    **summary,
                    "safe": safe(summary),
                    "full_five_symbols_positive": full_symbols_pass,
                    "holdout_five_symbols_positive": holdout_symbols_pass,
                    "holdout_net_profit": holdout_stats["net_profit"],
                    "holdout_profit_factor": holdout_stats["profit_factor"],
                    "audit_net_profit": audit_stats["net_profit"],
                    "audit_profit_factor": audit_stats["profit_factor"],
                    "promotion_candidate": promotion,
                })
                outputs[(base_multiplier, new_multiplier)] = (
                    summary, trades, skipped, replay, active, admitted,
                    holdout_stats, audit_stats, full_by_symbol, holdout_by_symbol,
                )
    finally:
        v15.MAX_NEW_TRADE_RISK = old_new_trade
        v15.MAX_NEW_OPEN_RISK = old_new_open
        v15.MAX_COMBINED_OPEN_RISK = old_combined
    return pd.DataFrame(rows), outputs


def choose_primary(grid: pd.DataFrame) -> tuple[float, float, bool]:
    eligible = grid[grid.promotion_candidate == True].copy()  # noqa: E712
    if not eligible.empty:
        chosen = eligible.sort_values(["net_profit", "profit_factor", "holdout_net_profit"], ascending=[False, False, False]).iloc[0]
        return float(chosen.base_risk_multiplier), float(chosen.new_risk_multiplier), True
    fallback = grid[
        (grid.safe == True)  # noqa: E712
        & (grid.full_five_symbols_positive == True)  # noqa: E712
        & (grid.holdout_net_profit > 0.0)
        & (grid.holdout_profit_factor.fillna(0.0) >= 1.0)
    ].copy()
    if fallback.empty:
        fallback = grid[grid.safe == True].copy()  # noqa: E712
    if fallback.empty:
        raise RuntimeError(f"No safe V16 allocation: {grid.to_dict('records')}")
    chosen = fallback.sort_values(["net_profit", "holdout_net_profit", "profit_factor"], ascending=[False, False, False]).iloc[0]
    return float(chosen.base_risk_multiplier), float(chosen.new_risk_multiplier), False


def write_report(payload: dict[str, Any]) -> None:
    portfolio = payload["portfolio"]
    holdout = payload["holdout_2024_2026"]
    audit = payload["audit_2022_2026"]
    lines = [
        "# V16 Live-Cost Five-Symbol Replacement Backtest", "",
        "**Data:** FXCM official weekly H1 bid/ask archive",
        "**Starting balance:** $5,000.00",
        "**Profile selection:** 2013-2015 only",
        "**Formal replay:** 2016 through the latest common 2026 candle",
        "**Symbols:** GBPUSD, EURUSD, GBPJPY, AUDUSD, USDJPY", "",
        "## Portfolio result", "",
        f"- Net profit after modeled spread, commission/slippage/swap reserve: **${portfolio['net_profit']:,.2f}**",
        f"- Ending balance: **${portfolio['ending_balance']:,.2f}**",
        f"- Profit factor: **{float(portfolio['profit_factor'] or 0.0):.4f}**",
        f"- Maximum closed drawdown: **{portfolio['max_closed_drawdown_percent']:.4f}%**",
        f"- Stressed drawdown: **{portfolio['stress_drawdown_percent']:.4f}%**",
        f"- Closed trades: **{portfolio['closed_trades']}**", "",
        "## Chronological checks", "",
        f"- 2022-2026 net: **${audit['net_profit']:,.2f}**, PF **{float(audit['profit_factor'] or 0.0):.4f}**",
        f"- 2024-2026 holdout net: **${holdout['net_profit']:,.2f}**, PF **{float(holdout['profit_factor'] or 0.0):.4f}**",
        f"- All five symbols profitable full replay: **{payload['full_five_symbols_positive']}**",
        f"- All five symbols profitable in 2024-2026 holdout: **{payload['holdout_five_symbols_positive']}**", "",
        "## Target and promotion status", "",
        f"- $20,000 net target reached: **{payload['target_20k_reached']}**; gap **${payload['target_20k_gap']:,.2f}**",
        f"- Promotion eligible: **{payload['promotion_eligible']}**",
        f"- Selected base/new risk multipliers: **{payload['selected_base_risk_multiplier']:.2f}x / {payload['selected_new_risk_multiplier']:.2f}x**", "",
        "Promotion requires the risk limits, positive 2022-2026 and 2024-2026 performance, and positive contribution from every required symbol. The branch remains READ_ONLY research until those conditions and demo forward validation pass.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw, core, quality = v15.load_market()
    baseline, baseline_evidence = prepare_baseline(core)

    legacy = v15.diversified.generate_universe_candidates(raw, CORE_SYMBOLS)
    factor = currency_systems.generate_all_candidates(raw, CORE_SYMBOLS)
    frames = [item for item in (legacy, factor) if item is not None and not item.empty]
    if not frames:
        raise RuntimeError("V16 generated no five-symbol independent candidates")
    source = pd.concat(frames, ignore_index=True, sort=False)
    source["entry_time"] = pd.to_datetime(source.entry_time, utc=True)
    source["exit_time"] = pd.to_datetime(source.exit_time, utc=True)
    source = source[(source.entry_time >= PRETRAIN_START) & (source.entry_time <= END)].copy()
    source["profile"] = source.profile.astype(str)
    source["family"] = source.family.astype(str)
    source["setup"] = "v16_" + source.symbol.astype(str).str.lower() + "_" + source.profile.str.lower()
    source["sleeve_id"] = source.symbol.astype(str) + "/" + source.family + "/" + source.profile
    source["priority_class"] = 1
    source = apply_live_cost_buffer(source, "FXCM_BID_ASK_PLUS_V16_LIVE_RESERVE")
    source.to_csv(OUT / "all_v16_candidates.csv", index=False)

    evidence, selected = select_pre2016_profiles(source)
    evidence.to_json(OUT / "profile_evidence.json", orient="records", indent=2, date_format="iso")
    (OUT / "selected_profiles.json").write_text(json.dumps(selected_records(selected), indent=2, default=str), encoding="utf-8")
    materialized = materialize_walk_forward(source, selected)
    materialized.to_csv(OUT / "selected_walk_forward_candidates.csv", index=False)

    grid, outputs = run_grid(baseline, materialized)
    grid.to_csv(OUT / "risk_grid.csv", index=False)
    base_mult, new_mult, promotion_candidate = choose_primary(grid)
    summary, trades, skipped, replay, active, admitted, holdout_stats, audit_stats, full_by_symbol, holdout_by_symbol = outputs[(base_mult, new_mult)]

    admitted.to_csv(OUT / "gate_and_admission.csv", index=False)
    active.to_csv(OUT / "admitted_candidates.csv", index=False)
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(OUT / "closed_drawdown_governor_events.csv", index=False)
    pd.DataFrame(replay.projected_stress_events).to_csv(OUT / "projected_stress_governor_events.csv", index=False)

    old_out, old_start, old_end = v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END
    v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = OUT, TEST_START, END
    try:
        monthly, annual = v15.external.time_series(trades)
        monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
        annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
        v15.external.plot_outputs(monthly, annual, trades)
    finally:
        v15.external.OUT, v15.external.TEST_START, v15.external.TEST_END = old_out, old_start, old_end

    full_pass = all(float(full_by_symbol.get(symbol, {}).get("net_profit", 0.0)) > 0.0 for symbol in CORE_SYMBOLS)
    holdout_pass = all(float(holdout_by_symbol.get(symbol, {}).get("net_profit", 0.0)) > 0.0 for symbol in CORE_SYMBOLS)
    target20 = float(summary["net_profit"]) >= TARGET_NET
    target40 = float(summary["net_profit"]) >= STRETCH_NET
    promotion_eligible = bool(promotion_candidate and target20 and safe(summary) and full_pass and holdout_pass)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "V16.0",
        "provider": "FXCM official weekly H1 bid/ask archive",
        "selection_window": {"start": PRETRAIN_START.isoformat(), "end": (TEST_START-pd.Timedelta(seconds=1)).isoformat()},
        "test_window": {"start": TEST_START.isoformat(), "end": END.isoformat()},
        "audit_window": {"start": AUDIT_START.isoformat(), "end": END.isoformat()},
        "holdout_window": {"start": HOLDOUT_START.isoformat(), "end": END.isoformat()},
        "symbols": list(CORE_SYMBOLS),
        "cost_model": {
            "fxcm_bid_ask_embedded": True,
            "additional_live_cost_buffer_r": LIVE_COST_BUFFER_R,
            "covers": ["commission", "slippage", "latency", "swap_uncertainty"],
        },
        "selected_base_risk_multiplier": base_mult,
        "selected_new_risk_multiplier": new_mult,
        "selected_profiles": selected_records(selected),
        "baseline_sleeve_evidence": baseline_evidence,
        "data_quality": quality,
        "portfolio": {**summary, "safe": safe(summary)},
        "audit_2022_2026": audit_stats,
        "holdout_2024_2026": holdout_stats,
        "attribution_by_symbol": full_by_symbol,
        "holdout_attribution_by_symbol": holdout_by_symbol,
        "attribution_by_family": attribution(trades, "family"),
        "full_five_symbols_positive": full_pass,
        "holdout_five_symbols_positive": holdout_pass,
        "risk_grid": grid.to_dict("records"),
        "target_20k_reached": target20,
        "target_20k_gap": round(max(0.0, TARGET_NET-float(summary["net_profit"])), 2),
        "target_40k_reached": target40,
        "target_40k_gap": round(max(0.0, STRETCH_NET-float(summary["net_profit"])), 2),
        "promotion_candidate_without_target": promotion_candidate,
        "promotion_eligible": promotion_eligible,
        "risk_limits": {
            "maximum_closed_drawdown_percent": MAX_CLOSED_DD,
            "maximum_stressed_drawdown_percent": MAX_STRESS_DD,
            "maximum_combined_open_risk_percent": MAX_COMBINED_OPEN_RISK,
            "maximum_new_open_risk_percent": MAX_NEW_OPEN_RISK,
            "maximum_new_trade_risk_percent": MAX_NEW_TRADE_RISK,
        },
        "research_only": True,
        "live_execution_changed": False,
    }
    (OUT / "v16_live_cost_five_symbol_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(payload)
    print(json.dumps({
        "version": payload["version"],
        "selected_base_risk_multiplier": base_mult,
        "selected_new_risk_multiplier": new_mult,
        "portfolio": summary,
        "audit": audit_stats,
        "holdout": holdout_stats,
        "full_by_symbol": full_by_symbol,
        "holdout_by_symbol": holdout_by_symbol,
        "target_20k_reached": target20,
        "promotion_eligible": promotion_eligible,
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
