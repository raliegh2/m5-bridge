# GBPUSD V4 Validation Report

## Scope

This report records the validation work completed for the frozen GBPUSD V4 strategy on the supplied H4 history from January 2016 through July 1, 2026.

Frozen configuration SHA-256:

`dec29542446673e043b16b20556f2a0bcaa65f096b81e5ecd71e61bbdb301e6b`

Any parameter change creates a new strategy version and invalidates direct comparison with these results.

## Frozen parameters

- Starting research balance: $5,000
- Risk per trade: 0.35% of current equity
- One GBPUSD position maximum
- Initial stop: 1.5 ATR, clipped to 20–150 pips
- Partial exit: 50% at 1R
- Break-even stop after partial
- Final target: 3R
- Trailing distance: 2.5 ATR after 1R
- Maximum hold: 72 H4 bars
- Daily loss limit: $250
- Total loss limit: $500
- Soft drawdown threshold: 6%
- Base spread floor: 0.8 pip
- Slippage: 0.3 pip per execution side
- Swap proxy: -0.2 pip per holding day

## Full-sample reference

| Metric | Result |
|---|---:|
| Ending balance | $5,537.57 |
| Net profit | $537.57 |
| Return | 10.75% |
| Trades | 99 |
| Profit factor | 2.14 |
| Win rate | 68.69% |
| Maximum mark-to-market drawdown | 1.74% |
| Daily Sharpe proxy | 0.79 |

This is a reference result, not untouched out-of-sample evidence, because the V4 rules were selected with knowledge of the broader historical data.

## Purged rolling walk-forward

Method:

- Four-year development window
- One-year test window
- Frozen parameters; no optimization inside any window
- Fifteen business days purged before and after each boundary
- Every test starts from a fresh $5,000 balance

| Window | Test period | Trades | Net | Return | PF | Max DD | Win rate |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | 2020-01-24 to 2020-12-14 | 11 | $39.49 | 0.79% | 1.87 | 0.79% | 72.73% |
| 2 | 2021-01-25 to 2021-12-14 | 11 | $130.11 | 2.60% | 5.54 | 0.67% | 81.82% |
| 3 | 2022-01-25 to 2022-12-14 | 12 | $73.18 | 1.46% | 2.78 | 0.70% | 75.00% |
| 4 | 2023-01-25 to 2023-12-14 | 6 | -$17.65 | -0.35% | 0.57 | 0.88% | 50.00% |
| 5 | 2024-01-25 to 2024-12-16 | 5 | $40.08 | 0.80% | 2.32 | 0.84% | 60.00% |
| 6 | 2025-01-24 to 2025-12-15 | 8 | $28.18 | 0.56% | 1.71 | 0.85% | 62.50% |

Aggregate out-of-sample observations:

- 53 test trades
- +$293.38 aggregate net profit across fresh-balance test windows
- Aggregate profit factor: 2.30
- Aggregate win rate: 69.81%
- Five of six annual test windows were profitable
- 2023 failed: -$17.65, PF 0.57

The 2023 failure shows that V4 is not regime-proof and can still experience losing periods.

## Historical event coverage

A genuine official Federal Reserve policy-statement calendar covering 2016–2026 was tested. The frozen ±10-minute event window produced the same result as the baseline because the FOMC statement times did not overlap V4's 12:00/16:00 UTC entry timestamps inside that window.

This is an official FOMC subset, not a complete GBP/USD macro calendar. Complete official coverage still needs BoE decisions, U.S. CPI, U.S. Employment Situation/NFP, and selected UK inflation/employment releases. No dates were estimated or fabricated.

A wider blackout must be treated as a separately frozen V4.1 candidate and re-run through the full validation process.

## Monte Carlo

### Bootstrap and execution-cost stress

20,000 simulations were run with historical trades sampled with replacement, spread floors varied from 0.8 to 2.0 pips, slippage varied from 0.3 to 1.0 pip per side, and a 2% per-trade probability of an extra adverse gap cost with a five-pip mean.

| Metric | P05 | Median | P95 |
|---|---:|---:|---:|
| Ending balance | $5,233.00 | $5,498.40 | $5,778.94 |
| Return | 4.66% | 9.97% | 15.58% |
| Maximum drawdown | 0.82% | 1.33% | 2.39% |
| Profit factor | 1.40 | 2.04 | 3.04 |

Conditional outcomes under this model:

- Probability of a losing terminal result: 0.055%
- Probability of PF below 1.0: 0.055%
- Probability of drawdown above 3%: 1.215%
- Probability of drawdown above 4%: 0.125%
- Probability of drawdown above 5%: 0.005%
- No simulation exceeded 6% drawdown

### Trade-order permutation

10,000 random orderings were tested:

- Median maximum drawdown: 1.26%
- 95th-percentile maximum drawdown: 2.04%
- Approximately 0.12% exceeded 3% drawdown
- None exceeded 4%

These probabilities are conditional on the historical distribution and the chosen stress model. They are not forecasts or guarantees.

## Forward-test protocol

A genuine forward test cannot be completed using past data. It begins only after parameters are frozen and new signals occur without code changes.

At approximately 9.4 historical trades per year:

- 20 completed positions may take about 2.1 years
- 30 completed positions may take about 3.2 years

Twenty-trade review:

- Frozen parameter hash unchanged
- Profit factor at least 1.25
- Expectancy above 0R
- Maximum drawdown no greater than 4%
- No duplicate or missing signals
- Execution costs within stress assumptions

Thirty-trade promotion review:

- Profit factor at least 1.50
- Expectancy at least 0.20R
- Maximum drawdown no greater than 5%
- No material divergence in signal frequency
- No unresolved data or execution defects

## Decision

V4 passed five of six purged annual test windows and produced an aggregate out-of-sample PF of approximately 2.30. Monte Carlo stress results were favorable under the modeled assumptions.

It is suitable for a controlled demo or approval-mode forward test. It is not yet justified for unattended live deployment because the full macro-event calendar and 20–30 genuinely forward positions remain incomplete.
