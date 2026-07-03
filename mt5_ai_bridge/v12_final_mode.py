"""Persistent runtime account-mode selection for the V12 executor."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


ACCOUNT_MODES = frozenset({"DEMO", "LIVE"})


class AccountModeStore:
    """Thread-safe DEMO/LIVE selection shared by terminal, UI, and executor."""

    def __init__(self, path: str = "v12_final_account_mode.json",
                 default: str = "DEMO") -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._mode = self._load(default)

    def _load(self, default: str) -> str:
        fallback = self._normalize(default)
        if not self.path.exists():
            return fallback
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._normalize(raw.get("account_mode", fallback))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _normalize(mode: str) -> str:
        value = str(mode).strip().upper()
        if value not in ACCOUNT_MODES:
            raise ValueError("Account mode must be DEMO or LIVE.")
        return value

    def get(self) -> str:
        with self._lock:
            return self._mode

    def set(self, mode: str) -> str:
        value = self._normalize(mode)
        with self._lock:
            self._mode = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"account_mode": value}, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        return value
