"""V17: a trading system composed from what the data actually shows.

This is not a twenty-seventh strategy. It is the same mean-reversion signal as
V16, with two changes, each forced by a measurement rather than chosen to make
a backtest look better.

**1. Only trade where the market mean-reverts.** V16's *gross* P&L is positive
on exactly the symbols whose variance ratio is below 1.0 and negative on those
above it -- AUDUSD (VR 0.926, +81), GBPUSD (0.934, +2,595), EURUSD (0.962,
+923) against GBPJPY (0.994, -4,637), USDJPY (1.011, -2,540), XAUUSD (1.031,
-3,300). Two independent measurements agreeing is worth acting on. The gate
uses VR < 1.0, the textbook boundary, not the observed flip near 0.97, because
fitting the gate to data already seen would defeat the point. It is recomputed
inside each fold on the training window only.

**2. Trade far less often.** Across both candidates, friction was roughly twice
any gross signal. Entry moves from 2.0 to 3.0 sigma, derived in
``research/v17_locked_system.json`` from the observed $1.005 gross against
$1.15 cost per trade: a wider stretch captures more sigma per trade while the
cost per trade is unchanged. The cost of this choice is far fewer trades, which
may itself fail the 200-trade gate.

Everything else -- lookback, exit, stop buffer, time stop, risk -- is inherited
from V16 unchanged, so V17 is one new specification and not a family.

Portfolio construction (volatility targeting, per-currency caps, concurrency)
comes from :mod:`mt5_ai_bridge.portfolio_v15`; with only 1.68 effective bets
among the USD majors, the currency cap is what stops this becoming one
leveraged USD position.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .candidate_v16 import ReversionConfig, add_bands
from .costs import ZERO_COST, CostModel
from .enums import Signal
from .instruments import instrument_for, quote_currency_of, settle
from .persistence import log_returns, variance_ratio
from .portfolio_v15 import (PortfolioConfig, PortfolioResult, PortfolioTrade,
                            currency_exposure)

__all__ = ["SystemConfig", "LOCKED_V17", "locked_system", "admit_by_persistence",
           "replay_system"]

LOCK_PATH = Path(__file__).resolve().parents[1] / "research" / "v17_locked_system.json"


@dataclass(frozen=True)
class SystemConfig(ReversionConfig):
    """V16's reversion parameters plus the persistence gate."""

    entry_z: float = 3.0
    stop_z: float = 5.0
    vr_horizon: int = 30
    vr_max: float = 1.0

    def validate(self) -> None:
        super().validate()
        if self.vr_horizon < 2:
            raise ValueError("vr_horizon must be at least 2")
        if not 0 < self.vr_max <= 1.5:
            raise ValueError("vr_max must be in (0, 1.5]")


LOCKED_V17 = SystemConfig()


def locked_system(path: Optional[Path] = None) -> SystemConfig:
    """Load the frozen system spec, refusing a file edited after the fact."""
    path = Path(path or LOCK_PATH)
    if not path.exists():
        raise FileNotFoundError(f"lock file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = SystemConfig(**payload.get("parameters", {}))
    cfg.validate()
    if cfg != LOCKED_V17:
        raise ValueError(
            f"lock file {path} disagrees with the code's LOCKED_V17 config.\n"
            f"  file: {asdict(cfg)}\n  code: {asdict(LOCKED_V17)}")
    return cfg


def admit_by_persistence(bars_by_symbol: Dict[str, pd.DataFrame],
                         cfg: SystemConfig = LOCKED_V17,
                         upto: Optional[int] = None) -> tuple[List[str], dict]:
    """Symbols whose measured variance ratio says they mean-revert.

    ``upto`` bounds the measurement to the training window, so a fold never
    admits a symbol on the strength of data it is about to be scored on.
    """
    admitted, detail = [], {}
    for symbol, bars in bars_by_symbol.items():
        window = bars.iloc[:upto] if upto else bars
        try:
            vr = variance_ratio(log_returns(window["close"].tolist()),
                                cfg.vr_horizon)
        except ValueError as exc:
            detail[symbol] = {"admitted": False, "reason": str(exc)}
            continue
        ok = vr.ratio < cfg.vr_max
        detail[symbol] = {"admitted": ok, "vr": round(vr.ratio, 4),
                          "p": round(vr.p_value, 4),
                          "reason": ("mean-reverting" if ok
                                     else f"VR {vr.ratio:.3f} >= {cfg.vr_max}")}
        if ok:
            admitted.append(symbol)
    return sorted(admitted), detail


def replay_system(bars_by_symbol: Dict[str, pd.DataFrame],
                  cfg: SystemConfig = LOCKED_V17,
                  portfolio: PortfolioConfig = PortfolioConfig(),
                  costs: Optional[Dict[str, CostModel]] = None,
                  starting_balance: float = 10_000.0,
                  admitted: Optional[Sequence[str]] = None,
                  converters: Optional[Dict[str, object]] = None
                  ) -> PortfolioResult:
    """Replay the V17 system across symbols on one shared account.

    ``costs`` maps symbol -> CostModel so each instrument pays its own spread
    (gold's is 30 of its pips, not 0.9). Symbols are stepped on a shared
    timeline so the risk caps see genuinely concurrent positions.
    """
    cfg.validate()
    portfolio.validate()

    symbols = [s for s in bars_by_symbol
               if admitted is None or s in set(admitted)]
    if not symbols:
        return PortfolioResult([], starting_balance, starting_balance, [])

    converters = converters or {}
    costs = costs or {}
    instruments = {s: instrument_for(s, converters.get(quote_currency_of(s)))
                   for s in symbols}

    prepared = {}
    for s in symbols:
        df = add_bands(bars_by_symbol[s], cfg)
        prepared[s] = df.set_index(df["time"].astype("int64"), drop=False)

    timeline = sorted({int(t) for df in prepared.values()
                       for t in df["time"].astype("int64")})

    balance = starting_balance
    equity_curve: List[float] = []
    trades: List[PortfolioTrade] = []
    open_positions: Dict[str, dict] = {}
    rejected = 0

    def row_at(symbol, now):
        df = prepared[symbol]
        if now not in df.index:
            return None
        row = df.loc[now]
        return row.iloc[0] if isinstance(row, pd.DataFrame) else row

    for now in timeline:
        # --- manage open positions ---
        for symbol in list(open_positions):
            row = row_at(symbol, now)
            if row is None:
                continue
            pos = open_positions[symbol]
            pos["bars_held"] += 1
            z = row["z"]
            side = pos["side"]

            exit_price = reason = None
            if side is Signal.BUY:
                if row["low"] <= pos["stop"]:
                    exit_price, reason = pos["stop"], "STOP"
                elif np.isfinite(z) and z >= -cfg.exit_z:
                    exit_price, reason = float(row["close"]), "REVERTED"
            else:
                if row["high"] >= pos["stop"]:
                    exit_price, reason = pos["stop"], "STOP"
                elif np.isfinite(z) and z <= cfg.exit_z:
                    exit_price, reason = float(row["close"]), "REVERTED"
            if exit_price is None and pos["bars_held"] >= cfg.max_holding_bars:
                exit_price, reason = float(row["close"]), "TIME"
            if exit_price is None:
                continue

            inst = instruments[symbol]
            nights = max(0, int((now - pos["entry_time"]) // 86_400))
            gross, trade_cost = settle(
                inst, side, pos["lots"], pos["entry"], float(exit_price),
                nights, costs.get(symbol, ZERO_COST), now)
            profit = gross - trade_cost
            balance += profit
            trades.append(PortfolioTrade(
                symbol=symbol, entry_time=pos["entry_time"], exit_time=now,
                side=side, entry=pos["entry"], exit=float(exit_price),
                lots=pos["lots"], profit=round(profit, 2),
                cost=round(trade_cost, 2), reason=reason))
            del open_positions[symbol]

        # --- consider new entries ---
        for symbol in symbols:
            if symbol in open_positions:
                continue
            if len(open_positions) >= portfolio.max_concurrent_positions:
                break
            row = row_at(symbol, now)
            if row is None:
                continue

            inst = instruments[symbol]
            atr, sd, z = row["atr"], row["sd"], row["z"]
            if not (np.isfinite(atr) and atr > 0 and np.isfinite(sd)
                    and sd > 0 and np.isfinite(z)):
                continue
            if atr / inst.pip < cfg.min_atr_pips:
                continue

            want = None
            if z <= -cfg.entry_z:
                want = Signal.BUY
            elif z >= cfg.entry_z:
                want = Signal.SELL
            if want is None:
                continue

            risk_pct = portfolio.risk_percent_per_trade
            open_risk = sum(p["risk_percent"] for p in open_positions.values())
            if open_risk + risk_pct > portfolio.max_total_risk_percent + 1e-9:
                rejected += 1
                continue

            base, quote = currency_exposure(symbol)
            ccy: Dict[str, float] = {}
            for p in open_positions.values():
                b, q = currency_exposure(p["symbol"])
                ccy[b] = ccy.get(b, 0.0) + p["risk_percent"]
                ccy[q] = ccy.get(q, 0.0) + p["risk_percent"]
            cap = portfolio.max_currency_risk_percent
            if (ccy.get(base, 0.0) + risk_pct > cap + 1e-9
                    or ccy.get(quote, 0.0) + risk_pct > cap + 1e-9):
                rejected += 1
                continue

            stop_distance = (cfg.stop_z - cfg.entry_z) * float(sd)
            if stop_distance <= 0:
                continue
            stop_pips = stop_distance / inst.pip
            pip_value_usd = inst.pip_value_per_lot_at(now)
            if portfolio.volatility_target:
                lots = (balance * (risk_pct / 100.0)) / (stop_pips * pip_value_usd)
            else:
                lots = cfg.risk_percent / 100.0
            lots = max(0.01, round(lots, 2))

            entry = float(row["close"])
            open_positions[symbol] = {
                "symbol": symbol, "side": want, "entry": entry,
                "entry_time": now, "lots": lots, "risk_percent": risk_pct,
                "bars_held": 0,
                "stop": (entry - stop_distance if want is Signal.BUY
                         else entry + stop_distance),
            }

        # --- mark to market ---
        floating = 0.0
        for symbol, pos in open_positions.items():
            row = row_at(symbol, now)
            if row is None:
                continue
            inst = instruments[symbol]
            direction = 1.0 if pos["side"] is Signal.BUY else -1.0
            unrealised = (direction * (float(row["close"]) - pos["entry"])
                          * pos["lots"] * inst.contract_size)
            floating += inst.to_usd(unrealised, now)
        equity_curve.append(round(balance + floating, 2))

    # --- close anything still open ---
    for symbol, pos in list(open_positions.items()):
        last = prepared[symbol].iloc[-1]
        when = int(last["time"])
        inst = instruments[symbol]
        nights = max(0, int((when - pos["entry_time"]) // 86_400))
        gross, trade_cost = settle(
            inst, pos["side"], pos["lots"], pos["entry"], float(last["close"]),
            nights, costs.get(symbol, ZERO_COST), when)
        profit = gross - trade_cost
        balance += profit
        trades.append(PortfolioTrade(
            symbol=symbol, entry_time=pos["entry_time"], exit_time=when,
            side=pos["side"], entry=pos["entry"], exit=float(last["close"]),
            lots=pos["lots"], profit=round(profit, 2),
            cost=round(trade_cost, 2), reason="EOD"))

    trades.sort(key=lambda t: t.exit_time)
    return PortfolioResult(trades=trades, starting_balance=starting_balance,
                           final_balance=round(balance, 2),
                           equity_curve=equity_curve,
                           rejected_for_risk=rejected)
