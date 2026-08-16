# V18: a functional trading system with evidence-driven risk management

Date: 2026-08-16

A complete, tested trading system. The risk management is the substance of it;
the edge gate is what makes the risk management meaningful.

## The design idea

Most retail systems set risk first — "risk 1% per trade" — chosen before anyone
knows whether the strategy makes money. That is backwards. **The correct size is
a function of the edge, and when the edge is zero the correct size is zero.**

So capital allocation here is mechanical, not a judgement call:

```
Signal  →  Validation  →  EdgeGate  →  RiskEngine  →  Order plan
```

Each stage can only *reduce* exposure; none can increase it. A signal without
out-of-sample evidence that survives deflation gets a Kelly fraction of zero,
and a zero Kelly fraction produces no position. There is no override flag —
the gate is the code path.

This has a property worth stating plainly: **the system is correct whether or
not an edge exists.** Today it allocates nothing and says why. If your real fill
costs turn out lower than the presets assume, or a new signal clears the gates,
it allocates without anyone editing a threshold.

## Risk management, layer by layer

### 1. Kelly sizing from the measured edge

`f* = (p·b − q) / b` from the signal's actual win rate and payoff ratio. A
negative edge clamps to zero rather than reversing the bet — the mathematics
saying "take the other side" is never what the caller meant.

### 2. Fractional Kelly, capped

Quarter Kelly by default. Full Kelly assumes the edge is known exactly; it never
is, and Kelly's growth curve falls away steeply above the optimum, so
overestimating is far more damaging than underestimating. A hard 2%-of-equity
cap sits on top, because a small sample can imply an absurd fraction — in the
demo, a 58%/1.6:1 record implies Kelly 0.318, quarter-Kelly 0.079, capped to
0.020.

### 3. Volatility targeting

Lots are derived **from** the stop distance, so risk per trade is constant
across instruments and regimes. A position that would round *below* the broker
minimum returns zero rather than rounding up — rounding up silently breaches the
risk budget, which is exactly the mechanism that made gold look profitable
earlier in this investigation.

### 4. Drawdown governor

Constant fractional sizing reduces absolute risk in a drawdown, but not fast
enough: recovering a 50% loss needs a 100% gain. Exposure tapers linearly from
1.0× at 5% drawdown to a floor of 0.25× at 20%, where it stops. Observed:

```
equity 10,000  dd  0.0%  -> 0.40 lots at 2.00% risk
equity  9,200  dd  8.0%  -> 0.31 lots at 1.70% risk
equity  8,900  dd 11.0%  -> 0.24 lots at 1.40% risk
equity  8,400  dd 16.0%  -> 0.15 lots at 0.90% risk
equity  8,100  dd 19.0%  -> 0.09 lots at 0.60% risk
```

### 5. Kill switches

Hard stops, distinct from the taper: the governor shrinks the bet, these refuse
it. Daily loss (2%), total drawdown (20%), consecutive losses (5), trades per
day (10). Daily trips clear at the next session; a total-drawdown trip latches
and does not clear on a recovery.

```
loss 5: sized 0.39 lots
loss 6: kill switch: 5 consecutive losses (limit 5)
```

### 6. Correlation-aware budgets

Aggregate 6%, per-currency 4%, per-symbol 2%, 5 concurrent. The per-currency cap
is the one that matters on this universe:

```
EURUSD: BUY 0.40 lots, risking 2.00%   USD exposure now 2.00%
GBPUSD: BUY 0.19 lots, risking 2.00%   USD exposure now 4.00%
AUDUSD: NO TRADE -- USD exposure 4.0% at the 4% cap
```

Three short-USD positions are one bet, not three. The measured universe gives
only **2.6 effective bets** from six symbols, so without this cap a "diversified"
book is a single leveraged USD position.

## What it does with this repository's signals

All three measured signals are refused, with the failing gate named:

| signal | OOS trades | OOS profit | PF | folds+ | deflated Sharpe | verdict |
|---|---:|---:|---:|---:|---:|---|
| V15 momentum (XAUUSD) | 1,006 | +2,217.49 | 1.082 | 60% | 0.000 | **BLOCKED** |
| V16 reversion (GBPUSD) | 2,202 | −145.48 | 0.997 | 20% | 0.000 | **BLOCKED** |
| V17 gated | 670 | −2,835.13 | 0.826 | 20% | 0.000 | **BLOCKED** |

V15 passes three of five gates — positive profit, enough trades, majority of
folds — and still fails on profit factor and deflated Sharpe at 1,379 trials.
That is the system doing its job: a marginally positive result found after 1,379
attempts is not evidence.

This state is pinned by a test
(`test_the_repo_s_own_measured_signals_are_all_refused`), so a future change
that quietly loosens a gate fails CI rather than a live account.

## Using it

```python
from mt5_ai_bridge.trading_system import (TradingSystem, SignalSpec,
                                          Validation, TradeIntent)
from mt5_ai_bridge.enums import Signal as Side

system = TradingSystem(starting_equity=10_000)

system.register(SignalSpec(
    name="my_signal", symbols=("EURUSD",), timeframe="H4",
    validation=Validation(
        out_of_sample_trades=640, out_of_sample_profit=5100.0,
        profit_factor=1.28, positive_fold_fraction=0.8,
        deflated_sharpe=0.971,
        n_trials=1379,                    # every spec tried, not just this one
        trade_profits=my_oos_trade_pnls,  # drives the Kelly fraction
    )))

system.mark(equity=10_000)
plan = system.plan("my_signal", TradeIntent(
    symbol="EURUSD", side=Side.BUY, entry=1.2000, stop=1.1950,
    pip=0.0001, pip_value_per_lot=10.0))

if plan.approved:
    print(plan.describe())   # EURUSD: BUY 0.40 lots, risking 2.00%
```

`n_trials` is the honest input and the easiest to understate. Use the running
count in `research/v15_trials.json`; the gate prints it in every refusal so it
cannot be quietly forgotten.

## What this system does not do

It does not contain a profitable signal, because this investigation did not find
one at retail costs on this universe — `research/SEARCH_RESULT.md` has the
measurements, including that on this data an in-sample-profitable specification
survived out of sample only 36% (GBPUSD) and 5% (EURUSD) of the time.

What it does is make that impossible to ignore. Plug in a signal with real
out-of-sample evidence and it trades; plug in a flattering backtest and it
refuses, by construction.

## Files

| file | purpose |
|---|---|
| `mt5_ai_bridge/risk_v18.py` | Kelly, vol targeting, drawdown governor, kill switches, budgets |
| `mt5_ai_bridge/trading_system.py` | signal registry, edge gate, order planning |
| `research/v18_system_demo.py` | the three scenarios above, runnable |
| `tests/test_risk_v18.py` | 41 tests on the risk layer |
| `tests/test_trading_system.py` | 24 tests on the gate and system |

813 tests pass (748 before this work, 65 added).
