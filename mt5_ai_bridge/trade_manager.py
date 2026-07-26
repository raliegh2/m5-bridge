"""Position management: closing trades and trailing stops via the client."""

from typing import List, Optional, Tuple

MAGIC = 20260629
COMMENT = "MT5 AI Bridge Close"


def close_position(client, ticket: int) -> Tuple[bool, str]:
    position = client.positions_get(ticket=ticket)

    if not position:
        return False, "Position not found."

    position = position[0]
    symbol = position.symbol
    volume = position.volume
    tick = client.symbol_info_tick(symbol)

    if tick is None:
        return False, "No tick data available."

    if position.type == client.POSITION_TYPE_BUY:
        order_type = client.ORDER_TYPE_SELL
        price = tick.bid
    else:
        order_type = client.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": client.TRADE_ACTION_DEAL,
        "position": ticket,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": MAGIC,
        "comment": COMMENT,
    }

    result = client.order_send(request)

    if result is None:
        return False, f"Close failed: {client.last_error()}"
    if result.retcode != client.TRADE_RETCODE_DONE:
        return False, f"Close rejected: {result.retcode} - {result.comment}"

    return True, f"Position closed successfully. Ticket: {ticket}"


def close_all_positions(client, symbol: Optional[str] = None
                        ) -> List[Tuple[int, bool, str]]:
    """Close every open position (optionally only for one symbol)."""
    positions = client.positions_get(symbol=symbol) if symbol \
        else client.positions_get()
    positions = positions or []

    results: List[Tuple[int, bool, str]] = []
    for p in positions:
        ok, message = close_position(client, p.ticket)
        results.append((p.ticket, ok, message))
    return results


# --------------------------------------------------------------------------
# Trailing stop
# --------------------------------------------------------------------------

def trailing_sl(is_buy: bool, entry: float, current: float,
                current_sl: float, pip: float, start_pips: float,
                distance_pips: float) -> Optional[float]:
    """Return a new (tighter) stop-loss price, or None to leave it alone.

    The stop only ever moves in the favourable direction once the position is
    at least ``start_pips`` in profit, trailing ``distance_pips`` behind price.
    """
    if pip <= 0:
        return None

    if is_buy:
        profit_pips = (current - entry) / pip
        if profit_pips < start_pips:
            return None
        new_sl = round(current - distance_pips * pip, 5)
        if current_sl and new_sl <= current_sl:   # never loosen
            return None
        return new_sl

    profit_pips = (entry - current) / pip
    if profit_pips < start_pips:
        return None
    new_sl = round(current + distance_pips * pip, 5)
    if current_sl and new_sl >= current_sl:
        return None
    return new_sl


def modify_position_sl(client, position, new_sl: float) -> Tuple[bool, str]:
    """Move a position's stop-loss (keeps its take-profit)."""
    request = {
        "action": client.TRADE_ACTION_SLTP,
        "position": position.ticket,
        "symbol": position.symbol,
        "sl": round(new_sl, 5),
        "tp": getattr(position, "tp", 0.0) or 0.0,
    }
    result = client.order_send(request)

    if result is None:
        return False, f"Modify failed: {client.last_error()}"
    if result.retcode != client.TRADE_RETCODE_DONE:
        return False, f"Modify rejected: {result.retcode} - {result.comment}"
    return True, f"SL -> {new_sl:.5f} on ticket {position.ticket}"


# --------------------------------------------------------------------------
# Trade-lifecycle management (breakeven + partial profit)
#
# These are the deterministic primitives the per-symbol Trade Manager agent
# acts THROUGH: the agent decides WHEN (from momentum), but the maths, the
# guardrails, and the order sends live here so they stay pure and testable and
# the agent can never size a trade UP or invent a worse-than-breakeven stop.
# --------------------------------------------------------------------------

def profit_pips(is_buy: bool, entry: float, current: float, pip: float) -> float:
    """Signed profit of an open position in pips (negative when underwater)."""
    if pip <= 0:
        return 0.0
    return ((current - entry) if is_buy else (entry - current)) / pip


def breakeven_sl(is_buy: bool, entry: float, current: float, current_sl: float,
                 pip: float, trigger_pips: float,
                 offset_pips: float = 0.0) -> Optional[float]:
    """Deterministic breakeven FLOOR (the rule safety net).

    Once the position is at least ``trigger_pips`` in profit, return a stop at
    entry (+ ``offset_pips`` in the favourable direction, e.g. to cover spread)
    -- but only if that is TIGHTER than the current stop. Returns None when the
    trade has not yet earned breakeven or the stop is already at/through it, so
    it only ever moves risk DOWN, never up.
    """
    if pip <= 0 or profit_pips(is_buy, entry, current, pip) < trigger_pips:
        return None
    if is_buy:
        new_sl = round(entry + offset_pips * pip, 5)
        if current_sl and new_sl <= current_sl:   # already at/better than BE
            return None
        return new_sl
    new_sl = round(entry - offset_pips * pip, 5)
    if current_sl and new_sl >= current_sl:
        return None
    return new_sl


def clamp_partial_lots(volume: float, fraction: float, min_lot: float = 0.01,
                       lot_step: float = 0.01) -> float:
    """How many lots to close for a requested partial, guardrailed.

    Bounds ``fraction`` to (0, 1], snaps to ``lot_step``, respects ``min_lot``,
    and never leaves a dangling remainder below ``min_lot`` (it closes the whole
    position instead). Returns 0.0 when a partial is not sensible (position at
    or below the minimum tradable size). This is the guard that keeps an agent's
    'take 30%' from ever producing an invalid or risk-increasing order.
    """
    if volume <= 0 or fraction <= 0:
        return 0.0
    frac = min(float(fraction), 1.0)
    if volume <= min_lot:            # too small to split -> all or nothing
        return round(volume, 2) if frac >= 1.0 else 0.0
    raw = volume * frac
    # snap down to the broker lot step
    steps = max(1, int(round(raw / lot_step)))
    lots = round(steps * lot_step, 2)
    lots = max(min_lot, min(lots, volume))
    if volume - lots < min_lot:      # would strand an untradable remainder
        return round(volume, 2)
    return lots


def partial_close(client, position, fraction: float, min_lot: float = 0.01,
                  lot_step: float = 0.01) -> Tuple[bool, str, float]:
    """Close part of a position at market. Returns (ok, message, lots_closed).

    Volume is guardrailed by ``clamp_partial_lots`` first, so this never sends
    an order larger than the position or smaller than the broker minimum.
    """
    lots = clamp_partial_lots(position.volume, fraction, min_lot, lot_step)
    if lots <= 0:
        return False, "Partial skipped: position too small to split.", 0.0
    if lots >= round(position.volume, 2):
        ok, msg = close_position(client, position.ticket)
        return ok, f"Full close (partial rounded to whole): {msg}", (
            position.volume if ok else 0.0)

    tick = client.symbol_info_tick(position.symbol)
    if tick is None:
        return False, "No tick data available.", 0.0
    if position.type == client.POSITION_TYPE_BUY:
        order_type, price = client.ORDER_TYPE_SELL, tick.bid
    else:
        order_type, price = client.ORDER_TYPE_BUY, tick.ask

    request = {
        "action": client.TRADE_ACTION_DEAL,
        "position": position.ticket,
        "symbol": position.symbol,
        "volume": lots,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": MAGIC,
        "comment": "MT5 AI Bridge Partial",
    }
    result = client.order_send(request)
    if result is None:
        return False, f"Partial failed: {client.last_error()}", 0.0
    if result.retcode != client.TRADE_RETCODE_DONE:
        return False, f"Partial rejected: {result.retcode} - {result.comment}", 0.0
    return True, f"Closed {lots:g} of {position.volume:g} on {position.ticket}", lots


def move_to_breakeven(client, position, pip: float, trigger_pips: float,
                      offset_pips: float = 0.0) -> Tuple[bool, str]:
    """Move a position's stop to breakeven if it has earned it (else no-op)."""
    is_buy = position.type == client.POSITION_TYPE_BUY
    entry = position.price_open
    current = getattr(position, "price_current", None)
    if current is None:
        tick = client.symbol_info_tick(position.symbol)
        current = (tick.bid if is_buy else tick.ask) if tick else entry
    new_sl = breakeven_sl(is_buy, entry, current, getattr(position, "sl", 0.0) or 0.0,
                          pip, trigger_pips, offset_pips)
    if new_sl is None:
        return False, "Breakeven not applicable yet."
    return modify_position_sl(client, position, new_sl)
