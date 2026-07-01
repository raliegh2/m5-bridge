"""SQLite journaling of signals, orders and risk events.

This is the persistence layer the dashboard and backtesting analysis read from.
It is intentionally dependency-free (stdlib ``sqlite3``) and safe to call from
the main loop on every iteration.

Each loop logs one *analysis* (a signal row). Two extra flags on that row let
the dashboard separate raw reads from real setups:

- ``setup``    : 1 when this analysis was a valid trade setup (the fast entry
                 signal agreed with the confirmed higher-timeframe trend).
- ``filtered`` : 1 when the entry wanted to trade but a filter blocked it
                 (trend not aligned, or an RSI veto).

Executed trades are counted from FILLED orders, so the five dashboard numbers —
analyses, raw signals, valid setups, executed trades, filtered-out setups — all
come straight from the journal.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_midnight() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")


class Journal:
    """Append-only trade journal backed by SQLite."""

    def __init__(self, db_path: str = "journal.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT,
                signal TEXT,
                reason TEXT,
                snapshot TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT,
                side TEXT,
                volume REAL,
                price REAL,
                sl REAL,
                tp REAL,
                ticket INTEGER,
                status TEXT,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                ok INTEGER,
                message TEXT,
                balance REAL,
                equity REAL,
                open_positions INTEGER
            );
            """
        )
        # Add the setup/filtered flags to older databases that predate them.
        existing = {row[1] for row in
                    self._conn.execute("PRAGMA table_info(signals)")}
        if "setup" not in existing:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN setup INTEGER DEFAULT 0")
        if "filtered" not in existing:
            self._conn.execute(
                "ALTER TABLE signals ADD COLUMN filtered INTEGER DEFAULT 0")
        self._conn.commit()

    # -- writes -------------------------------------------------------------
    def log_signal(self, symbol: str, signal: str, reason: str,
                   snapshot: Optional[dict] = None, setup: int = 0,
                   filtered: int = 0) -> int:
        cur = self._conn.execute(
            "INSERT INTO signals (ts, symbol, signal, reason, snapshot, setup, "
            "filtered) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_utcnow(), symbol, str(signal), reason,
             json.dumps(snapshot, default=str) if snapshot else None,
             int(setup), int(filtered)),
        )
        self._conn.commit()
        return cur.lastrowid

    def log_order(self, symbol: str, side: str, volume: float,
                  price: Optional[float], sl: Optional[float],
                  tp: Optional[float], ticket: Optional[int],
                  status: str, message: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO orders (ts, symbol, side, volume, price, sl, tp, "
            "ticket, status, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_utcnow(), symbol, str(side), volume, price, sl, tp, ticket,
             status, message),
        )
        self._conn.commit()
        return cur.lastrowid

    def log_risk_event(self, ok: bool, message: str, balance: float,
                       equity: float, open_positions: int) -> int:
        cur = self._conn.execute(
            "INSERT INTO risk_events (ts, ok, message, balance, equity, "
            "open_positions) VALUES (?, ?, ?, ?, ?, ?)",
            (_utcnow(), int(ok), message, balance, equity, open_positions),
        )
        self._conn.commit()
        return cur.lastrowid

    # -- reads (for dashboard / analysis) -----------------------------------
    def recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._recent("signals", limit)

    def recent_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._recent("orders", limit)

    def recent_risk_events(self, limit: int = 300) -> list[dict[str, Any]]:
        return self._recent("risk_events", limit)

    def _recent(self, table: str, limit: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_trades_today(self) -> int:
        """FILLED BUY/SELL orders opened since UTC midnight (for the daily cap)."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE status = 'FILLED' "
            "AND side IN ('BUY', 'SELL') AND ts >= ?", (_utc_midnight(),)
        ).fetchone()
        return row["c"]

    def signal_stats_today(self) -> dict[str, int]:
        """The five dashboard categories, counted since UTC midnight.

        analyses  = market evaluations performed
        raw_buy / raw_sell / raw_wait = raw entry-timeframe signal counts
        setups    = valid trade setups (all filters agreed)
        filtered  = signals rejected because a filter failed
        executed  = actual trades opened (FILLED orders)
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS analyses, "
            "COALESCE(SUM(signal = 'BUY'), 0) AS raw_buy, "
            "COALESCE(SUM(signal = 'SELL'), 0) AS raw_sell, "
            "COALESCE(SUM(signal NOT IN ('BUY', 'SELL')), 0) AS raw_wait, "
            "COALESCE(SUM(setup), 0) AS setups, "
            "COALESCE(SUM(filtered), 0) AS filtered "
            "FROM signals WHERE ts >= ?", (_utc_midnight(),)
        ).fetchone()
        return {
            "analyses": row["analyses"] or 0,
            "raw_buy": row["raw_buy"] or 0,
            "raw_sell": row["raw_sell"] or 0,
            "raw_wait": row["raw_wait"] or 0,
            "setups": row["setups"] or 0,
            "filtered": row["filtered"] or 0,
            "executed": self.count_trades_today(),
        }

    def day_start_equity(self) -> Optional[float]:
        """Equity recorded at the first risk event of the current UTC day."""
        row = self._conn.execute(
            "SELECT equity FROM risk_events WHERE ts >= ? ORDER BY id ASC LIMIT 1",
            (_utc_midnight(),)
        ).fetchone()
        return row["equity"] if row else None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
