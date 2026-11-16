"""SQLite store for rate-limit buckets, provider health, cooldowns, events and small caches.

One file under the data dir. WAL mode plus BEGIN IMMEDIATE makes it safe when
several CLI processes run at the same time (for example an agent running
searches in parallel): they share rate limits and cooldowns.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS buckets (
    key TEXT PRIMARY KEY,
    tokens REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS health (
    target TEXT NOT NULL,
    category TEXT NOT NULL,
    route TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 0,
    empty INTEGER NOT NULL DEFAULT 0,
    empty_suspect INTEGER NOT NULL DEFAULT 0,
    poor INTEGER NOT NULL DEFAULT 0,
    rate_limited INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    server_error INTEGER NOT NULL DEFAULT 0,
    network_error INTEGER NOT NULL DEFAULT 0,
    timeout INTEGER NOT NULL DEFAULT 0,
    parse_error INTEGER NOT NULL DEFAULT 0,
    config_error INTEGER NOT NULL DEFAULT 0,
    results_total INTEGER NOT NULL DEFAULT 0,
    latency_ewma REAL,
    success_ewma REAL,
    relevance_ewma REAL,
    consecutive_fail INTEGER NOT NULL DEFAULT 0,
    last_attempt REAL,
    last_ok REAL,
    last_outcome TEXT,
    last_error TEXT,
    PRIMARY KEY (target, category, route)
);
CREATE TABLE IF NOT EXISTS cooldowns (
    key TEXT PRIMARY KEY,
    until REAL NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT,
    strikes INTEGER NOT NULL DEFAULT 1,
    set_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    target TEXT NOT NULL,
    category TEXT NOT NULL,
    route TEXT NOT NULL,
    outcome TEXT NOT NULL,
    n INTEGER,
    latency REAL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated REAL NOT NULL
);
"""

MAX_EVENTS = 5000


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=20, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction, exclusive across threads and processes."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    # --- small key/value cache -------------------------------------------------

    def kv_get(self, key: str, max_age: float | None = None) -> Any:
        rows = self.query("SELECT value, updated FROM kv WHERE key = ?", (key,))
        if not rows:
            return None
        if max_age is not None and time.time() - rows[0]["updated"] > max_age:
            return None
        return json.loads(rows[0]["value"])

    def kv_age(self, key: str) -> float | None:
        rows = self.query("SELECT updated FROM kv WHERE key = ?", (key,))
        return time.time() - rows[0]["updated"] if rows else None

    def kv_set(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO kv (key, value, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                (key, json.dumps(value), time.time()),
            )

    def kv_delete(self, prefix: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM kv WHERE key LIKE ?", (prefix + "%",))

    def add_event(
        self, target: str, category: str, route: str, outcome: str, n: int, latency: float, detail: str
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO events (ts, target, category, route, outcome, n, latency, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), target, category, route, outcome, n, latency, detail[:500]),
            )
            c.execute(
                "DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?",
                (MAX_EVENTS,),
            )
