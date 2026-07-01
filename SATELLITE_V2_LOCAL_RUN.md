# GBPUSD V4 + Satellite V2 local setup

## 1. Install the branch

Open PowerShell:

```powershell
cd C:\Users\ralie\mt5-ai-bridge

git status
git fetch origin

if (git branch --list gbpusd-satellite-v2) {
    git switch gbpusd-satellite-v2
} else {
    git switch --track origin/gbpusd-satellite-v2
}

git pull origin gbpusd-satellite-v2
python -m pip install -r requirements.txt
```

## 2. Integrate the V2 portfolio and dashboard

```powershell
python tools\apply_gbpusd_satellite_v2.py
python -m pytest -q
```

Do not continue if pytest reports failures.

## 3. Configure `.env`

```powershell
Copy-Item .env .env.backup -ErrorAction SilentlyContinue
notepad .env.v4-satellite-v2.example
notepad .env
```

Merge the strategy values into the existing `.env` while preserving the private
MT5 login, password, and server values.

Required operating values:

```env
SYMBOL=GBPUSD
STRATEGY=gbpusd_v4_satellite_v2
MODE=READ_ONLY

RISK_PERCENT=0.35
SATELLITE_V2_BASE_LOT=0.08
MAX_OPEN_POSITIONS=2
MULTI_BOOK=false
TRAIL_ENABLED=false

DAILY_MAX_LOSS=250
TOTAL_MAX_LOSS=500

PORTFOLIO_V2_STATE_PATH=portfolio_v2_state.json
V4_STATE_PATH=v4_state.json

WRITE_DASHBOARD=true
DASHBOARD_PORT=8800
```

The Satellite V2 module clips the requested 0.08-lot size so a single trade
risks no more than 0.25% of current balance. The portfolio controller also caps
newly committed daily risk and combined open GBPUSD risk at 0.50%.

## 4. Start safely

Keep MetaTrader 5 open and logged in, then run:

```powershell
python bridge.py
```

Open the dashboard:

```text
http://127.0.0.1:8800
```

The dashboard should display:

- V4 Swing
- Satellite V2
- D1, H4, H1/M30, and M15 analysis rows
- engine labels beside open positions
- `[SATELLITE_V2:LONDON_PULLBACK_V2]` or
  `[SATELLITE_V2:NEW_YORK_RETEST_V2]` in recent order records

Remain in `MODE=READ_ONLY` until signal timing, engine labels, position sizing,
visible stops, break-even changes, and session exits have been verified.

Move to `MODE=APPROVAL` only for demo execution after the read-only checks pass.

## 5. Export complete history

The Satellite V2 long-term tests require M15 data. In MT5:

1. Open **Tools > Options > Charts**.
2. Increase **Max bars in chart** substantially.
3. Open the GBPUSD M15 chart.
4. Press **Home** repeatedly to load older history.

Then run:

```powershell
python export_gbpusd_history.py --years 11 --out-dir data
```

Confirm that `data\GBPUSD_M15.csv` begins at least ten years before its latest
record. The exporter now includes M15, M30, H1, H4, and D1.

## 6. Deployment gate

Do not merge Satellite V2 to main or enable unattended live execution until:

- all local tests pass;
- complete 10/5/3/2-year M15 tests are available;
- rolling walk-forward PF remains above the pre-registered threshold;
- later-period performance is materially stronger than the current PF 1.21;
- 20-30 genuinely forward demo trades reconcile with MT5 and the dashboard.
