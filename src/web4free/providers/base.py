"""Provider base class and shared context."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from web4free.http import Http, Response, app_user_agent, check
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.ratelimit import Bucket, RateLimiter
from web4free.store import Store


@dataclass(frozen=True)
class Caps:
    """What a provider supports for one category."""

    timelimit: str = "none"  # native | post (filter by result dates) | none
    region: bool = False
    safesearch: bool = False
    paging: bool = False  # can serve pages after the first
    max_results: int = 10  # most results one search call returns


@dataclass
class Context:
    store: Store
    limiter: RateLimiter
    config: dict[str, Any]
    proxy: str | None = None
    route: str = "direct"  # direct | proxy | tor
    timeout: float = 10.0
    max_wait: float = 3.0
    deadline: float = field(default_factory=lambda: time.time() + 3600)
    contact: str = ""

    def request_timeout(self) -> float:
        left = self.deadline - time.time()
        if left <= 0.5:
            raise ProviderError(Outcome.TIMEOUT, "search deadline reached")
        return min(self.timeout, left)

    def provider_config(self, name: str) -> dict[str, Any]:
        return dict(self.config.get("providers", {}).get(name, {}))


class Provider:
    name: ClassVar[str]
    kind: ClassVar[str]  # api (official keyless API) | scrape (public web page) | meta (metasearch)
    upstream: ClassVar[str]  # where results come from, used to spread fuse waves
    group: ClassVar[str]  # rate-limit and blocking scope, usually the host
    rate: ClassVar[tuple[Bucket, ...]]
    caps: ClassVar[dict[str, Caps]]
    priority: ClassVar[dict[str, float]]
    default_enabled: ClassVar[bool] = True
    impersonate: ClassVar[bool] = True  # False: honest User-Agent (open APIs)
    max_targets: ClassVar[int] = 1  # metasearch providers expand into several targets
    check_relevance: ClassVar[bool] = True  # False for feeds with titles only (too little text to judge)
    homepage: ClassVar[str] = ""
    basis: ClassVar[str] = ""  # why use is free and permitted, and who else uses it
    privacy: ClassVar[str] = ""
    limits: ClassVar[str] = ""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self._http: Http | None = None

    # --- identity --------------------------------------------------------------

    @property
    def id(self) -> str:
        """Target id: provider name, or provider:instance for metasearch instances."""
        return self.name

    @property
    def group_id(self) -> str:
        return self.group

    @property
    def rate_key(self) -> str:
        return f"{self.group_id}|{self.ctx.route}"

    def buckets(self) -> tuple[Bucket, ...]:
        over = self.ctx.provider_config(self.name)
        if "rate" in over:
            return (Bucket(float(over["rate"]), float(over.get("burst", 1))),)
        return self.rate

    def base_priority(self, category: str) -> float:
        over = self.ctx.provider_config(self.name)
        if "priority" in over:
            return float(over["priority"])
        return self.priority.get(category, 10.0)

    def enabled_by_default(self) -> bool:
        over = self.ctx.provider_config(self.name)
        return bool(over.get("enabled", self.default_enabled))

    def supports(self, category: str) -> bool:
        return category in self.caps

    def available_on_route(self) -> tuple[bool, str]:
        return True, ""

    # --- http ------------------------------------------------------------------

    def http(self) -> Http:
        if self._http is None:
            self._http = Http(
                proxy=self.ctx.proxy,
                timeout=self.ctx.timeout,
                impersonate=self.impersonate,
                user_agent=app_user_agent(self.ctx.contact),
                headers=self.default_headers(),
            )
        return self._http

    def default_headers(self) -> dict[str, str]:
        return {}

    def get(self, url: str, ok: tuple[int, ...] = (200,), **kwargs: Any) -> Response:
        return check(self.http().get(url, timeout=self.ctx.request_timeout(), **kwargs), ok)

    def post(self, url: str, ok: tuple[int, ...] = (200,), **kwargs: Any) -> Response:
        return check(self.http().post(url, timeout=self.ctx.request_timeout(), **kwargs), ok)

    def pace(self) -> bool:
        """Take a rate-limit token before an extra request (next page, helper call)."""
        ok, _ = self.ctx.limiter.acquire(self.rate_key, self.buckets(), self.ctx.max_wait, self.ctx.deadline)
        return ok

    # --- search ----------------------------------------------------------------

    def search(self, q: Query, limit: int) -> list[Result]:
        """Return up to `limit` results for page q.page (positions q.offset ...)."""
        fn: Callable[[Query, int], list[Result]] | None = getattr(self, f"search_{q.category}", None)
        if fn is None:
            raise ProviderError(Outcome.CONFIG, f"{self.name} does not support {q.category}")
        return fn(q, limit)

    def paged(
        self,
        q: Query,
        limit: int,
        page_size: int,
        fetch: Callable[[int], list[Result]],
        max_pages: int = 3,
    ) -> list[Result]:
        """Collect results across provider pages (1-based) for q.offset .. q.offset+limit."""
        first_page = q.offset // page_size + 1
        skip = q.offset % page_size
        out: list[Result] = []
        for i in range(max_pages):
            page = first_page + i
            if i > 0 and not self.pace():
                break
            batch = fetch(page)
            if i == 0:
                batch = batch[skip:]
            if not batch:
                break
            out.extend(batch)
            if len(out) >= limit or len(batch) < page_size // 2:
                break
        return out[:limit]

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.id}>"


def safesearch_level(q: Query) -> int:
    return {"off": 0, "moderate": 1, "on": 2}[q.safesearch]
