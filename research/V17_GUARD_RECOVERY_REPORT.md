# V17 Guard Recovery and Selective Sizing Report

Status: **RESEARCH ONLY — DO NOT DEPLOY**

GitHub Actions run: `28624354018`  
Artifact: `v17-guard-recovery-results` (`sha256:99c55cc2e6a253a709dfd915b62787d8ea83a13117cfd181dd32c86588c34935`)

## Tested changes

1. Repair the unreachable mature-engine recovery probe after the 30-day cooldown.
2. Admit exactly one recovery probe at 50% of base risk and block further same-engine signals until it closes.
3. Leave `GBPUSD_V10_PRECISION` satellite/anchor sizing unchanged.
4. Test a capacity-aware 1.10x uplift only for engines whose validation and untouched holdout segments both have positive net R and profit factor above 1.0.
5. Preserve all existing portfolio, symbol, GBP, basket and total-open-risk limits.

## Data limitation

The reverted V17 source data covers `2012-11-26` through `2022-03-04`. These results validate the execution-policy change against the prior V17 model; they do not constitute a current-broker-data deployment test.

## Portfolio results on $5,000

| Window | Legacy V17 | Guard recovery | Guard + selective | Legacy trades | Recovery trades | Recovery max DD | Recovery stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 10y | $284.53 | **$680.26** | $692.34 | 245 | 813 | 5.47% | 5.85% |
| 5y | -$123.86 | **$266.56** | $256.59 | 133 | 514 | 4.70% | 5.08% |
| 4y | -$305.08 | **-$50.47** | -$61.64 | 113 | 348 | 6.06% | 6.24% |
| 3y | $534.51 | **$456.36** | $464.08 | 231 | 322 | 3.96% | 4.18% |
| 2y | $20.73 | **$3.30** | $3.30 | 126 | 181 | 5.65% | 5.87% |
| 1y | $144.77 | **$126.63** | $135.93 | 174 | 184 | 2.20% | 3.20% |
| 6m | -$1.65 | **-$6.65** | -$6.65 | 95 | 96 | 2.94% | 3.58% |

## Decision

- **Guard recovery passes the research rule.** It removes permanent suspension, increases accepted swing trades and materially improves the 10-year, 5-year and 4-year outcomes. Maximum stress drawdown remains 6.24%.
- **Selective sizing fails.** The 1.10x policy improves some periods but reduces net profit in the 5-year and 4-year windows versus guard recovery alone.
- Keep satellite sizing unchanged.
- Keep swing base sizing unchanged.
- Recommended candidate for current-data shadow testing: **guard recovery only**.
