# V14.8 Strict Five-Symbol Swing + ICT Validation

## Outcome

V14.8 reached the revised acceptance target in the GitHub Actions replay.

- Historical window: **2012-03-05 through 2022-03-05**
- Starting balance: **$5,000.00**
- Net profit after modeled retail costs and fees: **$40,361.94**
- Ending balance: **$45,361.94**
- Return: **807.24%**
- Profit factor: **2.0408**
- Maximum closed drawdown: **8.2026%**
- Projected stressed drawdown: **9.9500%**
- Closed trades: **913**
- Skipped ICT candidates: **12**
- Margin above the $20,000 target: **$20,361.94**

The workflow compiled the implementation, passed the focused tests, ran the strict all-ten retail-cost replay, validated the target and evidence, uploaded the artifact and passed the enforcement gate.

## Ten independently validated sleeves

| Symbol | Swing sleeve | Swing risk | ICT sleeve | ICT risk |
|---|---|---:|---|---:|
| GBPUSD | Precision setup at 12:00 UTC | 1.25% | `gu_london_25` wide sweep, London | 0.60% |
| EURUSD | H4 pullback at 16:00 UTC | 0.50% | `eu_london_20` liquidity setup, London | 0.60% |
| GBPJPY | Core swing excluding Tuesday | 0.95% | `gj_ny_20` wide sweep, New York | 0.60% |
| AUDUSD | Trend pullback at 08:00 UTC | 0.50% | `au_london_relaxed`, SELL, excluding Wednesday | 0.40% |
| USDJPY | H4 24-bar breakout at 08:00 UTC | 0.55% | `ICT_BREAKOUT_H4` at 08:00 UTC | 0.30% |

Profile identity is part of the setup key. This prevents weak variants from being combined with an approved profile under one engine name.

## Chronological validation protocol

Each sleeve remained positive after its modeled cost allowance in all five chronological blocks:

- Training: 35%
- Validation: 20%
- Audit A: 15%
- Audit B: 15%
- Final validation: 15%

No sleeve was admitted merely because its aggregate ten-year result was positive.

## Profit attribution

| Symbol | Swing net | ICT net | Combined net |
|---|---:|---:|---:|
| GBPUSD | $8,401.72 | $6,970.62 | **$15,372.34** |
| EURUSD | $2,158.67 | $4,329.80 | **$6,488.47** |
| GBPJPY | $6,355.12 | $5,499.49 | **$11,854.61** |
| AUDUSD | $2,234.65 | $2,317.79 | **$4,552.44** |
| USDJPY | $1,780.36 | $313.71 | **$2,094.08** |

All ten sleeves contributed positive realized profit in the synchronized portfolio replay.

## Portfolio controls retained

- Maximum swing risk: 1.25% per trade
- Maximum ICT risk: 0.60% per trade
- Maximum open ICT risk: 1.75%
- Maximum combined open risk: 3.25%
- Closed-drawdown governor: 7.5% / 8.5% / 9.0% / 9.6%
- Projected stressed-equity admission limit: 9.95%
- Minimum executable risk after stress scaling: 0.025%

The projected-stress governor evaluates existing open risk and the proposed trade before entry. It reduces or rejects new risk when projected stressed equity would exceed the configured boundary.

## Important limitations

This is a historical research result, not a profit guarantee. The committed historical source ends in March 2022, so this is not yet a 2016-2026 validation. Spread, commission, slippage and carry were represented through fixed R-cost allowances rather than broker-tick reconstruction. V14.8 is not connected to the live runner, has not been merged and must remain research/READ_ONLY until fresh 2022-2026 data and demo forward testing are completed.
