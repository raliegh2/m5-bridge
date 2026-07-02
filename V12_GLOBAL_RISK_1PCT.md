# V12 Global Open-Risk Cap: 1.00%

The V12 account-level maximum simultaneous open-risk cap is now configured as:

```env
MAX_OPEN_RISK_PERCENT=1.00
```

The per-symbol and correlation controls remain unchanged:

- Maximum positions: 3
- Maximum risk in one symbol: 0.65%
- Aligned GBP exposure: 0.75%
- Mixed-direction GBP exposure: 0.50%

## Replay result on a $5,000 starting balance

| Window | Profit at 0.75% | Profit at 1.00% | Increase | Max DD |
|---|---:|---:|---:|---:|
| 10 years | $2,152.06 | $2,152.06 | $0.00 | 3.64% |
| 5 years | $1,648.01 | $1,648.01 | $0.00 | 2.35% |
| 3 years | $989.71 | $989.71 | $0.00 | 1.74% |
| 2 years | $891.45 | $891.45 | $0.00 | 1.74% |

Raising only the global cap accepted no additional historical trades. The remaining constraints continued to block the same overlapping candidates, so historical profit and drawdown were unchanged. The branch remains for READ_ONLY/demo validation.
