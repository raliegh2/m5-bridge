"""Research-only comparison of baseline V17 versus 20% higher swing risk.

Only completed-H4 swing engines are scaled. The incumbent GBPUSD precision
anchor/satellite is left unchanged. Both scenarios pass through the same V17
adaptive guard and the same portfolio position, symbol, GBP, basket and total
open-risk limits.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import v13_expanded_assets_backtest as base
from v17_guard import GuardConfig
from v17_quality_swing_runner import component_rows
from v17_replay_core import replay
from v17_select_core import merge_frames
from v17_selector import select_symbol

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v17_risk120_output"
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
SWING_RISK_MULTIPLIER = 1.20
SATELLITE_ENGINES = frozenset({"GBPUSD_V10_PRECISION"})


def is_swing_engine(engine: str) -> bool:
    """Identify only the V17 completed-H4 swing families."""
    name = str(engine).upper()
    return name.endswith("_SWING_BREAKOUT") or name.endswith("_SWING_PULLBACK")


def apply_swing_risk_multiplier(
    candidates: pd.DataFrame,
    multiplier: float = SWING_RISK_MULTIPLIER,
) -> pd.DataFrame:
    """Return a copy with swing risk scaled and satellite risk untouched."""
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    adjusted = candidates.copy()
    if adjusted.empty:
        return adjusted
    adjusted["original_risk_percent"] = adjusted["risk_percent"].astype(float)
    swing_mask = adjusted["engine"].map(is_swing_engine)
    adjusted.loc[swing_mask, "risk_percent"] = (
        adjusted.loc[swing_mask, "risk_percent"].astype(float) * multiplier
    )
    return adjusted


def _scenario_summary(candidates: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    summary, accepted = replay(candidates, start, end)
    summary["components"] = component_rows(accepted, start, end)
    return summary, accepted


def _flat_summary(label: str, scenario: str, summary: dict) -> dict:
    return {
        "period": label,
        "scenario": scenario,
        **{key: value for key, value in summary.items() if key != "components"},
    }


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

    baseline_candidates = merge_frames(selected_frames)
    boosted_candidates = apply_swing_risk_multiplier(baseline_candidates)
    baseline_candidates.to_csv(OUT / "baseline_candidates.csv", index=False)
    boosted_candidates.to_csv(OUT / "risk120_candidates.csv", index=False)

    satellite = boosted_candidates[boosted_candidates["engine"].isin(SATELLITE_ENGINES)]
    if not satellite.empty and not (
        satellite["risk_percent"].astype(float)
        == satellite["original_risk_percent"].astype(float)
    ).all():
        raise AssertionError("satellite risk changed during swing-only test")

    end = min(prepared[symbol][1]["time"].max() for symbol in SYMBOLS)
    start_all = max(prepared[symbol][1]["time"].min() for symbol in SYMBOLS)
    config = base.PortfolioConfig()
    results = {
        "status": "RESEARCH_ONLY",
        "data_source": base.DATA_URL,
        "common_start": start_all.isoformat(),
        "common_end": end.isoformat(),
        "starting_balance": 5000.0,
        "swing_risk_multiplier": SWING_RISK_MULTIPLIER,
        "satellite_engines_unchanged": sorted(SATELLITE_ENGINES),
        "portfolio_limits": asdict(config),
        "guard": asdict(GuardConfig()),
        "selection": selection,
        "windows": {},
    }
    overview: list[dict] = []
    components: list[dict] = []

    for label, offset in WINDOWS:
        start = max(start_all, end - offset)
        baseline, baseline_accepted = _scenario_summary(
            baseline_candidates, start, end
        )
        boosted, boosted_accepted = _scenario_summary(
            boosted_candidates, start, end
        )
        delta = {
            "net_profit": boosted["net_profit"] - baseline["net_profit"],
            "average_monthly_net": (
                boosted["average_monthly_net"] - baseline["average_monthly_net"]
            ),
            "trades": boosted["trades"] - baseline["trades"],
            "max_drawdown_percent": (
                boosted["max_drawdown_percent"] - baseline["max_drawdown_percent"]
            ),
            "stress_drawdown_percent": (
                boosted["stress_drawdown_percent"]
                - baseline["stress_drawdown_percent"]
            ),
        }
        results["windows"][label] = {
            "baseline": baseline,
            "swing_risk_120": boosted,
            "delta": delta,
        }

        baseline_accepted.to_csv(OUT / f"accepted_{label}_baseline.csv", index=False)
        boosted_accepted.to_csv(OUT / f"accepted_{label}_risk120.csv", index=False)
        overview.extend(
            (
                _flat_summary(label, "baseline", baseline),
                _flat_summary(label, "swing_risk_120", boosted),
            )
        )
        for scenario, summary in (("baseline", baseline), ("swing_risk_120", boosted)):
            for item in summary["components"]:
                components.append({"period": label, "scenario": scenario, **item})

    recent = ("1y", "6m")
    no_recent_loss_amplification = all(
        results["windows"][period]["swing_risk_120"]["net_profit"]
        >= results["windows"][period]["baseline"]["net_profit"]
        for period in recent
    )
    within_five_percent_closed_dd = all(
        results["windows"][period]["swing_risk_120"]["max_drawdown_percent"] <= 5.0
        for period, _ in WINDOWS
    )
    results["decision"] = {
        "no_recent_loss_amplification": no_recent_loss_amplification,
        "all_closed_drawdowns_at_or_below_5_percent": within_five_percent_closed_dd,
        "promote": no_recent_loss_amplification and within_five_percent_closed_dd,
        "rule": (
            "Do not promote if increasing swing risk worsens either the one-year "
            "or six-month net result, or if any closed-equity drawdown exceeds 5%."
        ),
    }

    pd.DataFrame(overview).to_csv(OUT / "scenario_overview.csv", index=False)
    pd.DataFrame(components).to_csv(OUT / "component_overview.csv", index=False)
    (OUT / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
