"""Plain-English explanation of a timeframe's read.

Turns a market snapshot (the dict from ``indicators.market_snapshot``) into a
short, beginner-friendly sentence describing WHY the timeframe looks bullish or
bearish — using ONLY values the bot actually computes. Nothing is invented: a
factor whose inputs are missing is simply skipped, and if nothing is available
the function says so rather than faking a reason.

Example:
    "price below EMA 200, EMA 50 below EMA 200, MACD below zero,
     bearish momentum, RSI 31."
"""

from typing import List, Optional

UNAVAILABLE = "Reason unavailable"


def explain_market(market: Optional[dict]) -> str:
    """Return a human-readable 'why' for a timeframe snapshot.

    Only reports on indicators present in ``market``; returns a clear fallback
    when there is nothing to explain."""
    if not market:
        return f"{UNAVAILABLE} — no market data."

    close = market.get("close")
    ema20 = market.get("ema_20")
    ema50 = market.get("ema_50")
    ema200 = market.get("ema_200")
    macd = market.get("macd")
    macd_signal = market.get("macd_signal")
    macd_hist = market.get("macd_hist")
    rsi = market.get("rsi_14")

    parts: List[str] = []

    # Regime: where price sits relative to the long trend (EMA 200).
    if close is not None and ema200 is not None:
        parts.append("price above EMA 200" if close > ema200
                     else "price below EMA 200")

    # Trend structure: EMA 50 vs EMA 200.
    if ema50 is not None and ema200 is not None:
        parts.append("EMA 50 above EMA 200" if ema50 > ema200
                     else "EMA 50 below EMA 200")

    # Short-term: price vs EMA 20.
    if close is not None and ema20 is not None:
        parts.append("price above EMA 20" if close > ema20
                     else "price below EMA 20")

    # MACD relative to the zero line (bull/bear regime).
    if macd is not None:
        parts.append("MACD above zero" if macd > 0 else "MACD below zero")

    # MACD cross (direction turning).
    if macd is not None and macd_signal is not None:
        parts.append("MACD above signal" if macd > macd_signal
                     else "MACD below signal")

    # Momentum from the histogram.
    if macd_hist is not None:
        if macd_hist > 0:
            parts.append("bullish momentum")
        elif macd_hist < 0:
            parts.append("bearish momentum")
        else:
            parts.append("flat momentum")

    # Relative strength.
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")

    if not parts:
        return f"{UNAVAILABLE} — indicators not ready yet."

    return ", ".join(parts) + "."
