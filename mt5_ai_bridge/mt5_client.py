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
        import MetaTrader5 as mt5  # lazy: only needed for live trading

        self._mt5 = mt5

        # Re-export the constants the rest of the package needs, so callers
        # never import MetaTrader5 directly.
        self.ORDER_TYPE_BUY = mt5.ORDER_TYPE_BUY
        self.ORDER_TYPE_SELL = mt5.ORDER_TYPE_SELL
        self.TRADE_ACTION_DEAL = mt5.TRADE_ACTION_DEAL
        self.TRADE_ACTION_SLTP = mt5.TRADE_ACTION_SLTP
        self.ORDER_TIME_GTC = mt5.ORDER_TIME_GTC
        self.TRADE_RETCODE_DONE = mt5.TRADE_RETCODE_DONE
        self.POSITION_TYPE_BUY = mt5.POSITION_TYPE_BUY
        self.POSITION_TYPE_SELL = mt5.POSITION_TYPE_SELL

    # -- connection ---------------------------------------------------------
    def initialize(self) -> bool:
        return self._mt5.initialize()

    def login(self, login: int, password: str, server: str) -> bool:
        return self._mt5.login(login, password=password, server=server)

    def shutdown(self) -> None:
        self._mt5.shutdown()

    def last_error(self) -> Any:
        return self._mt5.last_error()

    # -- account / market ---------------------------------------------------
    def account_info(self) -> Any:
        return self._mt5.account_info()

    def positions_get(self, **kwargs) -> Any:
        return self._mt5.positions_get(**kwargs)

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

    def symbol_info(self, symbol: str) -> Any:
        return self._mt5.symbol_info(symbol)

    def symbol_info_tick(self, symbol: str) -> Any:
        return self._mt5.symbol_info_tick(symbol)

    def timeframe(self, name: str) -> Any:
        """Map a timeframe name like ``"M30"`` to the MT5 constant."""
        try:
            return getattr(self._mt5, f"TIMEFRAME_{name.upper()}")
        except AttributeError as exc:
            raise ValueError(f"Unknown timeframe: {name!r}") from exc

    def copy_rates_from_pos(self, symbol: str, timeframe_name: str,
                            start: int, count: int) -> Any:
        return self._mt5.copy_rates_from_pos(
            symbol, self.timeframe(timeframe_name), start, count
        )

    # -- orders -------------------------------------------------------------
    def order_send(self, request: dict) -> Any:
        return self._mt5.order_send(request)


def create_client() -> RealMT5Client:
    """Factory for the live client (kept separate for easy patching)."""
    return RealMT5Client()
