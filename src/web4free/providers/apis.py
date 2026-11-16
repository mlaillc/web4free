"""Search vendors that officially offer keyless (no account, no key) access.

These have the clearest permission of all providers: each vendor documents the
keyless tier. They log queries, so do not send secrets in a search.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from web4free.http import check
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Provider
from web4free.providers.mcp import MCPClient
from web4free.quality import parse_date
from web4free.ratelimit import Bucket

RELATIVE_SPAN = {"d": "1d", "w": "7d", "m": "30d", "y": "365d"}


def _seconds_to_utc_midnight() -> float:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds() + 60


def _load(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as ex:
        raise ProviderError(Outcome.PARSE, f"unexpected tool output: {text[:120]!r}") from ex


class Exa(Provider):
    name = "exa"
    kind = "api"
    upstream = "exa"
    group = "mcp.exa.ai"
    rate = (Bucket.every(2.0, burst=3),)
    caps = {
        "text": Caps("native", region=True, safesearch=True, paging=True, max_results=100),
        "news": Caps("native", region=True, safesearch=True, paging=True, max_results=100),
    }
    priority = {"text": 100, "news": 80}
    impersonate = False
    homepage = "https://exa.ai/docs/get-started/exa-mcp"
    basis = "Exa's hosted MCP server has an official keyless tier: 'No API key is needed to try search and fetch, at a free rate limit.' Own neural index."
    privacy = "Exa receives the query and your IP address."
    limits = "Free rate limit, number not published; HTTP 429 when exceeded."

    URL = "https://mcp.exa.ai/mcp?tools=web_search_advanced_exa"

    def _mcp(self) -> MCPClient:
        return MCPClient(self.URL, self.http(), self.ctx.store, f"exa|{self.ctx.route}")

    def _search(self, q: Query, limit: int, category: str | None) -> list[Result]:
        n = min(100, q.offset + limit)
        args: dict[str, Any] = {
            "query": q.text,
            "numResults": n,
            "type": "auto",
            "enableHighlights": True,
            "highlightsMaxCharacters": 600,
        }
        if category:
            args["category"] = category
        since = q.since()
        if since:
            args["startPublishedDate"] = since.date().isoformat()
        if q.country:
            args["userLocation"] = q.country.upper()
        if q.safesearch != "off":
            args["moderation"] = True
        data = _load(self._mcp().call_tool("web_search_advanced_exa", args, self.ctx.request_timeout()))
        out = []
        for item in data.get("results", []):
            highlights = item.get("highlights") or []
            snippet = (
                " … ".join(h for h in highlights if h)
                or item.get("summary")
                or (item.get("text") or "")[:800]
            )
            out.append(
                Result(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=snippet,
                    date=parse_date(item.get("publishedDate")),
                    image=item.get("image") if category == "news" else None,
                )
            )
        return out[q.offset :]

    def search_text(self, q: Query, limit: int) -> list[Result]:
        return self._search(q, limit, None)

    def search_news(self, q: Query, limit: int) -> list[Result]:
        return self._search(q, limit, "news")


class Keenable(Provider):
    name = "keenable"
    kind = "api"
    upstream = "keenable"
    group = "api.keenable.ai"
    rate = (Bucket.per_hour(900, burst=10),)
    caps = {"text": Caps("native", safesearch=True, paging=True, max_results=50)}
    priority = {"text": 95}
    impersonate = False
    homepage = "https://docs.keenable.ai/api-reference/search"
    basis = "Official keyless endpoint POST /v1/search/public ('Search and fetch work without an account or API key'). Own index."
    privacy = "Keenable logs and stores keyless queries and results (per its docs)."
    limits = "1,000 requests/hour and 10/second per IP without a key."

    URL = "https://api.keenable.ai/v1/search/public"

    def default_headers(self) -> dict[str, str]:
        return {"X-Keenable-Title": "web4free", "Content-Type": "application/json"}

    def search_text(self, q: Query, limit: int) -> list[Result]:
        body: dict[str, Any] = {
            "query": q.text,
            "max_results": min(50, q.offset + limit),
            "mode": "pro",
            "snippet_max_length": 600,
        }
        if q.timelimit:
            body["published_after"] = RELATIVE_SPAN[q.timelimit]
        if q.safesearch != "off":
            body["safety_mode"] = "strict" if q.safesearch == "on" else "moderate"
        data = self.post(self.URL, json=body).json()
        out = []
        for item in data.get("results", []):
            url = item.get("url") or ""
            if url.startswith("https://cards.keenable.ai/"):
                continue  # generated answer cards expire after a day
            out.append(
                Result(
                    title=item.get("title") or "",
                    url=url,
                    snippet=item.get("snippet") or item.get("description") or "",
                    date=parse_date(item.get("published_at")),
                )
            )
        return out[q.offset :]


class Firecrawl(Provider):
    name = "firecrawl"
    kind = "api"
    upstream = "firecrawl"
    group = "api.firecrawl.dev"
    rate = (Bucket.per_minute(10, burst=3),)
    caps = {
        "text": Caps("native", region=True, paging=True, max_results=100),
        "news": Caps("post", region=True, paging=True, max_results=100),
        "images": Caps("none", region=True, paging=True, max_results=100),
    }
    priority = {"text": 92, "news": 90, "images": 76}
    impersonate = False
    homepage = "https://docs.firecrawl.dev/rate-limits"
    basis = (
        "Official keyless access (launched June 2026): REST /v2/search works with no Authorization header."
    )
    privacy = "Firecrawl receives the query and your IP address."
    limits = "Free plan limits per IP: 10 search requests/minute plus a daily request and credit cap (reset 00:00 UTC)."

    URL = "https://api.firecrawl.dev/v2/search"

    def _search(self, q: Query, limit: int, source: str) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "query": q.text,
            "limit": min(100, q.offset + limit),
            "sources": [source],
            "timeout": int(self.ctx.request_timeout() * 1000),
        }
        if source == "web" and q.timelimit:
            body["tbs"] = f"qdr:{q.timelimit}"
        if q.country:
            body["country"] = q.country.upper()
        resp = self.http().post(self.URL, json=body, timeout=self.ctx.request_timeout())
        if resp.status == 429:
            text = resp.text.lower()
            retry = resp.retry_after()
            if retry is None and ("day" in text or "daily" in text):
                retry = _seconds_to_utc_midnight()
            raise ProviderError(Outcome.RATE_LIMITED, f"HTTP 429: {resp.text[:120]}", retry)
        if resp.status == 402:
            raise ProviderError(
                Outcome.RATE_LIMITED, "keyless credit cap reached", _seconds_to_utc_midnight()
            )
        check(resp)
        data = resp.json()
        if not data.get("success", True):
            raise ProviderError(Outcome.SERVER, str(data.get("error") or "search failed")[:160])
        items = (data.get("data") or {}).get(source) or []
        return items[q.offset :]

    def search_text(self, q: Query, limit: int) -> list[Result]:
        return [
            Result(title=i.get("title") or "", url=i.get("url") or "", snippet=i.get("description") or "")
            for i in self._search(q, limit, "web")
        ]

    def search_news(self, q: Query, limit: int) -> list[Result]:
        return [
            Result(
                title=i.get("title") or "",
                url=i.get("url") or "",
                snippet=i.get("snippet") or i.get("description") or "",
                date=parse_date(i.get("date")),
                image=i.get("imageUrl"),
            )
            for i in self._search(q, limit, "news")
        ]

    def search_images(self, q: Query, limit: int) -> list[Result]:
        return [
            Result(
                title=i.get("title") or "",
                url=i.get("url") or "",
                image=i.get("imageUrl"),
                width=i.get("imageWidth"),
                height=i.get("imageHeight"),
            )
            for i in self._search(q, limit, "images")
        ]


class Parallel(Provider):
    name = "parallel"
    kind = "api"
    upstream = "parallel"
    group = "mcp.parallel.ai"
    rate = (Bucket.every(3.0, burst=3),)
    caps = {
        "text": Caps("post", max_results=10),
        "images": Caps("none", max_results=20),
    }
    priority = {"text": 88, "images": 72}
    impersonate = False
    homepage = "https://parallel.ai/articles/best-free-web-search-api"
    basis = "Parallel's hosted MCP server allows anonymous use: 'needs no account, API key, or card, and anonymous use is free at lower rate limits.'"
    privacy = "Parallel receives the query and your IP address."
    limits = "Lower anonymous rate limits, number not published."

    URL = "https://mcp.parallel.ai/mcp"

    def _mcp(self) -> MCPClient:
        return MCPClient(self.URL, self.http(), self.ctx.store, f"parallel|{self.ctx.route}")

    def search_text(self, q: Query, limit: int) -> list[Result]:
        args = {"objective": q.text, "search_queries": [q.text]}
        data = _load(self._mcp().call_tool("web_search", args, self.ctx.request_timeout()))
        out = []
        for item in data.get("results", []):
            excerpts = item.get("excerpts") or []
            out.append(
                Result(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=" … ".join(e for e in excerpts if e)[:1200],
                    date=parse_date(item.get("publish_date")),
                )
            )
        return out[:limit]

    def search_images(self, q: Query, limit: int) -> list[Result]:
        args = {"objective": f"images of {q.text}", "search_queries": [q.text]}
        data = _load(self._mcp().call_tool("image_search", args, self.ctx.request_timeout()))
        return [
            Result(
                title=item.get("title") or "",
                url=item.get("source_page_url") or "",
                image=item.get("image_url"),
                width=item.get("width"),
                height=item.get("height"),
            )
            for item in data.get("results", [])
        ][:limit]
