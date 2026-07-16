# V14.7 workflow status

- Backtest outcome: success
- Validation outcome: success
- Commit: c691be8cd3054caaffee508bb52913cd47a58149

```text
{
  "window": {
    "start": "2012-03-05T00:00:00+00:00",
    "end": "2022-03-05T00:00:00+00:00"
  },
  "coverage": {
    "active_swing_symbols": [
      "GBPUSD",
      "GBPJPY",
      "AUDUSD"
    ],
    "active_ict_symbols": [
      "USDJPY"
    ],
    "all_ten_sleeves_active": false
  },
  "activity": {
    "swing": {
      "trades": 410,
      "active_days": 346,
      "average_per_active_day": 1.185,
      "maximum_per_day": 3
    },
    "ict": {
      "trades": 136,
      "active_days": 136,
      "average_per_active_day": 1.0,
      "maximum_per_day": 1
    }
  },
  "previous_v14_6_2": {
    "swing_scale": 1.0,
    "ict_scale": 2.0,
    "net_profit": 8069.701021206678,
    "ending_balance": 13069.701021206678,
    "return_percent": 161.39402042413357,
    "profit_factor": 1.9429036150886525,
    "max_closed_drawdown_percent": 8.796869546776602,
    "stress_drawdown_percent": 9.979199079604385,
    "closed_trades": 489,
    "skipped_trades": 42,
    "safe": true,
    "target_reached": false,
    "governor_interventions": 25
  },
  "best_safe_portfolio": {
    "swing_scale": 1.8,
    "ict_scale": 2.5,
    "net_profit": 13355.021604037422,
    "ending_balance": 18355.02160403742,
    "return_percent": 267.1004320807485,
    "profit_factor": 1.6843286204165206,
    "max_closed_drawdown_percent": 8.297812537720429,
    "stress_drawdown_percent": 9.753676465871575,
    "closed_trades": 545,
    "skipped_trades": 1,
    "safe": true,
    "target_reached": false,
    "governor_interventions": 5
  },
  "profit_improvement_vs_v14_6_2": 5285.32,
  "target_reached": false,
  "target_gap": 6644.98,
  "output": "/home/runner/work/m5-bridge/m5-bridge/research/v14_7_five_symbol_20k_output"
}
```
