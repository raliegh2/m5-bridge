"""Regime router: Kaufman Efficiency Ratio (ER) directional / range classifier.

Per the FX & Metals Optimization Playbook, a trend-following system has an edge
in DIRECTIONAL regimes and gets whipsawed in RANGE regimes. ER measures how
"clean" recent movement is:

    ER(n) = |close[t] - close[t-n]| / sum(|close[i] - close[i-1]|)

ER approaches 1.0 for efficient directional movement and 0.0 for noisy chop.

This module is a pure classifier (no broker, no config) so it is trivially
testable. It is deliberately conservative: when ER is unknown (insufficient
data) trading is ALLOWED, so a data gap never silently halts the bot.

IMPORTANT: an ER gate is a hypothesis, not a guaranteed edge. On this repo's
own gold data a naive ER gate did NOT improve a raw trend signal after costs,
so the live gate ships OFF by default (REGIME_FILTER) and must be validated
out-of-sample before being relied upon.
"""

from typing import List, Optional


def efficiency_ratio(closes: List[float], period: int = 20) -> Optional[float]:
    """Kaufman Efficiency Ratio over the last ``period`` closes.

    Returns None when there are not enough closes. Returns 0.0 when the path
    length is zero (a flat series).
    """
    if closes is None or len(closes) <= period:
        return None
    window = closes[-(period + 1):]
    net = abs(window[-1] - window[0])
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path <= 0:
        return 0.0
    return net / path


def classify(er: Optional[float], directional_min: float = 0.35,
             range_max: float = 0.20) -> str:
    """Label the regime: 'directional' / 'range' / 'unclear' / 'unknown'."""
    if er is None:
        return "unknown"
    if er >= directional_min:
        return "directional"
    if er < range_max:
        return "range"
    return "unclear"


def trend_allowed(er: Optional[float], threshold: float) -> bool:
    """Whether trend engines may trade: True unless the market is clearly
    ranging (ER below ``threshold``). Unknown ER -> allowed (never block on a
    data gap)."""
    return er is None or er >= threshold
