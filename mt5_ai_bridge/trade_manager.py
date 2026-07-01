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
