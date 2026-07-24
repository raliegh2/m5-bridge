# MT5 AI Bridge

An automated MetaTrader 5 trading bridge: it reads market data, decides a
direction, sizes and styles the trade, runs pre-trade risk checks, and places
trades — journaling every decision to SQLite. You start it from a console; it
serves a **read-only live dashboard** website while it runs.

> **Use a demo account.** AUTO mode places real orders on whatever account you
> connect. You are responsible for that account.

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
