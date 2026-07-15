from __future__ import annotations

import pandas as pd

from research.v14_3_production_improved_backtest import (
    ALL_SYMBOLS,
    ICT_POLICIES,
    ImprovedReplay,
    PortfolioPolicy,
    baseline_replay,
    diagnostics,
)


def _stamp(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def _v12_rows() -> pd.DataFrame:
    rows = []
    engines = {
        "GBPUSD": "GBPUSD_V10_PRECISION",
        "EURUSD": "EURUSD_SWING_CORE",
        "GBPJPY": "GBPJPY_SWING_CORE",
        "AUDUSD": "AUDUSD_TREND_PULLBACK",
        "USDJPY": "USDJPY_SAFE_HAVEN_BREAKOUT",
    }
    for index, symbol in enumerate(ALL_SYMBOLS):
        rows.append({
            "entry_time": _stamp(f"2020-01-{index + 2:02d} 08:00:00"),
            "exit_time": _stamp(f"2020-01-{index + 2:02d} 12:00:00"),
            "symbol": symbol,
            "engine": engines[symbol],
            "setup": "TEST",
            "side": "BUY",
            "risk_percent": 0.10,
            "r_multiple": 1.0,
        })
    return pd.DataFrame(rows)


def _ict_rows() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "entry_time": _stamp("2023-01-02 09:00:00"),
            "exit_time": _stamp("2023-01-02 10:00:00"),
            "symbol": "GBPJPY",
            "engine": "ICT_V14_3",
            "setup": "sweep_reclaim_15",
            "side": "BUY",
            "r_multiple": -1.0,
        },
        {
            "entry_time": _stamp("2023-01-02 09:30:00"),
            "exit_time": _stamp("2023-01-02 10:30:00"),
            "symbol": "GBPJPY",
            "engine": "ICT_V14_3",
            "setup": "sweep_reclaim_30",
            "side": "BUY",
            "r_multiple": 1.0,
        },
        {
            "entry_time": _stamp("2023-01-03 09:00:00"),
            "exit_time": _stamp("2023-01-03 10:00:00"),
            "symbol": "GBPJPY",
            "engine": "ICT_V14_3",
            "setup": "sweep_reclaim_15",
            "side": "BUY",
            "r_multiple": 1.0,
        },
        {
            "entry_time": _stamp("2023-01-03 10:30:00"),
            "exit_time": _stamp("2023-01-03 11:00:00"),
            "symbol": "GBPUSD",
            "engine": "ICT_V14_3",
            "setup": "sweep_reclaim_30",
            "side": "SELL",
            "r_multiple": 1.0,
        },
    ])


def test_all_five_symbols_have_explicit_engine_coverage() -> None:
    report = diagnostics(_v12_rows(), _ict_rows(), _v12_rows().assign(engine_group="V12", pnl=1.0), pd.DataFrame())
    assert set(report) == set(ALL_SYMBOLS)
    assert all(report[symbol]["data_available"] for symbol in ALL_SYMBOLS)
    assert report["EURUSD"]["engines_expected"]
    assert report["AUDUSD"]["engines_expected"]
    assert report["USDJPY"]["engines_expected"]


def test_gbpjpy_policy_is_stricter_than_gbpusd() -> None:
    gbpjpy = ICT_POLICIES["GBPJPY"]
    gbpusd = ICT_POLICIES["GBPUSD"]
    assert gbpjpy.normal_risk == 0.20
    assert gbpjpy.post_loss_risk == 0.10
    assert gbpjpy.max_open_positions == 1
    assert gbpjpy.stop_after_daily_losses == 2
    assert gbpjpy.daily_loss_cap_percent == 0.50
    assert gbpjpy.normal_risk < gbpusd.normal_risk


def test_one_open_gbpjpy_trade_limit_rejects_overlap() -> None:
    summary, trades, skipped = ImprovedReplay(_v12_rows(), _ict_rows(), PortfolioPolicy()).run()
    assert "SYMBOL_OPEN_POSITION_LIMIT" in set(skipped["skip_reason"])
    accepted_gbpjpy = trades[(trades["engine_group"] == "ICT") & (trades["symbol"] == "GBPJPY")]
    assert accepted_gbpjpy["risk_percent"].max() <= 0.20
    assert summary["by_symbol"]["GBPJPY"]["trades"] >= 1


def test_baseline_and_improved_leave_v12_five_symbol_engines_enabled() -> None:
    baseline_summary, baseline_trades, _ = baseline_replay(_v12_rows(), _ict_rows(), PortfolioPolicy())
    improved_summary, improved_trades, _ = ImprovedReplay(_v12_rows(), _ict_rows(), PortfolioPolicy()).run()
    baseline_v12 = baseline_trades[baseline_trades["engine_group"] == "V12"]
    improved_v12 = improved_trades[improved_trades["engine_group"] == "V12"]
    assert set(baseline_v12["symbol"]) == set(ALL_SYMBOLS)
    assert set(improved_v12["symbol"]) == set(ALL_SYMBOLS)
    assert baseline_summary["by_engine_group"]["V12"]["trades"] == 5
    assert improved_summary["by_engine_group"]["V12"]["trades"] == 5
