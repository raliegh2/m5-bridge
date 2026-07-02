# Final Multi-Pair Backtest Data — V10 Profitability Candidate

## Scope clarification

The previous profitability comparison mixed two different scopes:

- **$983.63 / 19.67%** was the approximately one-year synchronized V9 multi-engine portfolio.
- **$646.26 / 12.93%** was the exact ten-year GBPUSD swing-only backtest.

The ten-year number looked lower because EURUSD, GBPJPY and the GBPUSD satellite were excluded. It was not evidence that the complete V9 portfolio had become less profitable.

The uploaded European pair is **EURUSD**, not EURGBP. No EURGBP dataset or strategy was supplied.

## Final pair sections

| Pair / engine | Test scope | Trades | Net profit | Profit factor | Win rate |
|---|---|---:|---:|---:|---:|
| GBPUSD Swing V5 | Exact ten-year H4 rerun | 125 | $646.26 | 2.1925 | 67.20% |
| EURUSD Satellite V7 | Synchronized V9 replay | 22 | $137.18 | 2.0585 | 50.00% |
| GBPJPY Satellite V7 | Synchronized V9 replay | 19 | $137.59 | 2.8684 | 52.63% |
| GBPUSD Satellite V3 | Synchronized V9 replay | 91 | $604.89 | 2.3471 | 49.45% |

## V10 improvement

V10 keeps the V9 hour-quality gate and shared portfolio risk controls, but reallocates risk toward the stronger satellite engines while reducing swing reservation enough to prevent it from unnecessarily blocking profitable intraday candidates:

| Engine | V9 risk | V10 risk |
|---|---:|---:|
| EURUSD Satellite V7 | 0.25% | **0.35%** |
| GBPJPY Satellite V7 | 0.25% | **0.35%** |
| GBPUSD Satellite V3 | 0.25% | **0.30%** |
| GBPUSD Swing V6 | 0.50% | **0.40%** |

| Portfolio metric | V9 | V10 | Change |
|---|---:|---:|---:|
| Ending balance | $5,983.63 | **$6,214.66** | +$231.03 profit |
| Net profit | $983.63 | **$1,214.66** | **+23.49%** |
| Return | 19.67% | **24.29%** | +4.62 pp |
| Trades | 139 | 139 | unchanged |
| Profit factor | 2.4417 | 2.4007 | -0.0410 |
| Realized drawdown | 1.575% | 2.107% | +0.532 pp |
| Stress drawdown | 2.067% | 2.793% | +0.725 pp |

V10 raises historical synchronized profit from **$983.63 to $1,214.66** while keeping stress drawdown below 3%.

## Engine contribution under V10

| Engine | Trades | Net profit | Profit factor | Win rate |
|---|---:|---:|---:|---:|
| EURUSD_SATELLITE_V7 | 22 | $195.83 | 2.0515 | 50.00% |
| GBPJPY_SATELLITE_V7 | 19 | $195.19 | 2.8476 | 52.63% |
| GBPUSD_SATELLITE_V2 | 91 | $739.62 | 2.3437 | 49.45% |
| GBPUSD_SWING_V6 | 7 | $84.02 | 4.3835 | 85.71% |

## Development and validation split

| Segment | V9 net | V10 net | V9 PF | V10 PF |
|---|---:|---:|---:|---:|
| Before 2026 | $649.15 | **$780.40** | 3.427 | 3.319 |
| 2026 validation segment | $296.04 | **$375.63** | 1.806 | 1.818 |

The profit increase appears in both segments rather than coming from only one isolated period.

## Transaction-cost stress

| Additional cost per trade | Net profit | Return | Profit factor | Stress DD |
|---|---:|---:|---:|---:|
| 0.03R | $1,132.53 | 22.65% | 2.263 | 2.869% |
| 0.05R | $1,078.38 | 21.57% | 2.177 | 2.919% |
| 0.10R | $945.07 | 18.90% | 1.980 | 3.046% |

The candidate remains profitable under the severe 0.10R-per-trade stress.

## GBPUSD swing ten-year risk replay

Applying the V10 0.40% swing allocation to the recorded exact ten-year V5 trade outcomes gives:

| Metric | Exact V5 | V10 risk replay |
|---|---:|---:|
| Net profit | $646.26 | **$803.57** |
| Return | 12.93% | **16.07%** |
| Profit factor | 2.1925 | 2.1036 |
| Maximum drawdown | 1.7403% | 1.7472% |

This is a **risk-reweighted ledger replay**, not a new raw-price signal simulation.

## Decision

V10 is a stronger profitability candidate than V9 in the synchronized test, but it remains a research configuration. It should stay in READ_ONLY or demo mode until the missing full-history M15 data is available and forward fills are reconciled.
