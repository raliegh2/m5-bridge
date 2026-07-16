# V14.6.3 raw intraday ensemble workflow status

- Backtest outcome: success
- Validation outcome: success
- Commit: 5dd6a26261ad9f40b735c506e198fd89c53eb8d3

```text
        {
          "engine": "AUDUSD_ICT_ASIA_LONDON",
          "side": "BUY",
          "hour": 9,
          "session": null,
          "excluded_weekday": null
        },
        {
          "engine": "AUDUSD_ICT_INTRADAY_AU_ASIA_LONDON_PULLBACK",
          "side": "BUY",
          "hour": 7,
          "session": null,
          "excluded_weekday": null
        },
        {
          "engine": "AUDUSD_ICT_ASIA_LONDON",
          "side": "SELL",
          "hour": 12,
          "session": null,
          "excluded_weekday": null
        }
      ],
      "evidence": {
        "development": {
          "trades": 57,
          "net_r": 9.532093,
          "expectancy_r": 0.16723,
          "profit_factor": 1.389388
        },
        "confirmation": {
          "trades": 31,
          "net_r": 7.224867,
          "expectancy_r": 0.23306,
          "profit_factor": 1.466384
        },
        "audit_a": {
          "trades": 18,
          "net_r": 5.387477,
          "expectancy_r": 0.299304,
          "profit_factor": 1.844946
        },
        "audit_b": {
          "trades": 20,
          "net_r": -0.086043,
          "expectancy_r": -0.004302,
          "profit_factor": 0.991173
        }
      },
      "total": {
        "trades": 126,
        "net_r": 22.058395,
        "expectancy_r": 0.175067,
        "profit_factor": 1.393236
      },
      "activity": {
        "trading_days": 126,
        "average_entries_per_active_day": 1.0,
        "p95_entries_per_active_day": 1.0,
        "maximum_entries_per_day": 1,
        "days_with_multiple_entries": 0
      },
      "candidate_count": 10575,
      "intraday_candidate_count": 9744,
      "diagnostic": {
        "full_pass": false,
        "components": [
          {
            "engine": "AUDUSD_ICT_ASIA_LONDON",
            "side": "SELL",
            "hour": 13,
            "session": null,
            "excluded_weekday": null
          },
          {
            "engine": "AUDUSD_ICT_ASIA_LONDON",
            "side": "BUY",
            "hour": 9,
            "session": null,
            "excluded_weekday": null
          },
          {
            "engine": "AUDUSD_ICT_INTRADAY_AU_ASIA_LONDON_PULLBACK",
            "side": "BUY",
            "hour": 7,
            "session": null,
            "excluded_weekday": null
          },
          {
            "engine": "AUDUSD_ICT_ASIA_LONDON",
            "side": "SELL",
            "hour": 12,
            "session": null,
            "excluded_weekday": null
          }
        ],
        "component_count": 4,
        "blocks": {
          "development": {
            "trades": 57,
            "net_r": 9.532093,
            "expectancy_r": 0.16723,
            "profit_factor": 1.389388
          },
          "confirmation": {
            "trades": 31,
            "net_r": 7.224867,
            "expectancy_r": 0.23306,
            "profit_factor": 1.466384
          },
          "audit_a": {
            "trades": 18,
            "net_r": 5.387477,
            "expectancy_r": 0.299304,
            "profit_factor": 1.844946
          },
          "audit_b": {
            "trades": 20,
            "net_r": -0.086043,
            "expectancy_r": -0.004302,
            "profit_factor": 0.991173
          }
        },
        "total": {
          "trades": 126,
          "net_r": 22.058395,
          "expectancy_r": 0.175067,
          "profit_factor": 1.393236
        }
      }
    }
  },
  "coverage": {
    "active_swing_symbols": [
      "AUDUSD",
      "EURUSD",
      "GBPJPY",
      "GBPUSD"
    ],
    "active_ict_symbols": [
      "EURUSD",
      "USDJPY"
    ],
    "all_three_failed_ict_symbols_fixed": false,
    "all_ten_sleeves_active": false
  },
  "baseline_v14_6": {
    "swing_scale": 0.9,
    "ict_scale": 1.5,
    "net_profit": 6282.968223828464,
    "ending_balance": 11282.968223828464,
    "return_percent": 125.6593644765693,
    "profit_factor": 1.9769245609508357,
    "max_closed_drawdown_percent": 8.505177779530758,
    "stress_drawdown_percent": 9.417577531124506,
    "closed_trades": 405,
    "skipped_trades": 42,
    "target_reached": false,
    "safe": true,
    "governor_interventions": 10
  },
  "best_safe_portfolio": {
    "swing_scale": 0.9,
    "ict_scale": 2.0,
    "net_profit": 6383.035643406654,
    "ending_balance": 11383.035643406654,
    "return_percent": 127.66071286813307,
    "profit_factor": 1.9356097038938038,
    "max_closed_drawdown_percent": 8.659772788672973,
    "stress_drawdown_percent": 9.469459106535764,
    "closed_trades": 405,
    "skipped_trades": 42,
    "safe": true,
    "target_reached": false,
    "governor_interventions": 13
  },
  "profit_improvement_vs_v14_6": 100.07,
  "target_reached": false,
  "target_gap": 27616.96,
  "output": "/home/runner/work/m5-bridge/m5-bridge/research/v14_6_3_raw_intraday_output"
}
```
