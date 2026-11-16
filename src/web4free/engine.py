"""The scheduler: choose providers, order them by priority and health, respect rate
limits and cooldowns, retry transient failures, move down the list, merge results."""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from web4free.health import Health
from web4free.merge import Merger
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers import BY_NAME, KINDS, Context, Provider
from web4free.quality import assess, within


@dataclass
class Attempt:
    target: str
    outcome: str
    n: int = 0
    latency: float = 0.0
    detail: str = ""
    relevance: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"provider": self.target, "outcome": self.outcome}
        if self.n:
            d["results"] = self.n
        if self.latency:
            d["seconds"] = round(self.latency, 2)
        if self.relevance is not None:
            d["relevance"] = round(self.relevance, 2)
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class SearchResponse:
    query: Query
    results: list[Result]
    attempts: list[Attempt]
    strategy: str
    route: str
    elapsed: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        q = self.query
        return {
            "query": q.text,
            "category": q.category,
            "page": q.page,
            "timelimit": q.timelimit,
            "region": q.region,
            "safesearch": q.safesearch,
            "strategy": self.strategy,
            "route": self.route,
            "elapsed": round(self.elapsed, 2),
            "results": [r.to_dict(q.category) for r in self.results],
            "attempts": [a.to_dict() for a in self.attempts],
        }


@dataclass
class _Run:
    """Outcome of one provider call, before it is recorded."""

    target: Provider
    outcome: Outcome
    results: list[Result] = field(default_factory=list)
    latency: float = 0.0
    detail: str = ""
    relevance: float | None = None
    retry_after: float | None = None
    wait_needed: float = 0.0


class SelectionError(ValueError):
    pass


def validate_tokens(tokens: list[str], targets: list[Provider]) -> None:
    known = set(BY_NAME) | {f"@{k}" for k in KINDS} | {t.id for t in targets}
    bad = [t for t in tokens if t.lower() not in known and not t.lower().startswith("searxng:")]
    if bad:
        names = ", ".join(sorted(BY_NAME))
        raise SelectionError(
            f"unknown provider(s): {', '.join(bad)}. Known: {names}; groups: @api, @scrape, @meta; "
            "SearXNG instances as searxng:<host>."
        )


def matches(target: Provider, token: str) -> bool:
    token = token.lower().strip()
    if token.startswith("@"):
        return target.kind == token[1:]
    return token in (target.name, target.id)


class Engine:
    def __init__(self, ctx: Context, targets: list[Provider]) -> None:
        self.ctx = ctx
        self.targets = targets
        self.health = Health(ctx.store)

    # --- planning ----------------------------------------------------------------

    def plan(
        self,
        q: Query,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        force: bool = False,
    ) -> tuple[list[Provider], list[Attempt]]:
        include = [t.lower() for t in include or [] if t.strip()]
        exclude = [t.lower() for t in exclude or [] if t.strip()]
        validate_tokens(include + exclude, self.targets)
        route = self.ctx.route
        skipped: list[Attempt] = []
        ranked: list[tuple[tuple[float, ...], Provider]] = []
        for t in self.targets:
            if not t.supports(q.category):
                continue
            if include:
                positions = [i for i, tok in enumerate(include) if matches(t, tok)]
                if not positions:
                    continue
            elif not t.enabled_by_default():
                continue
            if any(matches(t, tok) for tok in exclude):
                continue
            caps = t.caps[q.category]
            reason = ""
            ok_route, why = t.available_on_route()
            if not ok_route:
                reason = why
            elif q.timelimit and caps.timelimit == "none":
                reason = "no time filter support"
            elif q.page > 1 and not caps.paging:
                reason = "no paging support"
            elif q.safesearch == "on" and not caps.safesearch:
                reason = "no strict safe search"
            elif not force:
                cd = self.health.cooldown(t.id, q.category, t.group_id, route)
                if cd:
                    until, why = cd
                    reason = f"cooling down {_fmt_secs(until - time.time())} ({why})"
            if reason:
                skipped.append(Attempt(t.id, Outcome.SKIPPED.value, detail=reason))
                continue
            score = t.base_priority(q.category) * self.health.factor(t.id, q.category, route)
            key = (float(positions[0]), -score) if include else (-score,)
            ranked.append((key, t))
        ranked.sort(key=lambda kv: kv[0])
        plan: list[Provider] = []
        per_name: dict[str, int] = {}
        for _, t in ranked:
            used = per_name.get(t.name, 0)
            if used >= t.max_targets:
                continue  # spare instance, not used in this search
            per_name[t.name] = used + 1
            plan.append(t)
        return plan, skipped

    # --- one provider call -----------------------------------------------------

    def _call(
        self, t: Provider, q: Query, retries: int, snippet_length: int, wait: float | None = None
    ) -> _Run:
        max_wait = self.ctx.max_wait if wait is None else wait
        ok, need = self.ctx.limiter.acquire(t.rate_key, t.buckets(), max_wait, self.ctx.deadline)
        if not ok:
            return _Run(
                t, Outcome.SKIPPED, detail=f"rate limit: next slot in {_fmt_secs(need)}", wait_needed=need
            )
        caps = t.caps[q.category]
        tries = 0
        while True:
            started = time.time()
            try:
                raw = t.search(q, q.max_results)
                latency = time.time() - started
                since = q.since()
                if since is not None:
                    if caps.timelimit == "post":
                        raw = [r for r in raw if within(r.date, since)]
                    else:  # native: still drop results whose date is known and too old
                        raw = [r for r in raw if r.date is None or within(r.date, since)]
                a = assess(q, raw, snippet_length, t.check_relevance)
                detail = f"{a.dropped} invalid/duplicate dropped" if a.dropped else ""
                return _Run(t, a.outcome, a.results, latency, detail, a.relevance)
            except ProviderError as ex:
                latency = time.time() - started
                if ex.kind is Outcome.SKIPPED:
                    return _Run(t, Outcome.SKIPPED, latency=latency, detail=ex.message)
                time_left = self.ctx.deadline - time.time()
                if ex.kind.is_transient and tries < retries and time_left > 4:
                    tries += 1
                    time.sleep(0.4 + random.random())
                    ok, _ = self.ctx.limiter.acquire(
                        t.rate_key, t.buckets(), min(max_wait, 2.0), self.ctx.deadline
                    )
                    if ok:
                        continue
                if ex.kind is Outcome.RATE_LIMITED:
                    self.ctx.limiter.drain(t.rate_key, t.buckets())
                return _Run(t, ex.kind, latency=latency, detail=ex.message, retry_after=ex.retry_after)
            except Exception as ex:  # a parser bug must not break the search
                return _Run(
                    t,
                    Outcome.PARSE,
                    latency=time.time() - started,
                    detail=f"{ex.__class__.__name__}: {str(ex)[:150]}",
                )

    def _record(self, run: _Run, q: Query, outcome: Outcome | None = None) -> None:
        outcome = outcome or run.outcome
        self.health.record(
            target=run.target.id,
            category=q.category,
            group=run.target.group_id,
            route=self.ctx.route,
            outcome=outcome,
            n=len(run.results),
            latency=run.latency,
            detail=run.detail,
            retry_after=run.retry_after,
            relevance=run.relevance,
        )

    # --- search ------------------------------------------------------------------

    def search(
        self,
        q: Query,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        strategy: str = "fallback",
        fanout: int = 3,
        retries: int = 1,
        deadline: float = 45.0,
        snippet_length: int = 400,
        force: bool = False,
        sort: str = "relevance",
    ) -> SearchResponse:
        started = time.time()
        self.ctx.deadline = started + deadline
        plan, skipped = self.plan(q, include, exclude, force)
        merger = Merger(q.category, strategy)
        runs: list[_Run] = []
        poor: list[_Run] = []

        def take(run: _Run) -> None:
            runs.append(run)
            if run.outcome is Outcome.OK:
                # priority is only a tie-breaker in rank fusion: agreement between
                # providers and rank decide the order, so results interleave
                weight = 0.95 + 0.05 * min(1.0, run.target.base_priority(q.category) / 100.0)
                merger.add(run.target.id, run.results, weight)
            elif run.outcome is Outcome.POOR:
                poor.append(run)
            if run.outcome not in (Outcome.EMPTY, Outcome.SKIPPED):
                self._record(run, q)

        if strategy == "fuse":
            self._fuse(plan, q, retries, snippet_length, fanout, take)
        else:
            for t in plan:
                if len(merger) >= q.max_results:
                    break
                if time.time() > self.ctx.deadline - 1:
                    runs.append(_Run(t, Outcome.SKIPPED, detail="search deadline reached"))
                    continue
                take(self._call(t, q, retries, snippet_length))

        # Nothing usable yet: give rate-limited providers a longer wait, best first.
        if len(merger) == 0:
            for run in sorted([r for r in runs if r.wait_needed > 0], key=lambda r: r.wait_needed):
                left = self.ctx.deadline - time.time() - 2
                if run.wait_needed > left:
                    continue
                take(self._call(run.target, q, retries, snippet_length, wait=left))
                if len(merger):
                    break

        if len(merger) == 0 and poor:
            best = max(poor, key=lambda r: (r.relevance or 0, len(r.results)))
            merger.add(best.target.id, best.results, 1.0)

        # An empty answer while others found plenty points at a broken or blocked provider.
        others_ok = any(r.outcome is Outcome.OK and len(r.results) >= 3 for r in runs)
        for run in runs:
            if run.outcome is Outcome.EMPTY:
                self._record(run, q, Outcome.EMPTY_SUSPECT if others_ok else Outcome.EMPTY)

        results = merger.results(q.max_results)
        if q.category == "news":
            for r in results:
                if not r.source:
                    r.source = (urlsplit(r.url).hostname or "").removeprefix("www.") or None
        if sort == "date":
            results.sort(key=lambda r: r.date or "", reverse=True)

        attempts = [
            Attempt(r.target.id, r.outcome.value, len(r.results), r.latency, r.detail, r.relevance)
            for r in runs
        ]
        tried = {a.target for a in attempts}
        attempts += [s for s in skipped if s.target not in tried]
        notes = []
        if not results:
            notes.append(_why_empty(attempts, plan))
        return SearchResponse(q, results, attempts, strategy, self.ctx.route, time.time() - started, notes)

    def _fuse(
        self, plan: list[Provider], q: Query, retries: int, snippet_length: int, fanout: int, take: Any
    ) -> None:
        remaining = list(plan)
        successes = 0
        while remaining and successes < fanout and time.time() < self.ctx.deadline - 1:
            want = fanout - successes
            wave: list[Provider] = []
            upstreams: set[str] = set()
            for t in list(remaining):  # prefer distinct upstream sources in a wave
                if len(wave) >= want:
                    break
                source = t.name if t.upstream == "mixed" else t.upstream
                if source not in upstreams:
                    wave.append(t)
                    upstreams.add(source)
                    remaining.remove(t)
            for t in list(remaining):
                if len(wave) >= want:
                    break
                wave.append(t)
                remaining.remove(t)
            with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                results = list(pool.map(lambda t: self._call(t, q, retries, snippet_length), wave))
            for run in results:
                take(run)
                if run.outcome is Outcome.OK:
                    successes += 1

    # --- health probe --------------------------------------------------------------

    def probe(
        self, q: Query, include: list[str] | None, exclude: list[str] | None, force: bool
    ) -> list[Attempt]:
        """Try every planned provider once (no early stop) and record the outcomes."""
        self.ctx.deadline = time.time() + 600
        plan, skipped = self.plan(q, include, exclude, force)
        out: list[Attempt] = []
        for t in plan:
            self.ctx.deadline = time.time() + max(self.ctx.timeout * 3, 20)
            run = self._call(t, q, 0, 200)
            if run.outcome is not Outcome.SKIPPED:
                self._record(run, q)
            out.append(
                Attempt(t.id, run.outcome.value, len(run.results), run.latency, run.detail, run.relevance)
            )
        return out + skipped


def _fmt_secs(s: float) -> str:
    s = max(0, int(s))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _why_empty(attempts: list[Attempt], plan: list[Provider]) -> str:
    if not plan and not attempts:
        return "no provider supports this request (check --include/--exclude and filters)"
    if not plan:
        return "every matching provider is cooling down or skipped; see attempts, or use --force"
    kinds = {a.outcome for a in attempts if a.outcome != Outcome.SKIPPED.value}
    if kinds <= {Outcome.EMPTY.value, Outcome.EMPTY_SUSPECT.value}:
        return "providers answered but found nothing for this query"
    return "all providers failed or were skipped; see attempts"
