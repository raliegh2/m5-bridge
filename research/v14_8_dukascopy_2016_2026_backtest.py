"""External-data replay of the frozen V14.8 five-symbol portfolio.

Data source
-----------
Dukascopy H1 bid and ask candles downloaded independently of the repository's
original strategy-selection fixtures. H4 and D1 candles are rebuilt from H1.
The strategy specification and risk limits are frozen before this replay.

Window
------
Signals and portfolio accounting are reported from 2016-01-01 through
2026-07-16. Data from 2015 is used only as indicator warm-up.

Costs
-----
The replay retains V14.8's conservative all-in R allowances (swing 0.04R,
wide ICT 0.09R and strategy-family ICT 0.12R). Dukascopy bid/ask H1 data is
also used to report observed spread distributions. This remains a bar-based
research replay rather than a broker-native tick execution reconstruction.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import mt5_ai_bridge.v14_3_all_symbol_ict as ict_engine  # noqa: E402
from mt5_ai_bridge import v14_3_live_signals as live  # noqa: E402
from mt5_ai_bridge.v14_7_strategy_families import generate_symbol_candidates  # noqa: E402
from research import v14_6_five_symbol_dual_engine_target as dual  # noqa: E402
from research import v14_8_strict_all_ten_20k as v148  # noqa: E402

SYMBOLS = v148.SYMBOLS
DATA = ROOT / "research" / "dukascopy_2016_2026_data"
OUT = ROOT / "research" / "v14_8_dukascopy_2016_2026_output"
WARMUP_START = pd.Timestamp("2015-01-01T00:00:00Z")
TEST_START = pd.Timestamp("2016-01-01T00:00:00Z")
TEST_END = pd.Timestamp("2026-07-16T23:59:59Z")
FRESH_START = pd.Timestamp("2022-03-06T00:00:00Z")
TARGET_NET = 20_000.0
PIP_SIZE = {"GBPUSD": 0.0001, "EURUSD": 0.0001, "GBPJPY": 0.01, "AUDUSD": 0.0001, "USDJPY": 0.01}


class FrameClient:
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self.frames = frames

    def copy_rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int):
        frame = self.frames[(symbol, timeframe)].copy()
        stop = max(0, len(frame) - int(start_pos))
        start = max(0, stop - int(count))
        output = frame.iloc[start:stop].copy()
        output["time"] = output["time"].astype("int64") // 1_000_000_000
        return output[["time", "open", "high", "low", "close", "tick_volume"]].to_dict("records")


def load_h1(symbol: str, side: str) -> pd.DataFrame:
    path = DATA / f"{symbol}_H1_{side}.csv"
    frame = pd.read_csv(path)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "tick_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    frame = frame[(frame["time"] >= WARMUP_START) & (frame["time"] <= TEST_END)]
    return frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def resample_ohlc(h1: pd.DataFrame, rule: str) -> pd.DataFrame:
    indexed = h1.set_index("time")
    output = indexed.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
    )
    output = output.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return output.sort_values("time").reset_index(drop=True)


def load_market() -> tuple[dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]], dict[str, Any]]:
    market: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = {}
    quality: dict[str, Any] = {}
    for symbol in SYMBOLS:
        bid = load_h1(symbol, "bid")
        ask = load_h1(symbol, "ask")
        joined = bid[["time", "open"]].merge(
            ask[["time", "open"]], on="time", suffixes=("_bid", "_ask"), how="inner"
        )
        spread = (joined["open_ask"] - joined["open_bid"]) / PIP_SIZE[symbol]
        spread = spread[np.isfinite(spread) & (spread >= 0)]
        h4 = resample_ohlc(bid, "4h")
        d1 = resample_ohlc(bid, "1D")
        market[symbol] = (bid, h4, d1)

        test = bid[(bid["time"] >= TEST_START) & (bid["time"] <= TEST_END)]
        expected_hours = max(1.0, (TEST_END - TEST_START).total_seconds() / 3600.0)
        quality[symbol] = {
            "h1_bid_bars": int(len(bid)),
            "h1_ask_bars": int(len(ask)),
            "h4_bars": int(len(h4)),
            "d1_bars": int(len(d1)),
            "data_start": bid["time"].min().isoformat(),
            "data_end": bid["time"].max().isoformat(),
            "test_h1_bars": int(len(test)),
            "calendar_hour_coverage_percent": round(len(test) / expected_hours * 100.0, 2),
            "spread_pips": {
                "observations": int(len(spread)),
                "mean": round(float(spread.mean()), 4),
                "median": round(float(spread.median()), 4),
                "p90": round(float(spread.quantile(0.90)), 4),
                "p99": round(float(spread.quantile(0.99)), 4),
            },
        }
    return market, quality


def normalize_candidates(frame: pd.DataFrame, mode: str, family: str, profile: str, cost_r: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    output = frame.copy()
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    output["side"] = output["side"].map(
        lambda value: "BUY" if str(value).upper() in {"1", "BUY", "LONG"} else "SELL"
    )
    output["mode"] = mode
    output["family"] = output.get("family", family)
    output["profile"] = output.get("profile", profile).fillna(profile) if "profile" in output else profile
    output["selection_cost_r"] = pd.to_numeric(
        output.get("selection_cost_r", cost_r), errors="coerce"
    ).fillna(cost_r) if "selection_cost_r" in output else float(cost_r)
    return output


def build_external_candidates(market: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]) -> pd.DataFrame:
    frames_by_key: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol, (h1, h4, d1) in market.items():
        frames_by_key[(symbol, "H1")] = h1
        frames_by_key[(symbol, "H4")] = h4
        frames_by_key[(symbol, "D1")] = d1
    client = FrameClient(frames_by_key)
    prepared = {
        symbol: live.prepare_v12_frames(
            client,
            symbol,
            h1_count=len(market[symbol][0]),
            h4_count=len(market[symbol][1]),
            d1_count=len(market[symbol][2]),
        )
        for symbol in SYMBOLS
    }
    v12 = normalize_candidates(
        live.build_v12_candidates(prepared), "SWING", "V12_EXTERNAL", "NONE", 0.04
    )

    strategy_frames = []
    for symbol, (h1, h4, d1) in market.items():
        generated = generate_symbol_candidates(symbol, h1, h4, d1)
        if not generated.empty:
            strategy_frames.append(generated)
    strategy = pd.concat(strategy_frames, ignore_index=True, sort=False)
    strategy["entry_time"] = pd.to_datetime(strategy["entry_time"], utc=True)
    strategy["exit_time"] = pd.to_datetime(strategy["exit_time"], utc=True)

    dual.install_five_symbol_ict_profiles()
    ict_frames = []
    for symbol, (h1, h4, d1) in market.items():
        prepared_h1, _, _ = ict_engine.prepare_frames(h1, h4, d1)
        for profile in ict_engine.PROFILES[symbol]:
            generated = ict_engine.generate_candidates(symbol, prepared_h1, profile)
            if generated.empty:
                continue
            generated = normalize_candidates(
                generated, "ICT", "WIDE_SESSION_SWEEP", profile.name, 0.09
            )
            ict_frames.append(generated)
    wide_ict = pd.concat(ict_frames, ignore_index=True, sort=False)

    source = pd.concat([v12, strategy, wide_ict], ignore_index=True, sort=False)
    source["entry_time"] = pd.to_datetime(source["entry_time"], utc=True)
    source["exit_time"] = pd.to_datetime(source["exit_time"], utc=True)
    source["profile"] = source["profile"].fillna("NONE").astype(str)
    source["mode"] = source["mode"].astype(str)
    source["engine"] = source["engine"].astype(str)
    source["side"] = source["side"].astype(str).str.upper()
    source["selection_cost_r"] = pd.to_numeric(source["selection_cost_r"], errors="coerce")
    source = source[(source["entry_time"] >= TEST_START) & (source["entry_time"] <= TEST_END)]
    source = source.sort_values(["entry_time", "symbol", "mode", "engine", "profile"])
    source = source.drop_duplicates(
        ["entry_time", "exit_time", "symbol", "mode", "engine", "profile", "side"]
    ).reset_index(drop=True)
    return source


def full_block_validation(sleeve: v148.FrozenSleeve, evidence: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
    try:
        v148.validate_sleeve(sleeve, evidence)
        return True, None
    except RuntimeError as exc:
        return False, str(exc)


def fresh_stats(frame: pd.DataFrame) -> dict[str, Any]:
    fresh = frame[(frame["entry_time"] >= FRESH_START) & (frame["entry_time"] <= TEST_END)]
    return v148.block_stats(fresh)


def annual_candidate_stats(frame: pd.DataFrame) -> list[dict[str, Any]]:
    work = frame.copy()
    work["year"] = work["entry_time"].dt.year
    rows = []
    for year, group in work.groupby("year", sort=True):
        stats = v148.block_stats(group)
        rows.append({"year": int(year), **stats})
    return rows


def enrich_closed_trades(trades: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    keys = ["symbol", "setup", "side", "entry_time", "exit_time"]
    metadata = candidates[keys + ["raw_r_multiple", "cost_r"]].drop_duplicates(keys)
    output = trades.merge(metadata, on=keys, how="left")
    output["modeled_fee_dollars"] = output["risk_dollars"] * output["cost_r"].fillna(0.0)
    output["gross_pnl_before_modeled_cost"] = output["risk_dollars"] * output["raw_r_multiple"].fillna(output["r_multiple"])
    return output


def time_series(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    months = pd.date_range(TEST_START.floor("D"), TEST_END.floor("D"), freq="MS", tz="UTC")
    if trades.empty:
        monthly = pd.DataFrame({"month": months, "net_profit": 0.0, "modeled_fees": 0.0})
    else:
        work = trades.copy()
        work["exit_time"] = pd.to_datetime(work["exit_time"], utc=True)
        work["month"] = work["exit_time"].dt.to_period("M").dt.to_timestamp().dt.tz_localize("UTC")
        grouped = work.groupby("month", as_index=False).agg(
            net_profit=("pnl", "sum"),
            modeled_fees=("modeled_fee_dollars", "sum"),
            trades=("trade_id", "count"),
        )
        monthly = pd.DataFrame({"month": months}).merge(grouped, on="month", how="left").fillna(0.0)
    monthly["ending_equity"] = v148.STARTING_BALANCE + monthly["net_profit"].cumsum()
    monthly["peak_equity"] = monthly["ending_equity"].cummax().clip(lower=v148.STARTING_BALANCE)
    monthly["drawdown_percent"] = (
        (monthly["peak_equity"] - monthly["ending_equity"]) / monthly["peak_equity"] * 100.0
    )
    monthly["cumulative_modeled_fees"] = monthly["modeled_fees"].cumsum()

    annual = monthly.copy()
    annual["year"] = annual["month"].dt.year
    annual = annual.groupby("year", as_index=False).agg(
        net_profit=("net_profit", "sum"),
        modeled_fees=("modeled_fees", "sum"),
        ending_equity=("ending_equity", "last"),
        maximum_month_end_drawdown_percent=("drawdown_percent", "max"),
        trades=("trades", "sum"),
    )
    return monthly, annual


def plot_outputs(monthly: pd.DataFrame, annual: pd.DataFrame, trades: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(12, 6))
    plt.plot(monthly["month"], monthly["ending_equity"])
    plt.axhline(v148.STARTING_BALANCE, linewidth=1)
    plt.ylabel("Equity ($)")
    plt.title("V14.8 external-data monthly equity: 2016-2026")
    plt.tight_layout()
    figure.savefig(OUT / "monthly_equity_2016_2026.png", dpi=180)
    plt.close(figure)

    figure = plt.figure(figsize=(11, 6))
    plt.bar(annual["year"].astype(str), annual["net_profit"])
    plt.axhline(0, linewidth=1)
    plt.ylabel("Net profit after modeled costs ($)")
    plt.title("Annual retail-net profit: 2016-2026 YTD")
    plt.xticks(rotation=45)
    plt.tight_layout()
    figure.savefig(OUT / "annual_net_profit_2016_2026.png", dpi=180)
    plt.close(figure)

    figure = plt.figure(figsize=(12, 5))
    plt.plot(monthly["month"], monthly["drawdown_percent"])
    plt.ylabel("Month-end drawdown (%)")
    plt.title("Monthly equity drawdown: 2016-2026")
    plt.tight_layout()
    figure.savefig(OUT / "monthly_drawdown_2016_2026.png", dpi=180)
    plt.close(figure)

    if not trades.empty:
        by_symbol = trades.groupby("symbol", as_index=False)["pnl"].sum().sort_values("pnl", ascending=False)
        figure = plt.figure(figsize=(9, 5))
        plt.bar(by_symbol["symbol"], by_symbol["pnl"])
        plt.axhline(0, linewidth=1)
        plt.ylabel("Net profit ($)")
        plt.title("External-data profit by symbol")
        plt.tight_layout()
        figure.savefig(OUT / "profit_by_symbol.png", dpi=180)
        plt.close(figure)

        by_mode = trades.groupby("engine_group", as_index=False)["pnl"].sum()
        by_mode["engine_group"] = by_mode["engine_group"].replace({"V12": "Swing"})
        figure = plt.figure(figsize=(7, 5))
        plt.bar(by_mode["engine_group"], by_mode["pnl"])
        plt.axhline(0, linewidth=1)
        plt.ylabel("Net profit ($)")
        plt.title("Swing versus ICT net profit")
        plt.tight_layout()
        figure.savefig(OUT / "profit_by_mode.png", dpi=180)
        plt.close(figure)

    figure = plt.figure(figsize=(12, 5))
    plt.plot(monthly["month"], monthly["cumulative_modeled_fees"])
    plt.ylabel("Cumulative modeled fees ($)")
    plt.title("Cumulative retail cost and fee reserve")
    plt.tight_layout()
    figure.savefig(OUT / "cumulative_modeled_fees.png", dpi=180)
    plt.close(figure)


def write_report(payload: dict[str, Any]) -> None:
    summary = payload["portfolio"]
    lines = [
        "# V14.8 Dukascopy External-Data Test",
        "",
        f"**Test period:** {payload['window']['start'][:10]} through {payload['window']['end'][:10]}",
        f"**Fresh post-selection period:** {payload['fresh_period']['start'][:10]} through {payload['fresh_period']['end'][:10]}",
        "**Starting balance:** $5,000.00",
        "",
        "## Portfolio result",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Net profit after modeled retail costs | ${summary['net_profit']:,.2f} |",
        f"| Ending balance | ${summary['ending_balance']:,.2f} |",
        f"| Return | {summary['return_percent']:.2f}% |",
        f"| Profit factor | {float(summary['profit_factor'] or 0.0):.4f} |",
        f"| Maximum closed drawdown | {summary['max_closed_drawdown_percent']:.4f}% |",
        f"| Projected stressed drawdown | {summary['stress_drawdown_percent']:.4f}% |",
        f"| Closed trades | {summary['closed_trades']} |",
        f"| $20,000 target reached | {payload['target_reached']} |",
        "",
        "## Frozen-sleeve external validation",
        "",
        "| Symbol | Mode | Trades | Full-period five-block pass | Fresh 2022-2026 trades | Fresh net R | Fresh PF |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for item in payload["sleeves"]:
        fresh = item["fresh_2022_2026"]
        lines.append(
            f"| {item['symbol']} | {item['mode']} | {item['trade_count']} | {item['five_block_passed']} | "
            f"{fresh['trades']} | {fresh['net_r']:.4f} | {float(fresh['profit_factor'] or 0.0):.4f} |"
        )
    lines += [
        "",
        "## Method boundary",
        "",
        "- Signals are rebuilt from independent Dukascopy H1 bid candles; H4 and D1 are resampled from H1.",
        "- Ask candles are used to report observed H1 spread distributions.",
        "- Costs remain conservative fixed R allowances to preserve parity across strategy families.",
        "- This is not tick-level broker execution and does not reconstruct swap or commission schedules trade by trade.",
        "- The 2016-2022 period overlaps the strategy-development history. The period after 2022-03-05 is the genuinely fresh chronological check.",
        "- Historical performance does not guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    market, quality = load_market()
    source = build_external_candidates(market)
    source.to_csv(OUT / "all_external_candidates.csv", index=False)

    blocks = v148.chronological_blocks(TEST_START, TEST_END)
    swing_frames: list[pd.DataFrame] = []
    ict_frames: list[pd.DataFrame] = []
    sleeve_results: list[dict[str, Any]] = []
    for sleeve in v148.FROZEN_SLEEVES:
        try:
            frame = v148.materialize_sleeve(source, sleeve)
        except RuntimeError as exc:
            sleeve_results.append(
                {
                    "symbol": sleeve.symbol,
                    "mode": sleeve.mode,
                    "specification": asdict(sleeve),
                    "trade_count": 0,
                    "five_block_passed": False,
                    "validation_error": str(exc),
                    "evidence": {},
                    "fresh_2022_2026": v148.block_stats(pd.DataFrame(columns=["r_multiple"])),
                    "annual": [],
                }
            )
            continue
        frame = frame[(frame["entry_time"] >= TEST_START) & (frame["entry_time"] <= TEST_END)]
        evidence = v148.sleeve_evidence(frame, blocks)
        passed, error = full_block_validation(sleeve, evidence)
        sleeve_results.append(
            {
                "symbol": sleeve.symbol,
                "mode": sleeve.mode,
                "specification": asdict(sleeve),
                "trade_count": int(len(frame)),
                "five_block_passed": passed,
                "validation_error": error,
                "evidence": evidence,
                "fresh_2022_2026": fresh_stats(frame),
                "annual": annual_candidate_stats(frame),
            }
        )
        (swing_frames if sleeve.mode == "SWING" else ict_frames).append(frame)

    if not swing_frames or not ict_frames:
        raise RuntimeError("External source did not reproduce both swing and ICT candidate groups")
    swing = pd.concat(swing_frames, ignore_index=True, sort=False).sort_values(["entry_time", "symbol", "engine"])
    ict = pd.concat(ict_frames, ignore_index=True, sort=False).sort_values(["entry_time", "symbol", "engine"])
    swing.to_csv(OUT / "external_validated_swing_candidates.csv", index=False)
    ict.to_csv(OUT / "external_validated_ict_candidates.csv", index=False)

    old_risk, old_guards = v148.install_profile(ict)
    replay = v148.ProjectedStressReplay(swing, ict)
    try:
        summary, trades, skipped = replay.run()
    finally:
        v148.restore_profile(old_risk, old_guards)
    trades = enrich_closed_trades(trades, pd.concat([swing, ict], ignore_index=True, sort=False))
    trades.to_csv(OUT / "closed_trades.csv", index=False)
    skipped.to_csv(OUT / "skipped_candidates.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(OUT / "closed_drawdown_governor_events.csv", index=False)
    pd.DataFrame(replay.projected_stress_events).to_csv(OUT / "projected_stress_governor_events.csv", index=False)

    monthly, annual = time_series(trades)
    monthly.to_csv(OUT / "monthly_equity_profit_drawdown.csv", index=False)
    annual.to_csv(OUT / "annual_profit_fees_drawdown.csv", index=False)
    plot_outputs(monthly, annual, trades)

    active_swing = sorted(trades[trades["engine_group"] == "V12"]["symbol"].unique().tolist()) if not trades.empty else []
    active_ict = sorted(trades[trades["engine_group"] == "ICT"]["symbol"].unique().tolist()) if not trades.empty else []
    all_ten = set(active_swing) == set(SYMBOLS) and set(active_ict) == set(SYMBOLS)
    safe = (
        float(summary["max_closed_drawdown_percent"]) <= v148.MAX_CLOSED_DD
        and float(summary["stress_drawdown_percent"]) <= v148.MAX_STRESS_DD
    )
    payload = {
        "generated_at": datetime.now().isoformat(),
        "provider": "Dukascopy via dukascopy-node",
        "window": {"start": TEST_START.isoformat(), "end": TEST_END.isoformat()},
        "warmup_start": WARMUP_START.isoformat(),
        "fresh_period": {"start": FRESH_START.isoformat(), "end": TEST_END.isoformat()},
        "cost_model": {
            "swing_r_per_trade": 0.04,
            "wide_ict_r_per_trade": 0.09,
            "strategy_family_ict_r_per_trade": 0.12,
            "observed_bid_ask_spread_reported_separately": True,
        },
        "data_quality": quality,
        "sleeves": sleeve_results,
        "coverage": {
            "active_swing_symbols": active_swing,
            "active_ict_symbols": active_ict,
            "all_ten_sleeves_executed": all_ten,
            "all_ten_sleeves_reproduced": len(swing_frames) == 5 and len(ict_frames) == 5,
            "all_sleeves_passed_external_five_block_validation": all(item["five_block_passed"] for item in sleeve_results),
        },
        "portfolio": {**summary, "safe": safe},
        "attribution": v148.attribution(trades),
        "total_modeled_fee_dollars": round(float(trades["modeled_fee_dollars"].sum()), 2) if not trades.empty else 0.0,
        "target": {"net_profit": TARGET_NET, "ending_balance": v148.STARTING_BALANCE + TARGET_NET},
        "target_reached": float(summary["net_profit"]) >= TARGET_NET,
        "target_gap": round(max(0.0, TARGET_NET - float(summary["net_profit"])), 2),
        "monthly": monthly.assign(month=monthly["month"].astype(str)).to_dict("records"),
        "annual": annual.to_dict("records"),
    }
    (OUT / "v14_8_dukascopy_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(payload)
    print(json.dumps({
        "window": payload["window"],
        "fresh_period": payload["fresh_period"],
        "coverage": payload["coverage"],
        "portfolio": payload["portfolio"],
        "target_reached": payload["target_reached"],
        "target_gap": payload["target_gap"],
        "total_modeled_fee_dollars": payload["total_modeled_fee_dollars"],
        "sleeves": [
            {
                "symbol": item["symbol"],
                "mode": item["mode"],
                "trades": item["trade_count"],
                "five_block_passed": item["five_block_passed"],
                "fresh": item["fresh_2022_2026"],
            }
            for item in sleeve_results
        ],
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
