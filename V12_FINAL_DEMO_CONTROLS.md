# V12 Final $3,201.58 Supervised Research Controls

Status: **PROPOSAL-ONLY RESEARCH LAYER**

This branch ports the final research portfolio limits into broker-independent,
unit-tested code. It does not submit broker orders.

Named engines send a signal to `FinalV12Adapter`. The adapter reads MT5 account
and symbol data, calculates broker-aware volume, applies every risk rule, and
returns an exact proposal for human review. Any trade is entered manually by the
researcher outside this software.

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

Adaptive multipliers are `1.00`, `0.60`, `0.35` recovery probe, or `0.00`
blocked. The guard uses 16 trades, a 12-trade minimum, a 45-day cooldown, and
one reduced-risk recovery probe.

## Research safety overlays

- 1.50% daily-equity drawdown stop
- 5.00% peak-equity drawdown stop
- Broker-native pip-value sizing
- Volume rounded down, never up
- Spread ceilings per symbol
- Duplicate-proposal lock
- Persistent daily baseline, peak equity, cooldown, probe, and position-risk state
- Fail closed when manual or unregistered positions are open
- Fail closed when stop distance, pip value, symbol data, or account data is missing
- Explicit review callback required for every proposal
- No broker order submission

## Workflow

1. Copy `.env.v12-final-demo.example` to `.env` and enter MT5 credentials.
2. Set `MODE=APPROVAL`.
3. Add all five symbols to MT5 Market Watch.
4. Run `python v12_final_preflight.py`.
5. Route named V12 signals through `FinalV12Adapter.submit()`.
6. Review the calculated proposal.
7. Enter a trade manually in MT5 only if you independently choose to do so.
8. Record closed outcomes in R with `FinalV12Adapter.record_closed_trade()`.

The legacy generic `bridge.py` remains blocked whenever `V12_FINAL_PROFILE` is selected.
