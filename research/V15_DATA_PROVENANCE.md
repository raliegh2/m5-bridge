# V15 Backtest Data Provenance

No AUDUSD or USDJPY backtest was uploaded by the user before the expanded-asset research. The signal histories were generated from public OHLC CSV files obtained through the connected GitHub repository `ejtraderLabs/historical-data`.

The research generator used completed H4 and D1 candle information for GBPUSD, EURUSD, GBPJPY, AUDUSD and USDJPY. It produced deterministic candidate trades from the frozen strategy rules. V15 then consumed the frozen V14 candidate ledger rather than inventing a second set of trades.

Frozen V14 ledger:

- File: `all_candidates_improved.csv`
- Candidates: 1,613
- SHA-256: `84a749c8735d1a94ea9f44445aa8153c533a9090938ba77849c22d90c5e7d101`

The common public history runs from 26 November 2012 through 4 March 2022. This is third-party OHLC data, not a user-uploaded MT5 Strategy Tester report or broker tick export. The replay does not reproduce broker-specific spread variation, slippage, commissions, swaps, partial fills or exact intrabar stop/target ordering. Results are research evidence, not guaranteed live income.
