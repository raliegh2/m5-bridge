"""SQLite persistence.

Every statement is parameterised - no SQL is ever built by string formatting
with caller data. The connection is shared behind a lock with WAL enabled so
the capture threads and the HTTP threads can interleave safely.

Retention is enforced by :meth:`Store.purge_expired`: metrics, alerts, and
sessions older than the configured window are deleted, so observational data
about third parties does not accumulate indefinitely.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'viewer',
    created_at     REAL NOT NULL,
    disabled       INTEGER NOT NULL DEFAULT 0,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until   REAL NOT NULL DEFAULT 0,
    last_login     REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    expires_at  REAL NOT NULL,
    client      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS authorizations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cidr          TEXT NOT NULL,
    scope         TEXT NOT NULL,
    justification TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    revoked_at    REAL,
    revoked_by    TEXT
);
CREATE INDEX IF NOT EXISTS authorizations_active ON authorizations(revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS monitors (
    id          TEXT PRIMARY KEY,
    target      TEXT NOT NULL,
    protocols   TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    stopped_at  REAL,
    status      TEXT NOT NULL DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS monitors_status ON monitors(status);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id     TEXT NOT NULL,
    target         TEXT NOT NULL,
    started_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    ended_at       REAL,
    classification TEXT NOT NULL,
    severity       TEXT NOT NULL,
    score          REAL NOT NULL,
    peak_score     REAL NOT NULL,
    evidence       TEXT NOT NULL,
    top_sources    TEXT NOT NULL,
    acknowledged_by TEXT
);
CREATE INDEX IF NOT EXISTS alerts_recent ON alerts(started_at DESC);
CREATE INDEX IF NOT EXISTS alerts_monitor ON alerts(monitor_id, started_at DESC);

CREATE TABLE IF NOT EXISTS metrics (
    monitor_id     TEXT NOT NULL,
    ts             REAL NOT NULL,
    pps            REAL NOT NULL,
    bps            REAL NOT NULL,
    syn_pps        REAL NOT NULL,
    udp_pps        REAL NOT NULL,
    icmp_pps       REAL NOT NULL,
    unique_sources INTEGER NOT NULL,
    entropy        REAL NOT NULL,
    score          REAL NOT NULL,
    state          TEXT NOT NULL,
    PRIMARY KEY (monitor_id, ts)
);
CREATE INDEX IF NOT EXISTS metrics_ts ON metrics(ts);
"""


@dataclass(frozen=True)
class UserRow:
    id: int
    username: str
    password_hash: str
    role: str
    disabled: bool
    failed_attempts: int
    locked_until: float

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Store:
    """Thread-safe SQLite wrapper holding all durable state."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- low level -------------------------------------------------------
    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cursor

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # -- users -----------------------------------------------------------
    def create_user(self, username: str, password_hash: str, role: str) -> int:
        cursor = self._execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, password_hash, role, time.time()),
        )
        return int(cursor.lastrowid)

    def get_user(self, username: str) -> UserRow | None:
        rows = self._query("SELECT * FROM users WHERE username = ?", (username,))
        if not rows:
            return None
        row = rows[0]
        return UserRow(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            role=str(row["role"]),
            disabled=bool(row["disabled"]),
            failed_attempts=int(row["failed_attempts"]),
            locked_until=float(row["locked_until"]),
        )

    def get_user_by_id(self, user_id: int) -> UserRow | None:
        rows = self._query("SELECT username FROM users WHERE id = ?", (user_id,))
        return self.get_user(str(rows[0]["username"])) if rows else None

    def list_users(self) -> list[dict]:
        rows = self._query(
            "SELECT username, role, disabled, created_at, last_login FROM users ORDER BY username"
        )
        return [dict(row) for row in rows]

    def set_password(self, username: str, password_hash: str) -> None:
        self._execute(
            "UPDATE users SET password_hash = ?, failed_attempts = 0, locked_until = 0 "
            "WHERE username = ?",
            (password_hash, username),
        )

    def record_login_failure(self, username: str, lockout_seconds: int, max_attempts: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET failed_attempts = failed_attempts + 1 WHERE username = ?",
                (username,),
            )
            self._conn.execute(
                "UPDATE users SET locked_until = ? WHERE username = ? AND failed_attempts >= ?",
                (time.time() + lockout_seconds, username, max_attempts),
            )
            self._conn.commit()

    def record_login_success(self, username: str) -> None:
        self._execute(
            "UPDATE users SET failed_attempts = 0, locked_until = 0, last_login = ? "
            "WHERE username = ?",
            (time.time(), username),
        )

    # -- sessions --------------------------------------------------------
    def create_session(self, token_hash: str, user_id: int, csrf_token: str,
                       expires_at: float, client: str) -> None:
        now = time.time()
        self._execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, last_seen, "
            "expires_at, client) VALUES (?,?,?,?,?,?,?)",
            (token_hash, user_id, csrf_token, now, now, expires_at, client),
        )

    def get_session(self, token_hash: str) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
        return rows[0] if rows else None

    def touch_session(self, token_hash: str, when: float) -> None:
        self._execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (when, token_hash))

    def delete_session(self, token_hash: str) -> None:
        self._execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_sessions_for_user(self, user_id: int) -> None:
        self._execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # -- authorizations --------------------------------------------------
    def add_authorization(self, cidr: str, scope: str, justification: str,
                          created_by: str, expires_at: float) -> int:
        cursor = self._execute(
            "INSERT INTO authorizations (cidr, scope, justification, created_by, created_at, "
            "expires_at) VALUES (?,?,?,?,?,?)",
            (cidr, scope, justification, created_by, time.time(), expires_at),
        )
        return int(cursor.lastrowid)

    def list_authorizations(self, include_inactive: bool = False) -> list[dict]:
        if include_inactive:
            rows = self._query("SELECT * FROM authorizations ORDER BY created_at DESC")
        else:
            rows = self._query(
                "SELECT * FROM authorizations WHERE revoked_at IS NULL AND expires_at > ? "
                "ORDER BY created_at DESC",
                (time.time(),),
            )
        return [dict(row) for row in rows]

    def revoke_authorization(self, auth_id: int, revoked_by: str) -> bool:
        cursor = self._execute(
            "UPDATE authorizations SET revoked_at = ?, revoked_by = ? "
            "WHERE id = ? AND revoked_at IS NULL",
            (time.time(), revoked_by, auth_id),
        )
        return cursor.rowcount > 0

    # -- monitors --------------------------------------------------------
    def add_monitor(self, monitor_id: str, target: str, protocols: Iterable[str],
                    label: str, created_by: str) -> None:
        self._execute(
            "INSERT INTO monitors (id, target, protocols, label, created_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (monitor_id, target, ",".join(protocols), label, created_by, time.time()),
        )

    def stop_monitor(self, monitor_id: str, status: str = "stopped") -> None:
        self._execute(
            "UPDATE monitors SET stopped_at = ?, status = ? WHERE id = ?",
            (time.time(), status, monitor_id),
        )

    def list_monitors(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM monitors"
        params: tuple = ()
        if active_only:
            sql += " WHERE status = ?"
            params = ("running",)
        sql += " ORDER BY created_at DESC"
        return [dict(row) for row in self._query(sql, params)]

    # -- alerts ----------------------------------------------------------
    def open_alert(self, monitor_id: str, target: str, classification: str, severity: str,
                   score: float, evidence: dict, top_sources: list) -> int:
        now = time.time()
        cursor = self._execute(
            "INSERT INTO alerts (monitor_id, target, started_at, updated_at, classification, "
            "severity, score, peak_score, evidence, top_sources) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                monitor_id, target, now, now, classification, severity, score, score,
                json.dumps(evidence, separators=(",", ":")),
                json.dumps(top_sources, separators=(",", ":")),
            ),
        )
        return int(cursor.lastrowid)

    def update_alert(self, alert_id: int, *, classification: str, severity: str, score: float,
                     evidence: dict, top_sources: list) -> None:
        self._execute(
            "UPDATE alerts SET updated_at = ?, classification = ?, severity = ?, score = ?, "
            "peak_score = MAX(peak_score, ?), evidence = ?, top_sources = ? WHERE id = ?",
            (
                time.time(), classification, severity, score, score,
                json.dumps(evidence, separators=(",", ":")),
                json.dumps(top_sources, separators=(",", ":")),
                alert_id,
            ),
        )

    def close_alert(self, alert_id: int) -> None:
        now = time.time()
        self._execute(
            "UPDATE alerts SET ended_at = ?, updated_at = ? WHERE id = ? AND ended_at IS NULL",
            (now, now, alert_id),
        )

    def acknowledge_alert(self, alert_id: int, actor: str) -> bool:
        cursor = self._execute(
            "UPDATE alerts SET acknowledged_by = ? WHERE id = ? AND acknowledged_by IS NULL",
            (actor, alert_id),
        )
        return cursor.rowcount > 0

    def list_alerts(self, limit: int = 100, monitor_id: str | None = None,
                    active_only: bool = False) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if monitor_id:
            clauses.append("monitor_id = ?")
            params.append(monitor_id)
        if active_only:
            clauses.append("ended_at IS NULL")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        rows = self._query(
            f"SELECT * FROM alerts{where} ORDER BY started_at DESC LIMIT ?", params
        )
        out = []
        for row in rows:
            item = dict(row)
            item["evidence"] = _load_json(item.get("evidence"), {})
            item["top_sources"] = _load_json(item.get("top_sources"), [])
            out.append(item)
        return out

    # -- metrics ---------------------------------------------------------
    def add_metric(self, monitor_id: str, sample: dict) -> None:
        self._execute(
            "INSERT OR REPLACE INTO metrics (monitor_id, ts, pps, bps, syn_pps, udp_pps, "
            "icmp_pps, unique_sources, entropy, score, state) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                monitor_id,
                float(sample["ts"]),
                float(sample["pps"]),
                float(sample["bps"]),
                float(sample["syn_pps"]),
                float(sample["udp_pps"]),
                float(sample["icmp_pps"]),
                int(sample["unique_sources"]),
                float(sample["entropy"]),
                float(sample["score"]),
                str(sample["state"]),
            ),
        )

    def list_metrics(self, monitor_id: str, limit: int = 300) -> list[dict]:
        rows = self._query(
            "SELECT * FROM metrics WHERE monitor_id = ? ORDER BY ts DESC LIMIT ?",
            (monitor_id, max(1, min(int(limit), 5000))),
        )
        return [dict(row) for row in reversed(rows)]

    # -- maintenance -----------------------------------------------------
    def purge_expired(self, retention_days: int) -> dict[str, int]:
        """Delete data past its retention window. Returns per-table row counts."""
        now = time.time()
        cutoff = now - retention_days * 86400
        with self._lock:
            metrics = self._conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,)).rowcount
            alerts = self._conn.execute(
                "DELETE FROM alerts WHERE started_at < ? AND ended_at IS NOT NULL", (cutoff,)
            ).rowcount
            sessions = self._conn.execute(
                "DELETE FROM sessions WHERE expires_at < ?", (now,)
            ).rowcount
            self._conn.commit()
        return {"metrics": metrics, "alerts": alerts, "sessions": sessions}


def _load_json(raw: object, fallback: object) -> object:
    if not isinstance(raw, str):
        return fallback
    try:
        return json.loads(raw)
    except ValueError:
        return fallback
