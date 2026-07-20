"""Portfolio backtester: a single-symbol run must match backtest_books."""

import os
from dataclasses import replace

from mt5_ai_bridge.backtest_books import (run_multibook_backtest,
                                          _load_csv_with_time)
from mt5_ai_bridge.backtest_portfolio import (run_portfolio_backtest,
                                              pip_and_conv)

_CSV = os.path.join(os.path.dirname(__file__), "..", "GBPUSD_M5.csv")


def test_pip_and_conv_usd_vs_jpy():
    assert pip_and_conv("EURUSD", 152.0) == (0.0001, 1.0)
    pip, conv = pip_and_conv("USDJPY", 152.0)
    assert pip == 0.01 and round(conv, 5) == round(1 / 152.0, 5)


def test_single_symbol_portfolio_matches_backtest_books():
    from mt5_ai_bridge.config import load_settings
    df = _load_csv_with_time(_CSV).head(5000)
    s = replace(load_settings(dotenv=False), console_status=False,
                write_dashboard=False, serve_dashboard=False)

    books = run_multibook_backtest(df, s, 5000.0, 1.0, 0.0)
    port = run_portfolio_backtest({"GBPUSD": df.copy()}, s, 5000.0,
                                  {"GBPUSD": 1.0})

    assert port["trades"] == len(books.trades)
    assert abs(port["final_balance"] - books.final_balance) < 0.01
