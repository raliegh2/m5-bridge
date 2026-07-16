# V14.4 Live Profit Guard

Additive protection layer over the V14.3 research-risk parity bot. No
V14.3 frozen profile, signal rule, or admission value is modified; every
guard can only make the bot trade smaller or skip a trade, never larger.

## Why this exists (July 2026 demo forward-test diagnosis)

The demo account (login 109009028) lost about $2,660 (2.66%) in the first
~18 hours of AUTO forward testing: 12 fills, all from the GBP ICT M1 scalp
engine. Findings from `v14_3_satellite_live_executions.jsonl` and the live
state:

1. **Cost-blind economics.** The 10-year benchmark simulated exits as pure
   R multiples with zero spread/commission/slippage, and its edge is thin
   (portfolio PF 1.18, GBP ICT PF 1.158, average trade +$7). Live GBP ICT
   trades use 5-7.5 pip stops with 1.25R targets. At the implied 48.1%
   win rate, about **0.4 pips of round-trip cost erases the entire edge**
   (`python research/v14_4_cost_stress_report.py`). Fills were observed
   with up to 1.1 pips of spread on a 6-pip stop.
2. **Blind drawdown governor.** The live state file had been reset:
   `peak_equity` was 97,414 while the true peak was ~100,000, so the
   governor and continuous ICT scale saw ~0% drawdown instead of 2.7%,
   and post-loss counters were wiped.
3. **Disabled portfolio daily stop.** The parity config sets
   `daily_account_loss_limit_percent = 100.0`; only per-symbol caps were
   active while one engine family (GBP ICT) took every trade.
4. **Stale M1 fill.** One breakout_60_fade filled 52 minutes after its M1
   signal — inside the shared 90-minute limit designed for H1 engines, but
   meaningless for a 1-minute sweep/reclaim pattern.
5. **Out-of-sample drift risk.** The benchmark data ends March 2022. The
   locked filters (Tuesday exclusions, per-setup risk to three decimals)
   are running four years out of sample with no live feedback loop.

## What V14.4 adds

| Guard | Default | Effect |
| --- | --- | --- |
| Spread cost gate | spread ≤ 10% of stop distance | Rejects entries whose live spread consumes the researched edge (`V14_4_SPREAD_COST_GUARD`). |
| M1 staleness limit | 5 minutes | M1 scalp signals older than this are rejected (`STALE_M1_SIGNAL`); H1/H4 engines keep the shared 90-minute rule. |
| Daily loss stop | 1.50% of day-start equity | No new entries for the rest of the UTC day once breached (`V14_4_DAILY_LOSS_STOP`). |
| Live expectancy tiers | reduce at −4R, observe at −8R over last 20 closed trades (min 8 trades) | A bleeding setup trades at 50% risk; a persistently negative setup is demoted to 0.025% observation risk until its rolling window recovers. |
| Peak-equity seeding | 120 days of deal history | On first run, the balance peak is reconstructed from broker history so a reset state file cannot blind the drawdown governor. |

All thresholds are environment variables — see
`.env.v14-4-profit-guard.example`.

## Running

```
Start-V14-4-Satellite.bat
```

Uses its own state file (`state/v14_4_profit_guard_live_state.json`), the
same preflight, dashboard, broker-compatibility and reconciliation stack,
and the same demo-only execution gates as V14.3. Start in READ_ONLY and
verify `V14_4_*` rejection codes appear in the executions JSONL before
enabling AUTO.

## Honest expectations

These guards remove the identified live bleed mechanisms: cost-toxic
fills, stale scalps, uncapped daily sequences, and a blind governor. They
do **not** create edge. If the GBP ICT rules have decayed since March
2022, the expectancy tracker will progressively demote those setups to
observation risk — which caps the damage at micro size but also means the
bot may trade very little. That outcome is information, not failure: it
tells you which setups need re-research before risking more. Re-validate
the backtest with realistic per-trade costs before increasing any risk
tier.
