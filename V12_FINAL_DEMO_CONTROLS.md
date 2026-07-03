# V12 Final $3,201.58 Demo Controls

Status: **DEMO-ONLY SAFETY AND EXECUTION LAYER**

This branch ports the final research portfolio limits into broker-independent,
unit-tested code. It does **not** make the generic `bridge.py` strategy equivalent
to the named V12 signal engines. A named-engine signal adapter must call
`FinalDemoExecutor`; orders sent through the legacy `place_market_order` path do
not qualify as the final V12 model.

## Backtest-exact immutable limits

| Control | Value |
|---|---:|
| Symbols | GBPUSD, EURUSD, GBPJPY, AUDUSD, USDJPY |
| Maximum simultaneous positions | 5 |
| Maximum total open risk | 1.50% |
| GBPUSD precision symbol cap | 0.75% |
| Legacy-symbol cap | 0.75% |
| AUDUSD / USDJPY symbol cap | 0.25% |
| Aligned GBP exposure cap | 0.90% |
| Mixed-direction GBP exposure cap | 0.65% |

## Engine risk map

| Engine | Base risk | State |
|---|---:|---|
| GBPUSD V10 precision primary | 0.20% or 0.50% | Protected |
| GBPUSD V10 precision secondary | 0.40% | Protected |
| GBPUSD V5 pullback add-on | 0.40% | Protected |
| GBPUSD swing retest | 0.15% | Protected |
| EURUSD swing core | 0.25% | Protected |
| EURUSD swing retest | 0.10% × adaptive multiplier | Adaptive |
| GBPJPY swing core | 0.15% | Protected |
| AUDUSD trend pullback | 0.25% | Protected |
| USDJPY safe-haven breakout | 0.25% × adaptive multiplier | Adaptive |

Disabled engines:

- `GBPUSD_SWING_CORE`
- `GBPJPY_SWING_RETEST`

Adaptive multipliers are exactly `1.00`, `0.60`, `0.35` recovery probe, or
`0.00` blocked. The rolling guard uses 16 trades, a 12-trade minimum, a 45-day
cooldown, and one reduced-risk recovery probe.

## Additional demo safety overlays

These were not part of the historical backtest and may change live trade count,
but they are required to prevent unsafe demo execution:

- Demo-account verification before every order
- 1.50% daily-equity drawdown stop
- 5.00% peak-equity drawdown stop
- Broker-native pip-value sizing
- Volume rounded **down**, never up
- Spread ceilings per symbol
- Duplicate-order lock for the same signal
- Persistent daily baseline, peak equity, cooldown, probe, and position-risk state
- Fail closed when manual or unregistered positions are open
- Fail closed when stop distance, pip value, symbol data, or ticket data is missing

## Safe workflow

1. Copy `.env.v12-final-demo.example` to `.env` and enter demo credentials.
2. Add all five symbols to MT5 Market Watch.
3. Run `python v12_final_preflight.py`.
4. Route every named V12 signal through `FinalDemoExecutor.place()`.
5. Record every closed trade in R using `FinalDemoExecutor.record_closed_trade()`.
6. Do not start the legacy generic `bridge.py` and assume it reproduces this model.

The risk layer deliberately refuses live accounts and unknown strategy engines.
