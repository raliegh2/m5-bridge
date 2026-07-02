# V10 Multi-Symbol Live-Candidate Implementation and Backtest

## Implementation scope

The new controller coordinates **GBPUSD, EURUSD and GBPJPY** through one local
MT5 process. It adds:

- broker symbol resolution for suffixes such as `GBPUSD.a` or `EURUSDm`;
- completed-candle EURUSD/GBPJPY V7 feature generation;
- unique magic numbers;
- risk-based volume normalization;
- spread and broker stop-distance checks;
- `order_check` before `order_send`;
- one shared account-level open-risk controller;
- aligned and mixed GBP exposure caps;
- duplicate-signal suppression;
- atomic restart state;
- break-even, time-stop and force-flat handling;
- READ_ONLY, APPROVAL and AUTO execution modes.

The controller remains a **live candidate** and defaults to READ_ONLY.

## Synchronized backtest

| Metric | Result |
|---|---:|
| Period | 2025-07-07 to 2026-06-29 |
| Starting balance | $5,000.00 |
| Ending balance | **$6,217.99** |
| Net profit | **$1,217.99** |
| Return | **24.36%** |
| Trades | 138 |
| Win rate | 51.45% |
| Profit factor | **2.4302** |
| Realized drawdown | 2.107% |
| Open-risk stress drawdown | 2.793% |

## Comparison with the previous V10 allocation replay

| Metric | Previous V10 | Multi-symbol live candidate | Change |
|---|---:|---:|---:|
| Net profit | $1,214.66 | **$1,217.99** | $+3.33 |
| Profit factor | 2.4007 | **2.4302** | +0.0295 |
| Trades | 139 | 138 | -1 |
| Realized drawdown | 2.107% | 2.107% | +0.000 pp |

The multi-symbol candidate uses a portfolio-aware GBPUSD precision allocation:
A-grade swing entries use 0.40%, B-grade entries use 0.15%, accepted secondary
and pullback entries use 0.40%. This preserved account capacity for EURUSD and
GBPJPY while rejecting one overextended swing candidate.

## Engine contribution

| Engine | Trades | Net profit | Win rate | Profit factor |
|---|---:|---:|---:|---:|
| EURUSD Satellite V7 | 22 | $195.75 | 50.00% | 2.051 |
| GBPJPY Satellite V7 | 19 | $195.06 | 52.63% | 2.846 |
| GBPUSD Satellite V3 policy | 91 | $739.35 | 49.45% | 2.343 |
| GBPUSD precision swing | 6 | $87.83 | 83.33% | 10.439 |

## Cost stress

| Added cost per trade | Net profit | Return | Profit factor | Stress DD |
|---|---:|---:|---:|---:|
| 0.03R | $1,137.48 | 22.75% | 2.291 | 2.869% |
| 0.05R | $1,084.38 | 21.69% | 2.205 | 2.919% |
| 0.10R | $953.61 | 19.07% | 2.006 | 3.046% |

## Validation limits

This is a synchronized candidate-ledger replay. It is not a fresh tick-level
simulation. EURUSD and GBPJPY have approximately one year of synchronized
candidate history, while the GBPUSD precision gate was evaluated from completed
uploaded H4 bars. Real broker spread, slippage, partial fills and rejected orders
are represented only through cost stress.

The implementation should remain in READ_ONLY, then APPROVAL on a demo account,
before AUTO is considered.
