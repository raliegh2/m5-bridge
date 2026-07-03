# V13 RSI/EMA + Order-Flow Proxy Lax-Risk M5/M1 Report — Pip-Fixed + Fade Variants

Status: **research-only no-lookahead replay; pip-to-price conversion corrected**

## Design

- M5 controls trend/regime using EMA, RSI, ADX, MACD and ATR.
- M1 controls entries using RSI reclaim, EMA pullback/reclaim, EMA stack continuation and order-flow proxy breakout.
- Order flow is approximated with tick-volume z-score, signed-volume z-score, candle body direction and close location. It is not real DOM/footprint order flow.
- Risk was raised to 0.35% per trade; signal inversion/fade variants were also tested, contained by a 1.35% daily stop, 9% total drawdown stop, break-even/trailing logic and daily lockout after 4 losses.

## Data check

| Symbol | M1 median step | Rows | Note |
|---|---:|---:|---|
| GBPUSD | 1.00 min | 3,721,516 | OK |
| GBPJPY | 1.00 min | 3,721,448 | OK |
| EURUSD | 5.00 min | 745,177 | Excluded: not true M1 |

## Portfolio result after containment guards

| Metric | Value |
|---|---:|
| starting_balance | 5000.0000 |
| ending_balance | 5000.0000 |
| net_profit | 0.0000 |
| return_percent | 0.0000 |
| trades | 0 |
| wins | 0 |
| losses | 0 |
| win_rate | 0 |
| profit_factor | 0.0000 |
| max_drawdown_percent | 0 |
| avg_r | 0 |
| total_r | 0 |
| risk_per_trade_percent | 0.3500 |
| daily_stop_percent | 1.3500 |
| total_dd_stop_percent | 9.0000 |

## Selected setups

| Symbol | Strategy | Train R | Confirm R | Test R | Test Trades | Test PF |
|---|---|---:|---:|---:|---:|---:|
| GBPUSD | no RSI/EMA/order-flow setup passed lax risk gate |  |  |  |  |  |
| GBPJPY | no RSI/EMA/order-flow setup passed lax risk gate |  |  |  |  |  |
| EURUSD | M1 median step 5.00 minutes; excluded |  |  |  |  |  |

## Decision

The lax higher-risk RSI/EMA/order-flow proxy system did not produce a robust profitable result. Keep intraday disabled.
