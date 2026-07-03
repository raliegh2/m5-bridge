# V12 Final $3,201.58 Automatic Account Controls

Status: **AUTOMATIC EXECUTION, EXPLICIT DEMO/LIVE ACCOUNT SELECTION**

`v12_final_runner.py` reads closed H1, H4, and D1 candles, rebuilds the frozen
V12 candidate families, applies the unchanged portfolio risk layer, and routes
qualified signals to `MetaTrader5.order_send()`. Every attempt is written to
`v12_final_executions.jsonl`.

Startup asks whether the connected account is DEMO or LIVE. The executor fails
closed unless `account_info().trade_mode` matches that selection. A server-name
guess is never treated as proof. Once selected, execution is automatic and has
no per-order approval prompt.

The mode can be changed while running either from the dashboard buttons or by
typing `MODE DEMO` / `MODE LIVE` in the bot terminal. The persisted selection
is shared by the scanner, dashboard, preflight, and executor.

## Frozen portfolio controls

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

No individual engine requests more than 0.50% base risk. Disabled engines,
adaptive multipliers, spread ceilings, daily/peak drawdown stops, and the
historical signal logic are unchanged.

## Execution and recovery

- `TRADE_ACTION_DEAL` with the matching buy/sell order type
- Broker-native pip-value sizing and volume min/max/step normalization
- Entry, SL, TP, native filling mode, deviation, magic number, and comment
- Accepted-retcode verification; rejected requests fail closed
- Signal-key persistence before transmission to prevent duplicate submission
- Filled ticket persistence and open-position reconciliation
- Restart recovery by stable engine magic numbers
- Unknown/manual positions block new orders
- Full/partial close and SL/TP modification methods
- Persisted open risk refreshed after modifications and partial closes

## Commands

```powershell
Copy-Item .env.v12-final-demo.example .env
python v12_final_preflight.py
python v12_final_runner.py --once
python v12_final_runner.py --interval 60
python research/v12_final_runner_parity_backtest.py
```

The preferred `python v12_final_bot.py` entry point asks for account mode,
starts the scanner and dashboard together, and opens the dashboard browser.
Preflight sends no order. The generic `bridge.py` remains blocked whenever
`V12_FINAL_PROFILE` is selected.
