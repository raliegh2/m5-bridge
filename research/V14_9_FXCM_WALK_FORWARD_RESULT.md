# V14.9 FXCM Walk-Forward Result

## Outcome

V14.9 replaces the static V14.8 allocation with a 365-day, after-cost sleeve governor. The workflow completed successfully on the independent FXCM H1 bid/ask archive.

- Chart window: 2016-01-01 through 2026-05-01
- Calibration/shadow period: 2016-2018
- Capital deployment: 2019-01-01 through 2026-05-01
- Untouched chronological test: 2022-03-06 through 2026-05-01
- Starting balance: $5,000

## Portfolio

| Metric | V14.9 |
|---|---:|
| Net profit after modeled costs | **$2,027.92** |
| Ending balance | **$7,027.92** |
| Return | **40.56%** |
| Profit factor | **1.2771** |
| Maximum closed drawdown | **9.4506%** |
| Projected stressed drawdown | **9.4500%** |
| Closed trades | **312** |
| Modeled cost reserve | **$940.06** |
| Gap to $20,000 target | **$17,972.08** |

## Untouched 2022-2026 test

- Trades: 145
- Net profit: **$676.86**
- Profit factor: **1.1730**
- Win rate: **46.21%**

## Same-window V14.8 comparison

| Metric | Frozen V14.8 | V14.9 |
|---|---:|---:|
| Net profit | $179.20 | **$2,027.92** |
| Profit factor | 1.0304 | **1.2771** |
| Maximum closed drawdown | 9.4576% | **9.4506%** |
| Closed trades | 376 | 312 |

V14.9 improved net profit by **$1,848.72**, approximately **1,031.6%**, while remaining below the retained 9.60% closed-drawdown boundary.

## Five-symbol contribution

| Symbol | Net profit |
|---|---:|
| AUDUSD | $418.89 |
| EURUSD | $192.66 |
| GBPJPY | $528.19 |
| GBPUSD | $342.34 |
| USDJPY | $545.85 |

All five symbols executed both swing and ICT sections. Every symbol was profitable in the synchronized portfolio.

## Boundary

The $20,000 target was not reached. This is a bar-based research replay with modeled cost reserves, not tick-level broker execution. The branch remains draft and must not be merged into AUTO execution without broker-specific demo forward validation.
