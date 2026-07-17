# V14.7.1 workflow status

- Backtest outcome: success
- Validation outcome: success
- Commit: cfba5cbb1fcc0badba08d4974514313165e084ac

```text
{
  "window": {
    "start": "2012-03-05T00:00:00+00:00",
    "end": "2022-03-05T00:00:00+00:00"
  },
  "coverage": {
    "active_swing_symbols": [
      "GBPUSD",
      "EURUSD",
      "GBPJPY",
      "USDJPY"
    ],
    "active_ict_symbols": [
      "GBPJPY"
    ],
    "all_ten_sleeves_active": false
  },
  "activity": {
    "swing": {
      "trades": 490,
      "active_days": 408,
      "average_per_active_day": 1.201,
      "maximum_per_day": 4
    },
    "ict": {
      "trades": 91,
      "active_days": 91,
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
    "swing_scale": 0.8,
    "ict_scale": 2.4,
    "net_profit": 6539.904550109673,
    "ending_balance": 11539.904550109673,
    "return_percent": 130.79809100219347,
    "profit_factor": 1.6663397270978866,
    "max_closed_drawdown_percent": 7.45826715558135,
    "stress_drawdown_percent": 9.420151891883016,
    "closed_trades": 581,
    "skipped_trades": 0,
    "safe": true,
    "target_reached": false,
    "governor_interventions": 0
  },
  "profit_improvement_vs_v14_6_2": -1529.8,
  "target_reached": false,
  "target_gap": 13460.1,
  "output": "/home/runner/work/m5-bridge/m5-bridge/research/v14_7_1_five_symbol_20k_output"
}
```
