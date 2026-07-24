"""Abstraction over the MetaTrader5 library.

All direct contact with the ``MetaTrader5`` package is confined to this module.
Every other module talks to a *client* object with the small, explicit surface
below, which means the trading logic can be exercised in tests with a fake
client and no broker connection.

The ``MetaTrader5`` import is deliberately lazy (inside ``__init__``) so the
package can be imported on machines where the library is not installed
(e.g. CI / Linux), as long as a real client is never instantiated there.
"""

from typing import Any


class RealMT5Client:
    """Thin wrapper around the live MetaTrader5 terminal."""

    def __init__(self) -> None:
        import MetaTrader5 as mt5

        self._mt5 = mt5
        self.ORDER_TYPE_BUY = mt5.ORDER_TYPE_BUY
        self.ORDER_TYPE_SELL = mt5.ORDER_TYPE_SELL
        self.TRADE_ACTION_DEAL = mt5.TRADE_ACTION_DEAL
        self.TRADE_ACTION_SLTP = mt5.TRADE_ACTION_SLTP
        self.TRADE_ACTION_PENDING = mt5.TRADE_ACTION_PENDING
        self.TRADE_ACTION_REMOVE = mt5.TRADE_ACTION_REMOVE
        self.ORDER_TIME_GTC = mt5.ORDER_TIME_GTC
        self.ORDER_FILLING_FOK = getattr(mt5, "ORDER_FILLING_FOK", 0)
        self.ORDER_FILLING_IOC = getattr(mt5, "ORDER_FILLING_IOC", 1)
        self.ORDER_FILLING_RETURN = getattr(mt5, "ORDER_FILLING_RETURN", 2)
        self.TRADE_RETCODE_DONE = mt5.TRADE_RETCODE_DONE
        self.TRADE_RETCODE_PLACED = getattr(mt5, "TRADE_RETCODE_PLACED", None)
        self.TRADE_RETCODE_DONE_PARTIAL = getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", None)
        self.POSITION_TYPE_BUY = mt5.POSITION_TYPE_BUY
        self.POSITION_TYPE_SELL = mt5.POSITION_TYPE_SELL
<<<<<<< HEAD
        self.ACCOUNT_TRADE_MODE_DEMO = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        self.ACCOUNT_TRADE_MODE_REAL = getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)
        self.COPY_TICKS_ALL = getattr(mt5, "COPY_TICKS_ALL", -1)
        self.BOOK_TYPE_SELL = getattr(mt5, "BOOK_TYPE_SELL", 1)
        self.BOOK_TYPE_BUY = getattr(mt5, "BOOK_TYPE_BUY", 2)
=======
        self.DEAL_ENTRY_IN = mt5.DEAL_ENTRY_IN
        self.DEAL_ENTRY_OUT = mt5.DEAL_ENTRY_OUT
        self.DEAL_ENTRY_INOUT = mt5.DEAL_ENTRY_INOUT
        self.DEAL_ENTRY_OUT_BY = mt5.DEAL_ENTRY_OUT_BY
>>>>>>> origin/main

    def initialize(self) -> bool:
        return self._mt5.initialize()

    def login(self, login: int, password: str, server: str) -> bool:
        return self._mt5.login(login, password=password, server=server)

    def shutdown(self) -> None:
        self._mt5.shutdown()

    def last_error(self) -> Any:
        return self._mt5.last_error()

    def terminal_info(self) -> Any:
        return self._mt5.terminal_info()

    def account_info(self) -> Any:
        return self._mt5.account_info()

    def positions_get(self, **kwargs) -> Any:
        return self._mt5.positions_get(**kwargs)

<<<<<<< HEAD
    def history_deals_get(self, date_from, date_to, **kwargs) -> Any:
        return self._mt5.history_deals_get(date_from, date_to, **kwargs)

    def symbols_get(self, *args, **kwargs) -> Any:
        return self._mt5.symbols_get(*args, **kwargs)
=======
    def orders_get(self, **kwargs) -> Any:
        return self._mt5.orders_get(**kwargs)

    def history_deals_get(self, date_from, date_to, **kwargs) -> Any:
        """Return broker deal history for risk reconstruction and loss streaks."""
        return self._mt5.history_deals_get(date_from, date_to, **kwargs)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        """Subscribe a symbol in Market Watch so its bars/ticks are available.

        MT5 only streams data for symbols present in Market Watch; a symbol that
        is not selected returns empty rates ("no market data"). Returns False if
        the broker has no such symbol (usually a name mismatch).
        """
        try:
            return bool(self._mt5.symbol_select(symbol, enable))
        except Exception:  # noqa: BLE001
            return False
>>>>>>> origin/main

    def symbol_info(self, symbol: str) -> Any:
        return self._mt5.symbol_info(symbol)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return bool(self._mt5.symbol_select(symbol, enable))

    def symbol_info_tick(self, symbol: str) -> Any:
        return self._mt5.symbol_info_tick(symbol)

    def timeframe(self, name: str) -> Any:
        try:
            return getattr(self._mt5, f"TIMEFRAME_{name.upper()}")
        except AttributeError as exc:
            raise ValueError(f"Unknown timeframe: {name!r}") from exc

    def copy_rates_from_pos(self, symbol: str, timeframe_name: str, start: int, count: int) -> Any:
        return self._mt5.copy_rates_from_pos(symbol, self.timeframe(timeframe_name), start, count)

    def copy_ticks_from(self, symbol: str, date_from, count: int, flags: int) -> Any:
        return self._mt5.copy_ticks_from(symbol, date_from, count, flags)

    def market_book_add(self, symbol: str) -> bool:
        return bool(self._mt5.market_book_add(symbol))

    def market_book_get(self, symbol: str) -> Any:
        return self._mt5.market_book_get(symbol)

    def market_book_release(self, symbol: str) -> bool:
        return bool(self._mt5.market_book_release(symbol))

    def order_calc_profit(self, order_type: int, symbol: str, volume: float,
                          price_open: float, price_close: float) -> Any:
        return self._mt5.order_calc_profit(order_type, symbol, volume, price_open, price_close)

    def order_check(self, request: dict) -> Any:
        return self._mt5.order_check(request)

    def order_send(self, request: dict) -> Any:
        return self._mt5.order_send(request)


def create_client() -> RealMT5Client:
    return RealMT5Client()
