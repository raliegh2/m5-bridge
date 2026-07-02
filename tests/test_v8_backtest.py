import math

import pandas as pd

from mt5_ai_bridge.v8_backtest import build_equity_curves, run_replay


def _ledger(path):
    pd.DataFrame([
        {
            "symbol": "GBPUSD",
            "engine": "GBPUSD_SWING_V6",
            "setup": "CORE",
            "side": 1,
            "entry_time": "2026-01-01T10:00:00Z",
            "exit_time": "2026-01-02T10:00:00Z",
            "risk_percent": 0.50,
            "risk_dollars": 25.0,
            "r_multiple": 2.0,
            "pnl": 50.0,
        },
        {
            "symbol": "EURUSD",
            "engine": "EURUSD_SATELLITE_V7",
            "setup": "TEST",
            "side": -1,
            "entry_time": "2026-01-01T12:00:00Z",
            "exit_time": "2026-01-01T13:00:00Z",
            "risk_percent": 0.25,
            "risk_dollars": 12.5,
            "r_multiple": -1.0,
            "pnl": -12.5,
        },
    ]).to_csv(path, index=False)


def test_replay_rebuilds_profit_factor_and_stress_equity(tmp_path):
    trades = tmp_path / "trades.csv"
    rejected = tmp_path / "rejected.csv"
    _ledger(trades)
    pd.DataFrame([{"reason": "max_open_risk"}]).to_csv(rejected, index=False)

    result = run_replay(trades, rejected)
    assert result.portfolio.trades == 2
    assert result.portfolio.net_profit == 37.5
    assert math.isclose(result.portfolio.profit_factor, 4.0)
    assert result.ending_balance == 5037.5
    assert result.rejected_candidates == 1
    assert result.rejection_reasons == {"max_open_risk": 1}
    assert result.open_risk_stress_drawdown_percent > result.realized_drawdown_percent


def test_zero_duration_trade_enters_before_its_own_exit(tmp_path):
    trades = tmp_path / "zero.csv"
    pd.DataFrame([{
        "symbol": "GBPUSD",
        "engine": "GBPUSD_SATELLITE_V2",
        "setup": "ZERO",
        "side": 1,
        "entry_time": "2026-01-01T10:00:00Z",
        "exit_time": "2026-01-01T10:00:00Z",
        "risk_percent": 0.25,
        "risk_dollars": 12.5,
        "r_multiple": 1.0,
        "pnl": 12.5,
    }]).to_csv(trades, index=False)

    frame = pd.read_csv(trades, parse_dates=["entry_time", "exit_time"])
    realized, stress = build_equity_curves(frame)
    assert realized[-1] == 5012.5
    assert min(stress) == 4987.5
