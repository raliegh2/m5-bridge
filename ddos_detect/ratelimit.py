"""Token-bucket rate limiting.

Applied to the API and, more tightly, to the login endpoint. The limiter is
keyed by client address plus route class, and idle buckets are reaped so the
limiter itself cannot be turned into a memory-exhaustion target by rotating
keys - a real concern in a system whose whole subject is flooding.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .errors import RateLimitError

MAX_BUCKETS = 10_000
REAP_AFTER = 900.0


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Fixed-rate token bucket with burst equal to the per-minute allowance."""

    def __init__(self, per_minute: int, burst: int | None = None) -> None:
        self.rate = max(1, int(per_minute)) / 60.0
        self.capacity = float(burst if burst is not None else max(1, int(per_minute)))
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._last_reap = time.monotonic()

    def check(self, key: str, *, cost: float = 1.0, now: float | None = None) -> None:
        """Consume ``cost`` tokens for ``key`` or raise :class:`RateLimitError`."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._reap(now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, updated=now)
                if len(self._buckets) >= MAX_BUCKETS:
                    # Full table: fail closed for new keys rather than evicting
                    # an existing one, which an attacker could exploit to reset
                    # their own bucket by flooding the table.
                    raise RateLimitError("server is shedding load; try again shortly", 5.0)
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.rate)
            bucket.updated = now
            if bucket.tokens < cost:
                deficit = cost - bucket.tokens
                raise RateLimitError(
                    "rate limit exceeded", retry_after=max(1.0, deficit / self.rate)
                )
            bucket.tokens -= cost

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    def _reap(self, now: float) -> None:
        if now - self._last_reap < 60.0:
            return
        self._last_reap = now
        stale = [k for k, b in self._buckets.items() if now - b.updated > REAP_AFTER]
        for key in stale:
            del self._buckets[key]
