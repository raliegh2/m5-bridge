# Multi-Pair Swing V1 backtest

## Test scope

The disabled-by-default EURUSD and GBPJPY swing models were tested using the
uploaded H1 histories, resampled to H4, D1 and W1 with completed-candle joins.

Assumptions:

- starting balance: $5,000 per independent model
- EURUSD risk: 0.25% per trade
- GBPJPY risk: 0.20% per trade
- ATR stops and risk-based lot sizing
- partial exits, break-even and ATR trailing stops
- 72 H4-bar maximum hold
- spread floors, slippage and swap proxy
- no event-calendar filter because no complete event file was supplied

That missing event coverage makes these results less conservative than the final
production design.

## EURUSD

### Full available history

| Metric | Result |
|---|---:|
| Net profit | **-$216.04** |
| Return | -4.32% |
| Trades | 100 |
| Profit factor | **0.67** |
| Win rate | 40.00% |
| Maximum drawdown | 5.30% |
| Average trade | -$2.16 |

Direction breakdown:

- Long: 48 trades, -$38.99, PF 0.86
- Short: 52 trades, -$177.04, PF 0.52

The short model is the largest failure, but disabling shorts alone would still
leave a sub-1.0 long PF.

### 2024 through July 2026 forward-style period

| Period | Net | Trades | PF | Max DD |
|---|---:|---:|---:|---:|
| 2024 | -$60.41 | 14 | 0.40 | 1.25% |
| 2025 | -$15.79 | 3 | 0.25 | 0.33% |
| 2026 H1 | +$9.83 | 6 | 1.29 | 0.68% |
| Combined | **-$66.38** | 23 | **0.57** | 1.85% |

Decision: **EURUSD remains disabled.**

## GBPJPY

### Full available history

| Metric | Result |
|---|---:|
| Net profit | **+$7.44** |
| Return | +0.15% |
| Trades | 14 |
| Profit factor | **1.13** |
| Win rate | 42.86% |
| Maximum drawdown | 0.59% |
| Average trade | +$0.53 |

All 14 completed trades were long. The short regime produced no entries.

### 2024 through July 2026 forward-style period

| Period | Net | Trades | PF | Max DD |
|---|---:|---:|---:|---:|
| 2024 | +$4.69 | 2 | 1.59 | 0.27% |
| 2025 | $0.00 | 0 | n/a | 0.00% |
| 2026 H1 | $0.00 | 0 | n/a | 0.00% |
| Combined | **+$4.69** | 2 | **1.59** | 0.27% |

Decision: **GBPJPY remains disabled.** The apparent PF is based on only two
recent trades and is not statistically useful. The model is too restrictive and
produces insufficient evidence of an edge.

## GBPUSD V4 comparison for 2024 through July 2026

| Metric | Result |
|---|---:|
| Net profit | **+$91.42** |
| Return | +1.83% |
| Trades | 18 |
| Profit factor | **1.91** |
| Maximum drawdown | 0.90% |

Approximate independent arithmetic combination over that period:

- GBPUSD V4: +$91.42
- EURUSD V1: -$66.38
- GBPJPY V1: +$4.69
- Total: **+$29.74**

This arithmetic total does not simulate portfolio correlation and simultaneous
risk blocking. It demonstrates that adding the current EURUSD and GBPJPY models
would materially reduce the validated GBPUSD result.

## Final conclusion

The new explanatory features did not create a production-ready multi-pair edge
with the initial parameter set.

- GBPUSD V4 remains the strongest model.
- EURUSD must remain disabled.
- GBPJPY must remain disabled due to extremely low frequency and insufficient
  sample size.
- PR #6 should remain draft and unmerged.

The next iteration should not simply loosen thresholds. It should use purged
walk-forward selection over constrained pair-specific parameter families and
retain a null-model option that leaves a pair disabled when no robust model
exists.
