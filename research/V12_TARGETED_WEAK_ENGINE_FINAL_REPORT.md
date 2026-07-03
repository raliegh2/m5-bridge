# V12 Targeted Weak-Engine Optimization — Final Report

Status: **RESEARCH ONLY — DO NOT DEPLOY**

GitHub Actions run: `28632032631`  
Artifact digest: `sha256:7df26adcb2325385c07297d6db34d27938b9f05b57cded5f8a9ab8d890f0b5d1`

## Changes

- Preserved the $3,028.98 five-symbol V12 model as the baseline.
- Kept the broad profitable AUDUSD engine unchanged.
- Removed `GBPUSD_SWING_CORE` and `GBPJPY_SWING_RETEST` in the exploratory optimized scenario because both were full-sample losing sub-engines.
- Applied adaptive risk only to `USDJPY_SAFE_HAVEN_BREAKOUT` and `EURUSD_SWING_RETEST`.
- Preserved the strong GBPUSD precision, GBPUSD retest, EURUSD core, GBPJPY core, and AUDUSD engines.

## Portfolio results on $5,000

| Window | Baseline | Optimized | Difference | Optimized PF | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|
| Maximum history | $3,028.98 | **$3,201.58** | **+$172.60** | 1.606 | 4.93% | 5.25% |
| 5 years | $1,191.68 | **$1,517.54** | **+$325.86** | 1.674 | 3.43% | 4.06% |
| 3 years | $1,056.26 | $968.95 | -$87.32 | 1.624 | 3.96% | 4.73% |
| 2 years | $500.62 | **$649.09** | **+$148.48** | 1.663 | 3.43% | 4.06% |
| 1 year | $247.01 | **$289.65** | **+$42.64** | 1.485 | 3.79% | 4.50% |
| 6 months | $292.34 | **$331.78** | **+$39.44** | 2.257 | 1.02% | 1.70% |

## Maximum-history contribution by symbol

| Symbol | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| GBPUSD | 269 | **$1,670.12** | 2.020 |
| EURUSD | 157 | **$442.19** | 1.438 |
| GBPJPY | 180 | **$389.22** | 1.565 |
| AUDUSD | 198 | **$487.59** | 1.351 |
| USDJPY | 114 | **$212.45** | 1.383 |
| **Combined** | **918** | **$3,201.58** | **1.606** |

## Outcome

- Starting balance: **$5,000**.
- Net profit: **$3,201.58**.
- Ending balance: **$8,201.58**.
- Return: **64.03%**.
- Maximum drawdown fell from **5.75% to 4.93%**.
- Stress drawdown fell from **6.23% to 5.25%**.
- USDJPY improved from approximately **$136.25 / PF 1.066** to **$212.45 / PF 1.383**.
- AUDUSD remained broad and profitable at **$487.59**.

## Limitation

The three-year result fell by $87.32, and full-sample loser removal is post-hoc. The model must remain draft, READ_ONLY, unmerged, and undeployed until broker-native post-2022 forward validation confirms the improvements.
