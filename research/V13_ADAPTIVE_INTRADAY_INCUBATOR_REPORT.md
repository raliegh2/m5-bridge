# V13 Adaptive Intraday Incubator Backtest Report

Status: **research only — activation uses only past shadow-trade outcomes**

## Design

- Fixed strategy universe from researched intraday methods: trend breakout, EMA pullback, London/Asian range breakout, Bollinger squeeze, RSI/ATR mean reversion.
- A strategy starts disabled and paper-trades in shadow mode.
- It activates only after its most recent shadow trades show positive net R and PF.
- It disables after live rolling PF/net-R deterioration.
- No final test-period profit was used to choose winners in advance.

## Result

| Metric | Value |
|---|---:|
| Starting Balance | $5,000.00 |
| Ending Balance | $4,896.90 |
| Net Profit | $-103.10 |
| Return Percent | -2.06% |
| Trades | 52 |
| Wins | 16 |
| Losses | 36 |
| Win Rate | 30.77% |
| Profit Factor | 0.470 |
| Max Drawdown Percent | 2.52% |
| Avg Trade | $-1.98 |
| Max Win | $5.95 |
| Max Loss | $-6.57 |

## Decision

The adaptive incubator did not produce a positive result. Keep intraday disabled.
