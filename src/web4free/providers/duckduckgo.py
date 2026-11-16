"""DuckDuckGo: no-JS html page for web results; news.js / i.js / v.js JSON for the rest.

Same endpoints as SearXNG and ddgs. Over Tor, the official onion service is used.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lxml import html as lxml_html

from web4free.http import check, empty_or_blocked
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Provider
from web4free.quality import parse_date
from web4free.ratelimit import Bucket

ONION = "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
VQD_RE = re.compile(r"""vqd=["']?([0-9-]+)""")
VQD_TTL = 20 * 60

IMAGE_TIME = {"d": "Day", "w": "Week", "m": "Month", "y": "Year"}


def unwrap(href: str) -> str:
    """DuckDuckGo html links can be /l/?uddg=<target> redirects."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.path.startswith("/l/") or "uddg=" in parts.query:
        target = parse_qs(parts.query).get("uddg")
        if target:
            return target[0]
    return href


def parse_html(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for item in tree.xpath("//div[contains(@class, 'result__body') or contains(@class, 'links_main')]"):
        cls = " ".join(a.get("class", "") for a in item.iterancestors() if a.tag == "div")
        if "result--ad" in cls:
            continue
        link = item.xpath(".//a[contains(@class, 'result__a')]")
        if not link:
            continue
        href = unwrap(link[0].get("href", ""))
        snippet = item.xpath(".//*[contains(@class, 'result__snippet')]")
        out.append(
            Result(
                title=link[0].text_content(),
                url=href,
                snippet=snippet[0].text_content() if snippet else "",
            )
        )
    return out


def parse_lite(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for link in tree.xpath("//a[contains(@class, 'result-link')]"):
        row = link.getparent().getparent() if link.getparent() is not None else None
        snippet = ""
        if row is not None:
            nxt = row.getnext()
            if nxt is not None:
                cell = nxt.xpath(".//td[contains(@class, 'result-snippet')]")
                snippet = cell[0].text_content() if cell else ""
        out.append(Result(title=link.text_content(), url=unwrap(link.get("href", "")), snippet=snippet))
    return out


class DuckDuckGo(Provider):
    name = "duckduckgo"
    kind = "scrape"
    upstream = "bing"
    group = "duckduckgo.com"
    rate = (Bucket.every(4.0, burst=2),)
    caps = {
        "text": Caps("native", region=True, safesearch=True, paging=True, max_results=10),
        "news": Caps("native", region=True, safesearch=True, paging=True, max_results=30),
        "images": Caps("native", region=True, safesearch=True, paging=True, max_results=100),
        "videos": Caps("native", region=True, safesearch=True, paging=True, max_results=60),
    }
    priority = {"text": 80, "news": 84, "images": 80, "videos": 85}
    homepage = "https://html.duckduckgo.com/html/"
    basis = "Public no-JavaScript search pages and JSON endpoints, the same ones used by SearXNG and ddgs. Official onion service over Tor."
    privacy = "DuckDuckGo states it does not store personal search history."
    limits = "Unpublished; answers HTTP 202 with a duck captcha when it decides traffic is automated."

    def _base(self, sub: str) -> str:
        if self.ctx.route == "tor":
            return f"https://{ONION}"
        return f"https://{sub}duckduckgo.com"

    def _region(self, q: Query) -> str:
        return q.region if q.country else "wt-wt"

    # --- web -----------------------------------------------------------------

    def search_text(self, q: Query, limit: int) -> list[Result]:
        kp = {"on": "1", "moderate": "-1", "off": "-2"}[q.safesearch]

        def fetch(page: int) -> list[Result]:
            params: dict[str, Any] = {"q": q.text, "kl": self._region(q), "kp": kp}
            if q.timelimit:
                params["df"] = q.timelimit
            if page > 1:
                params["s"] = str((page - 1) * 10)
                params["dc"] = str((page - 1) * 10 + 1)
            base = self._base("html.")
            resp = self.get(f"{base}/html/", params=params, headers={"Referer": f"{base}/"})
            results = parse_html(resp.text)
            if results:
                return results
            empty_or_blocked(resp)
            if "No results." in resp.text or "No  results" in resp.text:
                return []
            # html layout changed or was empty: try the lite page once
            if not self.pace():
                return []
            lite = self.get(f"{self._base('lite.')}/lite/", params=params)
            results = parse_lite(lite.text)
            return results or empty_or_blocked(lite)

        return self.paged(q, limit, 10, fetch, max_pages=3)

    # --- vqd token for the JSON endpoints ---------------------------------------

    def _vqd(self, q: Query) -> str:
        key = f"ddg_vqd:{self.ctx.route}:{q.text}"
        cached = self.ctx.store.kv_get(key, max_age=VQD_TTL)
        if cached:
            return str(cached)
        resp = self.get(f"{self._base('')}/", params={"q": q.text, "ia": "web"})
        m = VQD_RE.search(resp.text)
        if not m:
            empty_or_blocked(resp)
            raise ProviderError(Outcome.PARSE, "vqd token not found")
        self.ctx.store.kv_set(key, m.group(1))
        if not self.pace():
            raise ProviderError(Outcome.SKIPPED, "rate limit after vqd request")
        return m.group(1)

    def _json(self, path: str, q: Query, params: dict[str, Any]) -> list[dict[str, Any]]:
        vqd = self._vqd(q)
        base = self._base("")
        params = {"q": q.text, "o": "json", "l": self._region(q), "vqd": vqd, **params}
        resp = self.http().get(
            f"{base}/{path}",
            params=params,
            headers={"Referer": f"{base}/", "Accept": "application/json, text/javascript, */*; q=0.01"},
            timeout=self.ctx.request_timeout(),
        )
        if resp.status == 403 or (resp.status == 200 and not resp.text.lstrip().startswith("{")):
            # stale vqd or challenge; forget the token so the next call fetches a new one
            self.ctx.store.kv_delete(f"ddg_vqd:{self.ctx.route}:{q.text}")
        check(resp)
        if not resp.text.lstrip().startswith("{"):
            empty_or_blocked(resp)
            raise ProviderError(Outcome.PARSE, "expected JSON from DuckDuckGo")
        return resp.json().get("results") or []

    def search_news(self, q: Query, limit: int) -> list[Result]:
        params: dict[str, Any] = {"noamp": "1", "p": {"on": "1", "moderate": "-1", "off": "-2"}[q.safesearch]}
        if q.timelimit:
            params["df"] = q.timelimit
        if q.offset:
            params["s"] = str(q.offset)
        items = self._json("news.js", q, params)
        return [
            Result(
                title=i.get("title") or "",
                url=i.get("url") or "",
                snippet=i.get("excerpt") or "",
                date=parse_date(str(i.get("date") or "")),
                source=i.get("source"),
                image=i.get("image"),
            )
            for i in items
        ][:limit]

    def search_images(self, q: Query, limit: int) -> list[Result]:
        o = q.options
        filters = [
            f"time:{IMAGE_TIME[q.timelimit]}" if q.timelimit else "",
            f"size:{o['size']}" if o.get("size") else "",
            f"color:{o['color']}" if o.get("color") else "",
            f"type:{o['type']}" if o.get("type") else "",
            f"layout:{o['layout']}" if o.get("layout") else "",
            f"license:{o['license']}" if o.get("license") else "",
        ]
        params: dict[str, Any] = {
            "p": "-1" if q.safesearch == "off" else "1",
            "ct": q.country.upper() or "US",
        }
        if any(filters):
            params["f"] = ",".join(filters)
        if q.offset:
            params["s"] = str(q.offset)
        items = self._json("i.js", q, params)
        return [
            Result(
                title=i.get("title") or "",
                url=i.get("url") or "",
                image=i.get("image"),
                thumbnail=i.get("thumbnail"),
                width=i.get("width"),
                height=i.get("height"),
                source=i.get("source"),
            )
            for i in items
        ][:limit]

    def search_videos(self, q: Query, limit: int) -> list[Result]:
        o = q.options
        filters = [
            f"publishedAfter:{q.timelimit}" if q.timelimit else "",
            f"videoDefinition:{o['resolution']}" if o.get("resolution") else "",
            f"videoDuration:{o['duration']}" if o.get("duration") else "",
            f"videoLicense:{o['license']}" if o.get("license") else "",
        ]
        params: dict[str, Any] = {"p": {"on": "1", "moderate": "-1", "off": "-2"}[q.safesearch]}
        if any(filters):
            params["f"] = ",".join(filters)
        if q.offset:
            params["s"] = str(q.offset)
        items = self._json("v.js", q, params)
        out = []
        for i in items:
            images = i.get("images") or {}
            out.append(
                Result(
                    title=i.get("title") or "",
                    url=i.get("content") or "",
                    snippet=i.get("description") or "",
                    duration=i.get("duration"),
                    publisher=i.get("publisher") or (i.get("uploader") or None),
                    date=parse_date(i.get("published")),
                    thumbnail=images.get("medium") or images.get("small"),
                    embed_url=i.get("embed_url"),
                )
            )
        return out[:limit]
