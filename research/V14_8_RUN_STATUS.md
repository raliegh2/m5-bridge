# V14.8 workflow status

- Backtest outcome: success
- Validation outcome: success
- Commit: 7cc06cb4e8dfd574d6a642b751afcec7017904c7

```text
      "GBPJPY",
      "GBPUSD",
      "USDJPY"
    ],
    "all_ten_sleeves_active": true
  },
  "best_safe_portfolio": {
    "starting_balance": 5000.0,
    "ending_balance": 45361.938745907944,
    "net_profit": 40361.938745907944,
    "return_percent": 807.2387749181589,
    "closed_trades": 913,
    "skipped_ict_trades": 12,
    "profit_factor": 2.0407900225880424,
    "max_closed_drawdown_percent": 8.202575834043822,
    "stress_drawdown_percent": 9.950000000000006,
    "by_symbol": {
      "AUDUSD": {
        "trades": 231,
        "net": 4552.436708482855,
        "profit_factor": 1.5924367176319185,
        "wins": 109,
        "losses": 122
      },
      "EURUSD": {
        "trades": 140,
        "net": 6488.47254192746,
        "profit_factor": 2.0868696749790256,
        "wins": 75,
        "losses": 65
      },
      "GBPJPY": {
        "trades": 216,
        "net": 11854.606002135242,
        "profit_factor": 1.9128065865259565,
        "wins": 82,
        "losses": 134
      },
      "GBPUSD": {
        "trades": 94,
        "net": 15372.344082974125,
        "profit_factor": 5.891743878618257,
        "wins": 59,
        "losses": 35
      },
      "USDJPY": {
        "trades": 232,
        "net": 2094.0794103882863,
        "profit_factor": 1.232766678058347,
        "wins": 131,
        "losses": 101
      }
    },
    "by_engine": {
      "AUDUSD_ICT_ASIA_LONDON": {
        "trades": 133,
        "net": 2317.7864379426396,
        "profit_factor": 1.5756086454579148,
        "wins": 71,
        "losses": 62
      },
      "AUDUSD_TREND_PULLBACK": {
        "trades": 98,
        "net": 2234.650270540215,
        "profit_factor": 1.6109628918547922,
        "wins": 38,
        "losses": 60
      },
      "EURUSD_ICT_LIQUIDITY": {
        "trades": 50,
        "net": 4329.802121675379,
        "profit_factor": 3.550969456624652,
        "wins": 34,
        "losses": 16
      },
      "EURUSD_SWING_SWING_PULLBACK_20": {
        "trades": 90,
        "net": 2158.670420252082,
        "profit_factor": 1.5052410610411797,
        "wins": 41,
        "losses": 49
      },
      "GBPJPY_ICT_WIDE_SWEEP": {
        "trades": 67,
        "net": 5499.485533953132,
        "profit_factor": 2.817506725258104,
        "wins": 43,
        "losses": 24
      },
      "GBPJPY_SWING_CORE": {
        "trades": 149,
        "net": 6355.120468182111,
        "profit_factor": 1.6379909564420385,
        "wins": 39,
        "losses": 110
      },
      "GBPUSD_ICT_WIDE_SWEEP": {
        "trades": 54,
        "net": 6970.622169318406,
        "profit_factor": 6.310688896736011,
        "wins": 40,
        "losses": 14
      },
      "GBPUSD_V10_PRECISION": {
        "trades": 40,
        "net": 8401.72191365572,
        "profit_factor": 5.59124693363971,
        "wins": 19,
        "losses": 21
      },
      "USDJPY_ICT_ICT_BREAKOUT_H4": {
        "trades": 133,
        "net": 313.7146840776655,
        "profit_factor": 1.0921564200630867,
        "wins": 80,
        "losses": 53
      },
      "USDJPY_SWING_SWING_BREAKOUT_24": {
        "trades": 99,
        "net": 1780.3647263106202,
        "profit_factor": 1.318358891991997,
        "wins": 51,
        "losses": 48
      }
    },
    "by_engine_group": {
      "ICT": {
        "trades": 437,
        "net": 19431.41094696722,
        "profit_factor": 2.442939428907799,
        "wins": 268,
        "losses": 169
      },
      "V12": {
        "trades": 476,
        "net": 20930.52779894075,
        "profit_factor": 1.8268507118335184,
        "wins": 188,
        "losses": 288
      }
    },
    "skip_reasons": {
      "COMBINED_OPEN_RISK_CAP": 8,
      "PROJECTED_STRESS_DRAWDOWN_CAP": 3,
      "SYMBOL_OPEN_POSITION_LIMIT": 1
    },
    "drawdown_governor": {
      "soft_start_percent": 7.5,
      "medium_start_percent": 8.5,
      "defensive_start_percent": 9.0,
      "hard_stop_percent": 9.6,
      "soft_multiplier": 0.98,
      "medium_multiplier": 0.82,
      "defensive_multiplier": 0.5,
      "minimum_risk_percent": 0.025
    },
    "projected_stress_governor": {
      "maximum_stress_drawdown_percent": 9.95,
      "minimum_trade_risk_percent": 0.025
    },
    "projected_stress_interventions": 2,
    "safe": true,
    "target_reached": true
  },
  "target_reached": true,
  "target_margin": 20361.94,
  "attribution": {
    "AUDUSD/ICT": {
      "trades": 133,
      "net_profit": 2317.79,
      "profit_factor": 1.575609
    },
    "AUDUSD/V12": {
      "trades": 98,
      "net_profit": 2234.65,
      "profit_factor": 1.610963
    },
    "EURUSD/ICT": {
      "trades": 50,
      "net_profit": 4329.8,
      "profit_factor": 3.550969
    },
    "EURUSD/V12": {
      "trades": 90,
      "net_profit": 2158.67,
      "profit_factor": 1.505241
    },
    "GBPJPY/ICT": {
      "trades": 67,
      "net_profit": 5499.49,
      "profit_factor": 2.817507
    },
    "GBPJPY/V12": {
      "trades": 149,
      "net_profit": 6355.12,
      "profit_factor": 1.637991
    },
    "GBPUSD/ICT": {
      "trades": 54,
      "net_profit": 6970.62,
      "profit_factor": 6.310689
    },
    "GBPUSD/V12": {
      "trades": 40,
      "net_profit": 8401.72,
      "profit_factor": 5.591247
    },
    "USDJPY/ICT": {
      "trades": 133,
      "net_profit": 313.71,
      "profit_factor": 1.092156
    },
    "USDJPY/V12": {
      "trades": 99,
      "net_profit": 1780.36,
      "profit_factor": 1.318359
    }
  },
  "output": "/home/runner/work/m5-bridge/m5-bridge/research/v14_8_strict_all_ten_output"
}
```
