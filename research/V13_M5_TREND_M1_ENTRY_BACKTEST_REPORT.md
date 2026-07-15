# V13 M5 Trend + M1 Entry Backtest Report

Status: **no-lookahead multi-timeframe research replay**

## Rule design

- M5 completed bars define trend/regime using EMA, ADX, ATR and MACD histogram.
- M1 bars define entries using breakout, pullback/reclaim and RSI-reclaim triggers.
- Entry occurs at the next M1 open after the signal candle.
- Strategy selection used train + confirmation only; test data was not used to select parameters.

## Data check

| Symbol | Uploaded M1 median step | Rows | Note |
|---|---:|---:|---|
| GBPUSD | 1.00 min | 3,721,516 | OK |
| GBPJPY | 1.00 min | 3,721,448 | OK |
| EURUSD | 5.00 min | 745,177 | Excluded: not true M1 |

## Final out-of-sample result

| Metric | Value |
|---|---:|
| starting_balance | 5000.0000 |
| ending_balance | 5000.0000 |
| net_profit | 0 |
| return_percent | 0 |
| trades | 0 |
| wins | 0 |
| losses | 0 |
| win_rate | 0 |
| profit_factor | 0 |
| max_drawdown_percent | 0 |
| avg_r | 0 |
| total_r | 0 |

## Selected strategies

| Symbol | Selected | Reason / Strategy | Test Net | Test PF | Test Trades |
|---|---|---|---:|---:|---:|
| GBPUSD | No | No M5-trend/M1-entry setup passed train+confirmation gates |  |  |  |
| GBPJPY | No | No M5-trend/M1-entry setup passed train+confirmation gates |  |  |  |
| EURUSD | No | Uploaded M1 median step is 5.00 minutes, not true M1 |  |  |  |

## Decision

The M5-trend/M1-entry extension did not pass the robust profitability gate. Keep intraday disabled.
