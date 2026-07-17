"""V14.11 macro carry external-data research replay.

The existing V14.9 SWING + ICT portfolio is retained. Candidate macro carry
profiles are selected only from 2016-03/2022 evidence, then combined with the
same 365-day walk-forward sleeve governor and portfolio drawdown controls.
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

from mt5_ai_bridge import v14_11_macro_carry as carry_engine  # noqa: E402
from research import v14_8_dukascopy_2016_2026_backtest as external  # noqa: E402
from research import v14_8_strict_all_ten_20k as v148  # noqa: E402
from research import v14_9_fxcm_walk_forward as v149  # noqa: E402

SYMBOLS = v149.SYMBOLS
FXCM_DATA = ROOT / "research" / "fxcm_2016_2026_data"
RATE_DATA = ROOT / "research" / "fred_short_rates_2014_2026"
OUT = ROOT / "research" / "v14_11_macro_carry_output"
TEST_START = v149.TEST_START
TRAIN_END = v149.TRAIN_END
VALIDATION_END = v149.VALIDATION_END
AUDIT_END = v149.AUDIT_END
FRESH_START = v149.FRESH_START
TEST_END = v149.TEST_END
PORTFOLIO_START = v149.PORTFOLIO_START
TARGET_NET = 20_000.0
STRICT_PROJECTED_STRESS_LIMIT = 9.45
MAX_CARRY_OPEN_RISK = 0.90
MAX_SELECTED_CARRY_SLEEVES = 3
CARRY_TRAILING_DAYS = 1095
CARRY_MIN_TRAILING_TRADES = 5


@dataclass(frozen=True)
class SelectedCarrySleeve:
    symbol: str
    profile: str
    family: str
    setup: str
    risk_percent: float
    selection_score: float


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v149.ratio_stats(frame)


def load_rates() -> dict[str, pd.DataFrame]:
    manifest_path = RATE_DATA / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result: dict[str, pd.DataFrame] = {}
    for item in manifest["downloads"]:
        currency = str(item["currency"])
        path = ROOT / str(item["file"])
        frame = pd.read_csv(path)
        frame["observation_date"] = pd.to_datetime(
            frame["observation_date"], utc=True, errors="coerce"
        )
        frame["rate_percent"] = pd.to_numeric(
            frame["rate_percent"], errors="coerce"
        )
        frame = frame.dropna().sort_values("observation_date")
        frame["available_date"] = frame["observation_date"] + pd.Timedelta(days=45)
        result[currency] = frame[
            ["available_date", "rate_percent"]
        ].drop_duplicates("available_date")
    if set(result) != {"USD", "GBP", "EUR", "AUD", "JPY"}:
        raise RuntimeError(f"Incomplete currency-rate coverage: {sorted(result)}")
    return result


def development_evidence(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        "training": ratio_stats(
            frame[(frame["entry_time"] >= TEST_START) & (frame["entry_time"] <= TRAIN_END)]
        ),
        "validation": ratio_stats(
            frame[(frame["entry_time"] > TRAIN_END) & (frame["entry_time"] <= VALIDATION_END)]
        ),
        "audit": ratio_stats(
            frame[(frame["entry_time"] > VALIDATION_END) & (frame["entry_time"] <= AUDIT_END)]
        ),
    }


def validate_evidence(evidence: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
    minimums = {"training": 8, "validation": 5, "audit": 3}
    for name, minimum in minimums.items():
        stats = evidence[name]
        if int(stats["trades"]) < minimum:
            return False, f"insufficient {name}: {stats}"
        if float(stats["net_r"]) <= 0.0:
            return False, f"negative {name}: {stats}"
        if float(stats["profit_factor"] or 0.0) < 1.03:
            return False, f"weak {name} PF: {stats}"
    return True, None


def evidence_score(evidence: dict[str, dict[str, Any]]) -> float:
    minimum_expectancy = min(
        float(evidence[name]["expectancy_r"] or 0.0) for name in evidence
    )
    minimum_pf = min(float(evidence[name]["profit_factor"] or 0.0) for name in evidence)
    trades = sum(int(evidence[name]["trades"]) for name in evidence)
    return minimum_expectancy * math.sqrt(max(1, trades)) + 0.025 * (minimum_pf - 1.0)


def risk_tier(evidence: dict[str, dict[str, Any]]) -> float:
    minimum_pf = min(float(evidence[name]["profit_factor"] or 0.0) for name in evidence)
    minimum_expectancy = min(
        float(evidence[name]["expectancy_r"] or 0.0) for name in evidence
    )
    if minimum_pf >= 1.55 and minimum_expectancy >= 0.12:
        return 0.35
    if minimum_pf >= 1.30 and minimum_expectancy >= 0.06:
        return 0.30
    return 0.20


def build_carry_candidates(
    market: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    rates: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        base_currency, quote_currency = carry_engine.PAIR_CURRENCIES[symbol]
        daily = market[symbol][2]
        for spec in carry_engine.candidate_specs():
            generated = carry_engine.generate_candidates(
                symbol,
                daily,
                rates[base_currency],
                rates[quote_currency],
                spec,
            )
            if generated.empty:
                continue
            generated["entry_time"] = pd.to_datetime(
                generated["entry_time"], utc=True
            )
            generated["exit_time"] = pd.to_datetime(generated["exit_time"], utc=True)
            generated = generated[
                (generated["entry_time"] >= TEST_START)
                & (generated["entry_time"] <= TEST_END)
            ].copy()
            generated["raw_r_multiple"] = pd.to_numeric(
                generated["r_multiple"], errors="coerce"
            )
            generated["cost_r"] = pd.to_numeric(
                generated["selection_cost_r"], errors="coerce"
            )
            generated["r_multiple"] = (
                generated["raw_r_multiple"] - generated["cost_r"]
            )
            frames.append(generated)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False).sort_values(
        ["entry_time", "symbol", "profile"]
    ).reset_index(drop=True)


def select_carry_sleeves(
    candidates: pd.DataFrame,
) -> tuple[list[SelectedCarrySleeve], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    passing: list[SelectedCarrySleeve] = []
    for (symbol, profile, family), group in candidates.groupby(
        ["symbol", "profile", "family"], sort=True
    ):
        evidence = development_evidence(group)
        passed, error = validate_evidence(evidence)
        score = evidence_score(evidence) if passed else -999.0
        risk = risk_tier(evidence) if passed else 0.0
        fresh = ratio_stats(
            group[(group["entry_time"] >= FRESH_START) & (group["entry_time"] <= TEST_END)]
        )
        rows.append(
            {
                "symbol": str(symbol),
                "profile": str(profile),
                "family": str(family),
                "passed_pre_holdout": passed,
                "failure_reason": error,
                "selection_score": score,
                "risk_percent": risk,
                "development_evidence": evidence,
                "fresh_shadow_evidence": fresh,
            }
        )
        if passed:
            passing.append(
                SelectedCarrySleeve(
                    symbol=str(symbol),
                    profile=str(profile),
                    family=str(family),
                    setup=f"v14_11_{str(symbol).lower()}_{str(profile).lower()}",
                    risk_percent=risk,
                    selection_score=score,
                )
            )

    # Select the strongest profile for each symbol, then the strongest three
    # symbols. This rule is based only on the pre-holdout score.
    best_by_symbol: dict[str, SelectedCarrySleeve] = {}
    for sleeve in sorted(passing, key=lambda item: item.selection_score, reverse=True):
        if sleeve.symbol not in best_by_symbol:
            best_by_symbol[sleeve.symbol] = sleeve
    selected = sorted(
        best_by_symbol.values(), key=lambda item: item.selection_score, reverse=True
    )[:MAX_SELECTED_CARRY_SLEEVES]
    return selected, rows


def materialize_carry(
    candidates: pd.DataFrame,
    sleeve: SelectedCarrySleeve,
) -> pd.DataFrame:
    frame = candidates[
        (candidates["symbol"].astype(str) == sleeve.symbol)
        & (candidates["profile"].astype(str) == sleeve.profile)
    ].copy()
    frame = frame.sort_values(["entry_time", "exit_time"]).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "profile", "side"]
    )
    frame["setup"] = sleeve.setup
    frame["risk_percent"] = float(sleeve.risk_percent)
    frame["sleeve_id"] = f"{sleeve.symbol}/MACRO_CARRY"
    frame["selection_score"] = float(sleeve.selection_score)
    return frame.reset_index(drop=True)


def apply_carry_gate(frame: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for sleeve_id, group in frame.groupby("sleeve_id", sort=False):
        work = group.sort_values("entry_time").copy()
        history = group.sort_values("exit_time")
        exits = history["exit_time"].to_numpy(dtype="datetime64[ns]")
        values = pd.to_numeric(history["r_multiple"], errors="coerce").to_numpy(float)
        cumulative = np.concatenate([[0.0], np.cumsum(values)])
        gross_profit = np.concatenate(
            [[0.0], np.cumsum(np.where(values > 0, values, 0.0))]
        )
        gross_loss = np.concatenate(
            [[0.0], np.cumsum(np.where(values < 0, -values, 0.0))]
        )
        times = work["entry_time"].to_numpy(dtype="datetime64[ns]")
        lower_times = (
            work["entry_time"] - pd.Timedelta(days=CARRY_TRAILING_DAYS)
        ).to_numpy(dtype="datetime64[ns]")
        left = np.searchsorted(exits, lower_times, side="left")
        right = np.searchsorted(exits, times, side="left")
        count = right - left
        net = cumulative[right] - cumulative[left]
        wins = gross_profit[right] - gross_profit[left]
        losses = gross_loss[right] - gross_loss[left]
        pf = np.divide(
            wins,
            losses,
            out=np.full_like(wins, 99.0),
            where=losses > 0,
        )
        expectancy = np.divide(
            net,
            count,
            out=np.zeros_like(net),
            where=count > 0,
        )
        work["gate_active"] = (
            (count >= CARRY_MIN_TRAILING_TRADES)
            & (net > 0)
            & (pf >= 1.0)
            & (work["entry_time"] >= PORTFOLIO_START)
        )
        work["gate_reason"] = np.where(
            work["gate_active"], "CARRY_TRAILING_EDGE_ACTIVE", "CARRY_TRAILING_EDGE_INACTIVE"
        )
        work["trailing_trades"] = count
        work["trailing_net_r"] = net
        work["trailing_profit_factor"] = pf
        work["trailing_expectancy_r"] = expectancy
        work["gate_score"] = expectancy * np.sqrt(np.maximum(count, 1))
        outputs.append(work)
    return pd.concat(outputs, ignore_index=True, sort=False)


def build_v149_candidates(source: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    evidence_rows: list[dict[str, Any]] = []
    for sleeve in v149.SLEEVES:
        frame = v149.filter_sleeve(source, sleeve)
        evidence = v149.development_blocks(frame)
        v149.validate_development(sleeve, evidence)
        frame["selection_score"] = 0.0
        frames.append(frame)
        evidence_rows.append(
            {
                "symbol": sleeve.symbol,
                "mode": sleeve.mode,
                "profile": sleeve.profile,
                "development_evidence": evidence,
            }
        )
    return pd.concat(frames, ignore_index=True, sort=False), evidence_rows


def portfolio_admission(combined: pd.DataFrame) -> pd.DataFrame:
    active: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    ordered = combined.sort_values(
        ["entry_time", "gate_score", "selection_score"],
        ascending=[True, False, False],
    )
    for row in ordered.itertuples(index=False):
        now = pd.Timestamp(row.entry_time)
        active = [item for item in active if item["exit_time"] > now]
        accepted = bool(row.gate_active)
        reason = str(row.gate_reason)
        requested = float(row.risk_percent)
        if accepted and any(item["symbol"] == row.symbol for item in active):
            accepted, reason = False, "SYMBOL_OPEN_POSITION_LIMIT"
        total_open = sum(item["risk_percent"] for item in active)
        ict_open = sum(
            item["risk_percent"] for item in active if item["mode"] == "ICT"
        )
        carry_open = sum(
            item["risk_percent"]
            for item in active
            if item["mode"] == "MACRO_CARRY"
        )
        if accepted and total_open + requested > v149.MAX_COMBINED_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_COMBINED_RISK_CAP"
        if accepted and row.mode == "ICT" and ict_open + requested > v149.MAX_ICT_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_ICT_RISK_CAP"
        if accepted and row.mode == "MACRO_CARRY" and carry_open + requested > MAX_CARRY_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_CARRY_RISK_CAP"
        if accepted:
            active.append(
                {
                    "symbol": row.symbol,
                    "mode": row.mode,
                    "exit_time": pd.Timestamp(row.exit_time),
                    "risk_percent": requested,
                }
            )
        decisions.append(
            {
                "portfolio_admitted": bool(accepted),
                "portfolio_admission_reason": reason,
            }
        )
    return pd.concat([ordered.reset_index(drop=True), pd.DataFrame(decisions)], axis=1)


def enrich_trades(trades: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    keys = ["symbol", "setup", "side", "entry_time", "exit_time"]
    metadata_columns = keys + [
        "raw_r_multiple",
        "cost_r",
        "mode",
        "family",
        "profile",
        "sleeve_id",
        "carry_r",
        "rate_differential_percent",
    ]
    available = [column for column in metadata_columns if column in candidates.columns]
    metadata = candidates[available].drop_duplicates(keys)
    output = trades.merge(metadata, on=keys, how="left")
    output["strategy_mode"] = output["mode"].fillna(
        output["engine_group"].replace({"V12": "SWING"})
    )
    output["modeled_fee_dollars"] = output["risk_dollars"] * output["cost_r"].fillna(0.0)
    output["gross_pnl_before_modeled_cost"] = (
        output["risk_dollars"]
        * output["raw_r_multiple"].fillna(output["r_multiple"])
    )
    return output


def run_portfolio(candidates: pd.DataFrame):
    admitted = portfolio_admission(candidates)
    active = admitted[
        admitted["portfolio_admitted"]
        & (admitted["entry_time"] >= PORTFOLIO_START)
        & (admitted["entry_time"] <= TEST_END)
    ].copy()
    swing_like = active[active["mode"] != "ICT"].sort_values(
        ["entry_time", "symbol"]
    )
    ict = active[active["mode"] == "ICT"].sort_values(["entry_time", "symbol"])
    original_limit = v148.PROJECTED_STRESS_LIMIT
    v148.PROJECTED_STRESS_LIMIT = STRICT_PROJECTED_STRESS_LIMIT
    try:
        summary, trades, skipped, replay = v149.run_replay(swing_like, ict)
    finally:
        v148.PROJECTED_STRESS_LIMIT = original_limit
    trades = enrich_trades(trades, active)
    return summary, trades, skipped, replay, admitted


def mode_attribution(trades: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for mode, group in trades.groupby("strategy_mode", sort=True):
        pnl = pd.to_numeric(group["pnl"], errors="coerce").dropna()
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
        result[str(mode)] = {
            "trades": int(len(group)),
            "net_profit": round(float(pnl.sum()), 2),
            "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else 99.0,
        }
    return result


def write_report(payload: dict[str, Any]) -> None:
    baseline = payload["baseline_v14_9"]
    portfolio = payload["portfolio"]
    fresh = payload["post_selection_2022_2026"]
    lines = [
        "# V14.11 FXCM Macro Carry Backtest",
        "",
        f"**Chart window:** {TEST_START.date()} through {TEST_END.date()}",
        f"**Deployment window:** {PORTFOLIO_START.date()} through {TEST_END.date()}",
        "**Starting balance:** $5,000.00",
        "",
        "## Portfolio comparison",
        "",
        "| Metric | V14.9 | V14.11 | Change |",
        "|---|---:|---:|---:|",
        f"| Net profit | ${baseline['net_profit']:,.2f} | ${portfolio['net_profit']:,.2f} | ${portfolio['net_profit'] - baseline['net_profit']:,.2f} |",
        f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${portfolio['ending_balance']:,.2f} | ${portfolio['ending_balance'] - baseline['ending_balance']:,.2f} |",
        f"| Profit factor | {float(baseline['profit_factor'] or 0.0):.4f} | {float(portfolio['profit_factor'] or 0.0):.4f} | {float(portfolio['profit_factor'] or 0.0) - float(baseline['profit_factor'] or 0.0):.4f} |",
        f"| Max closed DD | {baseline['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent']:.4f}% | {portfolio['max_closed_drawdown_percent'] - baseline['max_closed_drawdown_percent']:.4f} pp |",
        f"| Stress DD | {baseline['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent']:.4f}% | {portfolio['stress_drawdown_percent'] - baseline['stress_drawdown_percent']:.4f} pp |",
        f"| Closed trades | {baseline['closed_trades']} | {portfolio['closed_trades']} | {portfolio['closed_trades'] - baseline['closed_trades']} |",
        "",
        "## Selected carry sleeves",
        "",
        "| Symbol | Family | Profile | Risk | Selection score | Post-selection net R | Post-selection PF |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    evidence = {
        (item["symbol"], item["profile"]): item for item in payload["carry_evidence"]
    }
    for sleeve in payload["selected_carry_sleeves"]:
        item = evidence[(sleeve["symbol"], sleeve["profile"])]
        post = item["fresh_shadow_evidence"]
        lines.append(
            f"| {sleeve['symbol']} | {sleeve['family']} | {sleeve['profile']} | "
            f"{sleeve['risk_percent']:.2f}% | {sleeve['selection_score']:.4f} | "
            f"{post['net_r']:.4f} | {float(post['profit_factor'] or 0.0):.4f} |"
        )
    lines += [
        "",
        "## Post-selection March 2022-May 2026",
        "",
        f"- Portfolio trades: {fresh['trades']}",
        f"- Portfolio net profit: **${fresh['net_profit']:,.2f}**",
        f"- Portfolio profit factor: **{float(fresh['profit_factor'] or 0.0):.4f}**",
        "",
        "## Method boundary",
        "",
        "- Macro carry is independent of the existing SWING and ICT price-pattern engines.",
        "- Monthly OECD overnight-rate observations are delayed by 45 days before use.",
        "- Carry income is haircut by 50% and a further 0.10R all-in execution reserve is deducted.",
        "- Profiles are selected from 2016-2018 training, 2019-2020 validation and 2021-March 2022 audit only.",
        "- The 2022-2026 interval is a post-selection evaluation, but it is not described as a pristine project-level holdout because that market period has been inspected in earlier model research.",
        "- One position per symbol, 0.90% carry open risk, 1.75% ICT open risk and 3.25% combined open risk are enforced.",
        "- The 7.5/8.5/9.0/9.6 drawdown governor and 9.45% projected-stress ceiling remain active.",
        "- This is a bar-based research test, not a guarantee of broker swap credits or future profit.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    external.DATA = FXCM_DATA
    external.OUT = OUT
    external.TEST_START = TEST_START
    external.TEST_END = TEST_END
    external.FRESH_START = FRESH_START
    market, quality = external.load_market()
    rates = load_rates()

    existing_source = external.build_external_candidates(market)
    baseline_candidates, baseline_evidence = build_v149_candidates(existing_source)
    baseline_summary, baseline_trades, _, _, _ = run_portfolio(baseline_candidates)
    baseline_trades.to_csv(OUT / "baseline_v14_9_closed_trades.csv", index=False)

    carry_candidates = build_carry_candidates(market, rates)
    if carry_candidates.empty:
        raise RuntimeError("Macro carry generated no candidates")
    carry_candidates.to_csv(OUT / "all_macro_carry_candidates.csv", index=False)
    selected, carry_evidence = select_carry_sleeves(carry_candidates)
    if not selected:
        raise RuntimeError("No macro carry profile passed pre-holdout validation")
    selected_frames = [materialize_carry(carry_candidates, sleeve) for sleeve in selected]
    selected_carry = pd.concat(selected_frames, ignore_index=True, sort=False)
    selected_carry.to_csv(OUT / "selected_macro_carry_candidates.csv", index=False)
    gated_carry = apply_carry_gate(selected_carry)

    combined = pd.concat([baseline_candidates, gated_carry], ignore_index=True, sort=False)
    combined["selection_score"] = pd.to_numeric(
        combined.get("selection_score", 0.0), errors="coerce"
    ).fillna(0.0)
    summary, trades, skipped, replay, admitted = run_portfolio(combined)
    admitted.to_csv(OUT / "macro_carry_gate_and_admission.csv", index=False)
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(
        OUT / "closed_drawdown_governor_events.csv", index=False
    )
    pd.DataFrame(replay.projected_stress_events).to_csv(
        OUT / "projected_stress_governor_events.csv", index=False
    )

    monthly, annual = external.time_series(trades)
    monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
    annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
    external.plot_outputs(monthly, annual, trades)

    fresh_trades = trades[
        (pd.to_datetime(trades["entry_time"], utc=True) >= FRESH_START)
        & (pd.to_datetime(trades["entry_time"], utc=True) <= TEST_END)
    ]
    carry_trades = trades[trades["strategy_mode"] == "MACRO_CARRY"]
    fresh_carry = carry_trades[
        (pd.to_datetime(carry_trades["entry_time"], utc=True) >= FRESH_START)
        & (pd.to_datetime(carry_trades["entry_time"], utc=True) <= TEST_END)
    ]
    safe = (
        float(summary["max_closed_drawdown_percent"]) <= 9.60
        and float(summary["stress_drawdown_percent"]) <= 10.00
    )
    payload = {
        "generated_at": datetime.now().isoformat(),
        "provider": "FXCM H1 bid/ask plus FRED/OECD monthly short rates",
        "window": {"start": TEST_START.isoformat(), "end": TEST_END.isoformat()},
        "portfolio_window": {"start": PORTFOLIO_START.isoformat(), "end": TEST_END.isoformat()},
        "rate_publication_lag_days": 45,
        "carry_haircut": 0.50,
        "carry_cost_reserve_r": 0.10,
        "data_quality": quality,
        "baseline_evidence": baseline_evidence,
        "carry_evidence": carry_evidence,
        "selected_carry_sleeves": [asdict(item) for item in selected],
        "baseline_v14_9": baseline_summary,
        "portfolio": {**summary, "safe": safe},
        "improvement": {
            "net_profit": round(float(summary["net_profit"] - baseline_summary["net_profit"]), 2),
            "ending_balance": round(float(summary["ending_balance"] - baseline_summary["ending_balance"]), 2),
            "profit_factor": round(
                float(summary["profit_factor"] or 0.0) - float(baseline_summary["profit_factor"] or 0.0),
                6,
            ),
        },
        "post_selection_2022_2026": v149.dollar_stats(fresh_trades),
        "macro_carry_2022_2026": v149.dollar_stats(fresh_carry),
        "attribution_by_mode": mode_attribution(trades),
        "target": {"net_profit": TARGET_NET, "ending_balance": v148.STARTING_BALANCE + TARGET_NET},
        "target_reached": float(summary["net_profit"]) >= TARGET_NET,
        "target_gap": round(max(0.0, TARGET_NET - float(summary["net_profit"])), 2),
        "total_modeled_fee_dollars": round(float(trades["modeled_fee_dollars"].sum()), 2),
        "monthly": monthly.assign(month=monthly["month"].astype(str)).to_dict("records"),
        "annual": annual.to_dict("records"),
    }
    (OUT / "v14_11_macro_carry_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    pd.DataFrame(
        [
            {
                "symbol": item["symbol"],
                "profile": item["profile"],
                "family": item["family"],
                "passed_pre_holdout": item["passed_pre_holdout"],
                "failure_reason": item["failure_reason"],
                "selection_score": item["selection_score"],
                "risk_percent": item["risk_percent"],
                **{
                    f"{block}_{metric}": value
                    for block, stats in item["development_evidence"].items()
                    for metric, value in stats.items()
                },
                **{
                    f"post_selection_{metric}": value
                    for metric, value in item["fresh_shadow_evidence"].items()
                },
            }
            for item in carry_evidence
        ]
    ).to_csv(OUT / "macro_carry_evidence.csv", index=False)
    write_report(payload)
    print(
        json.dumps(
            {
                "baseline_v14_9": baseline_summary,
                "portfolio": payload["portfolio"],
                "improvement": payload["improvement"],
                "selected_carry_sleeves": payload["selected_carry_sleeves"],
                "post_selection_2022_2026": payload["post_selection_2022_2026"],
                "macro_carry_2022_2026": payload["macro_carry_2022_2026"],
                "attribution_by_mode": payload["attribution_by_mode"],
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
