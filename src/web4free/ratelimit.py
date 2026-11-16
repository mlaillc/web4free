"""Token-bucket rate limits, persisted so they hold across CLI runs and processes."""

from __future__ import annotations

import time
from dataclasses import dataclass

from web4free.store import Store


@dataclass(frozen=True)
class Bucket:
    """rate = tokens added per second; burst = bucket size."""

    rate: float
    burst: float

    @classmethod
    def per_minute(cls, n: float, burst: float | None = None) -> Bucket:
        return cls(n / 60.0, burst if burst is not None else max(1.0, n / 6))

    @classmethod
    def per_hour(cls, n: float, burst: float | None = None) -> Bucket:
        return cls(n / 3600.0, burst if burst is not None else max(1.0, n / 60))

    @classmethod
    def per_day(cls, n: float, burst: float | None = None) -> Bucket:
        return cls(n / 86400.0, burst if burst is not None else max(1.0, n / 24))

    @classmethod
    def every(cls, seconds: float, burst: float = 1.0) -> Bucket:
        return cls(1.0 / seconds, burst)


class RateLimiter:
    def __init__(self, store: Store) -> None:
        self.store = store

    def try_acquire(
        self, key: str, buckets: tuple[Bucket, ...] | list[Bucket], now: float | None = None
    ) -> float:
        """Take one token from every bucket. Returns 0 on success, else seconds to wait.

        Nothing is consumed unless all buckets have a token.
        """
        if not buckets:
            return 0.0
        now = time.time() if now is None else now
        with self.store.tx() as c:
            states: list[tuple[str, float]] = []
            wait = 0.0
            for i, b in enumerate(buckets):
                k = f"{key}#{i}"
                row = c.execute("SELECT tokens, updated FROM buckets WHERE key = ?", (k,)).fetchone()
                if row is None:
                    tokens = b.burst
                else:
                    tokens = min(b.burst, row["tokens"] + max(0.0, now - row["updated"]) * b.rate)
                states.append((k, tokens))
                if tokens < 1.0:
                    wait = max(wait, (1.0 - tokens) / b.rate)
            if wait > 0:
                return wait
            for k, tokens in states:
                c.execute(
                    "INSERT INTO buckets (key, tokens, updated) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET tokens = excluded.tokens, updated = excluded.updated",
                    (k, tokens - 1.0, now),
                )
            return 0.0

    def acquire(
        self,
        key: str,
        buckets: tuple[Bucket, ...] | list[Bucket],
        max_wait: float,
        deadline: float | None = None,
    ) -> tuple[bool, float]:
        """Wait up to max_wait (and not past deadline) for a token.

        Returns (acquired, seconds_still_needed_if_not).
        """
        waited = 0.0
        while True:
            need = self.try_acquire(key, buckets)
            if need <= 0:
                return True, 0.0
            remaining_wait = max_wait - waited
            if need > remaining_wait:
                return False, need
            if deadline is not None and time.time() + need > deadline:
                return False, need
            time.sleep(need + 0.01)
            waited += need + 0.01

    def drain(self, key: str, buckets: tuple[Bucket, ...] | list[Bucket]) -> None:
        """Empty the buckets (used after a 429 so the next call waits a full interval)."""
        now = time.time()
        with self.store.tx() as c:
            for i, _ in enumerate(buckets):
                c.execute(
                    "INSERT INTO buckets (key, tokens, updated) VALUES (?, 0, ?) "
                    "ON CONFLICT(key) DO UPDATE SET tokens = 0, updated = excluded.updated",
                    (f"{key}#{i}", now),
                )

    def reset(self, prefix: str = "") -> None:
        with self.store.tx() as c:
            c.execute("DELETE FROM buckets WHERE key LIKE ?", (prefix + "%",))
