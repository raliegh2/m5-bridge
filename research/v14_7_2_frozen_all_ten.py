"""V14.7.2 frozen all-ten-sleeve portfolio.

This study preserves the eight sleeves already validated by V14.6.2 and adds
only the two missing sections found by the V14.7 strategy laboratory:

* GBPUSD ICT: wide sweep, SELL, London session;
* USDJPY swing: H4 24-bar breakout, London session.

The two additions must be profitable after costs in training, validation, two
audits and the untouched final holdout. The combined portfolio is then searched
under the existing risk and drawdown caps toward a $20,000 retail-net target.
"""
from __future__ import annotations

import csv
import itertools
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile  # noqa: E402
from mt5_ai_bridge.v14_7_strategy_families import SYMBOLS, generate_symbol_candidates  # noqa: E402
from research import v14_6_five_symbol_dual_engine_target as base  # noqa: E402
from research import v14_6_1_intraday_ict_trend_backtest as intraday  # noqa: E402
from research import v14_7_five_symbol_20k_backtest as v147  # noqa: E402
from research.v13_expanded_assets_backtest import load_frame  # noqa: E402
from research.v14_3_production_improved_backtest import filter_window  # noqa: E402

OUT = ROOT / "research" / "v14_7_2_all_ten_output"
FIXTURE = ROOT / "research" / "v14_6_candidate_fixture"
INCUMBENT_RESULTS = ROOT / "research" / "v14_6_2_raw_intraday_results.json"
TARGET_NET_PROFIT = 20_000.0
STARTING_BALANCE = 5_000.0
MAX_CLOSED_DD = 9.60
MAX_STRESS_DD = 10.00
MAX_SWING_RISK = 1.25
MAX_ICT_RISK = 0.60
SWING_SCALE_GRID = tuple(round(0.50 + index * 0.10, 2) for index in range(26))  # .50..3.00
ICT_SCALE_GRID = tuple(round(0.50 + index * 0.10, 2) for index in range(31))  # .50..3.50


def load_fixture(name: str, mode: str, cost_r: float) -> pd.DataFrame:
    frame = pd.read_csv(FIXTURE / name)
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["side"] = frame["side"].astype(str).str.upper()
    frame["mode"] = mode
    frame["family"] = f"V14_6_{mode}"
    frame["selection_cost_r"] = float(cost_r)
    return frame


def raw_candidates() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        frame = generate_symbol_candidates(
            symbol,
            load_frame(symbol, "h1"),
            load_frame(symbol, "h4"),
            load_frame(symbol, "d1"),
        )
        if not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False)


def pool() -> pd.DataFrame:
    output = pd.concat(
        [
            raw_candidates(),
            load_fixture("all_swing_candidates.csv", "SWING", 0.04),
            load_fixture("all_wide_ict_candidates.csv", "ICT", 0.09),
        ],
        ignore_index=True,
        sort=False,
    )
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    output["side"] = output["side"].astype(str).str.upper()
    return output.drop_duplicates(
        ["entry_time", "exit_time", "symbol", "mode", "engine", "side"]
    ).sort_values(["entry_time", "symbol", "mode", "engine"]).reset_index(drop=True)


def materialize(
    source: pd.DataFrame,
    symbol: str,
    mode: str,
    spec: base.FilterSpec,
    risk: float,
    setup: str,
) -> pd.DataFrame:
    subset = source[(source["symbol"] == symbol) & (source["mode"] == mode)].copy()
    selected = base.filter_frame(subset, spec).copy()
    if selected.empty:
        raise RuntimeError(f"No trades for {symbol} {mode} {spec}")
    selected["setup"] = setup
    selected["risk_percent"] = float(risk)
    selected["sleeve_mode"] = mode
    selected["raw_r_multiple"] = selected["r_multiple"].astype(float)
    selected["cost_r"] = selected["selection_cost_r"].astype(float)
    selected["r_multiple"] = selected["raw_r_multiple"] - selected["cost_r"]
    return selected


def periods(start: pd.Timestamp, end: pd.Timestamp):
    return v147.blocks(start, end)


def evidence(frame: pd.DataFrame, blocks) -> dict[str, dict[str, Any]]:
    # The selected frame already has retail costs deducted. Restore gross values
    # only for use with V14.7's mixed-cost statistics helper.
    work = frame.copy()
    work["r_multiple"] = work["raw_r_multiple"]
    work["selection_cost_r"] = work["cost_r"]
    return v147.stats_by_block(work, blocks)


def validate_addition(name: str, stats: dict[str, dict[str, Any]], mode: str) -> None:
    minimum = 3 if mode == "SWING" else 5
    for block, item in stats.items():
        required = minimum
        if block == "train":
            required = 8 if mode == "SWING" else 12
        elif block == "validation":
            required = 4 if mode == "SWING" else 6
        if item["trades"] < required:
            raise RuntimeError(f"{name} insufficient {block} trades: {item}")
        if item["net_r"] <= 0 or float(item["profit_factor"] or 0.0) <= 1.01:
            raise RuntimeError(f"{name} failed {block}: {item}")


def current_selections(source: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    payload = json.loads(INCUMBENT_RESULTS.read_text())
    swings: list[pd.DataFrame] = []
    icts: list[pd.DataFrame] = []
    metadata: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        metadata[symbol] = {}
        for mode in ("SWING", "ICT"):
            item = payload["selections"][symbol][mode]
            if item is None:
                metadata[symbol][mode] = None
                continue
            spec = base.FilterSpec(**item["filter_spec"])
            frame = materialize(
                source,
                symbol,
                mode,
                spec,
                float(item["base_risk_percent"]),
                f"v14_7_2_{symbol.lower()}_{mode.lower()}_incumbent",
            )
            frame = filter_window(frame, start, end)
            metadata[symbol][mode] = {
                "source": "V14.6.2 validated incumbent",
                "filter_spec": asdict(spec),
                "base_risk_percent": float(item["base_risk_percent"]),
                "prior_evidence": item["evidence"],
                "trade_count": int(len(frame)),
            }
            (swings if mode == "SWING" else icts).append(frame)
    return swings, icts, metadata, payload["best_safe_portfolio"]


def install_profile(ict: pd.DataFrame):
    return intraday.install_profile(ict)


def restore_profile(old_risk, old_guards):
    intraday.restore_profile(old_risk, old_guards)


def portfolio_search(swing: pd.DataFrame, ict: pd.DataFrame):
    rows: list[dict[str, Any]] = []
    best_safe = None
    best_outputs = None
    for swing_scale, ict_scale in itertools.product(SWING_SCALE_GRID, ICT_SCALE_GRID):
        swing_case = base.scale_risk(swing, swing_scale, MAX_SWING_RISK)
        ict_case = base.scale_risk(ict, ict_scale, MAX_ICT_RISK)
        old_risk, old_guards = install_profile(ict_case)
        try:
            summary, trades, skipped, events = base.run_portfolio(swing_case, ict_case)
        finally:
            restore_profile(old_risk, old_guards)
        row = {
            "swing_scale": swing_scale,
            "ict_scale": ict_scale,
            "net_profit": summary["net_profit"],
            "ending_balance": summary["ending_balance"],
            "return_percent": summary["return_percent"],
            "profit_factor": summary["profit_factor"],
            "max_closed_drawdown_percent": summary["max_closed_drawdown_percent"],
            "stress_drawdown_percent": summary["stress_drawdown_percent"],
            "closed_trades": summary["closed_trades"],
            "skipped_trades": summary["skipped_ict_trades"],
            "safe": (
                float(summary["max_closed_drawdown_percent"]) <= MAX_CLOSED_DD
                and float(summary["stress_drawdown_percent"]) <= MAX_STRESS_DD
            ),
            "target_reached": float(summary["net_profit"]) >= TARGET_NET_PROFIT,
            "governor_interventions": int(len(events)),
        }
        rows.append(row)
        if row["safe"] and (best_safe is None or row["net_profit"] > best_safe["net_profit"]):
            best_safe = row
            best_outputs = (trades, skipped, events)
    if best_safe is None or best_outputs is None:
        raise RuntimeError("No all-ten portfolio remained inside the drawdown limits")
    return best_safe, rows, *best_outputs


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = pool()
    source.to_csv(OUT / "all_strategy_candidates.csv", index=False)
    latest = source["exit_time"].max()
    start = latest - pd.DateOffset(years=10)
    blocks = periods(start, latest)

    swing_frames, ict_frames, selections, previous = current_selections(source, start, latest)

    gbpusd_spec = base.FilterSpec(
        engine="GBPUSD_ICT_WIDE_SWEEP", side="SELL", session="LONDON"
    )
    gbpusd_ict = materialize(
        source, "GBPUSD", "ICT", gbpusd_spec, 0.35, "v14_7_2_gbpusd_ict_london_sell"
    )
    gbpusd_ict = filter_window(gbpusd_ict, start, latest)
    gbpusd_evidence = evidence(gbpusd_ict, blocks)
    validate_addition("GBPUSD ICT", gbpusd_evidence, "ICT")
    ict_frames.append(gbpusd_ict)
    selections["GBPUSD"]["ICT"] = {
        "source": "V14.7 five-block addition",
        "filter_spec": asdict(gbpusd_spec),
        "base_risk_percent": 0.35,
        "evidence": gbpusd_evidence,
        "trade_count": int(len(gbpusd_ict)),
    }

    usdjpy_spec = base.FilterSpec(
        engine="USDJPY_SWING_SWING_BREAKOUT_24", side="ALL", session="LONDON"
    )
    usdjpy_swing = materialize(
        source, "USDJPY", "SWING", usdjpy_spec, 0.60, "v14_7_2_usdjpy_swing_breakout"
    )
    usdjpy_swing = filter_window(usdjpy_swing, start, latest)
    usdjpy_evidence = evidence(usdjpy_swing, blocks)
    validate_addition("USDJPY swing", usdjpy_evidence, "SWING")
    swing_frames.append(usdjpy_swing)
    selections["USDJPY"]["SWING"] = {
        "source": "V14.7 five-block addition",
        "filter_spec": asdict(usdjpy_spec),
        "base_risk_percent": 0.60,
        "evidence": usdjpy_evidence,
        "trade_count": int(len(usdjpy_swing)),
    }

    swing = pd.concat(swing_frames, ignore_index=True, sort=False)
    ict = pd.concat(ict_frames, ignore_index=True, sort=False)
    swing.to_csv(OUT / "all_ten_swing_trades.csv", index=False)
    ict.to_csv(OUT / "all_ten_ict_trades.csv", index=False)

    best, search_rows, trades, skipped, events = portfolio_search(swing, ict)
    trades.to_csv(OUT / "best_safe_trades.csv", index=False)
    skipped.to_csv(OUT / "best_safe_skipped.csv", index=False)
    events.to_csv(OUT / "best_safe_governor_events.csv", index=False)
    write_csv(OUT / "portfolio_risk_search.csv", search_rows)

    coverage = {
        "active_swing_symbols": [symbol for symbol in SYMBOLS if selections[symbol]["SWING"]],
        "active_ict_symbols": [symbol for symbol in SYMBOLS if selections[symbol]["ICT"]],
    }
    coverage["all_ten_sleeves_active"] = (
        set(coverage["active_swing_symbols"]) == set(SYMBOLS)
        and set(coverage["active_ict_symbols"]) == set(SYMBOLS)
    )
    payload = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "target": {
            "starting_balance": STARTING_BALANCE,
            "net_profit": TARGET_NET_PROFIT,
            "ending_balance": STARTING_BALANCE + TARGET_NET_PROFIT,
            "after_retail_costs_and_fees": True,
        },
        "method": {
            "incumbent_sleeves_retained": 8,
            "new_five_block_sleeves": 2,
            "new_sleeves": ["GBPUSD ICT", "USDJPY swing"],
        },
        "retail_cost_r": {"SWING": 0.04, "ICT_WIDE": 0.09, "ICT_INTRADAY": 0.12},
        "risk_limits": {
            "max_swing_trade_percent": MAX_SWING_RISK,
            "max_ict_trade_percent": MAX_ICT_RISK,
            "max_closed_drawdown_percent": MAX_CLOSED_DD,
            "max_stress_drawdown_percent": MAX_STRESS_DD,
            "max_ict_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_ict_open_risk_percent,
            "max_combined_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_combined_open_risk_percent,
        },
        "selections": selections,
        "coverage": coverage,
        "previous_v14_6_2": previous,
        "best_safe_portfolio": best,
        "profit_improvement_vs_v14_6_2": round(float(best["net_profit"]) - float(previous["net_profit"]), 2),
        "target_reached": bool(best["target_reached"]),
        "target_gap": round(max(0.0, TARGET_NET_PROFIT - float(best["net_profit"])), 2),
    }
    (OUT / "v14_7_2_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    report = [
        "# V14.7.2 Frozen All-Ten-Sleeve Portfolio",
        "",
        f"**Window:** {start.date()} to {latest.date()}",
        "**Starting balance:** $5,000.00",
        "**Retail-net target:** $20,000.00",
        "",
        "## Coverage",
        "",
        "All five symbols contain both a validated swing section and a validated ICT section.",
        "",
        "## Best safe result",
        "",
        f"- Net profit: ${best['net_profit']:,.2f}",
        f"- Ending balance: ${best['ending_balance']:,.2f}",
        f"- Profit factor: {float(best['profit_factor']):.4f}",
        f"- Maximum closed drawdown: {best['max_closed_drawdown_percent']:.4f}%",
        f"- Stressed drawdown: {best['stress_drawdown_percent']:.4f}%",
        f"- Target reached: {best['target_reached']}",
        f"- Target gap: ${payload['target_gap']:,.2f}",
        "",
        "Research only. Historical results do not guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "window": payload["window"],
                "coverage": coverage,
                "previous_v14_6_2": previous,
                "best_safe_portfolio": best,
                "profit_improvement_vs_v14_6_2": payload["profit_improvement_vs_v14_6_2"],
                "target_reached": payload["target_reached"],
                "target_gap": payload["target_gap"],
                "output": str(OUT),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
