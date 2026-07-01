"""Position management: closing trades and protective stop updates via MT5."""

from dataclasses import dataclass
from typing import List, Optional, Tuple

MAGIC = 20260629
COMMENT = "MT5 AI Bridge Close"


@dataclass(frozen=True)
class StopManagementDecision:
    """Decision returned by the protective stop manager.

    ``new_sl`` is ``None`` when the stop should not move. ``reason`` is written
    for logs/dashboard output so the bot can explain why it did or did not move
    protection.
    """

    new_sl: Optional[float]
    reason: str
    profit_pips: float = 0.0
    mfe_ratio: float = 0.0


# --------------------------------------------------------------------------
# Closing positions
# --------------------------------------------------------------------------

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
# Protective stop logic
# --------------------------------------------------------------------------

def _round_price(price: float) -> float:
    return round(float(price), 5)


def _profit_pips(is_buy: bool, entry: float, current: float, pip: float) -> float:
    return ((current - entry) if is_buy else (entry - current)) / pip


def _is_tighter(is_buy: bool, current_sl: float, candidate: float) -> bool:
    """Return True only when candidate tightens risk, never loosens it."""
    if not current_sl:
        return True
    return candidate > current_sl if is_buy else candidate < current_sl


def managed_stop_loss(is_buy: bool, entry: float, current: float,
                      current_sl: float, pip: float,
                      trail_start_pips: float,
                      trail_distance_pips: float,
                      take_profit: Optional[float] = None,
                      break_even_at_r: float = 1.0,
                      break_even_buffer_pips: float = 0.5,
                      tp_lock_ratio: float = 2 / 3,
                      tp_lock_profit_ratio: float = 0.50
                      ) -> StopManagementDecision:
    """Return the next protective stop for an open trade.

    This is a hybrid exit manager:

    1. **Initial fixed SL/TP remains intact.** The bot still opens with the
       planned stop and target.
    2. **Break-even after meaningful follow-through.** Once the trade reaches
       ``break_even_at_r`` based on the original stop distance, the SL moves to
       entry plus a small buffer. This prevents a trade that moved properly in
       profit from becoming a full loser.
    3. **Protect profit near target.** When ``take_profit`` is supplied and at
       least ``tp_lock_ratio`` of the TP path is reached, lock a portion of the
       available profit instead of waiting for the whole move to reverse.
    4. **Trail after strong profit.** Once ``trail_start_pips`` is reached, the
       stop trails ``trail_distance_pips`` behind price.

    The function never loosens an existing stop.
    """
    if pip <= 0 or entry <= 0 or current <= 0:
        return StopManagementDecision(None, "invalid price/pip inputs")

    profit = _profit_pips(is_buy, entry, current, pip)
    if profit <= 0:
        return StopManagementDecision(None, "trade is not in profit", profit)

    candidates: list[tuple[float, str, float]] = []

    # 1R = distance from entry to the current/original stop. If no stop exists,
    # fall back to the trail start so behaviour stays safe and compatible.
    risk_pips = abs(entry - current_sl) / pip if current_sl else trail_start_pips
    if risk_pips > 0 and profit >= risk_pips * break_even_at_r:
        be = entry + break_even_buffer_pips * pip if is_buy else entry - break_even_buffer_pips * pip
        candidates.append((_round_price(be), "break-even protection", profit / risk_pips))

    # Protect profit when the trade has travelled a meaningful fraction toward TP.
    if take_profit:
        tp_distance = abs(take_profit - entry) / pip
        if tp_distance > 0:
            progress = profit / tp_distance
            if progress >= tp_lock_ratio:
                locked_pips = max(break_even_buffer_pips, profit * tp_lock_profit_ratio)
                lock = entry + locked_pips * pip if is_buy else entry - locked_pips * pip
                candidates.append((_round_price(lock), "2/3 TP profit lock", progress))

    # Classic trailing stop remains available after strong follow-through.
    if profit >= trail_start_pips:
        trail = current - trail_distance_pips * pip if is_buy else current + trail_distance_pips * pip
        candidates.append((_round_price(trail), "floating trailing stop", profit / max(trail_start_pips, 1)))

    valid = [(sl, reason, ratio) for sl, reason, ratio in candidates
             if _is_tighter(is_buy, current_sl, sl)]
    if not valid:
        return StopManagementDecision(None, "no tighter protective stop available", profit)

    # For BUY, the highest SL is most protective. For SELL, the lowest SL is.
    selected = max(valid, key=lambda item: item[0]) if is_buy else min(valid, key=lambda item: item[0])
    return StopManagementDecision(selected[0], selected[1], profit, selected[2])


def trailing_sl(is_buy: bool, entry: float, current: float,
                current_sl: float, pip: float, start_pips: float,
                distance_pips: float) -> Optional[float]:
    """Backward-compatible wrapper for existing callers.

    Existing app code already calls ``trailing_sl``. The wrapper now uses the
    stronger stop manager, so running bots gain break-even protection without
    requiring a large application rewrite.
    """
    decision = managed_stop_loss(
        is_buy=is_buy,
        entry=entry,
        current=current,
        current_sl=current_sl,
        pip=pip,
        trail_start_pips=start_pips,
        trail_distance_pips=distance_pips,
    )
    return decision.new_sl


def modify_position_sl(client, position, new_sl: float) -> Tuple[bool, str]:
    """Move a position's stop-loss (keeps its take-profit)."""
    request = {
        "action": client.TRADE_ACTION_SLTP,
        "position": position.ticket,
        "symbol": position.symbol,
        "sl": _round_price(new_sl),
        "tp": getattr(position, "tp", 0.0) or 0.0,
    }
    result = client.order_send(request)

    if result is None:
        return False, f"Modify failed: {client.last_error()}"
    if result.retcode != client.TRADE_RETCODE_DONE:
        return False, f"Modify rejected: {result.retcode} - {result.comment}"
    return True, f"SL -> {new_sl:.5f} on ticket {position.ticket}"
