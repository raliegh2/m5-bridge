# V12 Raised Risk and Correlation Limits

The selected V12 portfolio limits are:

```env
MAX_OPEN_RISK_PERCENT=1.00
V12_MAX_SYMBOL_RISK_PERCENT=0.75
ALIGNED_GBP_RISK_CAP_PERCENT=0.90
MIXED_GBP_RISK_CAP_PERCENT=0.65
MAX_OPEN_POSITIONS=3
```

The selected limits were compared with the previous true 1.00% global-cap baseline:

```env
MAX_OPEN_RISK_PERCENT=1.00
V12_MAX_SYMBOL_RISK_PERCENT=0.65
ALIGNED_GBP_RISK_CAP_PERCENT=0.75
MIXED_GBP_RISK_CAP_PERCENT=0.50
```

## Replay result on a $5,000 starting balance

| Window | Baseline profit | Raised-limit profit | Profit increase | Baseline max DD | Raised max DD | Baseline stress DD | Raised stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 10 years | $2,252.86 | $2,383.83 | $130.97 | 3.64% | 3.64% | 3.93% | 4.05% |
| 5 years | $1,704.26 | $1,769.66 | $65.40 | 2.35% | 2.35% | 2.84% | 2.84% |
| 3 years | $1,001.62 | $1,023.99 | $22.37 | 1.74% | 1.74% | 2.38% | 2.38% |
| 2 years | $882.59 | $904.51 | $21.93 | 1.74% | 1.74% | 2.28% | 2.28% |

The raised limits accepted eight additional trades over ten years and improved the ten-year profit factor from 1.498 to 1.523. Realized maximum drawdown was unchanged, while stress drawdown increased by approximately 0.12 percentage points.

A broader mixed-GBP cap of 0.90% accepted one additional losing historical trade and reduced profit, so the selected mixed cap remains 0.65% rather than matching the full 1.00% global limit.

The branch remains a READ_ONLY/demo validation candidate.
