"""V14.6 five-symbol dual-engine retail-target research.

Purpose
-------
Build and test an honest five-symbol portfolio in which GBPUSD, EURUSD,
GBPJPY, AUDUSD and USDJPY each have independently validated swing and ICT
sections. The acceptance target is $34,000 net profit from a $5,000 start over
the exact ten-year window AFTER an all-in retail cost and fee allowance.

The script does not force a weak sleeve into production and does not declare
success merely because a zero-cost replay reaches the target. Every selected
sleeve must be positive in chronological development, confirmation and holdout
segments after its own retail all-in R cost.

Research only: no MT5 connection, no order transmission and no live merge.
"""
from __future__ import annotations

import csv
import itertools
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import mt5_ai_bridge.v14_3_all_symbol_ict as ict_engine  # noqa: E402
import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile  # noqa: E402
from mt5_ai_bridge import v14_3_live_signals as live  # noqa: E402
from mt5_ai_bridge.v14_3_all_symbol_ict import IctProfile  # noqa: E402
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor  # noqa: E402
from mt5_ai_bridge.v14_3_profit_preserving_profile import SymbolGuard  # noqa: E402
from research.v13_expanded_assets_backtest import load_frame  # noqa: E402
from research.v14_3_drawdown_limited_backtest_v2 import AdmissionPreservingReplay  # noqa: E402
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates  # noqa: E402
from research.v14_6_swing_regeneration import CSVClient  # noqa: E402

OUT = ROOT / "research" / "v14_6_five_symbol_dual_engine_output"
LEGACY_ICT_SOURCE = (
    ROOT
    / "research"
    / "v14_3_true_combined_v12_ict_output"
    / "true_combined_closed_trades.csv"
)
DATA = ROOT / "research" / "data_v14_6"

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
STARTING_BALANCE = 5_000.0
TARGET_NET_PROFIT = 34_000.0
TARGET_ENDING_BALANCE = STARTING_BALANCE + TARGET_NET_PROFIT
OBSERVATION_RISK = 0.025
MAX_SWING_RISK = 1.25
MAX_ICT_RISK = 0.60
MAX_CLOSED_DD = 9.60
MAX_STRESS_DD = 10.00

# All-in retail allowances include spread, commission, normal slippage and a
# conservative carry/fee reserve. These are intentionally above V14.5's basic
# cost constants. They are applied before compounding and loss controls.
ALL_IN_COST_R = {
    "SWING": 0.04,
    "ICT_WIDE": 0.09,
    "ICT_LEGACY_M1": 0.16,
}

# Research-only risk search. The model may use lower risk than the selected
# sleeve's evidence tier, but never exceed these hard ceilings.
SWING_SCALE_GRID = tuple(round(0.60 + 0.10 * index, 2) for index in range(11))  # .60..1.60
ICT_SCALE_GRID = tuple(round(0.50 + 0.10 * index, 2) for index in range(11))  # .50..1.50


@dataclass(frozen=True)
class FilterSpec:
    engine: str
    side: str = "ALL"
    hour: int | None = None
    session: str | None = None
    excluded_weekday: int | None = None


@dataclass(frozen=True)
class SleeveSelection:
    symbol: str
    mode: str
    filter_spec: FilterSpec
    base_risk_percent: float
    setup_name: str
    evidence: dict[str, Any]


def utc_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_datetime(frame[column], utc=True)


def ratio_stats(frame: pd.DataFrame, cost_r: float) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "expectancy_r": None, "profit_factor": None}
    values = frame["r_multiple"].astype(float) - float(cost_r)
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
    }


def chronological_segments(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    span = end - start
    development_end = start + span * 0.50
    confirmation_end = start + span * 0.75
    entry = utc_series(frame, "entry_time")
    return {
        "development": frame[(entry >= start) & (entry < development_end)].copy(),
        "confirmation": frame[(entry >= development_end) & (entry < confirmation_end)].copy(),
        "holdout": frame[(entry >= confirmation_end) & (entry <= end)].copy(),
    }


def filter_frame(frame: pd.DataFrame, spec: FilterSpec) -> pd.DataFrame:
    output = frame[frame["engine"].astype(str) == spec.engine].copy()
    output["entry_time"] = utc_series(output, "entry_time")
    if spec.side != "ALL":
        output = output[output["side"].astype(str).str.upper() == spec.side]
    if spec.hour is not None:
        output = output[output["entry_time"].dt.hour == spec.hour]
    if spec.session is not None:
        hour = output["entry_time"].dt.hour
        ranges = {
            "ASIA": (0, 6),
            "LONDON": (6, 12),
            "NEW_YORK": (12, 18),
            "LATE": (18, 24),
        }
        low, high = ranges[spec.session]
        output = output[(hour >= low) & (hour < high)]
    if spec.excluded_weekday is not None:
        output = output[output["entry_time"].dt.weekday != spec.excluded_weekday]
    return output


def filter_candidates(frame: pd.DataFrame) -> list[FilterSpec]:
    specs: set[FilterSpec] = set()
    for engine, group in frame.groupby("engine"):
        name = str(engine)
        specs.add(FilterSpec(name))
        sides = sorted(group["side"].astype(str).str.upper().unique().tolist())
        hours = sorted(utc_series(group, "entry_time").dt.hour.unique().tolist())
        for side in sides:
            specs.add(FilterSpec(name, side=side))
        for weekday in range(5):
            specs.add(FilterSpec(name, excluded_weekday=weekday))
            for side in sides:
                specs.add(FilterSpec(name, side=side, excluded_weekday=weekday))
        for session in ("ASIA", "LONDON", "NEW_YORK", "LATE"):
            specs.add(FilterSpec(name, session=session))
            for side in sides:
                specs.add(FilterSpec(name, side=side, session=session))
        # Exact-hour filters are allowed only where the complete stream has at
        # least 18 trades, preventing one-off time-bucket selection.
        for hour in hours:
            hour_count = int((utc_series(group, "entry_time").dt.hour == hour).sum())
            if hour_count >= 18:
                specs.add(FilterSpec(name, hour=int(hour)))
                for side in sides:
                    specs.add(FilterSpec(name, side=side, hour=int(hour)))
    return sorted(
        specs,
        key=lambda item: (
            item.engine,
            item.side,
            -1 if item.hour is None else item.hour,
            item.session or "",
            -1 if item.excluded_weekday is None else item.excluded_weekday,
        ),
    )


def sleeve_passes(stats: dict[str, dict[str, Any]], mode: str) -> bool:
    minimums = {
        "SWING": {"development": 8, "confirmation": 4, "holdout": 4},
        "ICT": {"development": 10, "confirmation": 5, "holdout": 5},
    }[mode]
    pf_floor = {"development": 1.08, "confirmation": 1.03, "holdout": 1.03}
    for segment in ("development", "confirmation", "holdout"):
        item = stats[segment]
        if item["trades"] < minimums[segment]:
            return False
        if item["net_r"] <= 0:
            return False
        if float(item["profit_factor"] or 0.0) < pf_floor[segment]:
            return False
    return True


def evidence_score(stats: dict[str, dict[str, Any]]) -> float:
    expectations = [float(stats[name]["expectancy_r"] or -99.0) for name in stats]
    pfs = [float(stats[name]["profit_factor"] or 0.0) for name in stats]
    return min(expectations) * 100.0 + min(pfs) * 5.0 + sum(item["net_r"] for item in stats.values())


def risk_from_evidence(stats: dict[str, dict[str, Any]], mode: str) -> float:
    min_pf = min(float(item["profit_factor"] or 0.0) for item in stats.values())
    min_exp = min(float(item["expectancy_r"] or 0.0) for item in stats.values())
    if mode == "SWING":
        if min_pf >= 1.60 and min_exp >= 0.18:
            return 1.00
        if min_pf >= 1.35 and min_exp >= 0.10:
            return 0.80
        if min_pf >= 1.18 and min_exp >= 0.05:
            return 0.60
        return 0.40
    if min_pf >= 1.60 and min_exp >= 0.15:
        return 0.50
    if min_pf >= 1.35 and min_exp >= 0.08:
        return 0.35
    return 0.20


def select_sleeve(
    symbol: str,
    mode: str,
    frame: pd.DataFrame,
    cost_r: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[SleeveSelection | None, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for spec in filter_candidates(frame):
        selected = filter_frame(frame, spec)
        segments = chronological_segments(selected, start, end)
        stats = {name: ratio_stats(segment, cost_r) for name, segment in segments.items()}
        passed = sleeve_passes(stats, mode)
        candidates.append(
            {
                "spec": asdict(spec),
                "passed": passed,
                "score": round(evidence_score(stats), 6),
                "segments": stats,
                "total": ratio_stats(selected, cost_r),
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    passing = [item for item in candidates if item["passed"]]
    if not passing:
        return None, candidates[:50]
    winner = passing[0]
    spec = FilterSpec(**winner["spec"])
    risk = risk_from_evidence(winner["segments"], mode)
    setup_name = f"v14_6_{symbol.lower()}_{mode.lower()}_{abs(hash(str(spec))) % 10_000_000:07d}"
    return (
        SleeveSelection(
            symbol=symbol,
            mode=mode,
            filter_spec=spec,
            base_risk_percent=risk,
            setup_name=setup_name,
            evidence={
                "segments": winner["segments"],
                "total": winner["total"],
                "score": winner["score"],
            },
        ),
        candidates[:50],
    )


def build_continuous_swing_candidates() -> pd.DataFrame:
    client = CSVClient(DATA)
    prepared = {
        symbol: live.prepare_v12_frames(
            client,
            symbol,
            h1_count=3000,
            h4_count=23900,
            d1_count=3980,
        )
        for symbol in SYMBOLS
    }
    frames: list[pd.DataFrame] = []
    _, gbp_h4, _ = prepared["GBPUSD"]
    frames.extend(
        [
            live.study._gbpusd_precision(gbp_h4),
            live.study._gbpusd_retest_candidates(gbp_h4),
            live.study._v12_core_candidates("EURUSD", prepared["EURUSD"][1]),
            live.study._v12_core_candidates("GBPJPY", prepared["GBPJPY"][1]),
            live.study._audusd_candidates(prepared["AUDUSD"][1], live.AUDUSD_PARAMS),
            live.study._usdjpy_candidates(prepared["USDJPY"][1]),
        ]
    )
    usable = [frame.copy() for frame in frames if not frame.empty]
    output = pd.concat(usable, ignore_index=True, sort=False)
    output["entry_time"] = utc_series(output, "entry_time")
    output["exit_time"] = utc_series(output, "exit_time")
    output["side"] = output["side"].astype(str).str.upper()
    output = output.sort_values(["entry_time", "symbol", "engine", "setup"])
    output = output.drop_duplicates(["entry_time", "exit_time", "symbol", "engine", "side"])
    return output.reset_index(drop=True)


def install_five_symbol_ict_profiles() -> None:
    ict_engine.ENGINE_BY_SYMBOL.update(
        {
            "GBPUSD": "GBPUSD_ICT_WIDE_SWEEP",
            "GBPJPY": "GBPJPY_ICT_WIDE_SWEEP",
        }
    )
    ict_engine.SETUP_BY_SYMBOL.update(
        {
            "GBPUSD": "gbpusd_ict_wide_sweep",
            "GBPJPY": "gbpjpy_ict_wide_sweep",
        }
    )
    ict_engine.PROFILES.update(
        {
            "GBPUSD": (
                IctProfile("gu_london_15", 0, 6, 7, 12, 0.20, 0.00, 0.10, 1.5, 24),
                IctProfile("gu_london_20", 0, 6, 7, 12, 0.25, 0.00, 0.12, 2.0, 30),
                IctProfile("gu_london_25", 0, 6, 7, 13, 0.35, 0.00, 0.15, 2.5, 36),
                IctProfile("gu_ny_15", 7, 11, 12, 17, 0.20, 0.00, 0.10, 1.5, 24),
                IctProfile("gu_ny_20", 7, 11, 12, 18, 0.25, 0.00, 0.12, 2.0, 30),
            ),
            "GBPJPY": (
                IctProfile("gj_london_15", 0, 6, 7, 12, 0.20, 0.00, 0.12, 1.5, 24),
                IctProfile("gj_london_20", 0, 6, 7, 13, 0.25, 0.00, 0.15, 2.0, 30),
                IctProfile("gj_london_25", 0, 6, 7, 13, 0.35, 0.00, 0.18, 2.5, 36),
                IctProfile("gj_ny_15", 7, 11, 12, 17, 0.20, 0.00, 0.12, 1.5, 24),
                IctProfile("gj_ny_20", 7, 11, 12, 18, 0.25, 0.00, 0.15, 2.0, 30),
            ),
        }
    )


def build_continuous_ict_candidates() -> pd.DataFrame:
    install_five_symbol_ict_profiles()
    frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        raw_h1 = load_frame(symbol, "h1")
        raw_h4 = load_frame(symbol, "h4")
        raw_d1 = load_frame(symbol, "d1")
        h1, _, _ = ict_engine.prepare_frames(raw_h1, raw_h4, raw_d1)
        for profile in ict_engine.PROFILES[symbol]:
            candidates = ict_engine.generate_candidates(symbol, h1, profile)
            if not candidates.empty:
                frames.append(candidates)
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = utc_series(output, "entry_time")
    output["exit_time"] = utc_series(output, "exit_time")
    output["side"] = output["side"].astype(str).str.upper()
    output = output.sort_values(["entry_time", "symbol", "engine", "profile"])
    output = output.drop_duplicates(
        ["entry_time", "exit_time", "symbol", "engine", "profile", "side"]
    )
    return output.reset_index(drop=True)


def apply_selection(frame: pd.DataFrame, selection: SleeveSelection) -> pd.DataFrame:
    selected = filter_frame(frame, selection.filter_spec).copy()
    selected["setup"] = selection.setup_name
    selected["risk_percent"] = selection.base_risk_percent
    selected["sleeve_mode"] = selection.mode
    return selected


def all_in_cost(frame: pd.DataFrame, cost_r: float) -> pd.DataFrame:
    output = frame.copy()
    output["raw_r_multiple"] = output["r_multiple"].astype(float)
    output["cost_r"] = float(cost_r)
    output["r_multiple"] = output["raw_r_multiple"] - float(cost_r)
    return output


def make_symbol_guard(symbol: str) -> SymbolGuard:
    sessions = {
        "GBPUSD": (6, 20),
        "EURUSD": (6, 19),
        "GBPJPY": (6, 20),
        "AUDUSD": (0, 18),
        "USDJPY": (0, 20),
    }
    start, end = sessions[symbol]
    return SymbolGuard(
        post_loss_multiplier=0.70,
        max_open_positions=1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=0.90,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=6.0,
        win_pressure_recovery=0.75,
        session_start_hour_utc=start,
        session_end_hour_utc=end,
    )


def install_replay_profile(swing: pd.DataFrame, ict: pd.DataFrame) -> tuple[dict, dict]:
    old_risk = dict(profit_profile.SETUP_RISK_PERCENT)
    old_guards = dict(profit_profile.SYMBOL_GUARDS)
    profit_profile.SETUP_RISK_PERCENT.clear()
    for row in ict[["symbol", "setup", "risk_percent"]].drop_duplicates().itertuples(index=False):
        profit_profile.SETUP_RISK_PERCENT[(str(row.symbol).upper(), str(row.setup))] = float(row.risk_percent)
    profit_profile.SYMBOL_GUARDS.clear()
    profit_profile.SYMBOL_GUARDS.update({symbol: make_symbol_guard(symbol) for symbol in SYMBOLS})
    return old_risk, old_guards


def restore_replay_profile(old_risk: dict, old_guards: dict) -> None:
    profit_profile.SETUP_RISK_PERCENT.clear()
    profit_profile.SETUP_RISK_PERCENT.update(old_risk)
    profit_profile.SYMBOL_GUARDS.clear()
    profit_profile.SYMBOL_GUARDS.update(old_guards)


def governor() -> DrawdownGovernor:
    return DrawdownGovernor(
        soft_start_percent=7.50,
        medium_start_percent=8.50,
        defensive_start_percent=9.00,
        hard_stop_percent=9.60,
        soft_multiplier=0.98,
        medium_multiplier=0.82,
        defensive_multiplier=0.50,
        minimum_risk_percent=OBSERVATION_RISK,
    )


def run_portfolio(swing: pd.DataFrame, ict: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    replay = AdmissionPreservingReplay(swing, ict, governor())
    summary, trades, skipped = replay.run()
    return summary, trades, skipped, pd.DataFrame(replay.governor_events)


def scale_risk(frame: pd.DataFrame, scale: float, ceiling: float) -> pd.DataFrame:
    output = frame.copy()
    output["unscaled_risk_percent"] = output["risk_percent"].astype(float)
    output["risk_percent"] = (output["unscaled_risk_percent"] * float(scale)).clip(upper=ceiling)
    return output


def portfolio_search(
    swing: pd.DataFrame,
    ict: pd.DataFrame,
) -> tuple[dict[str, Any], list[dict[str, Any]], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    best_safe: dict[str, Any] | None = None
    best_target: dict[str, Any] | None = None
    best_outputs: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None

    for swing_scale, ict_scale in itertools.product(SWING_SCALE_GRID, ICT_SCALE_GRID):
        swing_case = scale_risk(swing, swing_scale, MAX_SWING_RISK)
        ict_case = scale_risk(ict, ict_scale, MAX_ICT_RISK)
        old_risk, old_guards = install_replay_profile(swing_case, ict_case)
        try:
            summary, trades, skipped, events = run_portfolio(swing_case, ict_case)
        finally:
            restore_replay_profile(old_risk, old_guards)
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
            "target_reached": float(summary["net_profit"]) >= TARGET_NET_PROFIT,
            "safe": (
                float(summary["max_closed_drawdown_percent"]) <= MAX_CLOSED_DD
                and float(summary["stress_drawdown_percent"]) <= MAX_STRESS_DD
            ),
            "governor_interventions": int(len(events)),
        }
        rows.append(row)
        if row["safe"] and (
            best_safe is None or float(row["net_profit"]) > float(best_safe["net_profit"])
        ):
            best_safe = row
            best_outputs = (trades, skipped, events)
        if row["target_reached"] and (
            best_target is None
            or (row["safe"] and not best_target["safe"])
            or (
                row["safe"] == best_target["safe"]
                and float(row["max_closed_drawdown_percent"])
                < float(best_target["max_closed_drawdown_percent"])
            )
        ):
            best_target = row
    if best_safe is None or best_outputs is None:
        raise RuntimeError("No portfolio configuration remained inside the drawdown boundary")
    return best_safe, rows, *best_outputs


def attribution(trades: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {}
    if trades.empty:
        return output
    for (symbol, group), frame in trades.groupby(["symbol", "engine_group"]):
        pnl = frame["pnl"].astype(float)
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
        output[f"{symbol}/{group}"] = {
            "trades": int(len(frame)),
            "net_profit": round(float(pnl.sum()), 2),
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        }
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(payload: dict[str, Any]) -> None:
    best = payload["best_safe_portfolio"]
    lines = [
        "# V14.6 Five-Symbol Swing + ICT Retail-Target Research",
        "",
        f"**Exact ten-year window:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        f"**Starting balance:** ${STARTING_BALANCE:,.2f}",
        f"**Acceptance target after retail costs and fees:** ${TARGET_NET_PROFIT:,.2f} net / ${TARGET_ENDING_BALANCE:,.2f} ending balance",
        "",
        "## Sleeve coverage",
        "",
        "| Symbol | Swing selected | ICT selected |",
        "|---|---|---|",
    ]
    for symbol in SYMBOLS:
        swing = payload["selections"][symbol]["SWING"]
        ict = payload["selections"][symbol]["ICT"]
        lines.append(
            f"| {symbol} | {swing is not None} | {ict is not None} |"
        )
    lines += [
        "",
        "## Best safe all-in retail result",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Net profit | ${best['net_profit']:,.2f} |",
        f"| Ending balance | ${best['ending_balance']:,.2f} |",
        f"| Profit factor | {float(best['profit_factor']):.4f} |",
        f"| Maximum closed drawdown | {best['max_closed_drawdown_percent']:.4f}% |",
        f"| Stressed drawdown | {best['stress_drawdown_percent']:.4f}% |",
        f"| Target reached | {best['target_reached']} |",
        f"| Gap to $34,000 | ${max(0.0, TARGET_NET_PROFIT - float(best['net_profit'])):,.2f} |",
        "",
        "## Retail all-in cost assumptions",
        "",
        f"- Swing: {ALL_IN_COST_R['SWING']:.2f}R per trade.",
        f"- Wide-stop H1 ICT: {ALL_IN_COST_R['ICT_WIDE']:.2f}R per trade.",
        f"- Legacy M1 ICT: {ALL_IN_COST_R['ICT_LEGACY_M1']:.2f}R per trade.",
        "",
        "## Acceptance rule",
        "",
        "The model is not deployable unless all ten sleeves are independently positive after costs, the combined retail-net result reaches $34,000, and drawdown remains inside the 9.6%/10% boundary. A failed target remains a valid research outcome and must not be relabeled as success.",
        "",
        "Research only. R-cost replay is not tick-level execution and cannot guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_search(rows: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    safe_rows = [row for row in rows if row["safe"]]
    figure = plt.figure(figsize=(10, 6))
    for ict_scale in sorted({row["ict_scale"] for row in safe_rows}):
        subset = sorted(
            [row for row in safe_rows if row["ict_scale"] == ict_scale],
            key=lambda item: item["swing_scale"],
        )
        plt.plot(
            [row["swing_scale"] for row in subset],
            [row["net_profit"] for row in subset],
            label=f"ICT x{ict_scale:.1f}",
        )
    plt.axhline(TARGET_NET_PROFIT, linewidth=1)
    plt.xlabel("Swing risk scale")
    plt.ylabel("Retail-net profit ($)")
    plt.title("V14.6 safe retail-net search")
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    figure.savefig(OUT / "retail_profit_search.png", dpi=170)
    plt.close(figure)

    figure = plt.figure(figsize=(10, 6))
    plt.scatter(
        [row["max_closed_drawdown_percent"] for row in rows],
        [row["net_profit"] for row in rows],
    )
    plt.axvline(MAX_CLOSED_DD, linewidth=1)
    plt.axhline(TARGET_NET_PROFIT, linewidth=1)
    plt.xlabel("Maximum closed drawdown (%)")
    plt.ylabel("Retail-net profit ($)")
    plt.title("V14.6 profit versus drawdown")
    plt.tight_layout()
    figure.savefig(OUT / "retail_profit_vs_drawdown.png", dpi=170)
    plt.close(figure)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    swing_all = build_continuous_swing_candidates()
    ict_all = build_continuous_ict_candidates()
    swing_all.to_csv(OUT / "all_swing_candidates.csv", index=False)
    ict_all.to_csv(OUT / "all_wide_ict_candidates.csv", index=False)

    latest = max(swing_all["exit_time"].max(), ict_all["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    swing_window = filter_window(swing_all, start, latest)
    ict_window = filter_window(ict_all, start, latest)

    selections: dict[str, dict[str, Any]] = {}
    selected_swing_frames: list[pd.DataFrame] = []
    selected_ict_frames: list[pd.DataFrame] = []
    candidate_rankings: dict[str, Any] = {}

    for symbol in SYMBOLS:
        selections[symbol] = {}
        candidate_rankings[symbol] = {}
        swing_symbol = swing_window[swing_window["symbol"] == symbol].copy()
        ict_symbol = ict_window[ict_window["symbol"] == symbol].copy()

        swing_selection, swing_ranking = select_sleeve(
            symbol, "SWING", swing_symbol, ALL_IN_COST_R["SWING"], start, latest
        )
        ict_selection, ict_ranking = select_sleeve(
            symbol, "ICT", ict_symbol, ALL_IN_COST_R["ICT_WIDE"], start, latest
        )
        selections[symbol]["SWING"] = None if swing_selection is None else asdict(swing_selection)
        selections[symbol]["ICT"] = None if ict_selection is None else asdict(ict_selection)
        candidate_rankings[symbol]["SWING"] = swing_ranking
        candidate_rankings[symbol]["ICT"] = ict_ranking

        if swing_selection is not None:
            selected_swing_frames.append(apply_selection(swing_symbol, swing_selection))
        if ict_selection is not None:
            selected_ict_frames.append(apply_selection(ict_symbol, ict_selection))

    active_swing_symbols = {frame.iloc[0]["symbol"] for frame in selected_swing_frames if not frame.empty}
    active_ict_symbols = {frame.iloc[0]["symbol"] for frame in selected_ict_frames if not frame.empty}
    all_ten_sleeves_active = active_swing_symbols == set(SYMBOLS) and active_ict_symbols == set(SYMBOLS)

    if not selected_swing_frames or not selected_ict_frames:
        raise RuntimeError("No validated swing or ICT sleeves were found")

    swing_selected = pd.concat(selected_swing_frames, ignore_index=True, sort=False)
    ict_selected = pd.concat(selected_ict_frames, ignore_index=True, sort=False)
    swing_selected = all_in_cost(swing_selected, ALL_IN_COST_R["SWING"])
    ict_selected = all_in_cost(ict_selected, ALL_IN_COST_R["ICT_WIDE"])

    # Include the legacy GBP M1 stream strictly at observation risk. It is not
    # counted as the validated ICT sleeve for five-symbol coverage.
    try:
        legacy = load_ict_candidates(LEGACY_ICT_SOURCE)
        legacy = filter_window(legacy, start, latest)
    except Exception:
        legacy = pd.DataFrame()
    if not legacy.empty:
        legacy = legacy.copy()
        legacy["setup"] = legacy.apply(
            lambda row: f"v14_6_legacy_{str(row['symbol']).lower()}_{str(row['setup']).lower()}",
            axis=1,
        )
        legacy["risk_percent"] = OBSERVATION_RISK
        legacy = all_in_cost(legacy, ALL_IN_COST_R["ICT_LEGACY_M1"])
        ict_selected = pd.concat([ict_selected, legacy], ignore_index=True, sort=False)
        ict_selected = ict_selected.sort_values(["entry_time", "symbol", "engine"])

    best_safe, search_rows, best_trades, best_skipped, best_events = portfolio_search(
        swing_selected, ict_selected
    )
    best_trades.to_csv(OUT / "best_safe_trades.csv", index=False)
    best_skipped.to_csv(OUT / "best_safe_skipped.csv", index=False)
    best_events.to_csv(OUT / "best_safe_governor_events.csv", index=False)
    write_csv(OUT / "portfolio_risk_search.csv", search_rows)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "target": {
            "starting_balance": STARTING_BALANCE,
            "net_profit": TARGET_NET_PROFIT,
            "ending_balance": TARGET_ENDING_BALANCE,
            "after_retail_costs_and_fees": True,
        },
        "all_in_cost_r": ALL_IN_COST_R,
        "risk_limits": {
            "max_swing_trade_percent": MAX_SWING_RISK,
            "max_ict_trade_percent": MAX_ICT_RISK,
            "max_closed_drawdown_percent": MAX_CLOSED_DD,
            "max_stress_drawdown_percent": MAX_STRESS_DD,
            "max_ict_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_ict_open_risk_percent,
            "max_combined_open_risk_percent": profit_profile.PORTFOLIO_GUARD.max_combined_open_risk_percent,
        },
        "selections": selections,
        "candidate_rankings": candidate_rankings,
        "coverage": {
            "active_swing_symbols": sorted(active_swing_symbols),
            "active_ict_symbols": sorted(active_ict_symbols),
            "all_ten_sleeves_active": all_ten_sleeves_active,
        },
        "best_safe_portfolio": best_safe,
        "target_reached": bool(best_safe["target_reached"]),
        "target_gap": round(max(0.0, TARGET_NET_PROFIT - float(best_safe["net_profit"])), 2),
        "attribution": attribution(best_trades),
    }
    (OUT / "v14_6_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(payload)
    plot_search(search_rows)

    print(
        json.dumps(
            {
                "window": payload["window"],
                "coverage": payload["coverage"],
                "best_safe_portfolio": best_safe,
                "target_reached": payload["target_reached"],
                "target_gap": payload["target_gap"],
                "attribution": payload["attribution"],
                "output": str(OUT),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
