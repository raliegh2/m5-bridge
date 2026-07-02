"""Compare legacy V17, cooldown recovery, and selective swing sizing.

The signal families and portfolio caps remain unchanged. This runner isolates
execution-policy changes: first repair the unreachable mature-engine recovery
probe, then test a modest 1.10x uplift only for symbols whose development,
validation and untouched holdout segments all remain profitable with PF > 1.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import v13_expanded_assets_backtest as base
from v17_guard import GuardConfig
from v17_replay_core import replay
from v17_select_core import merge_frames
from v17_selector import select_symbol

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v17_guard_recovery_output"
OUT.mkdir(parents=True, exist_ok=True)
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
WINDOWS = (
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("4y", pd.DateOffset(years=4)),
    ("3y", pd.DateOffset(years=3)),
    ("2y", pd.DateOffset(years=2)),
    ("1y", pd.DateOffset(years=1)),
    ("6m", pd.DateOffset(months=6)),
)
SELECTIVE_UPLIFT = 1.10


def validated_policy(selection: dict, candidates: pd.DataFrame) -> dict[str, float]:
    """Select only engines supported by validation and untouched holdout."""
    policy: dict[str, float] = {}
    for symbol, report in selection.items():
        if report.get("status") != "QUALIFIED":
            continue
        validation = report.get("validation", {})
        holdout = report.get("holdout", {})
        robust = (
            validation.get("net_r", 0.0) > 0
            and validation.get("profit_factor", 0.0) > 1.0
            and holdout.get("net_r", 0.0) > 0
            and holdout.get("profit_factor", 0.0) > 1.0
        )
        if not robust:
            continue
        engines = candidates.loc[
            (candidates["symbol"] == symbol)
            & candidates["engine"].str.contains("_SWING_", regex=False),
            "engine",
        ].unique()
        for engine in engines:
            policy[str(engine)] = SELECTIVE_UPLIFT
    policy.pop("GBPUSD_V10_PRECISION", None)
    return policy


def component_rows(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    if frame.empty:
        return []
    years = max((end - start).total_seconds() / (365.25 * 86400), 0.25)
    rows = []
    for (symbol, engine), group in frame.groupby(["symbol", "engine"]):
        pnl = group["risk_dollars"] * group["r_multiple"]
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
        rows.append({
            "symbol": symbol,
            "engine": engine,
            "trades": int(len(group)),
            "trades_per_year": float(len(group) / years),
            "gross_income": gross_profit,
            "gross_loss": gross_loss,
            "net_profit": gross_profit - gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
            "recovery_probes": int(group.get("is_recovery_probe", pd.Series(False, index=group.index)).sum()),
            "selectively_sized": int((group.get("selective_multiplier", pd.Series(1.0, index=group.index)) > 1.0).sum()),
        })
    return rows


def flatten(period: str, scenario: str, summary: dict) -> dict:
    return {"period": period, "scenario": scenario, **summary}


def main() -> None:
    prepared = {symbol: base.prepare(symbol) for symbol in SYMBOLS}
    selected_frames: list[pd.DataFrame] = []
    selection: dict[str, dict] = {}
    for symbol in SYMBOLS:
        h4 = prepared[symbol][1]
        anchor = base.gbpusd_precision_candidates(h4) if symbol == "GBPUSD" else None
        selected, report = select_symbol(symbol, h4, anchor)
        selected_frames.append(selected)
        selection[symbol] = report
        selected.to_csv(OUT / f"{symbol}_selected_candidates.csv", index=False)

    candidates = merge_frames(selected_frames)
    candidates.to_csv(OUT / "all_selected_candidates.csv", index=False)
    policy = validated_policy(selection, candidates)

    end = min(prepared[symbol][1]["time"].max() for symbol in SYMBOLS)
    start_all = max(prepared[symbol][1]["time"].min() for symbol in SYMBOLS)
    scenarios = {
        "legacy_v17": {"recovery_probes": False, "selective_multipliers": None},
        "guard_recovery": {"recovery_probes": True, "selective_multipliers": None},
        "guard_recovery_selective": {
            "recovery_probes": True,
            "selective_multipliers": policy,
        },
    }

    results = {
        "status": "RESEARCH_ONLY",
        "data_source": base.DATA_URL,
        "common_start": start_all.isoformat(),
        "common_end": end.isoformat(),
        "starting_balance": 5000.0,
        "portfolio_limits": asdict(base.PortfolioConfig()),
        "guard": asdict(GuardConfig()),
        "selective_policy": policy,
        "selection": selection,
        "windows": {},
    }
    overview: list[dict] = []
    components: list[dict] = []

    for label, offset in WINDOWS:
        start = max(start_all, end - offset)
        results["windows"][label] = {}
        for scenario, options in scenarios.items():
            summary, accepted = replay(candidates, start, end, **options)
            results["windows"][label][scenario] = summary
            accepted.to_csv(OUT / f"accepted_{label}_{scenario}.csv", index=False)
            overview.append(flatten(label, scenario, summary))
            for item in component_rows(accepted, start, end):
                components.append({"period": label, "scenario": scenario, **item})

    guard_pass = all(
        results["windows"][period]["guard_recovery"]["net_profit"]
        > results["windows"][period]["legacy_v17"]["net_profit"]
        for period in ("10y", "5y", "4y")
    ) and all(
        results["windows"][period]["guard_recovery"]["net_profit"]
        >= results["windows"][period]["legacy_v17"]["net_profit"] - 25.0
        for period in ("1y", "6m")
    ) and max(
        results["windows"][period]["guard_recovery"]["stress_drawdown_percent"]
        for period, _ in WINDOWS
    ) <= 6.50

    selective_pass = all(
        results["windows"][period]["guard_recovery_selective"]["net_profit"]
        >= results["windows"][period]["guard_recovery"]["net_profit"]
        for period in ("5y", "4y", "2y", "1y", "6m")
    )

    results["decision"] = {
        "guard_recovery_pass": guard_pass,
        "selective_sizing_pass": selective_pass,
        "recommended_scenario": "guard_recovery" if guard_pass else "legacy_v17",
        "rule": (
            "Promote recovery only if 10y/5y/4y net improve, recent 1y/6m do not "
            "deteriorate by more than $25, and stress drawdown stays <= 6.50%. "
            "Promote selective sizing only if it does not reduce net profit in "
            "5y, 4y, 2y, 1y or 6m versus recovery alone."
        ),
    }

    pd.DataFrame(overview).to_csv(OUT / "scenario_overview.csv", index=False)
    pd.DataFrame(components).to_csv(OUT / "component_overview.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
