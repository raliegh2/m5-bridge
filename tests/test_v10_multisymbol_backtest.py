import pandas as pd

from research.v10_multisymbol_backtest import metrics, replay


def candidate_frame():
    frame = pd.DataFrame([
        {
            "symbol": "EURUSD",
            "engine": "EURUSD_SATELLITE_V7",
            "setup": "EUR_MOMENTUM_SHORT",
            "side": -1,
            "entry_time": pd.Timestamp("2026-01-01T10:00:00Z"),
            "exit_time": pd.Timestamp("2026-01-01T12:00:00Z"),
            "risk_percent": 0.35,
            "r_multiple": 1.0,
            "source": "accepted",
            "candidate_order": 0,
        },
        {
            "symbol": "GBPUSD",
            "engine": "GBPUSD_SATELLITE_V2",
            "setup": "LONDON_PULLBACK_V2",
            "side": 1,
            "entry_time": pd.Timestamp("2026-01-02T10:00:00Z"),
            "exit_time": pd.Timestamp("2026-01-02T12:00:00Z"),
            "risk_percent": 0.30,
            "r_multiple": -1.0,
            "source": "accepted",
            "candidate_order": 1,
        },
    ])
    return frame


def test_replay_uses_shared_risk_and_compounding():
    trades, rejected, realized, stress = replay(candidate_frame())
    result = metrics(trades, realized, stress)
    assert len(rejected) == 0
    assert result.trades == 2
    assert result.ending_balance != 5000.0


def test_cost_stress_reduces_profit():
    base, _, realized, stress = replay(candidate_frame())
    stressed, _, srealized, sstress = replay(candidate_frame(), additional_cost_r=0.10)
    assert metrics(stressed, srealized, sstress).net_profit < metrics(base, realized, stress).net_profit
