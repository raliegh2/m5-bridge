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
from the broker library and fully unit-tested without it (93 tests).

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

The website is **display-only** — it shows balance/equity, open & day P/L,
risk:reward, EST clock, session, open positions with live pips, and the equity
curve. There are no buttons; the bot is controlled by its console.

If MT5 isn't ready yet, the page shows "MT5 not connected: …" and the bot keeps
retrying — it connects on its own once MetaTrader 5 is logged in.

## How a trade is decided and sized

1. **Direction** — the reasoning strategy reads the trend/regime and emits BUY,
   SELL, or WAIT, vetoing overbought/oversold extremes.
2. **Size** — base `LOT_SIZE` (0.09), doubled during the New York session.
3. **Style** — strong confluence trades **swing** (wide SL/TP); weaker trades
   **intraday** (tight SL/TP).
4. **Pyramiding** — in a strong trend it stacks up to `MAX_SAME_DIRECTION` (3)
   same-direction positions, each with a **staggered** SL/TP ladder.
5. **Throttling** — `MAX_TRADES_PER_DAY` and `MAX_OPEN_POSITIONS`; exits are the
   SL/TP on each order.

## Modes

`READ_ONLY` (observe), `APPROVAL` (prompt in the console), `AUTO` (hands-off).
Set `MODE` in `.env`.

## Key settings (`.env`)

| Variable | Meaning | Default |
|---|---|---|
| `MODE` | `READ_ONLY` / `APPROVAL` / `AUTO` | `AUTO` |
| `STRATEGY` | `trend` or `reasoning` | `reasoning` |
| `RSI_OVERBOUGHT` / `RSI_OVERSOLD` | Veto thresholds (100/0 disables) | `75` / `25` |
| `LOT_SIZE` / `NY_SIZE_MULTIPLIER` | Base lots / NY multiplier | `0.09` / `2.0` |
| `STRONG_TREND_CONFIDENCE` / `MAX_SAME_DIRECTION` | Pyramiding gate / max stack | `0.8` / `3` |
| `TP_STAGGER_STEP` / `SL_STAGGER_STEP` / `SL_FLOOR_PIPS` | Staggered exits | `0.5` / `0.25` / `10` |
| `DAILY_MAX_LOSS` / `TOTAL_MAX_LOSS` | Risk limits | `250` / `500` |
| `SERVE_DASHBOARD` / `DASHBOARD_PORT` | Live website on/off, port | `true` / `8800` |

Full list in `.env.example`. `.env` is git-ignored — never commit credentials.

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

```
Run Bot.bat                  # start the bot (console + live website)
bridge.py / preflight.py     # bot entrypoint / safe connection check
mt5_ai_bridge/
  app.py            # resilient loop, modes, plan + execute, serves dashboard
  config.py         # typed Settings from .env
  enums.py          # Mode / Signal / OrderSide
  mt5_client.py     # the ONLY module that imports MetaTrader5
  indicators.py     # EMA / RSI / MACD + market snapshot
  strategy.py / reasoning.py   # direction: trend rule / confluence + veto
  planner.py        # session sizing, intraday/swing, staggered exits
  risk_engine.py    # risk limits + daily-loss tracker
  execution.py / trade_manager.py   # place / close orders
  journal.py / dashboard.py    # SQLite journal + live HTML view
  control.py        # localhost web server that serves the dashboard
  backtest.py / data.py        # backtester + history loaders
  __main__.py       # backtest CLI
  logging_config.py
tests/              # pytest suite with a FakeMT5Client
```

See `ARCHITECTURE.md` for details and `ROADMAP.md` for status.
