# V13 Researched Intraday Strategy Improvement Report

Status: **RESEARCH BACKTEST — no future-looking selection used**

## Online-researched strategy families implemented

- Trend/channel breakout with higher-timeframe EMA and ADX trend-strength filter.
- EMA pullback/reclaim with MACD and RSI confirmation.
- London breakout of the Asian session range.
- Bollinger Band squeeze breakout.
- RSI/ATR mean reversion in low-ADX range regimes.

## Validation design

- Train: 2016-07-03 to 2021-12-31.
- Confirm: 2022-01-01 to 2022-12-31.
- Test: 2023-01-01 to 2026-07-03.
- Parameters were selected only from train + 2022 confirmation; test-period results were not used for strategy selection.

## Final result after adaptive guard

| Metric | Value |
|---|---:|
| Starting Balance | $5,000.00 |
| Ending Balance | $5,000.00 |
| Net Profit | $0.00 |
| Return Percent | 0.00% |
| Trades | 0 |
| Wins | 0 |
| Losses | 0 |
| Win Rate | 0.00% |
| Profit Factor | 0.000 |
| Max Drawdown Percent | 0.00% |
| Avg Trade | $0.00 |

## Selected strategies

| Symbol | Strategy | Train Net R | Train PF | Confirm Net R | Confirm PF | Test Net R | Test PF |
|---|---|---:|---:|---:|---:|---:|---:|
| None | No setup passed gates | 0 | 0 | 0 | 0 | 0 | 0 |

## Decision

The researched intraday extension did not produce a positive OOS result. Keep the V11/V13 intraday component disabled.
