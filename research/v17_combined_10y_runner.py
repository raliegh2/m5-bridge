"""Ten-year profitability report for V17 guard-recovery swings plus satellites.

The combined portfolio includes all selected V17 swing candidates and the only
admitted V17 satellite/precision engine, ``GBPUSD_V10_PRECISION``. It compares
swing-only and combined portfolios under the same guard-recovery and portfolio
risk controls, then outputs exact profit attribution for all five symbols.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import v13_expanded_assets_backtest as base
from v17_replay_core import replay
from v17_select_core import merge_frames
from v17_selector import select_symbol

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v17_combined_10y_output"
OUT.mkdir(parents=True, exist_ok=True)
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
SATELLITE_ENGINES = frozenset({"GBPUSD_V10_PRECISION"})
STARTING_BALANCE = 5000.0


def classify_engine(engine: str) -> str:
    return "satellite" if str(engine) in SATELLITE_ENGINES else "swing"


def profit_rows(accepted: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for symbol in SYMBOLS:
        group = accepted[accepted["symbol"] == symbol].copy()
        if group.empty:
            rows.append({
                "symbol": symbol,
                "trades": 0,
                "swing_trades": 0,
                "satellite_trades": 0,
                "gross_income": 0.0,
                "gross_loss": 0.0,
                "net_profit": 0.0,
                "swing_net_profit": 0.0,
                "satellite_net_profit": 0.0,
                "profit_factor": 0.0,
                "return_on_5000_percent": 0.0,
            })
            continue
        group["section"] = group["engine"].map(classify_engine)
        group["pnl"] = group["risk_dollars"] * group["r_multiple"]
        gross_income = float(group.loc[group["pnl"] > 0, "pnl"].sum())
        gross_loss = float(-group.loc[group["pnl"] < 0, "pnl"].sum())
        swing_net = float(group.loc[group["section"] == "swing", "pnl"].sum())
        satellite_net = float(group.loc[group["section"] == "satellite", "pnl"].sum())
        net = gross_income - gross_loss
        rows.append({
            "symbol": symbol,
            "trades": int(len(group)),
            "swing_trades": int((group["section"] == "swing").sum()),
            "satellite_trades": int((group["section"] == "satellite").sum()),
            "gross_income": gross_income,
            "gross_loss": gross_loss,
            "net_profit": net,
            "swing_net_profit": swing_net,
            "satellite_net_profit": satellite_net,
            "profit_factor": gross_income / gross_loss if gross_loss else (float("inf") if gross_income else 0.0),
            "return_on_5000_percent": net / STARTING_BALANCE * 100.0,
        })
    return pd.DataFrame(rows)


def engine_rows(accepted: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if accepted.empty:
        return pd.DataFrame(rows)
    data = accepted.copy()
    data["section"] = data["engine"].map(classify_engine)
    data["pnl"] = data["risk_dollars"] * data["r_multiple"]
    for (symbol, engine, section), group in data.groupby(["symbol", "engine", "section"]):
        gross_income = float(group.loc[group["pnl"] > 0, "pnl"].sum())
        gross_loss = float(-group.loc[group["pnl"] < 0, "pnl"].sum())
        rows.append({
            "symbol": symbol,
            "engine": engine,
            "section": section,
            "trades": int(len(group)),
            "gross_income": gross_income,
            "gross_loss": gross_loss,
            "net_profit": gross_income - gross_loss,
            "profit_factor": gross_income / gross_loss if gross_loss else (float("inf") if gross_income else 0.0),
        })
    return pd.DataFrame(rows)


def main() -> None:
    prepared = {symbol: base.prepare(symbol) for symbol in SYMBOLS}
    selected_frames = []
    selection = {}
    for symbol in SYMBOLS:
        h4 = prepared[symbol][1]
        satellite = base.gbpusd_precision_candidates(h4) if symbol == "GBPUSD" else None
        selected, report = select_symbol(symbol, h4, satellite)
        selected_frames.append(selected)
        selection[symbol] = report

    combined_candidates = merge_frames(selected_frames)
    swing_candidates = combined_candidates[
        ~combined_candidates["engine"].isin(SATELLITE_ENGINES)
    ].copy()

    end = min(prepared[symbol][1]["time"].max() for symbol in SYMBOLS)
    common_start = max(prepared[symbol][1]["time"].min() for symbol in SYMBOLS)
    start = max(common_start, end - pd.DateOffset(years=10))

    swing_summary, swing_accepted = replay(
        swing_candidates,
        start,
        end,
        recovery_probes=True,
    )
    combined_summary, combined_accepted = replay(
        combined_candidates,
        start,
        end,
        recovery_probes=True,
    )

    symbol_profit = profit_rows(combined_accepted)
    engine_profit = engine_rows(combined_accepted)
    section_profit = (
        engine_profit.groupby("section", as_index=False)[
            ["trades", "gross_income", "gross_loss", "net_profit"]
        ].sum()
        if not engine_profit.empty
        else pd.DataFrame(columns=["section", "trades", "gross_income", "gross_loss", "net_profit"])
    )

    combined_candidates.to_csv(OUT / "combined_candidates.csv", index=False)
    combined_accepted.to_csv(OUT / "accepted_combined_10y.csv", index=False)
    swing_accepted.to_csv(OUT / "accepted_swing_only_10y.csv", index=False)
    symbol_profit.to_csv(OUT / "profit_by_symbol_10y.csv", index=False)
    engine_profit.to_csv(OUT / "profit_by_engine_10y.csv", index=False)
    section_profit.to_csv(OUT / "profit_by_section_10y.csv", index=False)

    results = {
        "status": "RESEARCH_ONLY",
        "requested_window_years": 10,
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "actual_years": (end - start).total_seconds() / (365.25 * 86400),
        "starting_balance": STARTING_BALANCE,
        "satellite_engines": sorted(SATELLITE_ENGINES),
        "selection": selection,
        "swing_only": {
            **swing_summary,
            "ending_balance": STARTING_BALANCE + swing_summary["net_profit"],
        },
        "combined": {
            **combined_summary,
            "ending_balance": STARTING_BALANCE + combined_summary["net_profit"],
        },
        "satellite_portfolio_impact": {
            "net_profit_delta": combined_summary["net_profit"] - swing_summary["net_profit"],
            "trade_delta": combined_summary["trades"] - swing_summary["trades"],
            "max_drawdown_delta": combined_summary["max_drawdown_percent"] - swing_summary["max_drawdown_percent"],
            "stress_drawdown_delta": combined_summary["stress_drawdown_percent"] - swing_summary["stress_drawdown_percent"],
        },
        "profit_by_symbol": symbol_profit.to_dict(orient="records"),
        "profit_by_engine": engine_profit.to_dict(orient="records"),
        "profit_by_section": section_profit.to_dict(orient="records"),
    }
    (OUT / "results_10y.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# V17 Guard-Recovery Swing + Satellite 10-Year Profitability",
        "",
        "Status: **RESEARCH ONLY — DO NOT DEPLOY**",
        "",
        f"Requested window: 10 years; actual common coverage: `{start.isoformat()}` to `{end.isoformat()}`.",
        "",
        "## Portfolio comparison",
        "",
        "| Scenario | Trades | Net profit | Ending balance | Return | Profit factor | Max DD | Stress DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Swing only | {swing_summary['trades']} | ${swing_summary['net_profit']:.2f} | ${STARTING_BALANCE + swing_summary['net_profit']:.2f} | {swing_summary['return_percent']:.2f}% | {swing_summary['profit_factor']:.3f} | {swing_summary['max_drawdown_percent']:.2f}% | {swing_summary['stress_drawdown_percent']:.2f}% |",
        f"| Swing + admitted satellite | {combined_summary['trades']} | ${combined_summary['net_profit']:.2f} | ${STARTING_BALANCE + combined_summary['net_profit']:.2f} | {combined_summary['return_percent']:.2f}% | {combined_summary['profit_factor']:.3f} | {combined_summary['max_drawdown_percent']:.2f}% | {combined_summary['stress_drawdown_percent']:.2f}% |",
        "",
        "## Profit by symbol",
        "",
        "| Symbol | Trades | Swing net | Satellite net | Combined net | Profit factor |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in symbol_profit.to_dict(orient="records"):
        lines.append(
            f"| {row['symbol']} | {row['trades']} | ${row['swing_net_profit']:.2f} | ${row['satellite_net_profit']:.2f} | ${row['net_profit']:.2f} | {row['profit_factor']:.3f} |"
        )
    lines.extend([
        "",
        "## Satellite interpretation",
        "",
        "The reverted V17 model contains only one admitted satellite/precision engine: `GBPUSD_V10_PRECISION`. The unvalidated V18 M15 five-symbol family is excluded.",
    ])
    (OUT / "V17_COMBINED_10Y_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
