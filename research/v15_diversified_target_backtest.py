"""V15 diversified research portfolio targeting $20k/$40k ten-year profit.

The frozen V14.9 core remains unchanged. New capital is permitted only on
non-core instruments and only after a chronological trailing edge gate. New
systems use an expanded FXCM bid/ask universe, London/New York entries, direct
spread-aware simulation, bounded risk and the existing drawdown governors.

Research only. No MT5 account, broker connection or order transmission.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from mt5_ai_bridge import v15_diversified_strategies as diversified  # noqa: E402
from research import v14_8_dukascopy_2016_2026_backtest as external  # noqa: E402
from research import v14_9_fxcm_walk_forward as v149  # noqa: E402

DATA = ROOT / "research" / "fxcm_diversified_2013_2026_data"
OUT = ROOT / "research" / "v15_diversified_target_output"
WARMUP_START = pd.Timestamp("2013-01-01T00:00:00Z")
TEST_START = pd.Timestamp("2016-01-01T00:00:00Z")
TEST_END = pd.Timestamp("2026-05-31T23:59:59Z")
FINAL_HOLDOUT_START = pd.Timestamp("2024-01-01T00:00:00Z")
TARGET_NET_20K = 20_000.0
TARGET_NET_40K = 40_000.0
STARTING_BALANCE = 5_000.0
MAX_COMBINED_OPEN_RISK = 3.25
MAX_ICT_OPEN_RISK = 1.75
MAX_NEW_OPEN_RISK = 1.50
MAX_NEW_POSITIONS = 3
MAX_NEW_TRADE_RISK = 0.50
MAX_NEW_CURRENCY_EXPOSURE = 1.75
TRAILING_DAYS = 730
RECENT_DAYS = 365
DEPLOYMENT_START = TEST_START
RISK_MULTIPLIERS = (0.75, 1.00, 1.25)
CORE_SYMBOLS = tuple(v149.SYMBOLS)


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    values = pd.to_numeric(frame.get("r_multiple", pd.Series(dtype=float)), errors="coerce").dropna()
    if values.empty:
        return {
            "trades": 0,
            "net_r": 0.0,
            "expectancy_r": None,
            "profit_factor": None,
            "win_rate": None,
            "maximum_drawdown_r": 0.0,
        }
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    equity = values.cumsum()
    peak = equity.cummax().clip(lower=0.0)
    dd = peak - equity
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else 99.0,
        "win_rate": round(float((values > 0).mean()), 6),
        "maximum_drawdown_r": round(float(dd.max()), 6),
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


def load_h1(symbol: str, side: str) -> pd.DataFrame:
    path = DATA / f"{symbol}_H1_{side}.csv"
    frame = pd.read_csv(path)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "tick_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    frame = frame[(frame["time"] >= WARMUP_START) & (frame["time"] <= TEST_END)]
    return frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def load_market() -> tuple[
    dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    dict[str, Any],
]:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    available = sorted(manifest["available"])
    missing_core = sorted(set(CORE_SYMBOLS) - set(available))
    if missing_core:
        raise RuntimeError(f"Expanded FXCM dataset is missing core symbols: {missing_core}")

    raw: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    quality: dict[str, Any] = {}
    for symbol in available:
        bid = load_h1(symbol, "bid")
        ask = load_h1(symbol, "ask")
        joined = bid[["time", "open"]].merge(
            ask[["time", "open"]], on="time", suffixes=("_bid", "_ask"), how="inner"
        )
        spread = (joined["open_ask"] - joined["open_bid"]).replace([np.inf, -np.inf], np.nan).dropna()
        raw[symbol] = (bid, ask)
        quality[symbol] = {
            "bid_bars": int(len(bid)),
            "ask_bars": int(len(ask)),
            "start": max(bid["time"].min(), ask["time"].min()).isoformat(),
            "end": min(bid["time"].max(), ask["time"].max()).isoformat(),
            "mean_absolute_spread_price": round(float(spread.mean()), 8) if not spread.empty else None,
            "p90_absolute_spread_price": round(float(spread.quantile(0.90)), 8) if not spread.empty else None,
        }

    core: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = {}
    for symbol in CORE_SYMBOLS:
        bid, _ = raw[symbol]
        core[symbol] = (
            bid,
            external.resample_ohlc(bid, "4h"),
            external.resample_ohlc(bid, "1D"),
        )
    return raw, core, {"download_manifest": manifest, "symbols": quality}


def build_baseline_candidates(core_market: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    old_start, old_end = external.TEST_START, external.TEST_END
    external.TEST_START = pd.Timestamp("2016-01-01T00:00:00Z")
    external.TEST_END = min(TEST_END, pd.Timestamp("2026-05-31T23:59:59Z"))
    try:
        source = external.build_external_candidates(core_market)
    finally:
        external.TEST_START, external.TEST_END = old_start, old_end

    frames: list[pd.DataFrame] = []
    evidence_rows: list[dict[str, Any]] = []
    for sleeve in v149.SLEEVES:
        frame = v149.filter_sleeve(source, sleeve)
        evidence = v149.development_blocks(frame)
        v149.validate_development(sleeve, evidence)
        frame["strategy_group"] = np.where(frame["mode"] == "ICT", "V14_9_ICT", "V14_9_SWING")
        frame["priority_class"] = 0
        frames.append(frame)
        evidence_rows.append(
            {
                "symbol": sleeve.symbol,
                "mode": sleeve.mode,
                "profile": sleeve.profile,
                "setup": sleeve.setup,
                "risk_percent": sleeve.risk_percent,
                "development_evidence": evidence,
            }
        )
    candidates = pd.concat(frames, ignore_index=True, sort=False)
    gated = v149.apply_walk_forward_gate(candidates)
    gated["priority_score"] = pd.to_numeric(gated["gate_score"], errors="coerce").fillna(0.0)
    gated["requested_risk_percent"] = pd.to_numeric(gated["risk_percent"], errors="coerce")
    return gated, evidence_rows


def dynamic_new_gate(candidates: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for sleeve_id, group in candidates.groupby("sleeve_id", sort=False):
        work = group.sort_values(["entry_time", "exit_time"]).copy().reset_index(drop=True)
        decisions: list[dict[str, Any]] = []
        family = str(work.iloc[0]["family"])
        minimum_trades = 5 if family in {"D1_TREND", "D1_SQUEEZE", "D1_REVERSION", "CROSS_SECTIONAL"} else 8
        for row in work.itertuples(index=False):
            now = pd.Timestamp(row.entry_time)
            history = work[
                (work["exit_time"] < now)
                & (work["exit_time"] >= now - pd.Timedelta(days=TRAILING_DAYS))
            ]
            recent = history[history["exit_time"] >= now - pd.Timedelta(days=RECENT_DAYS)]
            stats = ratio_stats(history)
            recent_stats = ratio_stats(recent)
            pf = float(stats["profit_factor"] or 0.0)
            net_r = float(stats["net_r"])
            accepted = (
                now >= DEPLOYMENT_START
                and int(stats["trades"]) >= minimum_trades
                and net_r >= 1.0
                and pf >= 1.15
                and float(recent_stats["net_r"]) > 0.0
                and float(stats["maximum_drawdown_r"]) <= 6.0
            )
            risk = 0.20
            if accepted and pf >= 1.35 and net_r >= 2.0:
                risk = 0.30
            if accepted and pf >= 1.60 and net_r >= 4.0 and float(stats["maximum_drawdown_r"]) <= 4.5:
                risk = 0.45
            expectancy = float(stats["expectancy_r"] or 0.0)
            score = expectancy * math.sqrt(max(1, int(stats["trades"]))) * min(2.5, max(0.0, pf))
            decisions.append(
                {
                    "gate_active": bool(accepted),
                    "gate_reason": "ACTIVE" if accepted else "V15_SHADOW_TRAILING_EDGE_GATE",
                    "trailing_trades": int(stats["trades"]),
                    "trailing_net_r": net_r,
                    "trailing_profit_factor": stats["profit_factor"],
                    "trailing_expectancy_r": stats["expectancy_r"],
                    "trailing_maximum_drawdown_r": stats["maximum_drawdown_r"],
                    "recent_net_r": recent_stats["net_r"],
                    "priority_score": float(score),
                    "requested_risk_percent": min(MAX_NEW_TRADE_RISK, risk),
                }
            )
        outputs.append(pd.concat([work, pd.DataFrame(decisions)], axis=1))
    if not outputs:
        return pd.DataFrame()
    return pd.concat(outputs, ignore_index=True, sort=False).sort_values(
        ["entry_time", "priority_score", "symbol"], ascending=[True, False, True]
    ).reset_index(drop=True)


def build_new_candidates(raw_market: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    executable = sorted(set(raw_market) - set(CORE_SYMBOLS))
    source = diversified.generate_universe_candidates(raw_market, executable)
    if source.empty:
        raise RuntimeError("V15 diversified systems generated no candidates")
    source = source[(source["entry_time"] >= TEST_START) & (source["entry_time"] <= TEST_END)].copy()
    source["setup"] = (
        "v15_" + source["symbol"].astype(str).str.lower() + "_" + source["profile"].astype(str).str.lower()
    )
    source["sleeve_id"] = (
        source["symbol"].astype(str) + "/" + source["family"].astype(str) + "/" + source["profile"].astype(str)
    )
    source["risk_percent"] = 0.20
    source["priority_class"] = 1
    gated = dynamic_new_gate(source)
    return source, gated


def fx_currencies(symbol: str) -> tuple[str, str] | None:
    text = str(symbol).upper()
    currencies = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}
    if len(text) == 6 and text[:3] in currencies and text[3:] in currencies:
        return text[:3], text[3:]
    return None


def unified_admission(baseline: pd.DataFrame, new: pd.DataFrame, risk_multiplier: float = 1.0) -> pd.DataFrame:
    base = baseline.copy()
    base["requested_risk_percent"] = pd.to_numeric(base["requested_risk_percent"], errors="coerce")
    fresh = new.copy()
    fresh["requested_risk_percent"] = (
        pd.to_numeric(fresh["requested_risk_percent"], errors="coerce") * float(risk_multiplier)
    ).clip(upper=MAX_NEW_TRADE_RISK)
    combined = pd.concat([base, fresh], ignore_index=True, sort=False)
    combined["priority_class"] = pd.to_numeric(combined["priority_class"], errors="coerce").fillna(1).astype(int)
    combined["priority_score"] = pd.to_numeric(combined["priority_score"], errors="coerce").fillna(0.0)
    combined = combined.sort_values(
        ["entry_time", "priority_class", "priority_score", "symbol"],
        ascending=[True, True, False, True],
    ).reset_index(drop=True)

    active: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for row in combined.itertuples(index=False):
        now = pd.Timestamp(row.entry_time)
        active = [item for item in active if item["exit_time"] > now]
        accepted = bool(row.gate_active)
        reason = str(row.gate_reason)
        requested = float(row.requested_risk_percent)
        mode = str(row.mode)
        is_new = int(row.priority_class) == 1

        if accepted and any(item["symbol"] == str(row.symbol) for item in active):
            accepted, reason = False, "SYMBOL_OPEN_POSITION_LIMIT"
        total_open = sum(float(item["risk_percent"]) for item in active)
        ict_open = sum(float(item["risk_percent"]) for item in active if item["mode"] == "ICT")
        new_open = sum(float(item["risk_percent"]) for item in active if item["is_new"])
        new_count = sum(1 for item in active if item["is_new"])

        if accepted and total_open + requested > MAX_COMBINED_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_COMBINED_RISK_CAP"
        if accepted and mode == "ICT" and ict_open + requested > MAX_ICT_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_ICT_RISK_CAP"
        if accepted and is_new and new_open + requested > MAX_NEW_OPEN_RISK + 1e-12:
            accepted, reason = False, "V15_NEW_ENGINE_OPEN_RISK_CAP"
        if accepted and is_new and new_count >= MAX_NEW_POSITIONS:
            accepted, reason = False, "V15_NEW_ENGINE_POSITION_CAP"

        if accepted and is_new:
            pair = fx_currencies(str(row.symbol))
            if pair is not None:
                exposure: dict[str, float] = {}
                for item in active:
                    item_pair = fx_currencies(item["symbol"])
                    if item_pair is None:
                        continue
                    for currency in item_pair:
                        exposure[currency] = exposure.get(currency, 0.0) + float(item["risk_percent"])
                if any(exposure.get(currency, 0.0) + requested > MAX_NEW_CURRENCY_EXPOSURE + 1e-12 for currency in pair):
                    accepted, reason = False, "V15_CURRENCY_CONCENTRATION_CAP"

        if accepted:
            active.append(
                {
                    "symbol": str(row.symbol),
                    "mode": mode,
                    "exit_time": pd.Timestamp(row.exit_time),
                    "risk_percent": requested,
                    "is_new": is_new,
                }
            )
        decisions.append(
            {
                "portfolio_admitted": bool(accepted),
                "portfolio_admission_reason": reason,
                "admission_risk_percent": requested,
            }
        )
    output = pd.concat([combined.reset_index(drop=True), pd.DataFrame(decisions)], axis=1)
    output["risk_percent"] = pd.to_numeric(output["admission_risk_percent"], errors="coerce")
    return output


def replay_from_admission(admitted: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, Any, pd.DataFrame]:
    active = admitted[admitted["portfolio_admitted"]].copy()
    active = active[(active["entry_time"] >= TEST_START) & (active["entry_time"] <= TEST_END)]
    swing = active[active["mode"] != "ICT"].copy().sort_values(["entry_time", "symbol"])
    ict = active[active["mode"] == "ICT"].copy().sort_values(["entry_time", "symbol"])
    if swing.empty or ict.empty:
        raise RuntimeError("Unified admission produced an empty swing/diversified or ICT sleeve")
    summary, trades, skipped, replay = v149.run_replay(swing, ict)
    candidates = pd.concat([swing, ict], ignore_index=True, sort=False)
    trades = external.enrich_closed_trades(trades, candidates)
    keys = ["symbol", "setup", "side", "entry_time", "exit_time"]
    metadata_columns = keys + ["family", "profile", "timeframe", "strategy_group", "priority_class"]
    metadata_columns = [column for column in metadata_columns if column in candidates.columns]
    metadata = candidates[metadata_columns].drop_duplicates(keys)
    trades = trades.merge(metadata, on=keys, how="left")
    return summary, trades, skipped, replay, active


def baseline_replay(baseline: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    admitted = v149.portfolio_admission(baseline)
    active = admitted[admitted["portfolio_admitted"]].copy()
    active = active[(active["entry_time"] >= v149.PORTFOLIO_START) & (active["entry_time"] <= TEST_END)]
    swing = active[active["mode"] == "SWING"].copy()
    ict = active[active["mode"] == "ICT"].copy()
    summary, trades, _, _ = v149.run_replay(swing, ict)
    trades = external.enrich_closed_trades(trades, pd.concat([swing, ict], ignore_index=True, sort=False))
    return summary, trades


def attribution(trades: pd.DataFrame, column: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    if column not in trades.columns:
        return output
    for key, frame in trades.groupby(column, dropna=False):
        output[str(key)] = dollar_stats(frame)
    return output


def write_report(payload: dict[str, Any]) -> None:
    baseline = payload["baseline_v14_9"]
    primary = payload["primary_portfolio"]
    best = payload["best_safe_feasibility"]
    holdout = payload["final_holdout_2024_2026"]
    lines = [
        "# V15 Diversified Target Portfolio",
        "",
        f"**Data:** {payload['provider']}",
        f"**Backtest window:** {TEST_START.date()} through {TEST_END.date()}",
        f"**Final untouched reporting segment:** {FINAL_HOLDOUT_START.date()} through {TEST_END.date()}",
        "**Starting balance:** $5,000.00",
        "",
        "## Primary pre-registered allocation",
        "",
        "| Metric | V14.9 baseline | V15 diversified | Change |",
        "|---|---:|---:|---:|",
        f"| Net profit | ${baseline['net_profit']:,.2f} | ${primary['net_profit']:,.2f} | ${primary['net_profit'] - baseline['net_profit']:,.2f} |",
        f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${primary['ending_balance']:,.2f} | ${primary['ending_balance'] - baseline['ending_balance']:,.2f} |",
        f"| Profit factor | {float(baseline['profit_factor'] or 0.0):.4f} | {float(primary['profit_factor'] or 0.0):.4f} | {float(primary['profit_factor'] or 0.0) - float(baseline['profit_factor'] or 0.0):.4f} |",
        f"| Maximum closed drawdown | {baseline['max_closed_drawdown_percent']:.4f}% | {primary['max_closed_drawdown_percent']:.4f}% | {primary['max_closed_drawdown_percent'] - baseline['max_closed_drawdown_percent']:.4f} pp |",
        f"| Stressed drawdown | {baseline['stress_drawdown_percent']:.4f}% | {primary['stress_drawdown_percent']:.4f}% | {primary['stress_drawdown_percent'] - baseline['stress_drawdown_percent']:.4f} pp |",
        f"| Closed trades | {baseline['closed_trades']} | {primary['closed_trades']} | {primary['closed_trades'] - baseline['closed_trades']} |",
        "",
        "## Target status",
        "",
        f"- $20,000 net target reached: **{payload['target_20k_reached']}**; remaining gap ${payload['target_20k_gap']:,.2f}.",
        f"- $40,000 net stretch target reached: **{payload['target_40k_reached']}**; remaining gap ${payload['target_40k_gap']:,.2f}.",
        "",
        "## Final 2024-2026 segment",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Trades | {holdout['trades']} |",
        f"| Net profit | ${holdout['net_profit']:,.2f} |",
        f"| Profit factor | {float(holdout['profit_factor'] or 0.0):.4f} |",
        f"| Win rate | {float(holdout['win_rate'] or 0.0) * 100.0:.2f}% |",
        "",
        "## Safe feasibility grid",
        "",
        f"Best safe new-engine multiplier: **{best['risk_multiplier']:.2f}x**; net profit **${best['net_profit']:,.2f}**; max closed DD **{best['max_closed_drawdown_percent']:.4f}%**; stressed DD **{best['stress_drawdown_percent']:.4f}%**.",
        "",
        "## Controls retained",
        "",
        "- V14.9 swing and ICT definitions and their walk-forward gates are unchanged.",
        "- New systems trade only non-core instruments, so they cannot replace a core five-symbol signal.",
        "- New entries occur at 08:00, 12:00 or 16:00 UTC; Asia-session entry is excluded.",
        "- Bid/ask candles embed historical spread; a further slippage reserve is deducted.",
        "- New risk is capped at 0.50% per trade, 1.50% open and three simultaneous new positions.",
        "- Existing 1.75% ICT and 3.25% combined open-risk limits remain active.",
        "- The 7.5/8.5/9.0/9.6 drawdown governor and projected-stress ceiling remain active.",
        "",
        "Research only. The feasibility grid is not an out-of-sample promotion rule. No live execution changes are included.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw_market, core_market, data_quality = load_market()
    baseline_gated, baseline_evidence = build_baseline_candidates(core_market)
    baseline_summary, baseline_trades = baseline_replay(baseline_gated)
    baseline_trades.to_csv(OUT / "baseline_v14_9_closed_trades.csv", index=False)

    new_source, new_gated = build_new_candidates(raw_market)
    new_source.to_csv(OUT / "all_v15_diversified_candidates.csv", index=False)
    new_gated.to_csv(OUT / "v15_trailing_gate.csv", index=False)

    feasibility_rows: list[dict[str, Any]] = []
    run_outputs: dict[float, tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, Any, pd.DataFrame, pd.DataFrame]] = {}
    for multiplier in RISK_MULTIPLIERS:
        admitted = unified_admission(baseline_gated, new_gated, multiplier)
        summary, trades, skipped, replay, active = replay_from_admission(admitted)
        safe = (
            float(summary["max_closed_drawdown_percent"]) <= 9.60
            and float(summary["stress_drawdown_percent"]) <= 10.00
        )
        row = {
            "risk_multiplier": float(multiplier),
            **summary,
            "safe": bool(safe),
            "new_admitted_candidates": int((active["priority_class"] == 1).sum()),
        }
        feasibility_rows.append(row)
        run_outputs[float(multiplier)] = (summary, trades, skipped, replay, active, admitted)

    feasibility = pd.DataFrame(feasibility_rows).sort_values("risk_multiplier")
    feasibility.to_csv(OUT / "risk_feasibility_grid.csv", index=False)
    safe_rows = feasibility[feasibility["safe"]]
    if safe_rows.empty:
        raise RuntimeError("No safe V15 feasibility result remained within drawdown limits")
    primary_multiplier = 1.0
    primary_summary, trades, skipped, replay, active, admitted = run_outputs[primary_multiplier]
    best_summary = safe_rows.sort_values("net_profit", ascending=False).iloc[0].to_dict()

    admitted.to_csv(OUT / "combined_gate_and_admission.csv", index=False)
    active.to_csv(OUT / "admitted_candidates.csv", index=False)
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(OUT / "closed_drawdown_governor_events.csv", index=False)
    pd.DataFrame(replay.projected_stress_events).to_csv(OUT / "projected_stress_governor_events.csv", index=False)

    old_out, old_start, old_end = external.OUT, external.TEST_START, external.TEST_END
    external.OUT, external.TEST_START, external.TEST_END = OUT, TEST_START, TEST_END
    try:
        monthly, annual = external.time_series(trades)
        monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
        annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
        external.plot_outputs(monthly, annual, trades)
    finally:
        external.OUT, external.TEST_START, external.TEST_END = old_out, old_start, old_end

    holdout = trades[pd.to_datetime(trades["entry_time"], utc=True) >= FINAL_HOLDOUT_START].copy()
    new_trades = trades[trades["priority_class"] == 1].copy() if "priority_class" in trades else pd.DataFrame()
    target_20k_reached = float(primary_summary["net_profit"]) >= TARGET_NET_20K
    target_40k_reached = float(primary_summary["net_profit"]) >= TARGET_NET_40K
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "FXCM official weekly H1 bid/ask archive",
        "window": {"start": TEST_START.isoformat(), "end": TEST_END.isoformat()},
        "final_holdout_start": FINAL_HOLDOUT_START.isoformat(),
        "starting_balance": STARTING_BALANCE,
        "data_quality": data_quality,
        "core_symbols": list(CORE_SYMBOLS),
        "new_executable_symbols": sorted(set(raw_market) - set(CORE_SYMBOLS)),
        "strategy_specs": [asdict(spec) for spec in diversified.strategy_specs()],
        "baseline_sleeve_evidence": baseline_evidence,
        "new_gate": {
            "lookback_days": TRAILING_DAYS,
            "recent_days": RECENT_DAYS,
            "minimum_net_r": 1.0,
            "minimum_profit_factor": 1.15,
            "maximum_trade_risk_percent": MAX_NEW_TRADE_RISK,
            "maximum_new_open_risk_percent": MAX_NEW_OPEN_RISK,
            "maximum_new_positions": MAX_NEW_POSITIONS,
            "asia_entries_allowed": False,
        },
        "risk_limits": {
            "maximum_ict_open_risk_percent": MAX_ICT_OPEN_RISK,
            "maximum_combined_open_risk_percent": MAX_COMBINED_OPEN_RISK,
            "maximum_new_currency_exposure_percent": MAX_NEW_CURRENCY_EXPOSURE,
            "maximum_closed_drawdown_percent": 9.60,
            "maximum_stressed_drawdown_percent": 10.00,
        },
        "baseline_v14_9": baseline_summary,
        "primary_risk_multiplier": primary_multiplier,
        "primary_portfolio": {**primary_summary, "safe": True},
        "improvement_over_v14_9": {
            "net_profit": round(float(primary_summary["net_profit"] - baseline_summary["net_profit"]), 2),
            "ending_balance": round(float(primary_summary["ending_balance"] - baseline_summary["ending_balance"]), 2),
            "profit_factor": round(float(primary_summary["profit_factor"] or 0.0) - float(baseline_summary["profit_factor"] or 0.0), 6),
        },
        "best_safe_feasibility": best_summary,
        "feasibility_grid": feasibility_rows,
        "final_holdout_2024_2026": dollar_stats(holdout),
        "new_system_contribution": dollar_stats(new_trades),
        "attribution_by_strategy_group": attribution(trades, "strategy_group"),
        "attribution_by_family": attribution(trades, "family"),
        "attribution_by_symbol": attribution(trades, "symbol"),
        "target_20k_reached": target_20k_reached,
        "target_20k_gap": round(max(0.0, TARGET_NET_20K - float(primary_summary["net_profit"])), 2),
        "target_40k_reached": target_40k_reached,
        "target_40k_gap": round(max(0.0, TARGET_NET_40K - float(primary_summary["net_profit"])), 2),
        "research_only": True,
        "live_execution_changed": False,
    }
    (OUT / "v15_diversified_target_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(payload)
    print(json.dumps({
        "baseline": baseline_summary,
        "primary": primary_summary,
        "best_safe": best_summary,
        "holdout": payload["final_holdout_2024_2026"],
        "new_contribution": payload["new_system_contribution"],
        "target_20k_reached": target_20k_reached,
        "target_20k_gap": payload["target_20k_gap"],
        "target_40k_reached": target_40k_reached,
        "target_40k_gap": payload["target_40k_gap"],
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
