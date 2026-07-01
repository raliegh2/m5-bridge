"""Trend-following strategy.

Pure function of a market snapshot -> (Signal, reason, confidence). The
confidence score (0..1, based on how many conditions align) is groundwork for
the planned rule-based reasoning layer.
"""

from dataclasses import dataclass

from .enums import Signal


@dataclass(frozen=True)
class Decision:
    signal: Signal
    reason: str
    confidence: float

    # Tuple-like unpacking keeps backwards compatibility with callers that did
    # ``signal, reason = evaluate_strategy(...)``.
    def __iter__(self):
        yield self.signal
        yield self.reason


def evaluate_strategy(market: dict | None) -> Decision:
    if market is None:
        return Decision(Signal.WAIT, "No market data.", 0.0)

    bull_conditions = (
        market["ema_20"] > market["ema_50"],
        market["close"] > market["ema_20"],
        market["rsi_14"] > 55,
        market["macd"] > market["macd_signal"],
    )
    bear_conditions = (
        market["ema_20"] < market["ema_50"],
        market["close"] < market["ema_20"],
        market["rsi_14"] < 45,
        market["macd"] < market["macd_signal"],
    )

    if all(bull_conditions):
        return Decision(Signal.BUY, "Bullish trend confirmed.", 1.0)

    if all(bear_conditions):
        return Decision(Signal.SELL, "Bearish trend confirmed.", 1.0)

    # No full setup: report the strongest partial confluence as context.
    bull_score = sum(bull_conditions) / len(bull_conditions)
    bear_score = sum(bear_conditions) / len(bear_conditions)
    confidence = max(bull_score, bear_score)
    return Decision(Signal.WAIT, "No trade setup.", round(confidence, 2))
