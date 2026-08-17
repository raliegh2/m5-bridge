# On a $4,802 account

Date: 2026-08-16

Every backtest in this repository assumed **$10,000**. The live account holds
**$4,802.43**. That was my error, and it is not cosmetic — position size is
bounded below by the broker's minimum lot, so on a small account the smallest
trade the broker will accept can exceed the risk the model intends.

## Corrected results

Ten years, H4, tight costs, locked parameters, re-run at the real balance:

| model | symbol | net $ | CAGR | max DD | PF |
|---|---|---:|---:|---:|---:|
| **V16** | **AUDUSD** | **+337.52** | **+0.68%** | 16.83% | 1.039 |
| V19 | EURUSD | +98.41 | +0.20% | 16.08% | 1.017 |
| V19 | GBPUSD | −115.51 | −0.24% | 13.18% | 0.980 |
| V16 | EURUSD | −229.53 | −0.49% | 29.16% | 0.972 |
| V16 | GBPUSD | −276.79 | −0.59% | 13.89% | 0.969 |
| V19 | AUDUSD | −490.02 | −1.07% | 15.04% | 0.903 |

Against the previously reported $10,000 figures, the best case moves from
+$905 (0.87% CAGR, 15.54% DD) to **+$337.52 (0.68% CAGR, 16.83% DD)**. Return
falls and drawdown rises, because on a smaller account each position rounds
more coarsely against the risk budget, making risk lumpier.

**In plain terms: the single best configuration found across this entire
investigation returns about $34 a year on your account** — roughly £2.80 a
month — earned over 904 trades, with the account down ~$808 at its worst point.

## What a $4,802 account can and cannot size

Intended risk 0.5% = **$24.01 per trade**. Minimum position, and the risk it
forces:

| symbol | tradable | min lot | exposure | % of account | min risk | vs budget |
|---|---|---:|---:|---:|---:|---|
| EURUSD | yes | 0.01 | $1,157 | 24% | $19.04 | ok |
| GBPUSD | yes | 0.01 | $1,353 | 28% | $23.22 | ok |
| AUDUSD | yes | 0.01 | $708 | 15% | $13.93 | ok |
| **XAUUSD** | yes | 0.01 | **$4,376** | **91%** | **$38.40** | **1.6× over** |
| ONEQ | yes | 1 | $105 | 2% | $3.60 | ok |
| IVV | yes | 1 | $780 | 16% | $5.05 | ok |
| IWM | yes | 1 | $305 | 6% | $4.08 | ok |
| TQQQ | yes | 1 | $77 | 2% | $8.06 | ok |
| EEM | yes | 1 | $67 | 1% | $1.84 | ok |
| US30 | **no** | 0.10 | $5,366 | **112%** | $62.08 | 2.6× over |
| USTEC | **no** | 0.10 | $3,010 | 63% | $69.40 | 2.9× over |

**Gold is not tradable at proper risk on this account.** One minimum position
is 91% of the balance in notional, and its minimum risk is 1.6× the intended
budget. On any instrument where the minimum exceeds the budget the risk
engine's sizing is a fiction — every position is forced above target, so the
10% drawdown ceiling it computes will not hold. The engine is correct; the
account is too small for that instrument.

The indices would be worse still — US30's minimum position is 112% of the
balance — but they are disabled anyway (see `TRADABILITY.md`).

**Sizable at 0.5%:** EURUSD, GBPUSD, AUDUSD, and the ETFs ONEQ, IVV, IWM, VTI,
TQQQ, EEM.

## The wider point about account size

Even a genuinely good strategy returns trivial absolute money here. At 0.5%
risk per trade, a $4,802 account risks $24 a trade. A strategy compounding at a
strong 10% a year — far beyond anything measured in this repository — returns
about **$480 a year**. The best thing actually measured returns **$34**.

Two structural consequences worth being aware of:

1. **Diversification is limited.** The conservative risk profile allows three
   concurrent positions at 0.75% each. On $4,802 that is fine arithmetically,
   but the minimum-lot floor means the smallest instruments dominate what can
   actually be held.
2. **Costs are proportional, not fixed**, so they do not punish a small account
   specifically. Spread scales with lot size. The problem is not that costs are
   worse here — it is that the returns are small in absolute terms regardless.

## A bug this found

Re-running at the new balance first produced a positive CAGR on a negative net
profit — arithmetically impossible. Cause: `equity_curve(trades,
start=START_BALANCE)` bound its default at *definition* time, so the curve was
rebuilt from $10,000 while the CAGR divided by $4,802. `start` is now a
required argument.

Worth noting because it is the same failure mode as the gold contract size, the
JPY conversion and the index commission: **a value assumed in one place and
overridden in another, with nothing checking they agree.**

## Files

| file | purpose |
|---|---|
| `research/small_account_check.py` | minimum position vs risk budget, per instrument |
| `research/small_account.json` | the sizing table above |
| `research/ten_year_5k.json` | ten-year results at the real balance |
| `research/ten_year_profile.py` | now takes `--balance` |

856 tests pass.
