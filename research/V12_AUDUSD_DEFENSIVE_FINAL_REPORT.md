# V12 AUDUSD Defensive Sleeve — Final Research Report

Status: **POST-HOC RESEARCH ONLY — DO NOT DEPLOY**

GitHub Actions run: `28630149092`  
Artifact digest: `sha256:c6b2de82ddd6eee9d46dd197aaaf94ec5fda7f990c1c31bcdcf4c705b9bfd7de`

## Starting point

The prior expanded-capacity five-symbol model started with **$5,000** and produced **$3,028.98 net profit**, ending at **$8,028.98** over the available public common-history period from **26 November 2012 through 4 March 2022**.

Its broad AUDUSD engine was profitable over maximum history, five years, three years and two years, but lost **$71.74** in the one-year window and **$21.32** in the six-month window.

## Defensive AUDUSD rule

The defensive sleeve keeps only:

- completed H4 AUDUSD trend-pullback signals closing at **08:00 UTC**;
- **Monday or Thursday** signals;
- **0.20% risk per accepted AUDUSD trade**, reduced from 0.25%;
- the same V12 protected-engine portfolio controls and five-position/1.50% total-open-risk capacity.

The rule recorded 31 development trades with **+12.73R** and PF **2.273**, followed by 11 later validation trades with **+4.32R** and PF **3.162**. The sample is small and the rule was selected after historical analysis, so it is not deployment-grade evidence.

## Portfolio comparison

| Window | Broad AUDUSD 0.25% | Defensive AUDUSD 0.20% | Change | Defensive PF | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|
| Maximum history | $3,028.98 | **$2,685.85** | -$343.13 | 1.416 | 4.98% | 5.65% |
| 5 years | $1,191.68 | **$1,024.94** | -$166.74 | 1.338 | 4.98% | 5.65% |
| 3 years | $1,056.26 | **$882.20** | -$174.07 | 1.465 | 4.98% | 5.65% |
| 2 years | $500.62 | **$414.41** | -$86.21 | 1.337 | 4.98% | 5.65% |
| 1 year | $247.01 | **$336.30** | **+$89.30** | 1.503 | 3.56% | 4.37% |
| 6 months | $292.34 | **$317.95** | **+$25.61** | 2.027 | 1.10% | 2.17% |

## AUDUSD direct profitability after the change

| Window | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| Maximum history | 42 | **$219.16** | 2.544 |
| 5 years | 24 | **$145.46** | 5.505 |
| 3 years | 11 | **$46.82** | 3.153 |
| 2 years | 9 | **$33.58** | 4.331 |
| 1 year | 5 | **$13.17** | 2.326 |
| 6 months | 3 | **$3.31** | No losing trade in sample |

## Maximum-history five-symbol contribution

| Symbol | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| GBPUSD | 350 | **$1,591.47** | 1.814 |
| EURUSD | 153 | **$418.45** | 1.432 |
| GBPJPY | 275 | **$321.89** | 1.236 |
| AUDUSD | 42 | **$219.16** | 2.544 |
| USDJPY | 250 | **$134.89** | 1.067 |
| **Combined** | **1,070** | **$2,685.85** | **1.416** |

## Decision

- The defensive 0.20% AUDUSD sleeve is the best tested version for correcting the recent AUDUSD loss while retaining positive long-window contribution.
- It raises the one-year model result from **$247.01 to $336.30** and the six-month result from **$292.34 to $317.95**.
- It lowers maximum-history profit from **$3,028.98 to $2,685.85**, but also reduces maximum drawdown from **5.75% to 4.98%** and stress drawdown from **6.23% to 5.65%**.
- AUDUSD is positive in every tested window, but the recent samples contain only five one-year trades and three six-month trades. The apparent high profit factors are therefore not yet statistically reliable.
- Keep the branch draft, READ_ONLY, unmerged and undeployed. The rule requires a fresh broker-native forward/shadow test on data after March 2022 before it can replace the broad AUDUSD engine.
