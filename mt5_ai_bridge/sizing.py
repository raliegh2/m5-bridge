"""Volatility-adaptive stops and fixed-fractional position sizing.

Grounded in standard professional practice:
- ATR-based stops: stop distance = ATR x multiplier (wider when volatile). Each
  book uses its own timeframe's ATR, so swing (H4/D1) naturally gets wide stops
  and scalp (M5) tight ones.
- The 1-2% rule (fixed-fractional sizing): risk a fixed small fraction of the
  account per trade and derive the lot size FROM the stop distance, so risk per
  trade stays constant no matter how wide the ATR stop is.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class AtrConfig:
    enabled: bool = True
    period: int = 14
    sl_mult: float = 2.0        # ~2x ATR is a common sweet spot
    tp_mult: float = 4.0        # 1:2 reward:risk (tp = 2x sl distance)
    min_sl_pips: float = 8.0
    max_sl_pips: float = 200.0


@dataclass(frozen=True)
class RiskConfig:
    enabled: bool = True
    risk_percent: float = 0.5       # % of balance risked per trade
    pip_value_per_lot: float = 10.0  # $/pip per 1.0 lot (GBPUSD, USD account)
    min_lot: float = 0.01
    max_lot: float = 2.0
    lot_step: float = 0.01


def atr_stops(atr_price: Optional[float], pip: float,
              cfg: AtrConfig) -> Optional[Tuple[float, float]]:
    """(sl_pips, tp_pips) from ATR in price units, or None if ATR unavailable."""
    if not atr_price or atr_price != atr_price or pip <= 0:  # None / NaN / bad pip
        return None
    atr_pips = atr_price / pip
    sl = min(max(atr_pips * cfg.sl_mult, cfg.min_sl_pips), cfg.max_sl_pips)
    tp = atr_pips * cfg.tp_mult
    return round(sl, 1), round(tp, 1)


def risk_lot(balance: float, sl_pips: float, cfg: RiskConfig) -> float:
    """Fixed-fractional lot: risk cfg.risk_percent of balance over sl_pips."""
    if sl_pips <= 0 or cfg.pip_value_per_lot <= 0:
        return cfg.min_lot
    risk_amount = balance * (cfg.risk_percent / 100.0)
    raw = risk_amount / (sl_pips * cfg.pip_value_per_lot)
    steps = round(raw / cfg.lot_step)
    lot = steps * cfg.lot_step
    return round(min(max(lot, cfg.min_lot), cfg.max_lot), 2)
