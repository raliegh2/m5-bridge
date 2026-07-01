"""Rule-based reasoning layer.

A transparent, offline alternative to the simple trend strategy. Instead of a
single all-or-nothing boolean, it scores several independent indicator factors,
weights them into a bull/bear confidence, and only emits a trade when the
winning side clears a confidence threshold. It also *vetoes* trades into
overbought/oversold extremes.

It is a drop-in for ``strategy.evaluate_strategy``: ``ReasoningStrategy`` is
callable as ``strategy_fn(market) -> Decision`` and can be used directly by the
live loop and the backtester. No external API or network access.

The market dict is read defensively (``.get``) so factors whose inputs are
absent simply don't contribute, keeping the layer robust to partial snapshots.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .enums import Signal
from .strategy import Decision


@dataclass(frozen=True)
class ReasoningConfig:
    # Minimum confidence (0..1) for the winning side to produce a trade.
    threshold: float = 0.6

    # RSI zones.
    rsi_bull: float = 55.0
    rsi_bear: float = 45.0
    rsi_overbought: float = 75.0   # veto longs at/above this
    rsi_oversold: float = 25.0     # veto shorts at/below this

    # Factor weights.
    w_trend_fast: float = 1.0      # ema_20 vs ema_50
    w_trend_slow: float = 1.0      # ema_50 vs ema_200 (regime filter)
    w_price: float = 1.0           # close vs ema_20
    w_rsi: float = 1.0             # momentum zone
    w_macd_cross: float = 1.0      # macd vs signal
    w_macd_hist: float = 0.5       # histogram sign

    @property
    def total_weight(self) -> float:
        return (self.w_trend_fast + self.w_trend_slow + self.w_price +
                self.w_rsi + self.w_macd_cross + self.w_macd_hist)


@dataclass
class Scores:
    bull: float
    bear: float
    total_weight: float
    bull_reasons: List[str] = field(default_factory=list)
    bear_reasons: List[str] = field(default_factory=list)

    @property
    def bull_conf(self) -> float:
        return self.bull / self.total_weight if self.total_weight else 0.0

    @property
    def bear_conf(self) -> float:
        return self.bear / self.total_weight if self.total_weight else 0.0


def _vote(market: dict, a: str, b: str) -> int:
    """+1 if market[a] > market[b], -1 if <, 0 if equal/missing."""
    x, y = market.get(a), market.get(b)
    if x is None or y is None:
        return 0
    if x > y:
        return 1
    if x < y:
        return -1
    return 0


def score(market: dict, config: Optional[ReasoningConfig] = None) -> Scores:
    """Compute weighted bull/bear scores from indicator confluence."""
    cfg = config or ReasoningConfig()
    s = Scores(bull=0.0, bear=0.0, total_weight=cfg.total_weight)

    def add(vote: int, weight: float, label: str) -> None:
        if vote > 0:
            s.bull += weight
            s.bull_reasons.append(label)
        elif vote < 0:
            s.bear += weight
            s.bear_reasons.append(label)

    add(_vote(market, "ema_20", "ema_50"), cfg.w_trend_fast, "ema20/50 trend")
    add(_vote(market, "ema_50", "ema_200"), cfg.w_trend_slow, "ema50/200 regime")
    add(_vote(market, "close", "ema_20"), cfg.w_price, "price vs ema20")
    add(_vote(market, "macd", "macd_signal"), cfg.w_macd_cross, "macd cross")

    hist = market.get("macd_hist")
    if hist is not None:
        add(1 if hist > 0 else -1 if hist < 0 else 0, cfg.w_macd_hist, "macd hist")

    rsi = market.get("rsi_14")
    if rsi is not None:
        if rsi > cfg.rsi_bull:
            add(1, cfg.w_rsi, f"rsi {rsi:.0f}>{cfg.rsi_bull:.0f}")
        elif rsi < cfg.rsi_bear:
            add(-1, cfg.w_rsi, f"rsi {rsi:.0f}<{cfg.rsi_bear:.0f}")

    return s


class ReasoningStrategy:
    """Callable strategy_fn built on confluence scoring + veto."""

    def __init__(self, config: Optional[ReasoningConfig] = None) -> None:
        self.config = config or ReasoningConfig()

    def __call__(self, market: Optional[dict]) -> Decision:
        return reason(market, self.config)


def reason(market: Optional[dict],
           config: Optional[ReasoningConfig] = None) -> Decision:
    cfg = config or ReasoningConfig()
    if not market:
        return Decision(Signal.WAIT, "No market data.", 0.0)

    s = score(market, cfg)
    rsi = market.get("rsi_14")

    # Pick the stronger side, if it clears the threshold.
    if s.bull_conf > s.bear_conf and s.bull_conf >= cfg.threshold:
        if rsi is not None and rsi >= cfg.rsi_overbought:
            return Decision(Signal.WAIT,
                            f"Veto BUY: overbought (rsi={rsi:.0f}).",
                            round(s.bull_conf, 2))
        return Decision(Signal.BUY,
                        "Bull confluence: " + ", ".join(s.bull_reasons) + ".",
                        round(s.bull_conf, 2))

    if s.bear_conf > s.bull_conf and s.bear_conf >= cfg.threshold:
        if rsi is not None and rsi <= cfg.rsi_oversold:
            return Decision(Signal.WAIT,
                            f"Veto SELL: oversold (rsi={rsi:.0f}).",
                            round(s.bear_conf, 2))
        return Decision(Signal.SELL,
                        "Bear confluence: " + ", ".join(s.bear_reasons) + ".",
                        round(s.bear_conf, 2))

    conf = round(max(s.bull_conf, s.bear_conf), 2)
    return Decision(Signal.WAIT, f"No confident setup (conf={conf}).", conf)
