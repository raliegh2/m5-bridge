# GBPUSD V4 + Satellite local setup

## 1. Install the branch

Open PowerShell:

```powershell
cd C:\Users\ralie\mt5-ai-bridge

git status
git fetch origin

if (git branch --list gbpusd-v4-satellite) {
    git switch gbpusd-v4-satellite
} else {
    git switch --track origin/gbpusd-v4-satellite
}

git pull origin gbpusd-v4-satellite
python -m pip install -r requirements.txt
```

## 2. Integrate the portfolio and dashboard

```powershell
python tools\apply_gbpusd_v4_satellite.py
python -m pytest -q
```

The patch adds:

- `STRATEGY=gbpusd_v4_satellite` routing
- V4 and Satellite engine cards on the dashboard
- engine labels for open positions
- engine-prefixed signals and order records

## 3. Configure `.env`

Back up the current file:

```powershell
Copy-Item .env .env.backup -ErrorAction SilentlyContinue
notepad .env.v4-satellite.example
notepad .env
```

Copy the strategy values into the existing `.env`. Do not remove the MT5 login,
password, or server values.

Start with:

```env
MODE=READ_ONLY
STRATEGY=gbpusd_v4_satellite
SATELLITE_RISK_PERCENT=0.12
PORTFOLIO_MAX_RISK_PERCENT=0.50
MAX_OPEN_POSITIONS=2
```

## 4. Start the bot

Keep the MetaTrader 5 desktop terminal open and logged in, then run:

```powershell
python bridge.py
```

Open the dashboard at:

```text
http://127.0.0.1:8800
```

The dashboard should show two engine cards:

- **V4 Swing** — D1/H4 trend expansion
- **Satellite Intraday** — H1/M30 London/New York continuation

Recent signals and orders include `[V4_SWING]` or `[SATELLITE_INTRADAY]`.
Open positions include an **Engine** column after the integration patch.

Move to `MODE=APPROVAL` only after completed-candle timing, engine labels, state
files, lot sizing, hard stops, partial closes, and session exits are verified.

## 5. Export enough history for 10/5/3/2-year tests

In MT5, first set **Tools > Options > Charts > Max bars in chart** high enough.
Open a GBPUSD M30 chart and press **Home** repeatedly so MT5 loads older history.
Then run:

```powershell
python tools\export_gbpusd_history.py --years 11 --out-dir data
```

Confirm that the M30 export begins at least ten years before its latest date.

## 6. Run all requested windows

```powershell
python research\run_gbpusd_v4_satellite_backtests.py `
  --h4 data\GBPUSD_H4.csv `
  --m30 data\GBPUSD_M30.csv `
  --initial-balance 5000 `
  --satellite-risk 0.12 `
  --out-dir research\portfolio_results
```

The runner produces separate directories for `10y`, `5y`, `3y`, and `2y`, each
containing:

- `v4_trades.csv`
- `satellite_trades.csv`
- `combined_equity.csv`
- `combined_events.csv`
- `metrics.json`

It refuses to claim a window when the M30 file does not cover the full period.
Use `--allow-partial-coverage` only for development checks.

## 7. Before merging to main

Do not merge merely because the code runs. Require:

- all tests pass locally;
- complete 10/5/3/2-year M30 coverage;
- satellite PF at least 1.30 out of sample;
- combined PF at least 1.50 or a clearly superior return/drawdown trade-off;
- combined drawdown below the pre-registered limit;
- no opposing simultaneous GBPUSD exposure;
- a demo/approval forward test with reconciled dashboard and MT5 records.

When those gates pass, merge the V4 PR first, then merge the satellite PR.
