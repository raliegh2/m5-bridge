# Executor Agent

Also not an LLM call -- order construction and submission is deterministic
code in `execution.py`, gated on the Risk Manager's approval. This file
documents what it currently does.

## What it builds (`execution.place_market_order`)

- Accepts a broker `client`, `symbol`, side (BUY/SELL), `volume`, and
  optional stop-loss/take-profit distances in pips.
- Reads the live tick (`symbol_info_tick`) and pip size (`pip_size`, which
  handles 3/5-digit vs other quote conventions) to compute the actual
  entry price and SL/TP price levels.
- Builds an MT5 `TRADE_ACTION_DEAL` market-order request tagged with a
  fixed `magic` number and `comment` so the bot's own trades are always
  identifiable versus manual ones.
- Submits via `client.order_send` and checks `retcode` against
  `TRADE_RETCODE_DONE` -- anything else is treated as a rejection, not a
  silent failure.

## Pip value / sizing support (`execution.pip_value_per_lot`)

Derives $-per-pip from the broker's actual tick value/size rather than a
hardcoded constant, so position sizing stays correct across USD-quote pairs
(~$10/pip) and JPY-quote pairs (~$6.5/pip). Falls back to a configured
constant only when the broker doesn't expose tick fields (e.g. backtests).

## Why no LLM here

By the time execution runs, the Analyst has already picked a direction and
the Risk Manager has already approved a size -- there is no judgment call
left to make. Order construction should be exactly reproducible from
(symbol, side, size, stop distance) every time; that's what makes the
Executor unit-testable without a broker or a network call.
