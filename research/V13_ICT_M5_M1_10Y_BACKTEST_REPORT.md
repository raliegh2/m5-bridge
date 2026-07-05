# V13 ICT M5 Trend / M1 Entry 10-Year Backtest Report

Status: **completed uploaded-data no-lookahead research replay**

## Data used

| Symbol | M1 median step | Usable rows in 10-year window | Start | End | Decision |
|---|---:|---:|---|---|---|
| GBPUSD | 1.00 min | 3,721,516 | 2016-07-04 00:01:00 | 2026-07-03 17:48:00 | Tested |
| GBPJPY | 1.00 min | 3,721,448 | 2016-07-04 00:01:00 | 2026-07-03 17:48:00 | Tested |
| EURUSD | 5.00 min | N/A | 2016-01-04 00:00:00 | 2026-07-03 17:45:00 | Excluded: uploaded M1 file is not true M1 |

## Validation method

- M5 defined higher-timeframe bias/regime.
- M1 defined ICT-style entry timing.
- Entries occurred at the next M1 open after the signal candle.
- Train period: 2016-07-04 to 2021-12-31.
- Confirmation period: 2022-01-01 to 2022-12-31.
- Out-of-sample test period: 2023-01-01 to 2026-07-03.
- Test-period results were not used to select parameters.

## ICT-style rules tested

- Liquidity sweep of prior 30/60 M1 highs or lows.
- M5 EMA/MACD/RSI directional bias.
- M1 RSI reclaim and EMA reclaim.
- M1 displacement/FVG variant.
- London/New York intraday windows.
- Order-flow proxy using tick volume, signed volume, candle body direction, and close location.
- Inversion/fade variants were included.

## Portfolio result

| Metric | Value |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $4,773.00 |
| Net profit | $-227.00 |
| Return | -4.54% |
| Trades | 112 |
| Wins | 42 |
| Losses | 70 |
| Win rate | 37.50% |
| Profit factor | 0.811 |
| Max drawdown | 8.47% |
| Risk per trade | 0.35% |
| Daily stop | 1.35% |
| Total drawdown stop | 9.00% |

## Selected setup

| Symbol | Decision | Setup | Train result | Confirmation result | OOS test result |
|---|---|---|---:|---:|---:|
| GBPUSD | Selected | Inverted/fade `sweep_rsi`, 30-bar liquidity sweep, 1.5R target, 0.7 ATR stop, 90-minute max hold | +0.288R / PF 0.999 / 182 trades | +9.135R / PF 1.505 / 37 trades | -13.016R / PF 0.811 / 112 trades |
| GBPJPY | Not selected | Best candidate failed train/confirmation gate | -45.568R / PF 0.623 / 211 trades | +4.607R / PF 1.299 / 30 trades | Not traded |
| EURUSD | Excluded | Uploaded M1 file is 5-minute spacing | N/A | N/A | N/A |

## OOS result by year

| Year | Trades | Total R | Average R |
|---:|---:|---:|---:|
| 2023 | 39 | -0.934 | -0.024 |
| 2024 | 32 | -8.241 | -0.258 |
| 2025 | 23 | -5.121 | -0.223 |
| 2026 | 18 | +1.280 | +0.071 |

## Decision

The ICT-style intraday extension **failed the 10-year out-of-sample test**. It selected one GBPUSD setup using only training and confirmation data, but that setup lost money in the 2023-2026 out-of-sample period.

**Do not enable this ICT intraday extension in the V12 Final system yet.** The safest current action is to keep intraday disabled and keep V12 Final as the stronger research candidate.
