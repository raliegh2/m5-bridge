"""Autonomous paper execution for the final V12 strategy.

Uses live MT5 quotes for virtual fills while never calling MT5 order_send. The
existing V12 proposal/risk engine remains the validation authority. Virtual
positions are persisted in SQLite and are automatically closed when live bid/
ask reaches their stop-loss or take-profit.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .enums import OrderSide
from .execution import pip_size
from .v12_final_adapter import NamedEngineSignal
from .v12_final_execution import ExecutionResult, FinalExecutionRequest, FinalResearchExecutor
from .v12_final_state import StateStore


class FinalV12PaperAdapter:
    def __init__(self, client, state_path: str = "v12_final_paper_risk_state.json",
                 database_path: str = "v12_final_paper.db",
                 starting_balance: float = 100000.0,
                 max_deviation_points: int = 10,
                 approval_callback=None) -> None:
        del approval_callback
        self.client = client
        self.database_path = Path(database_path)
        self.starting_balance = float(starting_balance)
        self._lock = threading.RLock()
        self.executor = FinalResearchExecutor(
            client=client,
            approval_callback=lambda _proposal: True,
            state=StateStore(state_path),
            max_deviation_points=max_deviation_points,
        )
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    ticket INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_key TEXT UNIQUE NOT NULL,
                    symbol TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    setup TEXT NOT NULL,
                    side TEXT NOT NULL,
                    volume REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    risk_percent REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    exit_price REAL,
                    realized_pnl REAL,
                    close_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_paper_positions_status
                ON paper_positions(status);
                """
            )

    def submit(self, signal: NamedEngineSignal,
               now: Optional[datetime] = None) -> ExecutionResult:
        now = now or datetime.now(timezone.utc)
        request = FinalExecutionRequest(
            symbol=signal.symbol,
            engine=signal.engine,
            setup=signal.setup,
            side=signal.side,
            base_risk_percent=signal.base_risk_percent,
            stop_pips=signal.stop_pips,
            target_pips=signal.target_pips,
            signal_time=signal.signal_time.astimezone(timezone.utc),
        )
        approved = self.executor.place(request, now=now)
        if not approved.ok or approved.proposal is None:
            return approved

        tick = self.client.symbol_info_tick(signal.symbol)
        pip = pip_size(self.client, signal.symbol)
        if tick is None or pip is None:
            return ExecutionResult(False, "PAPER_MARKET_DATA_UNAVAILABLE",
                                   "Live quote unavailable for paper fill.",
                                   volume=approved.volume,
                                   risk_percent=approved.risk_percent)

        side = OrderSide(signal.side.upper())
        entry = float(tick.ask if side is OrderSide.BUY else tick.bid)
        stop = entry - signal.stop_pips * pip if side is OrderSide.BUY else entry + signal.stop_pips * pip
        target = entry + signal.target_pips * pip if side is OrderSide.BUY else entry - signal.target_pips * pip
        order_key = "|".join((
            signal.symbol,
            signal.engine,
            signal.setup,
            signal.side.upper(),
            signal.signal_time.astimezone(timezone.utc).isoformat(),
        ))

        try:
            with self._lock, self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO paper_positions (
                        order_key, symbol, engine, setup, side, volume,
                        entry_price, stop_loss, take_profit, risk_percent, opened_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_key, signal.symbol, signal.engine, signal.setup,
                        signal.side.upper(), approved.volume, entry, stop, target,
                        approved.risk_percent, now.isoformat(),
                    ),
                )
                ticket = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return ExecutionResult(False, "PAPER_DUPLICATE_ORDER",
                                   "The same V12 signal is already recorded.",
                                   volume=approved.volume,
                                   risk_percent=approved.risk_percent)

        return ExecutionResult(
            True,
            "PAPER_FILLED",
            "Virtual position opened automatically using the live MT5 quote.",
            ticket=ticket,
            volume=approved.volume,
            risk_percent=approved.risk_percent,
            proposal=approved.proposal,
        )

    def refresh(self, now: Optional[datetime] = None) -> list[dict]:
        """Mark open paper positions and close those whose SL or TP was reached."""
        now = now or datetime.now(timezone.utc)
        closed: list[dict] = []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY ticket"
            ).fetchall()
            for row in rows:
                tick = self.client.symbol_info_tick(row["symbol"])
                if tick is None:
                    continue
                side = row["side"]
                exit_price = float(tick.bid if side == "BUY" else tick.ask)
                reason = None
                if side == "BUY":
                    if exit_price <= float(row["stop_loss"]):
                        reason = "STOP_LOSS"
                    elif exit_price >= float(row["take_profit"]):
                        reason = "TAKE_PROFIT"
                else:
                    if exit_price >= float(row["stop_loss"]):
                        reason = "STOP_LOSS"
                    elif exit_price <= float(row["take_profit"]):
                        reason = "TAKE_PROFIT"
                if reason is None:
                    continue

                pnl = self._profit(row["symbol"], side, float(row["volume"]),
                                   float(row["entry_price"]), exit_price)
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET status='CLOSED', closed_at=?, exit_price=?,
                        realized_pnl=?, close_reason=?
                    WHERE ticket=?
                    """,
                    (now.isoformat(), exit_price, pnl, reason, int(row["ticket"])),
                )
                closed.append({
                    "ticket": int(row["ticket"]),
                    "symbol": row["symbol"],
                    "engine": row["engine"],
                    "exit_price": exit_price,
                    "realized_pnl": pnl,
                    "reason": reason,
                })
        return closed

    def snapshot(self) -> dict:
        with self._lock, self._connect() as conn:
            open_rows = [dict(row) for row in conn.execute(
                "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at DESC"
            ).fetchall()]
            realized = float(conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM paper_positions WHERE status='CLOSED'"
            ).fetchone()[0])
        floating = 0.0
        for row in open_rows:
            tick = self.client.symbol_info_tick(row["symbol"])
            if tick is None:
                row["current_price"] = None
                row["floating_pnl"] = 0.0
                continue
            current = float(tick.bid if row["side"] == "BUY" else tick.ask)
            pnl = self._profit(row["symbol"], row["side"], float(row["volume"]),
                               float(row["entry_price"]), current)
            row["current_price"] = current
            row["floating_pnl"] = pnl
            floating += pnl
        balance = self.starting_balance + realized
        return {
            "starting_balance": self.starting_balance,
            "balance": balance,
            "equity": balance + floating,
            "realized_pnl": realized,
            "floating_pnl": floating,
            "open_positions": open_rows,
        }

    def record_closed_trade(self, engine: str, r_multiple: float,
                            now: Optional[datetime] = None) -> None:
        self.executor.record_closed_trade(engine, r_multiple, now)

    def _profit(self, symbol: str, side: str, volume: float,
                entry: float, exit_price: float) -> float:
        order_type = self.client.ORDER_TYPE_BUY if side == "BUY" else self.client.ORDER_TYPE_SELL
        calculator = getattr(self.client, "order_calc_profit", None)
        if calculator is not None:
            value = calculator(order_type, symbol, volume, entry, exit_price)
            if value is not None:
                return round(float(value), 2)
        multiplier = 1.0 if side == "BUY" else -1.0
        return round((exit_price - entry) * volume * multiplier, 6)
