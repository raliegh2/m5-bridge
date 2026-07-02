# V17 Guard-Recovery Swing + Satellite 10-Year Profitability

Status: **RESEARCH ONLY — DO NOT DEPLOY**

GitHub Actions run: `28625317976`  
Artifact digest: `sha256:414634d25f501ec7f7d817af632ff2011321bcd6853379fa6f1878ee6cb2e04d`

## Scope

- Uses the V17 guard-recovery swing engine with unchanged base sizing.
- Includes the only admitted satellite/precision engine in the reverted V17 model: `GBPUSD_V10_PRECISION`.
- Excludes the unvalidated V18 M15/H1/D1 satellite family.
- Preserves all portfolio, position, symbol, GBP and basket risk limits.
- Requested window: 10 years.
- Actual common data coverage: `2012-11-26T20:00:00Z` through `2022-03-04T20:00:00Z` (9.27 years).
- Starting balance: **$5,000**.

## Portfolio profitability

| Scenario | Trades | Gross income | Gross loss | Net profit | Ending balance | Return | Avg monthly | Profit factor | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Guard-recovery swing only | 808 | $4,288.77 | $3,823.85 | $464.92 | $5,464.92 | 9.30% | $4.18 | 1.122 | 5.47% | 5.81% |
| Swing + admitted satellite | 813 | $4,548.20 | $3,867.94 | **$680.26** | **$5,680.26** | **13.61%** | **$6.12** | **1.176** | 5.47% | 5.85% |

Adding the admitted satellite changed total portfolio net profit by **+$215.34** and added five accepted portfolio trades. This is a path-dependent portfolio effect, not the satellite's direct trading income.

## Direct section contribution in the combined portfolio

| Section | Trades | Gross income | Gross loss | Net profit |
|---|---:|---:|---:|---:|
| Swing engines | 810 | $4,548.13 | $3,856.42 | **$691.70** |
| Admitted satellite | 3 | $0.07 | $11.52 | **-$11.44** |
| Combined | 813 | $4,548.20 | $3,867.94 | **$680.26** |

The satellite itself lost **$11.44**. Its presence changed trade admission, compounding and later swing-engine guard history, which produced a **+$215.34 total portfolio-path difference** versus the separate swing-only replay.

## Profit by symbol

| Symbol | Trades | Swing trades | Satellite trades | Gross income | Gross loss | Swing net | Satellite net | Combined net | Profit factor |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GBPUSD | 182 | 179 | 3 | $1,204.12 | $911.66 | $303.90 | -$11.44 | **$292.46** | 1.321 |
| EURUSD | 138 | 138 | 0 | $740.73 | $682.64 | $58.09 | $0.00 | **$58.09** | 1.085 |
| GBPJPY | 330 | 330 | 0 | $1,684.79 | $1,420.79 | $264.00 | $0.00 | **$264.00** | 1.186 |
| AUDUSD | 163 | 163 | 0 | $918.57 | $852.85 | $65.72 | $0.00 | **$65.72** | 1.077 |
| USDJPY | 0 | 0 | 0 | $0.00 | $0.00 | $0.00 | $0.00 | **$0.00** | 0.000 |
| **Total** | **813** | **810** | **3** | **$4,548.20** | **$3,867.94** | **$691.70** | **-$11.44** | **$680.26** | **1.176** |

## Decision

- Combined net profitability is positive, but modest: **$680.26**, or **13.61%** over 9.27 years.
- Average monthly profit is only **$6.12** on $5,000.
- GBPUSD and GBPJPY generate most of the profit.
- USDJPY has no qualified V17 trades and contributes zero.
- The admitted satellite does not have positive standalone expectancy in this sample and should not be promoted as an independent profit source.
- Keep this configuration research-only until tested with current broker-native data.
