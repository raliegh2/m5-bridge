"""Order execution. Builds and submits market orders via the injected client."""

from typing import Optional, Tuple

from .enums import OrderSide

MAGIC = 20260629
COMMENT = "MT5 AI Bridge Demo"


def _coerce_side(order_type) -> OrderSide:
    """Accept an OrderSide, a Signal, or a string ('BUY'/'SELL')."""
    if isinstance(order_type, OrderSide):
        return order_type
    raw = getattr(order_type, "value", order_type)
    return OrderSide(str(raw).upper())


def pip_size(client, symbol: str) -> Optional[float]:
    info = client.symbol_info(symbol)
    if info is None:
        return None
    if info.digits in (3, 5):
        return info.point * 10
    return info.point


def place_market_order(client, symbol: str, order_type, volume: float,
                       stop_loss_pips: Optional[float] = None,
                       take_profit_pips: Optional[float] = None,
                       magic: int = MAGIC, comment: str = COMMENT
                       ) -> Tuple[bool, str]:
    try:
        side = _coerce_side(order_type)
    except ValueError:
        return False, "Invalid order type. Use BUY or SELL."

    tick = client.symbol_info_tick(symbol)
    pip = pip_size(client, symbol)

    if tick is None:
        return False, "No tick data available."
    if pip is None:
        return False, "Could not calculate pip size."

    if side is OrderSide.BUY:
        trade_type = client.ORDER_TYPE_BUY
        price = tick.ask
        stop_loss = price - stop_loss_pips * pip if stop_loss_pips else None
        take_profit = price + take_profit_pips * pip if take_profit_pips else None
    else:  # OrderSide.SELL
        trade_type = client.ORDER_TYPE_SELL
        price = tick.bid
        stop_loss = price + stop_loss_pips * pip if stop_loss_pips else None
        take_profit = price - take_profit_pips * pip if take_profit_pips else None

    request = {
        "action": client.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": trade_type,
        "price": price,
        "deviation": 20,
        "magic": magic,
        "comment": comment,
        "type_time": client.ORDER_TIME_GTC,
    }
    if stop_loss is not None:
        request["sl"] = round(stop_loss, 5)
    if take_profit is not None:
        request["tp"] = round(take_profit, 5)

    result = client.order_send(request)

    if result is None:
        return False, f"Order failed: {client.last_error()}"
    if result.retcode != client.TRADE_RETCODE_DONE:
        return False, f"Order rejected: {result.retcode} - {result.comment}"

    return True, f"Order placed successfully. Ticket: {result.order}"
