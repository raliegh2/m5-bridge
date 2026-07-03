# V12 Plus Validated AUDUSD and USDJPY — Final Research Report

Status: **RESEARCH ONLY — DO NOT DEPLOY**

GitHub Actions run: `28630149092`  
Artifact digest: `sha256:c6b2de82ddd6eee9d46dd197aaaf94ec5fda7f990c1c31bcdcf4c705b9bfd7de`

## Test scope

- Starting balance: **$5,000**.
- Public common-history coverage: **26 November 2012 through 4 March 2022** (approximately 9.27 years).
- Regenerated V12 families: GBPUSD V10 precision, GBPUSD V5 pullback, and V12 GBPUSD/EURUSD/GBPJPY core and retest engines.
- Added engines: independently validated AUDUSD D1/H4 pullback and USDJPY D1/H4 40-bar breakout.
- Original V12 protected-engine policy restored; weaker unprotected engines retain the repaired cooldown/recovery-probe guard.
- Final AUDUSD research variant: 08:00 UTC Monday/Thursday defensive sleeve at 0.20% risk.

## Original five-symbol result

The broad expanded-capacity model produced **$3,028.98 net profit**, ending at **$8,028.98**, with a **60.58% return**. AUDUSD contributed **$489.40** over maximum history but lost **$71.74** in the one-year window and **$21.32** in the six-month window.

## Final defensive-AUDUSD result

| Window | Broad model | Defensive AUDUSD model | Change | Defensive PF | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|
| Maximum history | $3,028.98 | **$2,685.85** | -$343.13 | 1.416 | 4.98% | 5.65% |
| 5 years | $1,191.68 | **$1,024.94** | -$166.74 | 1.338 | 4.98% | 5.65% |
| 3 years | $1,056.26 | **$882.20** | -$174.07 | 1.465 | 4.98% | 5.65% |
| 2 years | $500.62 | **$414.41** | -$86.21 | 1.337 | 4.98% | 5.65% |
| 1 year | $247.01 | **$336.30** | **+$89.30** | 1.503 | 3.56% | 4.37% |
| 6 months | $292.34 | **$317.95** | **+$25.61** | 2.027 | 1.10% | 2.17% |

## AUDUSD direct profitability after the defensive change

| Window | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| Maximum history | 42 | **$219.16** | 2.544 |
| 5 years | 24 | **$145.46** | 5.505 |
| 3 years | 11 | **$46.82** | 3.153 |
| 2 years | 9 | **$33.58** | 4.331 |
| 1 year | 5 | **$13.17** | 2.326 |
| 6 months | 3 | **$3.31** | No losing trade in sample |

## Final five-symbol contribution

| Symbol | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| GBPUSD | 350 | **$1,591.47** | 1.814 |
| EURUSD | 153 | **$418.45** | 1.432 |
| GBPJPY | 275 | **$321.89** | 1.236 |
| AUDUSD | 42 | **$219.16** | 2.544 |
| USDJPY | 250 | **$134.89** | 1.067 |
| **Combined** | **1,070** | **$2,685.85** | **1.416** |

## Decision

- Use the 0.20% defensive AUDUSD sleeve as the preferred research candidate because AUDUSD is positive in every tested window and recent portfolio profit improves.
- The change sacrifices $343.13 of maximum-history profit in exchange for better recent stability and lower drawdown.
- The defensive rule was selected after historical analysis and recent samples are small. It is not statistically strong enough for AUTO deployment.
- Keep the branch draft, READ_ONLY, unmerged and undeployed pending a fresh broker-native forward/shadow test on post-March-2022 data.
