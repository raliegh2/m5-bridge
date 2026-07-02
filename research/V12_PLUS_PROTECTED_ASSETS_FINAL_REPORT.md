# V12 Plus Validated AUDUSD and USDJPY — Final Research Report

Status: **RESEARCH ONLY — DO NOT DEPLOY**

GitHub Actions run: `28627962492`  
Artifact digest: `sha256:6127e9110a7a29235400c6486c25ad26dddab3091b174d8cdd615048c360511b`

## Test scope

- Starting balance: **$5,000**.
- Public common-history coverage: **26 November 2012 through 4 March 2022** (approximately 9.27 years).
- Regenerated V12 families: GBPUSD V10 precision, GBPUSD V5 pullback, and V12 GBPUSD/EURUSD/GBPJPY core and retest engines.
- Added engines: independently validated AUDUSD D1/H4 pullback and USDJPY D1/H4 40-bar breakout.
- Original V12 protected-engine policy restored; weaker unprotected engines retain the repaired cooldown/recovery-probe guard.

## Independent validation

| Engine | Validation trades | Net R | Profit factor | Result |
|---|---:|---:|---:|---|
| AUDUSD trend pullback | 76 | 15.38R | 1.513 | PASS |
| USDJPY safe-haven breakout | 217 | 21.99R | 1.205 | PASS |

## Portfolio results

| Window | Regenerated V12 | V12 + both, original caps | V12 + both, expanded capacity | Capacity delta vs V12 | Capacity PF | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Maximum history | $2,170.25 | $2,745.35 | **$3,028.98** | +$858.73 | 1.386 | 5.75% | 6.23% |
| 5 years | $948.09 | $969.71 | **$1,191.68** | +$243.59 | 1.329 | 4.99% | 5.85% |
| 3 years | $638.78 | $912.29 | **$1,056.26** | +$417.48 | 1.460 | 5.09% | 5.94% |
| 2 years | $317.26 | $457.23 | **$500.62** | +$183.36 | 1.339 | 5.18% | 6.03% |
| 1 year | $321.71 | $268.92 | **$247.01** | -$74.70 | 1.317 | 4.90% | 5.75% |
| 6 months | $191.69 | $158.08 | **$292.34** | +$100.65 | 1.875 | 1.33% | 2.42% |

## Maximum-history profit by symbol — expanded-capacity scenario

| Symbol | Trades | Gross income | Gross loss | Net profit | Profit factor |
|---|---:|---:|---:|---:|---:|
| GBPUSD | 350 | $3,631.87 | $1,993.45 | **$1,638.41** | 1.822 |
| EURUSD | 153 | $1,423.62 | $992.97 | **$430.64** | 1.434 |
| GBPJPY | 274 | $1,723.18 | $1,388.90 | **$334.28** | 1.241 |
| AUDUSD | 198 | $1,878.99 | $1,389.59 | **$489.40** | 1.352 |
| USDJPY | 250 | $2,208.93 | $2,072.68 | **$136.25** | 1.066 |

## Maximum-history profit by engine

| Engine | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| GBPUSD_V10_PRECISION | 112 | $1,638.18 | 2.990 |
| AUDUSD_TREND_PULLBACK | 198 | $489.40 | 1.352 |
| EURUSD_SWING_CORE | 122 | $411.73 | 1.456 |
| GBPJPY_SWING_CORE | 180 | $388.08 | 1.562 |
| USDJPY_SAFE_HAVEN_BREAKOUT | 250 | $136.25 | 1.066 |
| GBPUSD_SWING_RETEST | 149 | $52.39 | 1.070 |
| EURUSD_SWING_RETEST | 31 | $18.92 | 1.211 |
| GBPUSD_SWING_CORE | 89 | -$52.16 | 0.877 |
| GBPJPY_SWING_RETEST | 94 | -$53.80 | 0.923 |

## Decision

- The expanded-capacity five-symbol portfolio produced **$3,028.98** net profit, ending at **$8,028.98**, with a **60.58%** return over the available 9.27-year history.
- It improved the regenerated protected V12 baseline by **$858.73** and raised maximum-history trades from **694 to 1,225**.
- AUDUSD contributed **$489.40** directly with PF **1.352**. USDJPY contributed **$136.25** directly with PF **1.066**.
- The expanded-capacity version increased maximum drawdown from **4.50% to 5.75%** and stress drawdown from **4.97% to 6.23%**.
- Recent behavior is mixed: the six-month result improved to **$292.34**, but the one-year result fell from **$321.71** for regenerated V12 to **$247.01**. AUDUSD was negative in that one-year segment.
- The historical archive documented **$2,383.83** for the exact original V12 ledger. This regeneration produced **$2,170.25** because GitHub Actions rebuilt candidates from public OHLC instead of replaying the exact archived CSV ledgers.
- Keep the branch draft and READ_ONLY. Current broker-native data through 2026 and spread/commission/slippage reconciliation are required before deployment.
