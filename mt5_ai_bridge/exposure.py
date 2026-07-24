"""Currency-factor exposure decomposition and caps.

A book that looks diversified by SYMBOL can be one concentrated bet by
CURRENCY: long EURUSD + long GBPUSD + long XAUUSD are all just SHORT USD, so a
dollar rally hits every leg at once. That is exactly how a "diversified" set of
longs turns into a single correlated drawdown.

This module decomposes open positions into per-currency NET risk and lets the
caller cap how much net risk may sit on any one currency, so correlated trades
are treated as the one bet they really are. Pure functions, no MT5 dependency.
"""

from typing import Iterable, Optional, Tuple

# Instruments that don't split cleanly 3+3 into currency legs.
_EXPLICIT_LEGS = {
    "XAUUSD": ("XAU", "USD"), "XAGUSD": ("XAG", "USD"),
    "XPTUSD": ("XPT", "USD"), "XPDUSD": ("XPD", "USD"),
    "XAUEUR": ("XAU", "EUR"), "XAGEUR": ("XAG", "EUR"),
}


def currency_legs(symbol: str) -> Optional[Tuple[str, str]]:
    """(base, quote) for a symbol, or None if it can't be parsed.

    Being LONG the symbol is long BASE / short QUOTE. Broker suffixes such as
    GBPUSD.r, EURUSD.pro or XAUUSD.m are tolerated by stripping non-letters and
    matching a known metal prefix before falling back to a 3+3 split.
    """
    if not symbol:
        return None
    s = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if s in _EXPLICIT_LEGS:
        return _EXPLICIT_LEGS[s]
    for prefix, legs in _EXPLICIT_LEGS.items():
        if s.startswith(prefix):
            return legs
    if len(s) >= 6:
        return (s[:3], s[3:6])
    return None


def factor_exposure(positions: Iterable) -> dict:
    """Net signed risk per currency across positions.

    Each item is ``(symbol, is_buy, risk)``. A long adds ``+risk`` to the base
    currency and ``-risk`` to the quote; a short does the reverse. Returns a
    dict currency -> net risk (positive = net long that currency). Positions
    whose symbol can't be parsed, or with zero/None risk, are ignored.
    """
    net: dict = {}
    for symbol, is_buy, risk in positions:
        legs = currency_legs(symbol)
        if legs is None or not risk:
            continue
        base, quote = legs
        sign = 1.0 if is_buy else -1.0
        net[base] = net.get(base, 0.0) + sign * float(risk)
        net[quote] = net.get(quote, 0.0) - sign * float(risk)
    return net


def projected_exposure(existing: Iterable, symbol: str, is_buy: bool,
                       risk: float) -> dict:
    """Factor exposure of ``existing`` plus one candidate position."""
    return factor_exposure(list(existing) + [(symbol, is_buy, risk)])


def breach(existing: Iterable, symbol: str, is_buy: bool, risk: float,
           cap: float) -> Optional[Tuple[str, float]]:
    """Would adding this position over-concentrate a single currency?

    Returns ``(currency, projected_net)`` for the worst offending leg if adding
    ``(symbol, is_buy, risk)`` pushes any currency's absolute net risk above
    ``cap``; otherwise ``None``. A non-positive (or None) cap disables the
    check. Only the two currencies this trade touches can newly breach, so
    those are the only ones tested.
    """
    if cap is None or cap <= 0:
        return None
    legs = currency_legs(symbol)
    if legs is None:
        return None
    proj = projected_exposure(existing, symbol, is_buy, risk)
    worst = None
    for ccy in legs:
        val = proj.get(ccy, 0.0)
        if abs(val) > cap + 1e-9:
            if worst is None or abs(val) > abs(worst[1]):
                worst = (ccy, round(val, 4))
    return worst
