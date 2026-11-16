"""ddgs (Dux Distributed Global Search) as one opaque metasearch provider.

Always called with backend="auto": ddgs picks and balances its own engines.
"""

from __future__ import annotations

from typing import Any

from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Provider
from web4free.quality import parse_date
from web4free.ratelimit import Bucket


class Ddgs(Provider):
    name = "ddgs"
    kind = "meta"
    upstream = "mixed"
    group = "ddgs"
    rate = (Bucket.every(3.0, burst=2),)
    caps = {
        "text": Caps("native", region=True, safesearch=True, paging=True, max_results=50),
        "news": Caps("native", region=True, safesearch=True, paging=True, max_results=50),
        "images": Caps("native", region=True, safesearch=True, paging=True, max_results=100),
        "videos": Caps("native", region=True, safesearch=True, paging=True, max_results=60),
    }
    priority = {"text": 60, "news": 60, "images": 60, "videos": 60}
    impersonate = False
    homepage = "https://github.com/deedy5/ddgs"
    basis = "MIT-licensed metasearch library (pip install ddgs), widely used by agent frameworks. Used with backend='auto'."
    privacy = "The engines ddgs picks receive the query and your IP address."
    limits = "Depends on the engines ddgs picks."

    def search(self, q: Query, limit: int) -> list[Result]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

        kwargs: dict[str, Any] = {
            "region": q.region if q.country else "wt-wt",
            "safesearch": q.safesearch,
            "timelimit": q.timelimit,
            "max_results": limit,
            "page": q.page,
            "backend": "auto",
        }
        o = q.options
        if q.category == "images":
            for ours, theirs in (
                ("size", "size"),
                ("color", "color"),
                ("type", "type_image"),
                ("layout", "layout"),
                ("license", "license_image"),
            ):
                if o.get(ours):
                    kwargs[theirs] = o[ours]
        elif q.category == "videos":
            for ours, theirs in (
                ("resolution", "resolution"),
                ("duration", "duration"),
                ("license", "license_videos"),
            ):
                if o.get(ours):
                    kwargs[theirs] = o[ours]
        timeout = max(2, int(self.ctx.request_timeout()))
        try:
            with DDGS(proxy=self.ctx.proxy, timeout=timeout) as d:
                rows = getattr(d, q.category)(q.text, **kwargs)
        except RatelimitException as ex:
            raise ProviderError(Outcome.RATE_LIMITED, str(ex)[:160]) from ex
        except TimeoutException as ex:
            raise ProviderError(Outcome.TIMEOUT, str(ex)[:160]) from ex
        except DDGSException as ex:
            msg = str(ex)
            if "no results" in msg.lower():
                return []
            raise ProviderError(Outcome.SERVER, msg[:160]) from ex
        return [self._convert(q.category, r) for r in rows][:limit]

    @staticmethod
    def _convert(category: str, r: dict[str, Any]) -> Result:
        if category == "text":
            return Result(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
        if category == "news":
            return Result(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("body", ""),
                date=parse_date(str(r.get("date") or "")),
                source=r.get("source"),
                image=r.get("image"),
            )
        if category == "images":
            return Result(
                title=r.get("title", ""),
                url=r.get("url", ""),
                image=r.get("image"),
                thumbnail=r.get("thumbnail"),
                width=_int(r.get("width")),
                height=_int(r.get("height")),
                source=r.get("source"),
            )
        images = r.get("images") or {}
        return Result(
            title=r.get("title", ""),
            url=r.get("content", ""),
            snippet=r.get("description", ""),
            duration=r.get("duration"),
            publisher=r.get("publisher"),
            date=parse_date(r.get("published")),
            thumbnail=images.get("medium") or images.get("small") if isinstance(images, dict) else None,
            embed_url=r.get("embed_url"),
        )


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
