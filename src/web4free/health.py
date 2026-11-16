"""Provider health: per-attempt stats, cooldowns (circuit breaker) and a score used for ordering.

Two cooldown scopes:
- group: blocks and rate limits apply to a whole host for this network route
  (a captcha from bing.com hits Bing web, news, images and videos alike);
- target: parse drift, repeated timeouts, empty or poor results apply to one
  provider target and one category.

Cooldowns grow exponentially with repeated strikes and reset on success.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from web4free.models import Outcome
from web4free.store import Store


@dataclass(frozen=True)
class Policy:
    base: float  # seconds for the first cooldown
    cap: float  # longest cooldown
    scope: str  # "group" or "target"
    after: int  # consecutive failures before a cooldown starts


HOUR = 3600.0
POLICIES: dict[Outcome, Policy] = {
    Outcome.RATE_LIMITED: Policy(300, 6 * HOUR, "group", 1),
    Outcome.BLOCKED: Policy(1800, 24 * HOUR, "group", 1),
    Outcome.SERVER: Policy(60, HOUR, "target", 2),
    Outcome.NETWORK: Policy(60, HOUR, "target", 2),
    Outcome.TIMEOUT: Policy(60, HOUR, "target", 2),
    Outcome.PARSE: Policy(HOUR, 24 * HOUR, "target", 2),
    Outcome.EMPTY_SUSPECT: Policy(1800, 12 * HOUR, "target", 3),
    Outcome.POOR: Policy(900, 6 * HOUR, "target", 3),
}

PRIOR = 0.75  # assumed success rate for a provider with no recent history
HALF_LIFE = 6 * HOUR  # old evidence fades toward PRIOR so providers get retried
ALPHA = 0.3  # EWMA weight of the newest observation

COUNTER_COLUMNS = {
    Outcome.OK: "ok",
    Outcome.EMPTY: "empty",
    Outcome.EMPTY_SUSPECT: "empty_suspect",
    Outcome.POOR: "poor",
    Outcome.RATE_LIMITED: "rate_limited",
    Outcome.BLOCKED: "blocked",
    Outcome.SERVER: "server_error",
    Outcome.NETWORK: "network_error",
    Outcome.TIMEOUT: "timeout",
    Outcome.PARSE: "parse_error",
    Outcome.CONFIG: "config_error",
}

SUCCESS_VALUE = {Outcome.OK: 1.0, Outcome.EMPTY: 0.6, Outcome.POOR: 0.2}


def group_key(group: str, route: str) -> str:
    return f"group:{group}|{route}"


def target_key(target: str, category: str, route: str) -> str:
    return f"target:{target}|{category}|{route}"


def _ewma(old: float | None, new: float) -> float:
    return new if old is None else (1 - ALPHA) * old + ALPHA * new


class Health:
    def __init__(self, store: Store) -> None:
        self.store = store

    # --- cooldowns -------------------------------------------------------------

    def cooldown(self, target: str, category: str, group: str, route: str) -> tuple[float, str] | None:
        """Return (until, reason) if the target may not be used now."""
        now = time.time()
        rows = self.store.query(
            "SELECT until, kind, reason FROM cooldowns WHERE key IN (?, ?) AND until > ? ORDER BY until DESC",
            (group_key(group, route), target_key(target, category, route), now),
        )
        if not rows:
            return None
        r = rows[0]
        return r["until"], f"{r['kind']}: {r['reason'] or ''}".strip(": ")

    def _set_cooldown(
        self, c: Any, key: str, policy: Policy, kind: Outcome, reason: str, retry_after: float | None
    ) -> float:
        now = time.time()
        row = c.execute("SELECT strikes FROM cooldowns WHERE key = ?", (key,)).fetchone()
        strikes = (row["strikes"] + 1) if row else 1
        if retry_after and retry_after > 0:
            duration = min(policy.cap, max(retry_after, 5.0))
        else:
            duration = min(policy.cap, policy.base * 2 ** (strikes - 1))
        until = now + duration
        c.execute(
            "INSERT INTO cooldowns (key, until, kind, reason, strikes, set_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET until = excluded.until, kind = excluded.kind, "
            "reason = excluded.reason, strikes = excluded.strikes, set_at = excluded.set_at",
            (key, until, kind.value, reason[:200], strikes, now),
        )
        return until

    # --- recording ---------------------------------------------------------------

    def record(
        self,
        *,
        target: str,
        category: str,
        group: str,
        route: str,
        outcome: Outcome,
        n: int = 0,
        latency: float = 0.0,
        detail: str = "",
        retry_after: float | None = None,
        relevance: float | None = None,
    ) -> float | None:
        """Record one attempt. Returns the cooldown end time if one was set."""
        if outcome is Outcome.SKIPPED:
            return None
        now = time.time()
        col = COUNTER_COLUMNS[outcome]
        until = None
        with self.store.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO health (target, category, route) VALUES (?, ?, ?)",
                (target, category, route),
            )
            row = c.execute(
                "SELECT success_ewma, latency_ewma, relevance_ewma, consecutive_fail FROM health "
                "WHERE target = ? AND category = ? AND route = ?",
                (target, category, route),
            ).fetchone()
            success = _ewma(row["success_ewma"], SUCCESS_VALUE.get(outcome, 0.0))
            lat = _ewma(row["latency_ewma"], latency) if latency > 0 else row["latency_ewma"]
            rel = _ewma(row["relevance_ewma"], relevance) if relevance is not None else row["relevance_ewma"]
            if outcome is Outcome.OK:
                consecutive = 0
            elif outcome.is_failure:
                consecutive = row["consecutive_fail"] + 1
            else:
                consecutive = row["consecutive_fail"]
            c.execute(
                f"UPDATE health SET attempts = attempts + 1, {col} = {col} + 1, "
                "results_total = results_total + ?, latency_ewma = ?, success_ewma = ?, relevance_ewma = ?, "
                "consecutive_fail = ?, last_attempt = ?, last_ok = CASE WHEN ? THEN ? ELSE last_ok END, "
                "last_outcome = ?, last_error = CASE WHEN ? THEN ? ELSE last_error END "
                "WHERE target = ? AND category = ? AND route = ?",
                (
                    n,
                    lat,
                    success,
                    rel,
                    consecutive,
                    now,
                    outcome is Outcome.OK,
                    now,
                    outcome.value,
                    outcome.is_failure,
                    detail[:300],
                    target,
                    category,
                    route,
                ),
            )
            if outcome is Outcome.OK:
                c.execute(
                    "DELETE FROM cooldowns WHERE key IN (?, ?)",
                    (target_key(target, category, route), group_key(group, route)),
                )
            policy = POLICIES.get(outcome)
            if policy and consecutive >= policy.after:
                key = (
                    group_key(group, route)
                    if policy.scope == "group"
                    else target_key(target, category, route)
                )
                until = self._set_cooldown(c, key, policy, outcome, detail, retry_after)
        self.store.add_event(target, category, route, outcome.value, n, latency, detail)
        return until

    # --- scoring -----------------------------------------------------------------

    def factor(self, target: str, category: str, route: str) -> float:
        """0..1 multiplier on a provider's base priority."""
        rows = self.store.query(
            "SELECT success_ewma, latency_ewma, relevance_ewma, last_attempt FROM health "
            "WHERE target = ? AND category = ? AND route = ?",
            (target, category, route),
        )
        if not rows or rows[0]["success_ewma"] is None:
            return 0.2 + 0.8 * PRIOR
        r = rows[0]
        age = max(0.0, time.time() - (r["last_attempt"] or 0))
        decay = 0.5 ** (age / HALF_LIFE)
        success = PRIOR + (r["success_ewma"] - PRIOR) * decay
        lat = r["latency_ewma"] if r["latency_ewma"] is not None else 2.0
        rel = r["relevance_ewma"] if r["relevance_ewma"] is not None else 0.8
        return (0.2 + 0.8 * success) * (1.0 / (1.0 + lat / 10.0)) * (0.6 + 0.4 * rel)

    # --- reporting / maintenance ------------------------------------------------

    def rows(self, category: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM health"
        params: tuple[Any, ...] = ()
        if category:
            sql += " WHERE category = ?"
            params = (category,)
        sql += " ORDER BY category, target, route"
        return [dict(r) for r in self.store.query(sql, params)]

    def active_cooldowns(self) -> list[dict[str, Any]]:
        now = time.time()
        return [
            dict(r)
            for r in self.store.query("SELECT * FROM cooldowns WHERE until > ? ORDER BY until", (now,))
        ]

    def events(self, limit: int = 50, target: str | None = None) -> list[dict[str, Any]]:
        if target:
            rows = self.store.query(
                "SELECT * FROM events WHERE target = ? OR target LIKE ? ORDER BY id DESC LIMIT ?",
                (target, target + ":%", limit),
            )
        else:
            rows = self.store.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def reset(self, target: str | None = None) -> None:
        with self.store.tx() as c:
            if target is None:
                for table in ("health", "cooldowns", "events", "buckets"):
                    c.execute(f"DELETE FROM {table}")
                return
            like = target + ":%"
            c.execute("DELETE FROM health WHERE target = ? OR target LIKE ?", (target, like))
            c.execute("DELETE FROM events WHERE target = ? OR target LIKE ?", (target, like))
            c.execute(
                "DELETE FROM cooldowns WHERE key LIKE ? OR key LIKE ?",
                (f"target:{target}|%", f"target:{target}:%"),
            )

    def clear_cooldowns(self, target: str | None = None) -> int:
        with self.store.tx() as c:
            if target is None:
                cur = c.execute("DELETE FROM cooldowns")
            else:
                cur = c.execute(
                    "DELETE FROM cooldowns WHERE key LIKE ? OR key LIKE ? OR key LIKE ?",
                    (f"target:{target}|%", f"target:{target}:%", f"group:{target}%"),
                )
            return cur.rowcount
