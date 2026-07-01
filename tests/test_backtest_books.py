"""Multi-book backtester: broker simulator fills + an end-to-end replay."""

import pandas as pd

from mt5_ai_bridge.backtest_books import BacktestBroker, run_multibook_backtest
from tests.fakes import make_settings


def _df(rows):
    return pd.DataFrame(rows)


def test_broker_fills_take_profit():
    df = _df([{"time": 0, "open": 1.20, "high": 1.20, "low": 1.19, "close": 1.20},
              {"time": 300, "open": 1.20, "high": 1.26, "low": 1.20, "close": 1.25}])
    b = BacktestBroker(df, 10_000)
    b.advance(0)
    b.order_send({"action": b.TRADE_ACTION_DEAL, "symbol": "GBPUSD",
                  "volume": 0.10, "type": b.ORDER_TYPE_BUY, "price": 1.20,
                  "sl": 1.18, "tp": 1.25, "magic": 1})
    assert len(b.open) == 1
    b.advance(1)                       # high 1.26 >= tp 1.25
    assert len(b.open) == 0 and len(b.closed) == 1
    assert b.closed[0]["reason"] == "TP"
    assert b.closed[0]["profit"] == 500.0   # (1.25-1.20)*0.1*100000


def test_broker_fills_stop_loss():
    df = _df([{"time": 0, "open": 1.20, "high": 1.20, "low": 1.20, "close": 1.20},
              {"time": 300, "open": 1.20, "high": 1.20, "low": 1.17, "close": 1.18}])
    b = BacktestBroker(df, 10_000)
    b.advance(0)
    b.order_send({"action": b.TRADE_ACTION_DEAL, "symbol": "GBPUSD",
                  "volume": 0.10, "type": b.ORDER_TYPE_BUY, "price": 1.20,
                  "sl": 1.18, "tp": 1.30, "magic": 1})
    b.advance(1)                       # low 1.17 <= sl 1.18
    assert b.closed[0]["reason"] == "SL"
    assert b.closed[0]["profit"] == -200.0


def test_end_to_end_waits_until_all_confirmation_charts_are_ready():
    # This short M5 sample cannot warm up EMA 200 on D1. The conservative gate
    # must stay flat instead of inventing confirmation from incomplete data.
    base, n, t0 = 1.2000, 320, 1_700_000_000
    rows = [{"time": t0 + i * 300, "open": base + i * 2e-4 - 1e-4,
             "high": base + i * 2e-4 + 3e-4, "low": base + i * 2e-4 - 3e-4,
             "close": base + i * 2e-4} for i in range(n)]
    settings = make_settings(multi_book=True, strategy="reasoning",
                             reasoning_threshold=0.5, rsi_overbought=100,
                             rsi_oversold=0, strong_trend_confidence=0.5,
                             trail_enabled=True)
    result = run_multibook_backtest(_df(rows), settings, 10_000)

    assert len(result.equity_curve) == n
    summary = result.summary()
    assert "overall" in summary and "by_book" in summary
    assert summary["overall"]["trades"] == 0
    assert summary["by_book"] == {}
