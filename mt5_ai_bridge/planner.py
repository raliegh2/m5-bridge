"""Trade planning: session-aware sizing, intraday/swing style rotation, and
staggered (laddered) SL/TP across pyramid levels.

``build_plan`` turns a strategy ``Decision`` (plus the current time and the
pyramid ``level``) into a ``TradePlan``. All functions are pure and unit-tested
without a broker.

Rules:
- Direction comes from the signal (BUY long / SELL short).
- Base size is ``base_lot``; during the New York session it is multiplied by
  ``ny_multiplier``.
- Style rotates by trend strength: confidence >= ``swing_confidence`` -> swing
  (wider SL/TP); otherwise intraday (tighter).
- Staggered exits: for the k-th position stacked into a trend, the take-profit
  widens (let later runners ride further) and the stop tightens (down to a
  floor) so the whole stack does not exit at one price.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from .enums import Signal


@dataclass(frozen=True)
class SessionConfig:
    ny_start_hour: int = 12
    ny_end_hour: int = 21


@dataclass(frozen=True)
class SizingConfig:
    base_lot: float = 0.09
    ny_multiplier: float = 2.0
    min_lot: float = 0.01
    lot_step: float = 0.01


@dataclass(frozen=True)
class StyleConfig:
    swing_confidence: float = 0.7
    intraday_sl_pips: float = 20.0
    intraday_tp_pips: float = 40.0
    swing_sl_pips: float = 80.0
    swing_tp_pips: float = 160.0


@dataclass(frozen=True)
class StaggerConfig:
    # Per extra pyramid level: TP grows by tp_step, SL shrinks by sl_step,
    # never below sl_floor pips.
    tp_step: float = 0.5
    sl_step: float = 0.25
    sl_floor: float = 10.0


@dataclass(frozen=True)
class TradePlan:
    side: Signal
    volume: float
    sl_pips: float
    tp_pips: float
    style: str
    session: str
    confidence: float
    level: int = 1

    def describe(self) -> str:
        return (f"{self.side.value} {self.volume} lots | {self.style} | "
                f"{self.session} | lvl {self.level} | "
                f"SL {self.sl_pips:g}/TP {self.tp_pips:g} pips | "
                f"conf={self.confidence}")


def is_ny_session(now_utc: datetime, cfg: SessionConfig) -> bool:
    return cfg.ny_start_hour <= now_utc.hour < cfg.ny_end_hour


def round_lot(volume: float, cfg: SizingConfig) -> float:
    steps = round(volume / cfg.lot_step)
    vol = steps * cfg.lot_step
    return round(max(vol, cfg.min_lot), 2)


def position_size(in_ny: bool, cfg: SizingConfig) -> float:
    raw = cfg.base_lot * (cfg.ny_multiplier if in_ny else 1.0)
    return round_lot(raw, cfg)


def choose_style(confidence: float, cfg: StyleConfig) -> Tuple[str, float, float]:
    if confidence >= cfg.swing_confidence:
        return "swing", cfg.swing_sl_pips, cfg.swing_tp_pips
    return "intraday", cfg.intraday_sl_pips, cfg.intraday_tp_pips


def stagger(sl_pips: float, tp_pips: float, level: int,
            cfg: StaggerConfig) -> Tuple[float, float]:
    """Ladder SL/TP for the k-th stacked position (level 1 = base, unchanged)."""
    steps = max(level - 1, 0)
    tp = tp_pips * (1 + steps * cfg.tp_step)
    sl = max(sl_pips * (1 - steps * cfg.sl_step), cfg.sl_floor)
    return round(sl, 1), round(tp, 1)


def build_plan(decision, now_utc: Optional[datetime] = None,
               session: SessionConfig = SessionConfig(),
               sizing: SizingConfig = SizingConfig(),
               style: StyleConfig = StyleConfig(),
               level: int = 1,
               stagger_cfg: StaggerConfig = StaggerConfig()) -> Optional[TradePlan]:
    """Turn a strategy Decision into a concrete TradePlan, or None."""
    if decision is None or not decision.signal.is_trade:
        return None

    now_utc = now_utc or datetime.now(timezone.utc)
    in_ny = is_ny_session(now_utc, session)
    style_name, base_sl, base_tp = choose_style(decision.confidence, style)
    sl_pips, tp_pips = stagger(base_sl, base_tp, level, stagger_cfg)
    volume = position_size(in_ny, sizing)

    return TradePlan(
        side=decision.signal,
        volume=volume,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        style=style_name,
        session="NY" if in_ny else "OFF",
        confidence=decision.confidence,
        level=level,
    )
