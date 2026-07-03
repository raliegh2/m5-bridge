"""Run the final V12 strategy with autonomous paper execution.

This launcher uses live MT5 prices but creates only virtual positions in
v12_final_paper.db. It never calls MT5 order_send.

Run:
    python v12_final_paper_bot.py
"""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("DASHBOARD_PORT", "8801")

import v12_final_dashboard as dashboard
from mt5_ai_bridge.v12_final_paper import FinalV12PaperAdapter

_CURRENT_ADAPTER: "DashboardPaperAdapter | None" = None
_ORIGINAL_MARKET_SNAPSHOT = dashboard._market_snapshot


class DashboardPaperAdapter(FinalV12PaperAdapter):
    def __init__(self, client, state_path: str = "v12_final_paper_risk_state.json",
                 approval_callback=None, max_deviation_points: int = 10) -> None:
        global _CURRENT_ADAPTER
        super().__init__(
            client=client,
            state_path=state_path,
            database_path=os.getenv("V12_PAPER_DATABASE", "v12_final_paper.db"),
            starting_balance=float(os.getenv("V12_PAPER_STARTING_BALANCE", "100000")),
            max_deviation_points=max_deviation_points,
            approval_callback=approval_callback,
        )
        _CURRENT_ADAPTER = self
        self._stop_refresh = threading.Event()
        threading.Thread(target=self._refresh_loop, daemon=True).start()

    def _refresh_loop(self) -> None:
        while not self._stop_refresh.is_set():
            try:
                self.refresh()
            except Exception as exc:  # noqa: BLE001
                print(f"Paper position refresh error: {type(exc).__name__}: {exc}")
            self._stop_refresh.wait(1.0)


def paper_market_snapshot(client):
    account, _mt5_positions, symbols = _ORIGINAL_MARKET_SNAPSHOT(client)
    adapter = _CURRENT_ADAPTER
    if adapter is None:
        return account, [], symbols

    paper = adapter.snapshot()
    paper_account = {
        "login": "PAPER",
        "server": "V12 LIVE QUOTE PAPER ENGINE",
        "balance": paper["balance"],
        "equity": paper["equity"],
        "profit": paper["floating_pnl"],
        "margin": 0.0,
        "free_margin": paper["equity"],
    }
    positions = [
        {
            "ticket": row["ticket"],
            "symbol": row["symbol"],
            "side": row["side"],
            "volume": row["volume"],
            "open": row["entry_price"],
            "current": row.get("current_price"),
            "sl": row["stop_loss"],
            "tp": row["take_profit"],
            "profit": row.get("floating_pnl", 0.0),
            "magic": 0,
            "comment": f"PAPER:{row['engine']}:{row['setup']}",
        }
        for row in paper["open_positions"]
    ]
    return paper_account, positions, symbols


class PaperStatus(dashboard.SharedStatus):
    def __init__(self) -> None:
        super().__init__()
        self.update(execution_mode="PAPER_AUTO")


dashboard.FinalV12Adapter = DashboardPaperAdapter
dashboard.SharedStatus = PaperStatus
dashboard._market_snapshot = paper_market_snapshot
dashboard.HTML = (
    dashboard.HTML
    .replace("PROPOSAL ONLY", "PAPER AUTO")
    .replace("Open MT5 positions", "Open paper positions")
    .replace("Recent qualified proposals", "Recent paper executions")
)


if __name__ == "__main__":
    dashboard.main()
