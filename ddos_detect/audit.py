"""Tamper-evident audit log.

Monitoring another party's traffic is a consequential act, so every decision
that authorises, starts, or stops observation is recorded here. Records form a
keyed hash chain: each entry commits to the previous entry's digest under the
per-installation secret, so an edited, reordered, or deleted record is
detectable by :func:`verify_chain` even by someone with write access to the
file - they cannot recompute the chain without the secret.

The log is append-only, line-delimited JSON, and never contains packet
contents or credentials.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .crypto import chain_digest
from .validation import validate_text

GENESIS = "genesis"


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: float
    actor: str
    action: str
    detail: dict
    prev: str
    digest: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "ts": self.ts,
                "actor": self.actor,
                "action": self.action,
                "detail": self.detail,
                "prev": self.prev,
                "digest": self.digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def _payload(seq: int, ts: float, actor: str, action: str, detail: dict) -> str:
    return json.dumps(
        {"seq": seq, "ts": ts, "actor": actor, "action": action, "detail": detail},
        sort_keys=True,
        separators=(",", ":"),
    )


class AuditLog:
    """Append-only, hash-chained audit log backed by a file."""

    def __init__(self, path: Path, secret: bytes) -> None:
        self._path = Path(path)
        self._secret = secret
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._seq, self._head = self._read_head()

    @property
    def path(self) -> Path:
        return self._path

    def _read_head(self) -> tuple[int, str]:
        if not self._path.exists():
            return 0, GENESIS
        seq, head = 0, GENESIS
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    seq = int(record["seq"])
                    head = str(record["digest"])
                except (ValueError, KeyError, TypeError):
                    # A malformed tail (e.g. a torn write) must not silently
                    # reset the chain; keep the last good head so the next
                    # append still commits to it and verification flags the gap.
                    continue
        return seq, head

    def record(self, action: str, *, actor: str = "system", **detail: object) -> AuditRecord:
        """Append one record and return it."""
        action = validate_text(action, field="action", max_len=64)
        actor = validate_text(actor, field="actor", max_len=64)
        safe_detail = _coerce_detail(detail)
        with self._lock:
            seq = self._seq + 1
            ts = time.time()
            payload = _payload(seq, ts, actor, action, safe_detail)
            digest = chain_digest(self._secret, self._head, payload)
            entry = AuditRecord(seq, ts, actor, action, safe_detail, self._head, digest)
            # Open per-append in exclusive-create-or-append mode and fsync so a
            # crash cannot lose an authorisation decision that already took effect.
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (entry.to_json() + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            self._seq, self._head = seq, digest
        return entry

    def read(self, limit: int = 200) -> list[AuditRecord]:
        """Return the most recent records, newest first."""
        records = list(self._iter_records())
        return list(reversed(records[-limit:]))

    def _iter_records(self) -> Iterator[AuditRecord]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    yield AuditRecord(
                        seq=int(raw["seq"]),
                        ts=float(raw["ts"]),
                        actor=str(raw["actor"]),
                        action=str(raw["action"]),
                        detail=dict(raw["detail"]),
                        prev=str(raw["prev"]),
                        digest=str(raw["digest"]),
                    )
                except (ValueError, KeyError, TypeError):
                    continue

    def verify_chain(self) -> tuple[bool, str]:
        """Recompute the chain. Returns ``(ok, message)``."""
        prev = GENESIS
        expected_seq = 1
        count = 0
        for record in self._iter_records():
            if record.seq != expected_seq:
                return False, f"sequence gap at record {record.seq} (expected {expected_seq})"
            if record.prev != prev:
                return False, f"record {record.seq} does not link to its predecessor"
            payload = _payload(record.seq, record.ts, record.actor, record.action, record.detail)
            if chain_digest(self._secret, prev, payload) != record.digest:
                return False, f"record {record.seq} digest mismatch (content was altered)"
            prev = record.digest
            expected_seq += 1
            count += 1
        return True, f"{count} record(s) verified"


def _coerce_detail(detail: dict) -> dict:
    """Reduce detail values to JSON-safe scalars with bounded size."""
    out: dict[str, object] = {}
    for key, value in list(detail.items())[:32]:
        name = str(key)[:64]
        if isinstance(value, (int, float, bool)) or value is None:
            out[name] = value
        elif isinstance(value, (list, tuple)):
            out[name] = [str(item)[:120] for item in list(value)[:20]]
        else:
            out[name] = str(value)[:500]
    return out
