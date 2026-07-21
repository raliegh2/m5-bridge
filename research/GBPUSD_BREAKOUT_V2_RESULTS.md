# GBPUSD Breakout V2 — Proxy Results

## Data and execution assumptions

- Source: `GBPUSD_H4_201601040000_202607011200.csv`
- Coverage: 2016-01-04 through 2026-07-01
- 16,331 H4 bars
- D1 candles derived from completed H4 candles
- Signals use completed H4 and D1 candles only
- Entry at the next H4 open
- One GBPUSD position maximum
- Risk: 0.50% of current balance per trade
- MT5 spread field used with a conservative 0.8-pip minimum
- Additional slippage: 0.3 pip per execution side
- Swap proxy: -0.2 pip per holding day
- Conservative stop-first handling when a bar touches stop and target

## Final fixed rules

- D1 EMA20/EMA50 regime and close on the EMA20 side
- H4 close breaks the prior 55 completed H4 highs/lows
- H4 ADX(14) >= 15
- H4 tick volume >= 0.80 x its 20-bar average
- Entry only at H4 closes ending 12:00 or 16:00 UTC
- Initial stop: 2.0 x H4 ATR(14), clipped to 20-150 pips
- Target: 2.0R
- No partial profit taking
- Trailing starts at 1.0R
- Trailing distance: 2.5 x H4 ATR
- Maximum hold: 90 H4 bars

## Full-period result at 0.50% risk

| Metric | Result |
|---|---:|
| Starting balance | $100,000.00 |
| Ending balance | **$115,264.56** |
| Net profit | **+$15,264.56** |
| Return | **+15.26%** |
| Trades | **160** |
| Win rate | **49.38%** |
| Profit factor | **1.35** |
| Maximum mark-to-market drawdown | **5.59%** |
| Daily Sharpe proxy | **0.47** |

## Development/validation split

The parameters were selected on the earlier portion and then checked on the later period.

| Period | Trades | Net result | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|
| 2016-2021 development | 92 | **+$7,482.26** | **1.29** | 5.59% |
| 2022-Jul 2026 validation | 68 | **+$7,188.65** | **1.43** | 3.18% |

The split totals differ slightly from the continuous full-period result because each split begins with a fresh $100,000 balance while the full run compounds continuously.

## Annual independent checks

| Year | Trades | Net result | Profit factor |
|---|---:|---:|---:|
| 2016 | 12 | +$777.02 | 1.26 |
| 2017 | 16 | -$429.34 | 0.89 |
| 2018 | 16 | +$2,320.93 | 1.50 |
| 2019 | 16 | +$2,830.24 | 1.77 |
| 2020 | 18 | +$110.78 | 1.02 |
| 2021 | 15 | +$689.30 | 1.17 |
| 2022 | 19 | +$5,119.29 | 2.63 |
| 2023 | 15 | +$888.87 | 1.22 |
| 2024 | 15 | +$3,249.40 | 2.04 |
| 2025 | 15 | -$1,402.19 | 0.69 |
| 2026 through July 1 | 4 | -$683.02 | 0.32 |

The engine is profitable overall but is not profitable every year. 2025 and partial 2026 are losing periods and must not be hidden.

## Cost stress tests

| Cost model | Net result | Profit factor | Max drawdown |
|---|---:|---:|---:|
| Base: 0.8-pip floor, 0.3-pip slippage | +$15,264.56 | 1.35 | 5.59% |
| Higher cost: 1.5-pip floor, 0.6-pip slippage | +$12,412.16 | 1.28 | 5.69% |
| Stress: 2.0-pip floor, 1.0-pip slippage | +$11,849.36 | 1.27 | 5.80% |

## Parameter-neighborhood stability

A 729-combination neighborhood was tested around the final rules:

- channel: 45/55/65 H4 bars
- ADX: 12/15/18
- volume ratio: 0.7/0.8/0.9
- stop: 1.75/2.0/2.25 ATR
- target: 1.75/2.0/2.25R
- trail: 2.25/2.5/2.75 ATR

Results:

- 100% of combinations were profitable over the full sample
- 93.28% produced profit factor above 1.20
- median net profit: $12,216.52
- median profit factor: 1.28
- worst neighborhood net profit: $3,930.67
- best neighborhood net profit: $21,316.70

This is more credible than selecting one isolated parameter peak, but it is still historical research rather than a guarantee of future profit.

## What was removed from the losing engine

1. The losing trend-pullback and range-mean-reversion variants were disabled.
2. Partial profit at 1R was removed because it reduced the payoff from successful trends.
3. Weekly confirmation was removed because it delayed entries without improving the selected breakout model.
4. The engine now permits only one GBPUSD position.
5. Entry is restricted to the 12:00 and 16:00 UTC H4 closes.
6. Generic fixed-pip trailing is replaced with H4 ATR trailing.
7. Legacy intraday and multi-book engines are bypassed whenever `STRATEGY=gbpusd_breakout_v2`.

## Deployment status

The proxy is materially better than the prior engine, but the recent 2025-partial-2026 weakness means it should first run in `READ_ONLY`, `APPROVAL`, or demo mode. Live deployment should require forward performance consistent with the proxy's spread, slippage, and signal frequency assumptions.
