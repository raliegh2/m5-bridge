"""Exact-ten-year V14.13 transaction-cost regime comparison.

Rebuilds the documented enhanced V14.3 candidate stream and compares the
unchanged V14.3 allocation with a cost-regime overlay under zero, demo, retail,
and stressed transaction-cost reserves.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor
from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    PORTFOLIO_GUARD,
    scaled_risk_percent,
)
from mt5_ai_bridge.v14_3_satellite_symbol_profile import (
    apply_satellite_v12_risk,
    filter_satellite_ict,
    install_satellite_ict_risk,
)
from mt5_ai_bridge.v14_13_cost_regime_profile import (
    CostRegimeConfig,
    cost_regime_decision,
    strict_retail_profile,
)
from research.v14_3_drawdown_limited_backtest_v2 import AdmissionPreservingReplay
from research.v14_3_five_symbol_ict_10y_backtest import (
    build_new_ict_candidates,
    install_all_symbol_ict_profile,
)
from research.v14_3_production_improved_backtest import (
    filter_window,
    load_ict_candidates,
    load_v12,
    summarize,
)
from research.v14_3_satellite_symbol_profit_backtest import combine_ict, run_replay
from research.v14_3_satellite_symbol_profit_backtest_v2 import load_raw_shadow_candidates

ROOT = Path(__file__).resolve().parents[1]
V12_LEDGER = ROOT / "research" / "v12_final_ledger_output" / "v12_final_trade_ledger.csv"
ICT_SOURCE = (
    ROOT
    / "research"
    / "v14_3_true_combined_v12_ict_output"
    / "true_combined_closed_trades.csv"
)
OUT = ROOT / "research" / "v14_13_cost_regime_output"
GEN = OUT / "generated_candidates"

EXPECTED_ZERO_COST_NET = 34_690.840749742056

COST_SCENARIOS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
    "stress_cost": {"V12": 0.05, "ICT": 0.18},
}

V12_TARGET_R = {
    ("GBPUSD_V10_PRECISION", "PRIMARY_16UTC_BREAKOUT"): 3.0,
    ("GBPUSD_V10_PRECISION", "SECONDARY_12UTC_BREAKOUT"): 3.0,
    ("GBPUSD_V10_PRECISION", "GBPUSD_SWING_V5_PULLBACK_ADDON"): 2.5,
    ("GBPUSD_SWING_RETEST", "H4_BREAKOUT_RETEST"): 4.0,
    ("EURUSD_SWING_CORE", "H4_DONCHIAN_BREAKOUT"): 3.0,
    ("EURUSD_SWING_RETEST", "H1_BREAKOUT_RETEST"): 3.0,
    ("GBPJPY_SWING_CORE", "H4_DONCHIAN_BREAKOUT"): 3.0,
    ("AUDUSD_TREND_PULLBACK", "D1_H4_EMA_PULLBACK_04_08UTC"): 2.0,
    ("USDJPY_SAFE_HAVEN_BREAKOUT", "D1_H4_40BAR_BREAKOUT"): 3.0,
}
ICT_TARGET_R = {
    "eurusd_ict_liquidity": 2.0,
    "audusd_ict_asia_london": 1.5,
    "usdjpy_ict_session_sweep": 1.5,
}


def prepare() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    GEN.mkdir(parents=True, exist_ok=True)
    install_all_symbol_ict_profile()
    v12 = apply_weak_symbol_profile(load_v12(V12_LEDGER))
    existing_ict = load_ict_candidates(ICT_SOURCE)
    _, selection = build_new_ict_candidates(GEN)
    raw_new = load_raw_shadow_candidates(GEN)

    install_satellite_ict_risk()
    live_v12 = apply_satellite_v12_risk(v12)
    live_new = filter_satellite_ict(raw_new)
    live_ict = combine_ict(existing_ict, live_new)
    return live_v12, live_ict, selection


def apply_cost(frame: pd.DataFrame, cost_r: float) -> pd.DataFrame:
    output = frame.copy()
    output["raw_r_multiple"] = output["r_multiple"].astype(float)
    output["cost_r"] = float(cost_r)
    output["r_multiple"] = output["raw_r_multiple"] - float(cost_r)
    return output


class CostRegimeReplay(AdmissionPreservingReplay):
    """Same chronology, loss controls and caps, with V14.13 pre-entry risk caps."""

    def __init__(
        self,
        v12: pd.DataFrame,
        ict: pd.DataFrame,
        governor: DrawdownGovernor,
        costs: dict[str, float],
        config: CostRegimeConfig,
    ) -> None:
        super().__init__(v12, ict, governor)
        self.costs = {key: float(value) for key, value in costs.items()}
        self.cost_config = config
        self.cost_regime_events: list[dict[str, Any]] = []

    @staticmethod
    def _target_r(group: str, row: dict[str, Any]) -> float:
        if group == "V12":
            return float(V12_TARGET_R[(str(row["engine"]), str(row["setup"]))])
        return float(ICT_TARGET_R.get(str(row["setup"]), 1.25))

    def run(self):
        stream = [(row["entry_time"], "V12", row) for row in self.v12.to_dict("records")]
        stream += [(row["entry_time"], "ICT", row) for row in self.ict.to_dict("records")]
        stream.sort(key=lambda item: (item[0], 0 if item[1] == "V12" else 1))

        for now, group, row in stream:
            self.close_due(now)
            self.reset_day(now)
            total_open = sum(item["risk_percent"] for item in self.active)
            ict_open = sum(
                item["risk_percent"]
                for item in self.active
                if item["engine_group"] == "ICT"
            )

            if group == "V12":
                base_risk = float(row["risk_percent"])
            else:
                blocked = self.reject_reason(row, now)
                if blocked:
                    self.skipped.append({**row, "skip_reason": blocked})
                    continue
                symbol = str(row["symbol"])
                pressure = (
                    self.day.global_consecutive_losses > 0
                    or self.day.loss_pressure[symbol] > 0
                    or self.day.daily_pnl[symbol] < 0
                )
                base_risk = scaled_risk_percent(
                    symbol,
                    str(row["setup"]),
                    self.dd(),
                    pressure,
                )

            cost_r = self.costs[group]
            target_r = self._target_r(group, row)
            decision = cost_regime_decision(
                symbol=str(row["symbol"]),
                engine=str(row["engine"]),
                setup=str(row["setup"]),
                mode=group,
                side=str(row.get("side", "")),
                entry_time=now,
                base_risk_percent=base_risk,
                all_in_cost=cost_r,
                target_r=target_r,
                config=self.cost_config,
            )
            self.cost_regime_events.append(
                {
                    "entry_time": now,
                    "symbol": row["symbol"],
                    "engine": row["engine"],
                    "setup": row["setup"],
                    "side": row.get("side", ""),
                    **decision.__dict__,
                }
            )
            if decision.is_shadow:
                self.skipped.append(
                    {
                        **row,
                        "skip_reason": "V14_13_COST_REGIME_SHADOW",
                        "cost_regime_reason": decision.reason,
                        "all_in_cost_r": cost_r,
                    }
                )
                continue

            requested = min(base_risk, float(decision.risk_percent))
            if group == "ICT":
                if ict_open + requested > PORTFOLIO_GUARD.max_ict_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "ICT_OPEN_RISK_CAP"})
                    continue
                if total_open + requested > PORTFOLIO_GUARD.max_combined_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"})
                    continue

            current_dd = self.dd()
            approved = self.governor.apply(requested, current_dd)
            if approved <= 0:
                self.skipped.append({**row, "skip_reason": "DRAWDOWN_GOVERNOR_HARD_STOP"})
                continue
            if approved < requested - 1e-12:
                self.governor_events.append(
                    {
                        "entry_time": now,
                        "symbol": row["symbol"],
                        "engine": row["engine"],
                        "drawdown_percent": current_dd,
                        "requested_risk_percent": requested,
                        "approved_risk_percent": approved,
                        "multiplier": approved / requested if requested else 0.0,
                    }
                )

            raw_r = float(row["r_multiple"])
            net_r = raw_r - cost_r
            item = {
                "trade_id": self.trade_id,
                "engine_group": group,
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row.get("side", ""),
                "entry_time": now,
                "exit_time": row["exit_time"],
                "risk_percent": requested,
                "executed_risk_percent": approved,
                "risk_dollars": self.balance * approved / 100.0,
                "raw_r_multiple": raw_r,
                "cost_r": cost_r,
                "r_multiple": net_r,
                "cost_regime": decision.regime,
                "cost_regime_reason": decision.reason,
                "admission_reason": "V14_13_COST_REGIME",
            }
            self.trade_id += 1
            self.active.append(item)
            if group == "ICT":
                self.day.total_entries.append(now)
                self.day.entries[str(item["symbol"])].append(now)
            stressed = self.balance - sum(x["risk_dollars"] for x in self.active)
            self.stress_dd = max(
                self.stress_dd,
                (self.peak - stressed) / self.peak * 100.0,
            )

        self.close_due(pd.Timestamp.max.tz_localize("UTC"))
        summary = summarize(
            PORTFOLIO_GUARD.starting_balance,
            self.balance,
            self.max_dd,
            self.stress_dd,
            self.closed,
            self.skipped,
        )
        trades = pd.DataFrame(self.closed)
        summary["drawdown_governor"] = self.governor.__dict__
        summary["governor_interventions"] = len(self.governor_events)
        summary["cost_regime_counts"] = dict(
            Counter(trades.get("cost_regime", pd.Series(dtype=str)).astype(str))
        )
        summary["modeled_cost_dollars"] = (
            float((trades["risk_dollars"] * trades["cost_r"]).sum())
            if not trades.empty
            else 0.0
        )
        return summary, trades, pd.DataFrame(self.skipped)


def baseline_case(
    v12: pd.DataFrame,
    ict: pd.DataFrame,
    governor: DrawdownGovernor,
    costs: dict[str, float],
):
    summary, trades, skipped, events = run_replay(
        apply_cost(v12, costs["V12"]),
        apply_cost(ict, costs["ICT"]),
        governor,
    )
    summary["modeled_cost_dollars"] = (
        float(
            (
                trades["risk_dollars"]
                * trades["engine_group"].map(
                    {"V12": float(costs["V12"]), "ICT": float(costs["ICT"])}
                )
            ).sum()
        )
        if not trades.empty
        else 0.0
    )
    return summary, trades, skipped, events


def ratio_stats(values: pd.Series) -> dict[str, Any]:
    series = pd.to_numeric(values, errors="coerce").dropna()
    gross_profit = float(series[series > 0].sum())
    gross_loss = float(-series[series < 0].sum())
    return {
        "trades": int(len(series)),
        "net_r": float(series.sum()),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
    }


def strict_profile_evidence(ict: pd.DataFrame) -> dict[str, Any]:
    frame = ict.copy()
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["strict_profile"] = frame.apply(
        lambda row: strict_retail_profile(
            str(row["symbol"]),
            str(row["setup"]),
            str(row.get("side", "")),
            row["entry_time"],
        ),
        axis=1,
    )
    selected = frame[
        frame["strict_profile"]
        & frame["symbol"].isin(["GBPUSD", "GBPJPY"])
    ].copy()
    output: dict[str, Any] = {}
    for cost_name, cost_r in (("retail_cost", 0.13), ("stress_cost", 0.18)):
        cost_output: dict[str, Any] = {}
        for year in (2023, 2024, 2025, 2026):
            group = selected[selected["entry_time"].dt.year == year]
            cost_output[str(year)] = ratio_stats(
                group["r_multiple"].astype(float) - cost_r
            )
        cost_output["all"] = ratio_stats(
            selected["r_multiple"].astype(float) - cost_r
        )
        output[cost_name] = cost_output
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.13 Cost-Regime Research-Parity Backtest",
        "",
        f"**Exact ten-year window:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        "**Starting balance:** $5,000.00",
        "",
        "V14.13 leaves the documented V14.3 zero-cost allocation unchanged. When transaction cost is non-zero, it funds only cost-resilient V12/satellite sleeves and frozen GBP setup/time/side subsets. Cost-negative candidates remain in the diagnostic stream without forcing a broker order.",
        "",
        "## Exact ten-year comparison",
        "",
        "| Cost scenario | Model | Net profit | Ending balance | PF | Closed DD | Stress DD | Trades | Modeled costs |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cost_name in COST_SCENARIOS:
        for model in ("current_v14_3", "v14_13"):
            summary = payload["results"][f"{cost_name}/{model}"]["summary"]
            label = "Current V14.3" if model == "current_v14_3" else "V14.13"
            lines.append(
                f"| {cost_name} | {label} | ${summary['net_profit']:,.2f} | "
                f"${summary['ending_balance']:,.2f} | {summary['profit_factor']:.4f} | "
                f"{summary['max_closed_drawdown_percent']:.4f}% | "
                f"{summary['stress_drawdown_percent']:.4f}% | "
                f"{summary['closed_trades']} | ${summary['modeled_cost_dollars']:,.2f} |"
            )

    lines += [
        "",
        "## Frozen high-cost GBP evidence",
        "",
        "| Cost | Year | Trades | Net R | PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for cost_name, blocks in payload["strict_profile_evidence"].items():
        for year in ("2023", "2024", "2025", "2026", "all"):
            stats = blocks[year]
            lines.append(
                f"| {cost_name} | {year} | {stats['trades']} | "
                f"{stats['net_r']:.2f} | {float(stats['profit_factor'] or 0):.3f} |"
            )

    lines += [
        "",
        "## Preserved controls",
        "",
        "- Original completed-candle entry rules, stop construction, full-position exits and setup identities.",
        "- Original setup-specific maximum risk; V14.13 can only reduce or shadow a trade.",
        "- V14.4 spread gate, M1 staleness limit, broker-net expectancy tracking, daily stop and peak reconstruction.",
        "- 1.75% ICT and 3.25% combined admission caps.",
        "- 7.5/8.5/9.0/9.6 drawdown governor and symbol loss/cooldown controls.",
        "",
        "## Limitations",
        "",
        "This is an R-multiple replay with fixed cost reserves, not a tick-perfect broker simulation. Zero-cost parity is a reproducibility benchmark, not an achievable live condition. Historical profitability does not guarantee future results; READ_ONLY and demo-forward validation remain mandatory.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    v12, ict, selection = prepare()
    latest = max(v12["exit_time"].max(), ict["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    v12 = filter_window(v12, start, latest)
    ict = filter_window(ict, start, latest)

    governor = DrawdownGovernor(
        soft_start_percent=7.50,
        medium_start_percent=8.50,
        defensive_start_percent=9.00,
        hard_stop_percent=9.60,
        soft_multiplier=0.98,
        medium_multiplier=0.82,
        defensive_multiplier=0.50,
        minimum_risk_percent=0.025,
    )
    config = CostRegimeConfig()
    results: dict[str, Any] = {}
    summary_rows: list[dict[str, Any]] = []

    for cost_name, costs in COST_SCENARIOS.items():
        baseline, baseline_trades, baseline_skipped, baseline_events = baseline_case(
            v12, ict, governor, costs
        )
        replay = CostRegimeReplay(v12, ict, governor, costs, config)
        improved, improved_trades, improved_skipped = replay.run()

        folder = OUT / "ledgers" / cost_name
        folder.mkdir(parents=True, exist_ok=True)
        baseline_trades.to_csv(folder / "current_v14_3_trades.csv", index=False)
        baseline_skipped.to_csv(folder / "current_v14_3_skipped.csv", index=False)
        baseline_events.to_csv(folder / "current_v14_3_governor.csv", index=False)
        improved_trades.to_csv(folder / "v14_13_trades.csv", index=False)
        improved_skipped.to_csv(folder / "v14_13_skipped.csv", index=False)
        pd.DataFrame(replay.cost_regime_events).to_csv(
            folder / "v14_13_cost_regime_decisions.csv",
            index=False,
        )
        pd.DataFrame(replay.governor_events).to_csv(
            folder / "v14_13_governor.csv",
            index=False,
        )

        for model, summary in (
            ("current_v14_3", baseline),
            ("v14_13", improved),
        ):
            results[f"{cost_name}/{model}"] = {"summary": summary}
            summary_rows.append(
                {
                    "cost_scenario": cost_name,
                    "model": model,
                    **{
                        key: summary[key]
                        for key in (
                            "starting_balance",
                            "ending_balance",
                            "net_profit",
                            "return_percent",
                            "profit_factor",
                            "max_closed_drawdown_percent",
                            "stress_drawdown_percent",
                            "closed_trades",
                            "modeled_cost_dollars",
                        )
                    },
                }
            )

    zero_new = results["zero_cost/v14_13"]["summary"]
    if abs(float(zero_new["net_profit"]) - EXPECTED_ZERO_COST_NET) > 0.02:
        raise RuntimeError(f"V14.13 zero-cost parity failed: {zero_new}")

    payload = {
        "generated_at": datetime.now().isoformat(),
        "research_only": True,
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "cost_scenarios_r": COST_SCENARIOS,
        "cost_regime_config": config.__dict__,
        "source_selection": selection,
        "strict_profile_evidence": strict_profile_evidence(ict),
        "results": results,
    }
    (OUT / "v14_13_results.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    write_csv(OUT / "comparison_summary.csv", summary_rows)
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
