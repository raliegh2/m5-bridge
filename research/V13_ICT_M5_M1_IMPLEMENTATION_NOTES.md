# V13 ICT-Style M5 Trend / M1 Entry Implementation Notes

Status: **implementation prepared; full in-chat optimization could not complete within the current execution window**

## Objective

Build an ICT-style intraday extension where:

- M5 defines the higher-timeframe intraday bias/regime.
- M1 defines the exact entry.
- Entries are based only on information available at the signal candle.
- Orders enter at the next M1 open.
- The test does not select parameters from future out-of-sample results.

## ICT-style concepts translated into objective rules

Because ICT concepts are often discretionary, this branch translates them into strict rules:

| ICT-style concept | Objective implementation |
|---|---|
| Higher-timeframe bias | M5 EMA/MACD/RSI regime filter |
| Liquidity sweep | M1 candle sweeps prior 30/60-bar high or low and closes back inside the level |
| Displacement | M1 candle body expands beyond recent body average and closes near the candle extreme |
| Fair value gap | Three-candle imbalance: bullish low greater than high two candles back, or bearish high lower than low two candles back |
| Kill zone | London and New York intraday windows |
| Order-flow proxy | Tick-volume z-score, signed-volume z-score, candle body direction, and close location |
| Risk containment | 0.35% risk per trade, daily stop, total drawdown stop, loss-lockout gate |

## Data limitation

The uploaded files show:

| Symbol | M1 usability |
|---|---|
| GBPUSD | True M1 data available |
| GBPJPY | True M1 data available |
| EURUSD | Uploaded M1 file has 5-minute spacing, so it is not valid for M1-entry validation |

## Execution limitation

The full 10-year M1 optimization repeatedly evaluates millions of one-minute candles per symbol. During this chat execution, the ICT M5/M1 optimization exceeded the available runtime before a completed result could be produced.

## Required local run

Place the uploaded data files in:

```text
research/data/
```

Then run the local ICT harness:

```powershell
cd C:\Users\ralie\mt5-ai-bridge
git switch v13-v12-final-plus-v11-intraday
python research\v13_ict_m5_m1_backtest.py
```

Expected outputs:

```text
research/v13_ict_m5_m1_out/V13_ICT_M5_M1_BACKTEST_REPORT.md
research/v13_ict_m5_m1_out/selected.csv
research/v13_ict_m5_m1_out/all_ict_rank.csv
research/v13_ict_m5_m1_out/ict_oos_trades.csv
research/v13_ict_m5_m1_out/equity_curve.csv
research/v13_ict_m5_m1_out/summary.json
```

## Promotion rule

Do not enable the ICT intraday extension unless the out-of-sample result beats the V12 Final baseline after drawdown, spread, and capacity constraints are applied.
