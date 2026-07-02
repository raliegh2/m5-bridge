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
from v17_signal_families import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v17_output"
OUT.mkdir(parents=True, exist_ok=True)
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")


def component_rows(frame, start, end):
    rows = []
    if frame.empty:
        return rows
    years = max((end - start).total_seconds() / (365.25 * 86400), 0.25)
    for (symbol, engine), group in frame.groupby(["symbol", "engine"]):
        pnl = group["risk_dollars"] * group["r_multiple"]
        gp = float(pnl[pnl > 0].sum())
        gl = float(-pnl[pnl < 0].sum())
        rows.append({
            "symbol": symbol,
            "engine": engine,
            "trades": int(len(group)),
            "trades_per_year": float(len(group) / years),
            "gross_income": gp,
            "gross_loss": gl,
            "net_profit": gp - gl,
            "profit_factor": gp / gl if gl else float("inf"),
        })
    return rows


def main():
    prepared = {symbol: base.prepare(symbol) for symbol in SYMBOLS}
    selected_frames, selection = [], {}
    for symbol in SYMBOLS:
        h4 = prepared[symbol][1]
        anchor = base.gbpusd_precision_candidates(h4) if symbol == "GBPUSD" else None
        selected, report = select_symbol(symbol, h4, anchor)
        selected.to_csv(OUT / f"{symbol}_selected_candidates.csv", index=False)
        selected_frames.append(selected)
        selection[symbol] = report
    candidates = merge_frames(selected_frames)
    candidates.to_csv(OUT / "all_selected_candidates.csv", index=False)
    end = min(prepared[symbol][1]["time"].max() for symbol in SYMBOLS)
    start_all = max(prepared[symbol][1]["time"].min() for symbol in SYMBOLS)
    results = {
        "data_source": base.DATA_URL,
        "common_start": start_all.isoformat(),
        "common_end": end.isoformat(),
        "selection": selection,
        "guard": asdict(GuardConfig()),
        "windows": {},
    }
    overview = []
    components = []
    for years in (10, 5, 3, 2, 1):
        start = max(start_all, end - pd.DateOffset(years=years))
        summary, accepted = replay(candidates, start, end)
        summary["components"] = component_rows(accepted, start, end)
        results["windows"][str(years)] = summary
        accepted.to_csv(OUT / f"accepted_{years}y.csv", index=False)
        overview.append({"period": years, **{k: v for k, v in summary.items() if k != "components"}})
        for item in summary["components"]:
            components.append({"period": years, **item})
    pd.DataFrame(overview).to_csv(OUT / "profit_overview.csv", index=False)
    pd.DataFrame(components).to_csv(OUT / "component_overview.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
