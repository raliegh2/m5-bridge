"""Persistent scheduler audit trail and completed-candle cursors.

The execution log records candidates.  This companion journal records every
scheduled scan, including scans that produce no candidate, so a quiet engine is
distinguishable from an engine that was never called.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ScanAuditJournal:
    def __init__(
        self,
        cursor_path: str | Path = "state/v14_21_scan_cursors.json",
        event_path: str | Path = "state/v14_21_scan_events.jsonl",
        recent_limit: int = 250,
    ) -> None:
        self.cursor_path = Path(cursor_path)
        self.event_path = Path(event_path)
        self.recent_limit = max(25, int(recent_limit))
        self.cursors: dict[str, int] = {}
        self.recent: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.cursor_path.read_text(encoding="utf-8"))
            self.cursors = {
                str(key): int(value)
                for key, value in dict(payload.get("cursors") or {}).items()
            }
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            self.cursors = {}
        try:
            lines = self.event_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        for line in reversed(lines[-self.recent_limit :]):
            try:
                self.recent.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def cursor(self, scope: str) -> int | None:
        value = self.cursors.get(str(scope))
        return int(value) if value is not None else None

    def set_cursor(self, scope: str, completed_bar_time: int) -> None:
        self.cursors[str(scope)] = int(completed_bar_time)
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cursor_path.with_suffix(self.cursor_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "cursors": self.cursors,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.cursor_path)

    def record(
        self,
        scope: str,
        outcome: str,
        *,
        completed_bar_time: int | None = None,
        candidate_count: int | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "scope": str(scope),
            "outcome": str(outcome),
            "completed_bar_time": completed_bar_time,
            "candidate_count": candidate_count,
            "details": dict(details or {}),
            "error": error,
        }
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        self.recent.insert(0, event)
        del self.recent[self.recent_limit :]
        return event

    def snapshot(self) -> dict[str, Any]:
        latest_by_scope: dict[str, dict[str, Any]] = {}
        for event in self.recent:
            latest_by_scope.setdefault(str(event.get("scope")), event)
        return {
            "cursors": dict(self.cursors),
            "latest_by_scope": latest_by_scope,
            "recent_events": list(self.recent[:50]),
            "cursor_path": str(self.cursor_path),
            "event_path": str(self.event_path),
        }
