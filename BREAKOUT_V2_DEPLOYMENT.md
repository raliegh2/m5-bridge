# GBPUSD Breakout V2 Deployment

## Why this engine replaces the previous stack

The earlier mixed regime engine lost money because all three components were negative after costs. Breakout V2 disables those components and runs one proxy-validated setup family only.

Key corrections:

- no intraday engine;
- no range mean-reversion engine;
- no pullback engine;
- no simultaneous GBPUSD positions;
- no 50% partial close at 1R;
- completed H4 and D1 candles only;
- one H4 breakout setup can be consumed only once;
- D1 trend, H4 ADX and tick-volume confirmation;
- H4 ATR stop and trailing logic;
- shared daily/total account-loss controls remain active.

## Install locally

```powershell
cd C:\Users\ralie\mt5-ai-bridge
git fetch origin
git checkout gbpusd-breakout-v2
git pull
python -m pytest -q
```

The dedicated Breakout V2 execution path is integrated directly in this
version. The legacy patch helper remains only for older checkouts.

The patch changes `mt5_ai_bridge/app.py` so `STRATEGY=gbpusd_breakout_v2` receives a dedicated execution path and returns before the legacy books can place trades.

## Configure

Copy the values from `.env.breakout-v2.example` into your existing `.env`. Preserve your private MT5 credentials.

Minimum required strategy settings:

```env
SYMBOL=GBPUSD
STRATEGY=gbpusd_breakout_v2
MODE=APPROVAL
RISK_PERCENT=0.5
MAX_OPEN_POSITIONS=1
MAX_SAME_DIRECTION=1
MIN_SAME_DIRECTION=1
MULTI_BOOK=false
TRAIL_ENABLED=false
```

`RISK_PERCENT=0.5` means 0.50% of current balance, not 50%.

## Start safely

```powershell
python bridge.py
```

Use this progression:

1. `MODE=READ_ONLY` to verify signals and dashboard behavior.
2. `MODE=APPROVAL` on demo to inspect every proposed order.
3. Demo automation only after completed-candle timing and lot sizing match expectations.
4. Live use only after a forward sample is consistent with the proxy.

## Automated demo runner

After configuring demo MT5 credentials in `.env`, either double-click
`Run Breakout V2 Demo Auto.bat` or run:

```powershell
python -m mt5_ai_bridge.breakout_v2_runner
```

The dedicated runner forces `GBPUSD`, `gbpusd_breakout_v2`, 0.50% base risk,
one position maximum, legacy books off, and generic trailing off. It also forces
`REQUIRE_DEMO=true`; AUTO orders fail closed unless MT5 explicitly identifies
the connected account as a demo. `APPROVAL` mode separately requires the exact
text `YES` before every Breakout V2 order.

## Reproduce the historical proxy

```powershell
python research\run_gbpusd_breakout_v2_proxy.py `
  --h4 GBPUSD_H4_201601040000_202607011200.csv `
  --out gbpusd_breakout_v2_trades.csv `
  --equity-out gbpusd_breakout_v2_equity.csv
```

Expected reference result for the supplied H4 export:

- ending balance: approximately $115,264.56;
- net profit: approximately $15,264.56;
- 160 trades;
- profit factor: approximately 1.35;
- maximum mark-to-market drawdown: approximately 5.59%.

Small differences can occur if candle aggregation, pandas versions or broker spread interpretation differ. Large differences mean the implementation or input format must be investigated before trading.

## Rollback

The original branch remains unchanged. To return to it:

```powershell
git reset --hard
git checkout closed-candle-fix
```

Do not run `git reset --hard` while you have uncommitted work that must be preserved.
