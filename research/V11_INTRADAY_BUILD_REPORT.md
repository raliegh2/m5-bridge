# V11 Intraday Walk-Forward Profitability Build Report

Status: **RESEARCH / DEMO ONLY — DO NOT DEPLOY LIVE**

Branch: `v11-intraday-walkforward-profitability`  
Base branch: `strategy-engine-v10-profitability`

## Objective

Build on the best intraday-performing baseline without simply multiplying risk.
The research target is to work toward a **$50/week equivalent** on a $5,000
account by improving expectancy, filtering weak trades, and adding validation
rather than forcing more entries.

## Why V10 is the base

V10 is the strongest intraday-heavy baseline in the current repo lineage:

- V10 synchronized net profit: **$1,214.66**
- Approximate weekly equivalent: **$23.36/week**
- Profit factor: **2.4007**
- Stress drawdown: **2.793%**
- Main contribution came from intraday satellite engines, especially GBPUSD

The target requires roughly a **2.14x improvement** over V10's annualized profit.
That should not be attempted through a blanket 2.14x risk increase because it
would likely push drawdown and correlation exposure outside the intended safety
bounds.

## Available-data backtest result

A first available-data replay has been added:

- `research/V11_AVAILABLE_DATA_BACKTEST_REPORT.md`
- `research/v11_available_data_backtest_results.json`

Because the raw V8/V9/V10 accepted/rejected candidate ledgers are not present in
this branch, this is a **risk-reweighted aggregate replay estimate**, not a raw
execution backtest.

Result:

- estimated V11 base-risk net profit: **$1,099.40**
- estimated ending balance: **$6,099.40**
- estimated return: **21.99%**
- estimated weekly equivalent: **$21.14/week**
- difference versus V10: **-$115.25**
- estimated V10 profit retained: **90.51%**

Decision: V11 base-risk policy remains profitable but is not yet a $50/week
system. The next required step is a raw candidate-ledger walk-forward test.

## Added in this branch

### `mt5_ai_bridge/strategy_engine_v11.py`

Adds a V11 profile with:

- READ_ONLY default
- $50/week research target
- 0.90% maximum total open risk
- 0.60% aligned GBP cap
- 0.45% mixed GBP cap
- 0.75% daily new-risk cap
- 0.40% maximum per-trade risk
- engine-specific risk tiers
- quality-scored admission thresholds
- walk-forward validation gates
- setup-level promotion requirements

### `research/v11_intraday_walkforward.py`

Adds a standalone validation harness for exported trade ledgers. It requires an
input CSV with:

```text
entry_time,engine,setup,profit_dollars
```

Optional columns:

```text
risk_dollars,r_multiple,symbol
```

The script reports:

- overall net profit
- profit factor
- win rate
- average trade
- maximum drawdown
- walk-forward pass rate
- engine/setup attribution
- pass/fail gate

### `.env.strategy-engine-v11.example`

Adds READ_ONLY V11 environment values while preserving the existing MT5 credential
separation.

### `tests/test_strategy_engine_v11.py`

Adds targeted tests for:

- READ_ONLY safety default
- portfolio risk caps
- engine aliases
- risk tiers
- engine-specific quality gates
- quality-score behavior

## V11 risk policy

| Engine | Base risk | Strong risk | Exceptional risk | Daily count |
|---|---:|---:|---:|---:|
| GBPUSD_SATELLITE_V3 | 0.30% | 0.35% | 0.40% | 2 |
| EURUSD_SATELLITE_V7 | 0.30% | 0.35% | 0.40% | 2 |
| GBPJPY_SATELLITE_V7 | 0.25% | 0.35% | 0.40% | 1 |
| GBPUSD_SWING_V6 | 0.25% | 0.35% | 0.40% | 1 |

Risk is promoted only when the setup quality score passes the strong or
exceptional threshold. The weekly profit target must never force a trade or
increase size after losses.

## Quality-score model

The V11 profile introduces a normalized quality score using:

- trend strength
- EMA separation
- candle body quality
- relative volume confirmation
- pullback/retest quality
- session range quality
- spread-to-ATR penalty
- overextension penalty

This is intended to replace a pure hard-coded hour gate with a more robust market
condition gate. Session filters can still exist, but the final admission decision
should be based on quality and out-of-sample evidence.

## Required validation gate before promotion

Do not merge into a live execution branch until all of these pass:

1. Local tests pass.
2. Full broker-native M15 or lower-timeframe execution replay is available.
3. The walk-forward report passes at least 70% of windows.
4. Aggregate out-of-sample PF is at least 1.40 after cost stress.
5. Each promoted setup has at least 30 accepted trades.
6. Stress drawdown remains below the strategy limit.
7. MT5 demo forward test records at least 30-50 reconciled trades.
8. Dashboard, journal and MT5 fills reconcile by ticket, symbol, engine and setup.

## Local commands

```powershell
cd C:\Users\ralie\mt5-ai-bridge
git fetch origin
git switch v11-intraday-walkforward-profitability
python -m pip install -r requirements.txt
python -m pytest tests\test_strategy_engine_v11.py -q
python -m py_compile mt5_ai_bridge\strategy_engine_v11.py research\v11_intraday_walkforward.py
```

Run a ledger validation after exporting or generating a synchronized trade ledger:

```powershell
python research\v11_intraday_walkforward.py `
  --trades research\v10_or_v11_trade_ledger.csv `
  --windows 6 `
  --min-pf 1.40 `
  --min-trades 30 `
  --min-pass-rate 0.70 `
  --out research\V11_WALKFORWARD_REPORT.md `
  --json-out research\v11_walkforward_report.json
```

## Current limitation

This branch adds the V11 risk/quality/validation layer and the walk-forward
harness. It does **not** yet wire V11 into the live broker order loop. That should
be done only after the profile passes tests and the existing V10/V9 engines are
adapted to emit setup-quality diagnostics consistently.
