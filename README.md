# MT5 AI Bridge

An automated MetaTrader 5 trading bridge: it reads market data, decides a
direction, sizes and styles the trade, runs pre-trade risk checks, and places
trades — journaling every decision to SQLite. You start it from a console; it
serves a **read-only live dashboard** website while it runs.

> **Use a demo account.** AUTO mode places real orders on whatever account you
> connect. You are responsible for that account.

## The tactical book — the current model

Two runners live here. `bridge.py` is the original FX/gold intraday-and-swing
bridge described below. **`tactical_bot.py` is the model to run**, and it came
out of measuring the other one honestly.

It holds two sleeves — US large-cap equity and gold — and each month holds a
sleeve only while its price is above its **ten-month moving average**, otherwise
that half sits in cash. Long or flat, never short. One decision per sleeve per
month.

```bash
python tactical_bot.py --dry-run   # decide and print, never sends an order
python tactical_bot.py             # act if the calendar month has turned
python tactical_bot.py --force     # rebalance now, ignoring the calendar
```

```ini
TACTICAL_ENABLED=true
TACTICAL_WEIGHT_SCHX=0.5      # US large cap
TACTICAL_WEIGHT_IAU=0.5       # gold
TACTICAL_FRACTION_INVESTED=0.70
MODE=APPROVAL
```

### What it returns, and at what risk

On 20.7 years of split-adjusted daily history including 2008, costs charged on
every switch, at the configured 70% invested:

| | value |
|---|---:|
| annual return | 6.35% |
| worst rolling ten-year drawdown | **7.7%** |
| $5,000 over ten years, median | $7,528 |
| $5,000 over ten years, worst window | $6,519 |

The full sizing ladder is in [`research/FIVE_K_TEN_YEAR.md`](research/FIVE_K_TEN_YEAR.md);
roughly one point of annual return buys one point of drawdown.

### Why these two symbols

**Gold is what makes the risk budget work.** IVV and VTI correlate 0.988 — one
bet with two tickers — while gold against timed equity correlates −0.07. At a
fixed 10% drawdown budget the difference is decisive:

| book | invested | CAGR |
|---|---:|---:|
| timed equity + gold | 90% | 8.15% |
| held equity + gold | 37% | 4.05% |
| timed equity only | 35% | 3.15% |
| held equity only | 16% | 1.61% |

**SCHX rather than IVV** purely for share granularity: IVV at $780 buys two
units of a $1,750 target. Their daily returns correlate 0.9882 over 4,211
shared days. **IAU rather than spot XAUUSD** because gold's minimum order is
1 oz — about $4,354 of notional against a $1,750 target, untradeable at the
right size on a $5,000 account.

### What it is not

It is **beta harvested at a controlled risk level, not alpha**. The return is
equity and gold going up; the drawdown reduction is mechanical. The rule's
risk-adjusted improvement over buy-and-hold is **not statistically significant**
(bootstrap p = 0.29), and its advantage is concentrated in 2008 and 2022 — the
two bear markets in the sample. Half the book is in gold, which returned 10.74%
a year over this window; if gold merely holds flat for a decade the median
outcome falls from about $7,500 to about $5,500.

Every strategy this repo tried that claimed more than that was measured and
failed. See [`research/TACTICAL_RESULT.md`](research/TACTICAL_RESULT.md),
[`research/ETF_PORTFOLIO.md`](research/ETF_PORTFOLIO.md) and
[`research/CROSS_SECTIONAL_RESULT.md`](research/CROSS_SECTIONAL_RESULT.md).

### How it uses the risk system

`RiskEngine.size` derives lots from a stop distance and a measured Kelly edge.
This book has no stop and is sized by weight, so Kelly does not apply and
forcing it would be theatre. What does apply is wired in full: the
**DrawdownGovernor** scales exposure down as equity falls from its peak, and the
**KillSwitch** flattens every sleeve outright. Targets floor to whole shares,
positions are matched on magic `20260801` so the book never touches another
engine's trades, and exits are placed before entries.

## Requirements

- Windows with the MetaTrader 5 terminal installed and logged in
- Python 3.10+ (the same `python` for everything)
- A MetaTrader 5 **demo** account

The `MetaTrader5` package only runs on Windows. The trading logic is decoupled
from the broker library and unit-tested without it.

## One-time setup

```bash
pip install -r requirements.txt
copy .env.example .env      # then edit .env with your demo credentials
python preflight.py         # safe connection check (never trades)
```

## Run it

1. Make sure MetaTrader 5 is open and logged in, with **Algo Trading** enabled.
2. Double-click **`Run Bot.bat`** (or run `python bridge.py` in a terminal).
   - It opens a **console window** showing live logs.
   - It serves the live dashboard and opens your browser to
     **`http://127.0.0.1:8800`**.
3. Watch it trade on the dashboard. **To stop the bot, press `Ctrl+C` in its
   console** (or close that window).

The live `bridge.py` entrypoint wraps every intraday/Gold and swing order with a
persistent account-level session guard. By default it stops new entries after a
1% daily equity loss, a 40% giveback from an activated session-profit peak,
three consecutive completed losses, excessive trade frequency, or a requested
volume above 0.40 lots. Existing closes and trailing-stop updates continue while
a lock is active. See [`SESSION_RISK_GUARD.md`](SESSION_RISK_GUARD.md).

The website shows balance/equity, open and day P/L, risk:reward, EST clock,
session, open positions with live pips, engine decisions, currency exposure,
and the equity curve.

If MT5 is not ready yet, the page shows `MT5 not connected: ...` and the bot
keeps retrying — it connects on its own once MetaTrader 5 is logged in.

## How a trade is decided and sized

1. **Direction** — the reasoning strategy reads the trend/regime and emits BUY,
   SELL, or WAIT, vetoing overbought/oversold extremes.
2. **Dual engines** — intraday uses M15/M30 timing while swing uses H4/D1 trend
   with matching lower-timeframe timing. Both share account limits.
3. **Stops** — ATR-based when available, with engine-specific fallbacks.
4. **Size** — fixed-fractional risk sizing derives lots from balance, stop
   distance, and broker tick value. Gold has lower built-in risk defaults.
5. **Portfolio controls** — aggregate open-risk and per-currency factor caps
   prevent several correlated symbols from becoming one oversized bet.
6. **Session controls** — daily loss, peak-profit giveback, loss streak, trade
   count, entry interval, and final lot-size gates sit below every engine.

The sizing code contains no martingale or loss-based volume multiplier. Lot
sizes can differ because engine risk, symbol tick value, and ATR stop distance
differ.

## Modes

`READ_ONLY` (observe), `APPROVAL` (prompt in the console), `AUTO` (hands-off).
Set `MODE` in `.env`.

## Key settings (`.env`)

| Variable | Meaning | Default |
|---|---|---|
| `MODE` | `READ_ONLY` / `APPROVAL` / `AUTO` | `AUTO` in example |
| `STRATEGY` | `trend` or `reasoning` | `reasoning` in example |
| `SYMBOLS` | Symbols traded concurrently | blank → `SYMBOL` |
| `INTRADAY_RISK_PERCENT` / `SWING_RISK_PERCENT` | Engine risk per trade | `0.11` / `1.05` |
| `COMBINED_RISK_CEILING` | Maximum aggregate open risk | `2.5%` |
| `FACTOR_CAPS` / `MAX_CURRENCY_RISK` | Correlated currency-exposure cap | `true` / `2.0%` |
| `DAILY_MAX_LOSS` / `TOTAL_MAX_LOSS` | Legacy dollar risk limits | `250` / `500` |
| `SESSION_MAX_DAILY_LOSS_PERCENT` | Persistent daily equity stop | `1.0%` |
| `SESSION_PROFIT_LOCK_ACTIVATION_PERCENT` | Peak-profit guard activation | `1.0%` |
| `SESSION_MAX_PROFIT_GIVEBACK_PERCENT` | Allowed peak-profit giveback | `40%` |
| `SESSION_MAX_CONSECUTIVE_LOSSES` | Completed-loss daily cutoff | `3` |
| `SESSION_MAX_TRADES_PER_DAY` | Account-wide entry cap | `8` |
| `SESSION_MAX_TRADES_PER_SYMBOL_PER_DAY` | Per-symbol entry cap | `4` |
| `SESSION_MINIMUM_MINUTES_BETWEEN_ENTRIES` | Cross-engine entry spacing | `15` |
| `SESSION_MAXIMUM_LOT` | Final order-volume ceiling | `0.40` |
| `SERVE_DASHBOARD` / `DASHBOARD_PORT` | Live website on/off, port | `true` / `8800` |

Full list in `.env.example` and `SESSION_RISK_GUARD.md`. `.env` is git-ignored —
never commit credentials.

## Backtesting & static dashboard

```bash
python -m mt5_ai_bridge data/GBPUSD_M30.csv --strategy reasoning --threshold 0.6 --trades
python -m mt5_ai_bridge.dashboard --db journal.db --out dashboard.html
```

Backtests charge realistic broker costs by default (`--cost typical`; also
`tight` / `wide` / `zero`, or override `--spread` / `--slippage` /
`--commission`). The summary reports `total_costs` and `gross_profit` so the
gap between a gross and a net result is always visible.

## Validating a strategy honestly

A backtest number is not evidence until it survives out-of-sample testing and a
correction for how many variants you tried.

```bash
# Walk-forward: choose parameters on train, score on the unseen slice after it,
# then deflate the result by the number of parameter sets searched.
python research/honest_walk_forward.py --csv GBPUSD_M5.csv

# Run the pre-registered V15 candidate against its locked parameters.
python research/v15_forward_test.py --csv GBPUSD_M5.csv
```

Both print an explicit PASS/FAIL against named gates. See
`research/V15_EDGE_INVESTIGATION.md` for what these currently say about this
repository, and `mt5_ai_bridge/validation.py` for the statistics.

## Why isn't it trading?

The session guard reports only the first gate that refused an entry. To see all
of them against the persisted guard state:

```bash
python -m mt5_ai_bridge.entry_diagnostics --symbol GBPUSD --volume 0.1
```

While the bot runs, `guard.rejections.report()` aggregates every refusal so a
day with no trades can be explained afterwards.

## Testing

```bash
python -m pytest -q
```

## Project structure

```text
Run Bot.bat                  # start the bot (console + live website)
bridge.py / preflight.py     # guarded live entrypoint / safe connection check
mt5_ai_bridge/
  app.py            # resilient loop, dual engines, plan + execute, dashboard
  session_guard.py  # persistent account-level circuit breakers for every entry
  config.py         # typed Settings from .env
  enums.py          # Mode / Signal / OrderSide
  mt5_client.py     # the ONLY module that imports MetaTrader5
  indicators.py     # EMA / RSI / MACD + market snapshot
  strategy.py / reasoning.py   # direction: trend rule / confluence + veto
  books.py / planner.py         # intraday+swing books, sizing and staggered exits
  sizing.py         # ATR stops + fixed-fractional lots
  costs.py          # spread / slippage / commission / swap model
  validation.py     # walk-forward splits, deflated Sharpe, PASS/FAIL gates
  candidate_v15.py  # pre-registered momentum candidate (locked parameters)
  entry_diagnostics.py  # every blocking entry gate + rejection ledger
  risk_engine.py    # legacy account loss/open-position limits
  exposure.py       # correlated per-currency factor-risk caps
  execution.py / trade_manager.py   # place, close and trail orders
  journal.py / dashboard.py    # SQLite journal + live HTML view
  control.py        # localhost dashboard/control server
  backtest.py / data.py        # backtester + history loaders
  __main__.py       # backtest CLI
  logging_config.py
tests/              # pytest suite with fake MT5 clients
```

See `ARCHITECTURE.md`, `SESSION_RISK_GUARD.md`, and `ROADMAP.md` for details.
