"""V14.10 multi-engine FXCM research portfolio.

V14.9's validated SWING + ICT sleeves are retained. Three additional,
independent engine classes are generated from the same completed FXCM candles:
BREAKOUT, MOMENTUM and MEAN_REVERSION. Alternative specifications are selected
strictly from 2016-03/2022 training, validation and audit evidence. The frozen
selection is then evaluated on the untouched March 2022-May 2026 interval.

Research only: no MT5 account, broker connection, order transmission or AUTO
integration is present.
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

from mt5_ai_bridge import v14_10_alternative_engines as alt_engine  # noqa: E402
from research import v14_8_dukascopy_2016_2026_backtest as external  # noqa: E402
from research import v14_8_strict_all_ten_20k as v148  # noqa: E402
from research import v14_9_fxcm_walk_forward as v149  # noqa: E402

SYMBOLS = v149.SYMBOLS
DATA = ROOT / "research" / "fxcm_2016_2026_data"
OUT = ROOT / "research" / "v14_10_multi_engine_fxcm_output"
TEST_START = v149.TEST_START
TRAIN_END = v149.TRAIN_END
VALIDATION_END = v149.VALIDATION_END
AUDIT_END = v149.AUDIT_END
FRESH_START = v149.FRESH_START
TEST_END = v149.TEST_END
PORTFOLIO_START = v149.PORTFOLIO_START
TARGET_NET = 20_000.0
STRICT_PROJECTED_STRESS_LIMIT = 9.45
MAX_ALT_OPEN_RISK = 1.20
MAX_SELECTED_ALT_SLEEVES = 10


@dataclass(frozen=True)
class SelectedAlternativeSleeve:
    symbol: str
    mode: str
    profile: str
    family: str
    timeframe: str
    setup: str
    risk_percent: float
    selection_score: float


def ratio_stats(frame: pd.DataFrame) -> dict[str, Any]:
    return v149.ratio_stats(frame)


def development_blocks(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
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


def validate_alt_evidence(
    timeframe: str,
    evidence: dict[str, dict[str, Any]],
) -> tuple[bool, str | None]:
    minimums = (18, 10, 6) if timeframe == "H1" else (8, 5, 3)
    for block, minimum in zip(("training", "validation", "audit"), minimums):
        stats = evidence[block]
        if int(stats["trades"]) < minimum:
            return False, f"insufficient {block}: {stats}"
        if float(stats["net_r"]) <= 0.0:
            return False, f"negative {block}: {stats}"
        if float(stats["profit_factor"] or 0.0) < 1.03:
            return False, f"weak {block} PF: {stats}"
    return True, None


def risk_tier(evidence: dict[str, dict[str, Any]]) -> float:
    minimum_pf = min(float(evidence[name]["profit_factor"] or 0.0) for name in evidence)
    minimum_expectancy = min(float(evidence[name]["expectancy_r"] or 0.0) for name in evidence)
    if minimum_pf >= 1.45 and minimum_expectancy >= 0.08:
        return 0.35
    if minimum_pf >= 1.25 and minimum_expectancy >= 0.04:
        return 0.30
    return 0.20


def selection_score(evidence: dict[str, dict[str, Any]]) -> float:
    minimum_expectancy = min(float(evidence[name]["expectancy_r"] or 0.0) for name in evidence)
    minimum_pf = min(float(evidence[name]["profit_factor"] or 0.0) for name in evidence)
    total_trades = sum(int(evidence[name]["trades"]) for name in evidence)
    return float(minimum_expectancy * math.sqrt(max(1, total_trades)) + 0.025 * (minimum_pf - 1.0))


def build_alternative_candidates(
    market: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol, (h1, h4, d1) in market.items():
        generated = alt_engine.generate_symbol_candidates(symbol, h1, h4, d1)
        if generated.empty:
            continue
        generated["entry_time"] = pd.to_datetime(generated["entry_time"], utc=True)
        generated["exit_time"] = pd.to_datetime(generated["exit_time"], utc=True)
        generated = generated[
            (generated["entry_time"] >= TEST_START)
            & (generated["entry_time"] <= TEST_END)
        ].copy()
        generated["raw_r_multiple"] = pd.to_numeric(generated["r_multiple"], errors="coerce")
        generated["cost_r"] = pd.to_numeric(generated["selection_cost_r"], errors="coerce")
        generated["r_multiple"] = generated["raw_r_multiple"] - generated["cost_r"]
        frames.append(generated)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    return output.sort_values(["entry_time", "symbol", "mode", "profile"]).reset_index(drop=True)


def select_alternative_sleeves(
    candidates: pd.DataFrame,
) -> tuple[list[SelectedAlternativeSleeve], list[dict[str, Any]]]:
    evidence_rows: list[dict[str, Any]] = []
    passing: list[tuple[SelectedAlternativeSleeve, dict[str, dict[str, Any]]]] = []
    group_columns = ["symbol", "mode", "profile", "family", "timeframe"]
    for key, group in candidates.groupby(group_columns, sort=True):
        symbol, mode, profile, family, timeframe = [str(value) for value in key]
        evidence = development_blocks(group)
        passed, error = validate_alt_evidence(timeframe, evidence)
        score = selection_score(evidence) if passed else -999.0
        risk = risk_tier(evidence) if passed else 0.0
        sleeve = SelectedAlternativeSleeve(
            symbol=symbol,
            mode=mode,
            profile=profile,
            family=family,
            timeframe=timeframe,
            setup=f"v14_10_{symbol.lower()}_{mode.lower()}_{profile.lower()}",
            risk_percent=risk,
            selection_score=score,
        )
        fresh = ratio_stats(
            group[(group["entry_time"] >= FRESH_START) & (group["entry_time"] <= TEST_END)]
        )
        evidence_rows.append(
            {
                "symbol": symbol,
                "mode": mode,
                "profile": profile,
                "family": family,
                "timeframe": timeframe,
                "passed_pre_holdout": passed,
                "failure_reason": error,
                "selection_score": score,
                "risk_percent": risk,
                "development_evidence": evidence,
                "fresh_shadow_evidence": fresh,
            }
        )
        if passed:
            passing.append((sleeve, evidence))

    # At most one profile per symbol and alternative engine class. This prevents
    # correlated variants of the same idea from multiplying portfolio exposure.
    best_by_key: dict[tuple[str, str], SelectedAlternativeSleeve] = {}
    for sleeve, _ in sorted(passing, key=lambda item: item[0].selection_score, reverse=True):
        key = (sleeve.symbol, sleeve.mode)
        if key not in best_by_key:
            best_by_key[key] = sleeve
    selected = sorted(
        best_by_key.values(),
        key=lambda sleeve: sleeve.selection_score,
        reverse=True,
    )[:MAX_SELECTED_ALT_SLEEVES]
    return selected, evidence_rows


def materialize_alt_sleeve(
    candidates: pd.DataFrame,
    sleeve: SelectedAlternativeSleeve,
) -> pd.DataFrame:
    frame = candidates[
        (candidates["symbol"].astype(str) == sleeve.symbol)
        & (candidates["mode"].astype(str) == sleeve.mode)
        & (candidates["profile"].astype(str) == sleeve.profile)
    ].copy()
    if frame.empty:
        raise RuntimeError(f"Selected alternative sleeve is empty: {sleeve}")
    frame = frame.sort_values(["entry_time", "exit_time", "side"]).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "mode", "profile", "side"]
    )
    frame["setup"] = sleeve.setup
    frame["risk_percent"] = float(sleeve.risk_percent)
    frame["sleeve_id"] = f"{sleeve.symbol}/{sleeve.mode}"
    frame["selection_score"] = float(sleeve.selection_score)
    frame["strategy_mode"] = sleeve.mode
    return frame.reset_index(drop=True)


def build_v149_candidates(source: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    for sleeve in v149.SLEEVES:
        frame = v149.filter_sleeve(source, sleeve)
        block_evidence = v149.development_blocks(frame)
        v149.validate_development(sleeve, block_evidence)
        frame["strategy_mode"] = sleeve.mode
        frames.append(frame)
        evidence.append(
            {
                "symbol": sleeve.symbol,
                "mode": sleeve.mode,
                "profile": sleeve.profile,
                "setup": sleeve.setup,
                "risk_percent": sleeve.risk_percent,
                "development_evidence": block_evidence,
                "fresh_shadow_evidence": ratio_stats(
                    frame[(frame["entry_time"] >= FRESH_START) & (frame["entry_time"] <= TEST_END)]
                ),
            }
        )
    return pd.concat(frames, ignore_index=True, sort=False), evidence


def multi_engine_portfolio_admission(gated: pd.DataFrame) -> pd.DataFrame:
    active: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    ordered = gated.sort_values(
        ["entry_time", "gate_score", "selection_score"],
        ascending=[True, False, False],
    )
    for row in ordered.itertuples(index=False):
        now = pd.Timestamp(row.entry_time)
        active = [item for item in active if item["exit_time"] > now]
        accepted = bool(row.gate_active)
        reason = str(row.gate_reason)
        requested = float(row.risk_percent)
        mode = str(row.mode)
        if accepted and any(item["symbol"] == row.symbol for item in active):
            accepted, reason = False, "SYMBOL_OPEN_POSITION_LIMIT"
        total_open = sum(float(item["risk_percent"]) for item in active)
        ict_open = sum(float(item["risk_percent"]) for item in active if item["mode"] == "ICT")
        alt_open = sum(
            float(item["risk_percent"])
            for item in active
            if item["mode"] in {"BREAKOUT", "MOMENTUM", "MEAN_REVERSION"}
        )
        if accepted and total_open + requested > v149.MAX_COMBINED_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_COMBINED_RISK_CAP"
        if accepted and mode == "ICT" and ict_open + requested > v149.MAX_ICT_OPEN_RISK + 1e-12:
            accepted, reason = False, "PRE_REPLAY_ICT_RISK_CAP"
        if (
            accepted
            and mode in {"BREAKOUT", "MOMENTUM", "MEAN_REVERSION"}
            and alt_open + requested > MAX_ALT_OPEN_RISK + 1e-12
        ):
            accepted, reason = False, "PRE_REPLAY_ALT_RISK_CAP"
        if accepted:
            active.append(
                {
                    "symbol": row.symbol,
                    "mode": mode,
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


def run_strict_replay(active: pd.DataFrame):
    swing_like = active[active["mode"] != "ICT"].copy().sort_values(["entry_time", "symbol"])
    ict = active[active["mode"] == "ICT"].copy().sort_values(["entry_time", "symbol"])
    if swing_like.empty or ict.empty:
        raise RuntimeError("Multi-engine admission produced an empty swing-like or ICT stream")
    original_limit = v148.PROJECTED_STRESS_LIMIT
    v148.PROJECTED_STRESS_LIMIT = STRICT_PROJECTED_STRESS_LIMIT
    try:
        summary, trades, skipped, replay = v149.run_replay(swing_like, ict)
    finally:
        v148.PROJECTED_STRESS_LIMIT = original_limit
    return summary, trades, skipped, replay


def build_portfolio(
    candidates: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, Any, pd.DataFrame]:
    gated = v149.apply_walk_forward_gate(candidates)
    admitted = multi_engine_portfolio_admission(gated)
    active = admitted[admitted["portfolio_admitted"]].copy()
    active = active[
        (active["entry_time"] >= PORTFOLIO_START)
        & (active["entry_time"] <= TEST_END)
    ]
    summary, trades, skipped, replay = run_strict_replay(active)
    trades = enrich_trades(trades, active)
    return summary, trades, skipped, replay, admitted


def attribution_by_mode(trades: pd.DataFrame) -> dict[str, Any]:
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


def attribution_by_symbol(trades: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for symbol, group in trades.groupby("symbol", sort=True):
        pnl = pd.to_numeric(group["pnl"], errors="coerce").dropna()
        result[str(symbol)] = {
            "trades": int(len(group)),
            "net_profit": round(float(pnl.sum()), 2),
        }
    return result


def plot_outputs(monthly: pd.DataFrame, annual: pd.DataFrame, trades: pd.DataFrame) -> None:
    external.plot_outputs(monthly, annual, trades)
    import matplotlib.pyplot as plt

    mode = trades.groupby("strategy_mode", as_index=False)["pnl"].sum().sort_values("pnl", ascending=False)
    figure = plt.figure(figsize=(10, 5))
    plt.bar(mode["strategy_mode"], mode["pnl"])
    plt.axhline(0, linewidth=1)
    plt.ylabel("Net profit after modeled costs ($)")
    plt.title("V14.10 profit by independent engine class")
    plt.xticks(rotation=30)
    plt.tight_layout()
    figure.savefig(OUT / "profit_by_engine_class.png", dpi=180)
    plt.close(figure)

    alt = trades[trades["strategy_mode"].isin(["BREAKOUT", "MOMENTUM", "MEAN_REVERSION"])]
    if not alt.empty:
        family = alt.groupby("family", as_index=False)["pnl"].sum().sort_values("pnl", ascending=False)
        figure = plt.figure(figsize=(11, 5))
        plt.bar(family["family"], family["pnl"])
        plt.axhline(0, linewidth=1)
        plt.ylabel("Net profit after modeled costs ($)")
        plt.title("Alternative-engine profit by family")
        plt.xticks(rotation=35)
        plt.tight_layout()
        figure.savefig(OUT / "alternative_profit_by_family.png", dpi=180)
        plt.close(figure)


def write_report(payload: dict[str, Any]) -> None:
    base = payload["baseline_v14_9"]
    enhanced = payload["portfolio"]
    fresh = payload["fresh_2022_2026"]
    lines = [
        "# V14.10 FXCM Multi-Engine Portfolio",
        "",
        f"**External data:** {payload['provider']}",
        f"**Chart window:** {TEST_START.date()} through {TEST_END.date()}",
        f"**Capital deployment:** {PORTFOLIO_START.date()} through {TEST_END.date()}",
        f"**Untouched test:** {FRESH_START.date()} through {TEST_END.date()}",
        "**Starting balance:** $5,000.00",
        "",
        "## Portfolio comparison",
        "",
        "| Metric | V14.9 | V14.10 | Change |",
        "|---|---:|---:|---:|",
        f"| Net profit | ${base['net_profit']:,.2f} | ${enhanced['net_profit']:,.2f} | ${enhanced['net_profit'] - base['net_profit']:,.2f} |",
        f"| Ending balance | ${base['ending_balance']:,.2f} | ${enhanced['ending_balance']:,.2f} | ${enhanced['ending_balance'] - base['ending_balance']:,.2f} |",
        f"| Profit factor | {float(base['profit_factor'] or 0.0):.4f} | {float(enhanced['profit_factor'] or 0.0):.4f} | {float(enhanced['profit_factor'] or 0.0) - float(base['profit_factor'] or 0.0):.4f} |",
        f"| Max closed DD | {base['max_closed_drawdown_percent']:.4f}% | {enhanced['max_closed_drawdown_percent']:.4f}% | {enhanced['max_closed_drawdown_percent'] - base['max_closed_drawdown_percent']:.4f} pp |",
        f"| Stress DD | {base['stress_drawdown_percent']:.4f}% | {enhanced['stress_drawdown_percent']:.4f}% | {enhanced['stress_drawdown_percent'] - base['stress_drawdown_percent']:.4f} pp |",
        f"| Closed trades | {base['closed_trades']} | {enhanced['closed_trades']} | {enhanced['closed_trades'] - base['closed_trades']} |",
        "",
        "## Untouched March 2022-May 2026",
        "",
        f"- Trades: {fresh['trades']}",
        f"- Net profit: **${fresh['net_profit']:,.2f}**",
        f"- Profit factor: **{float(fresh['profit_factor'] or 0.0):.4f}**",
        f"- Win rate: **{float(fresh['win_rate'] or 0.0) * 100.0:.2f}%**",
        "",
        "## Selected alternative engines",
        "",
        "| Symbol | Engine class | Profile | Family | Risk | Pre-holdout score | Fresh net R | Fresh PF |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    evidence_by_key = {
        (item["symbol"], item["mode"], item["profile"]): item
        for item in payload["alternative_evidence"]
    }
    for item in payload["selected_alternative_sleeves"]:
        evidence = evidence_by_key[(item["symbol"], item["mode"], item["profile"])]
        fresh_stats = evidence["fresh_shadow_evidence"]
        lines.append(
            f"| {item['symbol']} | {item['mode']} | {item['profile']} | {item['family']} | "
            f"{item['risk_percent']:.2f}% | {item['selection_score']:.4f} | "
            f"{fresh_stats['net_r']:.4f} | {float(fresh_stats['profit_factor'] or 0.0):.4f} |"
        )
    lines += [
        "",
        "## Method boundary",
        "",
        "- Alternative strategies are separate BREAKOUT, MOMENTUM and MEAN_REVERSION engines; they are not renamed swing or ICT trades.",
        "- Profiles were selected only from 2016-2018 training, 2019-2020 validation and 2021-March 2022 audit evidence.",
        "- The March 2022-May 2026 interval was not used for alternative profile selection, risk tiering or gate configuration.",
        "- Every sleeve remains subject to the prior-365-day after-cost walk-forward gate.",
        "- One position per symbol, 1.20% alternative open risk, 1.75% ICT open risk and 3.25% combined open risk are enforced.",
        "- The 7.5/8.5/9.0/9.6 closed-drawdown governor and 9.45% projected-stress admission ceiling remain active.",
        "- H1 bid/ask candles are from FXCM; H4 and D1 are resampled from H1 bid data.",
        "- This remains a bar-based research replay, not a tick-level guarantee of broker fills or profits.",
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

    existing_source = external.build_external_candidates(market)
    existing_source.to_csv(OUT / "existing_swing_ict_candidates.csv", index=False)
    base_candidates, base_evidence = build_v149_candidates(existing_source)

    alt_candidates = build_alternative_candidates(market)
    if alt_candidates.empty:
        raise RuntimeError("Alternative engines produced no candidates")
    alt_candidates.to_csv(OUT / "all_alternative_candidates.csv", index=False)
    selected_alt, alt_evidence = select_alternative_sleeves(alt_candidates)
    if not selected_alt:
        raise RuntimeError("No alternative engine passed pre-holdout validation")
    selected_frames = [materialize_alt_sleeve(alt_candidates, sleeve) for sleeve in selected_alt]
    selected_alt_candidates = pd.concat(selected_frames, ignore_index=True, sort=False)
    selected_alt_candidates.to_csv(OUT / "selected_alternative_candidates.csv", index=False)

    # Strict V14.9 baseline on the same source and deployment window.
    baseline_summary, baseline_trades, baseline_skipped, baseline_replay, baseline_admission = build_portfolio(
        base_candidates
    )
    baseline_trades.to_csv(OUT / "baseline_v14_9_closed_trades.csv", index=False)

    combined = pd.concat([base_candidates, selected_alt_candidates], ignore_index=True, sort=False)
    combined["selection_score"] = pd.to_numeric(
        combined.get("selection_score", 0.0), errors="coerce"
    ).fillna(0.0)
    combined = combined.sort_values(["entry_time", "symbol", "mode", "setup"])
    summary, trades, skipped, replay, admitted = build_portfolio(combined)
    admitted.to_csv(OUT / "multi_engine_gate_and_admission.csv", index=False)
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
    plot_outputs(monthly, annual, trades)

    fresh_trades = trades[
        (pd.to_datetime(trades["entry_time"], utc=True) >= FRESH_START)
        & (pd.to_datetime(trades["entry_time"], utc=True) <= TEST_END)
    ].copy()
    fresh_alt = fresh_trades[
        fresh_trades["strategy_mode"].isin(["BREAKOUT", "MOMENTUM", "MEAN_REVERSION"])
    ]
    safe = (
        float(summary["max_closed_drawdown_percent"]) <= 9.60
        and float(summary["stress_drawdown_percent"]) <= 10.00
    )
    payload = {
        "generated_at": datetime.now().isoformat(),
        "provider": "FXCM official weekly H1 candle archive",
        "window": {"start": TEST_START.isoformat(), "end": TEST_END.isoformat()},
        "portfolio_window": {"start": PORTFOLIO_START.isoformat(), "end": TEST_END.isoformat()},
        "untouched_test": {"start": FRESH_START.isoformat(), "end": TEST_END.isoformat()},
        "selection_protocol": {
            "training_end": TRAIN_END.isoformat(),
            "validation_end": VALIDATION_END.isoformat(),
            "audit_end": AUDIT_END.isoformat(),
            "holdout_not_used_for_selection": True,
            "maximum_one_profile_per_symbol_and_alternative_mode": True,
        },
        "risk_limits": {
            "maximum_alternative_trade_percent": 0.35,
            "maximum_alternative_open_risk_percent": MAX_ALT_OPEN_RISK,
            "maximum_ict_open_risk_percent": v149.MAX_ICT_OPEN_RISK,
            "maximum_combined_open_risk_percent": v149.MAX_COMBINED_OPEN_RISK,
            "projected_stress_admission_limit_percent": STRICT_PROJECTED_STRESS_LIMIT,
            "maximum_closed_drawdown_percent": 9.60,
            "maximum_stress_drawdown_percent": 10.00,
        },
        "data_quality": quality,
        "baseline_sleeves": base_evidence,
        "alternative_evidence": alt_evidence,
        "selected_alternative_sleeves": [asdict(item) for item in selected_alt],
        "baseline_v14_9": baseline_summary,
        "portfolio": {**summary, "safe": safe},
        "improvement": {
            "net_profit": round(float(summary["net_profit"] - baseline_summary["net_profit"]), 2),
            "ending_balance": round(float(summary["ending_balance"] - baseline_summary["ending_balance"]), 2),
            "profit_factor": round(
                float(summary["profit_factor"] or 0.0) - float(baseline_summary["profit_factor"] or 0.0),
                6,
            ),
            "closed_drawdown_points": round(
                float(summary["max_closed_drawdown_percent"])
                - float(baseline_summary["max_closed_drawdown_percent"]),
                6,
            ),
        },
        "fresh_2022_2026": v149.dollar_stats(fresh_trades),
        "fresh_alternative_2022_2026": v149.dollar_stats(fresh_alt),
        "attribution_by_engine_class": attribution_by_mode(trades),
        "attribution_by_symbol": attribution_by_symbol(trades),
        "total_modeled_fee_dollars": round(float(trades["modeled_fee_dollars"].sum()), 2),
        "target": {"net_profit": TARGET_NET, "ending_balance": v148.STARTING_BALANCE + TARGET_NET},
        "target_reached": float(summary["net_profit"]) >= TARGET_NET,
        "target_gap": round(max(0.0, TARGET_NET - float(summary["net_profit"])), 2),
        "monthly": monthly.assign(month=monthly["month"].astype(str)).to_dict("records"),
        "annual": annual.to_dict("records"),
    }
    (OUT / "v14_10_multi_engine_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    pd.DataFrame(
        [
            {
                "symbol": item["symbol"],
                "mode": item["mode"],
                "profile": item["profile"],
                "family": item["family"],
                "timeframe": item["timeframe"],
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
                    f"fresh_{metric}": value
                    for metric, value in item["fresh_shadow_evidence"].items()
                },
            }
            for item in alt_evidence
        ]
    ).to_csv(OUT / "alternative_engine_evidence.csv", index=False)
    write_report(payload)
    print(
        json.dumps(
            {
                "baseline_v14_9": baseline_summary,
                "portfolio": payload["portfolio"],
                "improvement": payload["improvement"],
                "fresh_2022_2026": payload["fresh_2022_2026"],
                "fresh_alternative_2022_2026": payload["fresh_alternative_2022_2026"],
                "selected_alternative_sleeves": payload["selected_alternative_sleeves"],
                "attribution_by_engine_class": payload["attribution_by_engine_class"],
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
