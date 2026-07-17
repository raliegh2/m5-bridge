"""V14.8 strict five-symbol swing + ICT retail-cost portfolio.

Each of GBPUSD, EURUSD, GBPJPY, AUDUSD and USDJPY contributes one swing sleeve
and one ICT sleeve. Every frozen sleeve must remain profitable after its retail
cost allowance in five chronological blocks:

* training: 35%;
* validation: 20%;
* audit A: 15%;
* audit B: 15%;
* final validation: 15%.

The portfolio target is $20,000 net profit from a $5,000 start. Closed drawdown
must remain below 9.60% and projected stressed drawdown below 10.00%.
Research only: no MT5 account, order or broker API is used.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile  # noqa: E402
from mt5_ai_bridge.v14_3_profit_preserving_profile import (  # noqa: E402
    PORTFOLIO_GUARD,
    scaled_risk_percent,
)
from mt5_ai_bridge.v14_8_projected_stress_governor import (  # noqa: E402
    ProjectedStressGovernor,
)
from research import v14_6_five_symbol_dual_engine_target as base  # noqa: E402
from research import v14_6_1_intraday_ict_trend_backtest as intraday  # noqa: E402
from research import v14_7_2_frozen_all_ten as candidate_source  # noqa: E402
from research import v14_7_five_symbol_20k_backtest as v147  # noqa: E402
from research.v14_3_drawdown_limited_backtest_v2 import (  # noqa: E402
    AdmissionPreservingReplay,
)
from research.v14_3_production_improved_backtest import (  # noqa: E402
    filter_window,
    summarize,
)

OUT = ROOT / "research" / "v14_8_strict_all_ten_output"
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
STARTING_BALANCE = 5_000.0
TARGET_NET_PROFIT = 20_000.0
TARGET_ENDING_BALANCE = STARTING_BALANCE + TARGET_NET_PROFIT
MAX_CLOSED_DD = 9.60
MAX_STRESS_DD = 10.00
PROJECTED_STRESS_LIMIT = 9.95

SESSION_HOURS = {
    "ASIA": (0, 6),
    "LONDON": (6, 12),
    "NEW_YORK": (12, 18),
    "LATE": (18, 24),
}


@dataclass(frozen=True)
class FrozenSleeve:
    symbol: str
    mode: str
    engine: str
    setup: str
    risk_percent: float
    profile: str | None = None
    side: str | None = None
    hour: int | None = None
    session: str | None = None
    excluded_weekday: int | None = None


FROZEN_SLEEVES = (
    # Swing sleeves.
    FrozenSleeve(
        "GBPUSD", "SWING", "GBPUSD_V10_PRECISION",
        "v14_8_gbpusd_swing_precision_12", 1.25,
        profile="NONE", hour=12,
    ),
    FrozenSleeve(
        "EURUSD", "SWING", "EURUSD_SWING_SWING_PULLBACK_20",
        "v14_8_eurusd_swing_pullback_16", 0.50,
        profile="SWING_PULLBACK_20", hour=16,
    ),
    FrozenSleeve(
        "GBPJPY", "SWING", "GBPJPY_SWING_CORE",
        "v14_8_gbpjpy_swing_core_no_tuesday", 0.95,
        profile="NONE", excluded_weekday=1,
    ),
    FrozenSleeve(
        "AUDUSD", "SWING", "AUDUSD_TREND_PULLBACK",
        "v14_8_audusd_swing_pullback_08", 0.50,
        profile="NONE", hour=8,
    ),
    FrozenSleeve(
        "USDJPY", "SWING", "USDJPY_SWING_SWING_BREAKOUT_24",
        "v14_8_usdjpy_swing_breakout_08", 0.55,
        profile="SWING_BREAKOUT_24", hour=8,
    ),
    # ICT sleeves. Profiles are isolated before replay so weak variants cannot
    # share an execution setup with a validated profile.
    FrozenSleeve(
        "GBPUSD", "ICT", "GBPUSD_ICT_WIDE_SWEEP",
        "v14_8_gbpusd_ict_gu_london_25", 0.60,
        profile="gu_london_25", session="LONDON",
    ),
    FrozenSleeve(
        "EURUSD", "ICT", "EURUSD_ICT_LIQUIDITY",
        "v14_8_eurusd_ict_eu_london_20", 0.60,
        profile="eu_london_20", session="LONDON",
    ),
    FrozenSleeve(
        "GBPJPY", "ICT", "GBPJPY_ICT_WIDE_SWEEP",
        "v14_8_gbpjpy_ict_gj_ny_20", 0.60,
        profile="gj_ny_20", session="NEW_YORK",
    ),
    FrozenSleeve(
        "AUDUSD", "ICT", "AUDUSD_ICT_ASIA_LONDON",
        "v14_8_audusd_ict_relaxed_sell_no_wednesday", 0.40,
        profile="au_london_relaxed", side="SELL", excluded_weekday=2,
    ),
    FrozenSleeve(
        "USDJPY", "ICT", "USDJPY_ICT_ICT_BREAKOUT_H4",
        "v14_8_usdjpy_ict_breakout_h4_08", 0.30,
        profile="ICT_BREAKOUT_H4", hour=8,
    ),
)


def profile_key(frame: pd.DataFrame) -> pd.Series:
    return frame["profile"].fillna("NONE").astype(str)


def materialize_sleeve(source: pd.DataFrame, sleeve: FrozenSleeve) -> pd.DataFrame:
    selected = source[
        (source["symbol"].astype(str) == sleeve.symbol)
        & (source["mode"].astype(str) == sleeve.mode)
        & (source["engine"].astype(str) == sleeve.engine)
    ].copy()
    if sleeve.profile is not None:
        selected = selected[profile_key(selected) == sleeve.profile]
    if sleeve.side is not None:
        selected = selected[selected["side"].astype(str).str.upper() == sleeve.side]
    if sleeve.hour is not None:
        selected = selected[selected["entry_time"].dt.hour == sleeve.hour]
    if sleeve.session is not None:
        low, high = SESSION_HOURS[sleeve.session]
        selected = selected[
            (selected["entry_time"].dt.hour >= low)
            & (selected["entry_time"].dt.hour < high)
        ]
    if sleeve.excluded_weekday is not None:
        selected = selected[
            selected["entry_time"].dt.weekday != sleeve.excluded_weekday
        ]
    if selected.empty:
        raise RuntimeError(f"Frozen sleeve produced no trades: {sleeve}")

    selected = selected.sort_values(
        ["entry_time", "exit_time", "symbol", "engine", "side"]
    ).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "engine", "profile", "side"]
    )
    selected["setup"] = sleeve.setup
    selected["risk_percent"] = float(sleeve.risk_percent)
    selected["sleeve_mode"] = sleeve.mode
    selected["raw_r_multiple"] = selected["r_multiple"].astype(float)
    selected["cost_r"] = selected["selection_cost_r"].astype(float)
    selected["r_multiple"] = (
        selected["raw_r_multiple"] - selected["cost_r"]
    )
    return selected.reset_index(drop=True)


def block_stats(frame: pd.DataFrame) -> dict[str, Any]:
    values = frame["r_multiple"].astype(float)
    if values.empty:
        return {
            "trades": 0,
            "net_r": 0.0,
            "expectancy_r": None,
            "profit_factor": None,
        }
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(values)),
        "net_r": round(float(values.sum()), 6),
        "expectancy_r": round(float(values.mean()), 6),
        "profit_factor": (
            round(gross_profit / gross_loss, 6) if gross_loss > 0 else 99.0
        ),
    }


def chronological_blocks(
    start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    span = end - start
    return {
        "training": (start, start + span * 0.35),
        "validation": (start + span * 0.35, start + span * 0.55),
        "audit_a": (start + span * 0.55, start + span * 0.70),
        "audit_b": (start + span * 0.70, start + span * 0.85),
        "final_validation": (
            start + span * 0.85,
            end + pd.Timedelta(seconds=1),
        ),
    }


def sleeve_evidence(
    frame: pd.DataFrame,
    blocks: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> dict[str, dict[str, Any]]:
    entry = pd.to_datetime(frame["entry_time"], utc=True)
    return {
        name: block_stats(frame[(entry >= low) & (entry < high)])
        for name, (low, high) in blocks.items()
    }


def validate_sleeve(
    sleeve: FrozenSleeve,
    evidence: dict[str, dict[str, Any]],
) -> None:
    minimums = (
        {
            "training": 8,
            "validation": 4,
            "audit_a": 3,
            "audit_b": 3,
            "final_validation": 3,
        }
        if sleeve.mode == "SWING"
        else {
            "training": 12,
            "validation": 6,
            "audit_a": 5,
            "audit_b": 5,
            "final_validation": 5,
        }
    )
    for name, minimum in minimums.items():
        stats = evidence[name]
        if stats["trades"] < minimum:
            raise RuntimeError(
                f"{sleeve.setup} has insufficient {name} trades: {stats}"
            )
        if stats["net_r"] <= 0:
            raise RuntimeError(f"{sleeve.setup} failed {name}: {stats}")
        if float(stats["profit_factor"] or 0.0) <= 1.01:
            raise RuntimeError(f"{sleeve.setup} failed {name} PF: {stats}")


def annual_evidence(
    frame: pd.DataFrame, start: pd.Timestamp
) -> dict[str, Any]:
    work = frame.copy()
    work["year_index"] = (
        (work["entry_time"] - start).dt.days // 365
    ).clip(lower=0, upper=9)
    yearly = {
        str(int(year)): block_stats(group)
        for year, group in work.groupby("year_index")
    }
    positive = sum(item["net_r"] > 0 for item in yearly.values())
    return {
        "active_years": int(len(yearly)),
        "positive_years": int(positive),
        "yearly": yearly,
    }


class ProjectedStressReplay(AdmissionPreservingReplay):
    """Admission-preserving replay with a projected stressed-equity ceiling."""

    def __init__(self, v12: pd.DataFrame, ict: pd.DataFrame) -> None:
        super().__init__(v12, ict, base.governor())
        self.projected_governor = ProjectedStressGovernor(
            maximum_stress_drawdown_percent=PROJECTED_STRESS_LIMIT,
            minimum_trade_risk_percent=0.025,
        )
        self.projected_stress_events: list[dict[str, Any]] = []

    def run(self):
        stream = [
            (row["entry_time"], "V12", row)
            for row in self.v12.to_dict("records")
        ]
        stream += [
            (row["entry_time"], "ICT", row)
            for row in self.ict.to_dict("records")
        ]
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
                requested = float(row["risk_percent"])
                reason = "V14_8_SWING_PROJECTED_STRESS_GOVERNED"
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
                requested = scaled_risk_percent(
                    symbol,
                    str(row["setup"]),
                    self.dd(),
                    pressure,
                )
                if (
                    ict_open + requested
                    > PORTFOLIO_GUARD.max_ict_open_risk_percent + 1e-12
                ):
                    self.skipped.append(
                        {**row, "skip_reason": "ICT_OPEN_RISK_CAP"}
                    )
                    continue
                if (
                    total_open + requested
                    > PORTFOLIO_GUARD.max_combined_open_risk_percent + 1e-12
                ):
                    self.skipped.append(
                        {**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"}
                    )
                    continue
                reason = "V14_8_ICT_PROJECTED_STRESS_GOVERNED"

            current_dd = self.dd()
            approved = self.governor.apply(requested, current_dd)
            if approved <= 0.0:
                self.skipped.append(
                    {**row, "skip_reason": "DRAWDOWN_GOVERNOR_HARD_STOP"}
                )
                continue

            existing_open_risk_dollars = sum(
                item["risk_dollars"] for item in self.active
            )
            stress_approved = self.projected_governor.apply(
                approved,
                balance=self.balance,
                peak_balance=self.peak,
                existing_open_risk_dollars=existing_open_risk_dollars,
            )
            if stress_approved <= 0.0:
                self.skipped.append(
                    {**row, "skip_reason": "PROJECTED_STRESS_DRAWDOWN_CAP"}
                )
                continue
            if stress_approved < approved - 1e-12:
                self.projected_stress_events.append(
                    {
                        "entry_time": now,
                        "symbol": row["symbol"],
                        "engine": row["engine"],
                        "setup": row["setup"],
                        "closed_drawdown_percent": current_dd,
                        "requested_risk_percent": requested,
                        "closed_dd_approved_risk_percent": approved,
                        "projected_stress_approved_risk_percent": stress_approved,
                    }
                )
            approved = stress_approved

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
                "r_multiple": float(row["r_multiple"]),
                "admission_reason": reason,
            }
            self.trade_id += 1
            self.active.append(item)
            if group == "ICT":
                self.day.total_entries.append(now)
                self.day.entries[item["symbol"]].append(now)

            stressed = self.balance - sum(
                active["risk_dollars"] for active in self.active
            )
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
        summary["drawdown_governor"] = self.governor.__dict__
        summary["projected_stress_governor"] = asdict(self.projected_governor)
        summary["projected_stress_interventions"] = len(
            self.projected_stress_events
        )
        return summary, pd.DataFrame(self.closed), pd.DataFrame(self.skipped)


def install_profile(ict: pd.DataFrame) -> tuple[dict, dict]:
    return intraday.install_profile(ict)


def restore_profile(old_risk: dict, old_guards: dict) -> None:
    intraday.restore_profile(old_risk, old_guards)


def attribution(trades: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for (symbol, group), frame in trades.groupby(["symbol", "engine_group"]):
        pnl = frame["pnl"].astype(float)
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
        output[f"{symbol}/{group}"] = {
            "trades": int(len(frame)),
            "net_profit": round(float(pnl.sum()), 2),
            "profit_factor": (
                round(gross_profit / gross_loss, 6)
                if gross_loss > 0
                else 99.0
            ),
        }
    return output


def write_report(payload: dict[str, Any]) -> None:
    best = payload["best_safe_portfolio"]
    lines = [
        "# V14.8 Strict Five-Symbol Swing + ICT Portfolio",
        "",
        f"**Window:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        f"**Starting balance:** ${STARTING_BALANCE:,.2f}",
        f"**Retail-net target:** ${TARGET_NET_PROFIT:,.2f}",
        "",
        "## Coverage",
        "",
        "| Symbol | Swing validated | ICT validated |",
        "|---|---:|---:|",
    ]
    for symbol in SYMBOLS:
        lines.append(f"| {symbol} | True | True |")
    lines += [
        "",
        "## Best frozen portfolio result",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Net profit after modeled costs | ${best['net_profit']:,.2f} |",
        f"| Ending balance | ${best['ending_balance']:,.2f} |",
        f"| Return | {best['return_percent']:.2f}% |",
        f"| Profit factor | {float(best['profit_factor']):.4f} |",
        f"| Maximum closed drawdown | {best['max_closed_drawdown_percent']:.4f}% |",
        f"| Stressed drawdown | {best['stress_drawdown_percent']:.4f}% |",
        f"| Closed trades | {best['closed_trades']} |",
        f"| Target reached | {payload['target_reached']} |",
        f"| Margin above target | ${payload['target_margin']:,.2f} |",
        "",
        "## Validation rule",
        "",
        "Every frozen sleeve is positive after costs in training, validation, audit A, audit B and final validation. Profile identity is part of the setup key, preventing weaker variants from being mixed into an approved sleeve.",
        "",
        "Research only. Historical results and R-cost modeling do not guarantee future profitability. Fresh 2022-2026 data and demo forward testing remain mandatory.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = candidate_source.pool()
    source["entry_time"] = pd.to_datetime(source["entry_time"], utc=True)
    source["exit_time"] = pd.to_datetime(source["exit_time"], utc=True)
    source["side"] = source["side"].astype(str).str.upper()

    latest = source["exit_time"].max()
    start = latest - pd.DateOffset(years=10)
    source = filter_window(source, start, latest)
    blocks = chronological_blocks(start, latest)

    swing_frames: list[pd.DataFrame] = []
    ict_frames: list[pd.DataFrame] = []
    selections: dict[str, dict[str, Any]] = {
        symbol: {} for symbol in SYMBOLS
    }
    for sleeve in FROZEN_SLEEVES:
        frame = materialize_sleeve(source, sleeve)
        frame = filter_window(frame, start, latest)
        evidence = sleeve_evidence(frame, blocks)
        validate_sleeve(sleeve, evidence)
        selections[sleeve.symbol][sleeve.mode] = {
            "specification": asdict(sleeve),
            "trade_count": int(len(frame)),
            "evidence": evidence,
            "annual_evidence": annual_evidence(frame, start),
        }
        (swing_frames if sleeve.mode == "SWING" else ict_frames).append(
            frame
        )

    swing = pd.concat(swing_frames, ignore_index=True, sort=False).sort_values(
        ["entry_time", "symbol", "engine"]
    )
    ict = pd.concat(ict_frames, ignore_index=True, sort=False).sort_values(
        ["entry_time", "symbol", "engine"]
    )
    swing.to_csv(OUT / "validated_swing_candidates.csv", index=False)
    ict.to_csv(OUT / "validated_ict_candidates.csv", index=False)

    old_risk, old_guards = install_profile(ict)
    replay = ProjectedStressReplay(swing, ict)
    try:
        summary, trades, skipped = replay.run()
    finally:
        restore_profile(old_risk, old_guards)

    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(
        OUT / "closed_drawdown_governor_events.csv", index=False
    )
    pd.DataFrame(replay.projected_stress_events).to_csv(
        OUT / "projected_stress_governor_events.csv", index=False
    )

    active_swing = sorted(
        trades[trades["engine_group"] == "V12"]["symbol"].unique().tolist()
    )
    active_ict = sorted(
        trades[trades["engine_group"] == "ICT"]["symbol"].unique().tolist()
    )
    all_ten = set(active_swing) == set(SYMBOLS) and set(active_ict) == set(
        SYMBOLS
    )
    safe = (
        float(summary["max_closed_drawdown_percent"]) <= MAX_CLOSED_DD
        and float(summary["stress_drawdown_percent"]) <= MAX_STRESS_DD
    )
    target_reached = float(summary["net_profit"]) >= TARGET_NET_PROFIT
    best = {
        **summary,
        "safe": safe,
        "target_reached": target_reached,
    }

    payload = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "target": {
            "starting_balance": STARTING_BALANCE,
            "net_profit": TARGET_NET_PROFIT,
            "ending_balance": TARGET_ENDING_BALANCE,
            "after_retail_costs_and_fees": True,
        },
        "validation_protocol": {
            "training_percent": 35,
            "validation_percent": 20,
            "audit_a_percent": 15,
            "audit_b_percent": 15,
            "final_validation_percent": 15,
            "all_blocks_must_be_profitable_after_costs": True,
        },
        "risk_limits": {
            "maximum_swing_trade_percent": 1.25,
            "maximum_ict_trade_percent": 0.60,
            "maximum_ict_open_risk_percent": PORTFOLIO_GUARD.max_ict_open_risk_percent,
            "maximum_combined_open_risk_percent": PORTFOLIO_GUARD.max_combined_open_risk_percent,
            "maximum_closed_drawdown_percent": MAX_CLOSED_DD,
            "maximum_stress_drawdown_percent": MAX_STRESS_DD,
            "projected_stress_admission_limit_percent": PROJECTED_STRESS_LIMIT,
        },
        "coverage": {
            "active_swing_symbols": active_swing,
            "active_ict_symbols": active_ict,
            "all_ten_sleeves_active": all_ten,
        },
        "selections": selections,
        "best_safe_portfolio": best,
        "attribution": attribution(trades),
        "target_reached": target_reached,
        "target_margin": round(
            float(summary["net_profit"]) - TARGET_NET_PROFIT, 2
        ),
    }
    (OUT / "v14_8_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(payload)

    print(
        json.dumps(
            {
                "window": payload["window"],
                "coverage": payload["coverage"],
                "best_safe_portfolio": best,
                "target_reached": target_reached,
                "target_margin": payload["target_margin"],
                "attribution": payload["attribution"],
                "output": str(OUT),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
