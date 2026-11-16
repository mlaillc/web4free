"""Open, keyless APIs and feeds: Wikipedia, Wikimedia Commons, Openverse, Mwmbl, GDELT,
Hacker News (Algolia) and Google News RSS.

These send an honest web4free User-Agent (Wikimedia's policy asks for one).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from lxml import etree

from web4free.http import check
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Provider
from web4free.quality import parse_date
from web4free.ratelimit import Bucket

GNEWS_WHEN = {"d": "1d", "w": "7d", "m": "30d", "y": "1y"}
GDELT_SPAN = {"d": "1d", "w": "1w", "m": "1m", "y": "3m"}  # GDELT DOC keeps a rolling 3 months
GDELT_LANG = {
    "en": "english",
    "de": "german",
    "fr": "french",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "ru": "russian",
    "ja": "japanese",
    "zh": "chinese",
    "ar": "arabic",
}


class Wikipedia(Provider):
    name = "wikipedia"
    kind = "api"
    upstream = "wikipedia"
    group = "wikipedia.org"
    rate = (Bucket.every(1.0, burst=3),)
    caps = {"text": Caps("none", region=True, paging=True, max_results=50)}
    priority = {"text": 40}
    impersonate = False
    homepage = "https://www.mediawiki.org/wiki/API:Search"
    basis = "Official MediaWiki Action API, keyless; serial requests and a descriptive User-Agent per API etiquette."
    privacy = "Wikimedia receives the query and your IP address."
    limits = "No hard read limit; be serial and polite."

    def search_text(self, q: Query, limit: int) -> list[Result]:
        lang = q.lang if q.lang.isalpha() and len(q.lang) <= 3 else "en"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": q.text,
            "srlimit": str(min(50, limit)),
            "sroffset": str(q.offset),
            "srprop": "snippet|timestamp",
            "format": "json",
            "formatversion": "2",
            "utf8": "1",
        }
        data = self.get(f"https://{lang}.wikipedia.org/w/api.php", params=params).json()
        if "error" in data:
            raise ProviderError(Outcome.SERVER, str(data["error"].get("info", "API error"))[:160])
        return [
            Result(
                title=i.get("title", ""),
                url=f"https://{lang}.wikipedia.org/wiki/{quote(i.get('title', '').replace(' ', '_'))}",
                snippet=i.get("snippet", ""),
                date=parse_date(i.get("timestamp")),
            )
            for i in data.get("query", {}).get("search", [])
        ]


class WikimediaCommons(Provider):
    name = "wikimedia"
    kind = "api"
    upstream = "wikimedia"
    group = "commons.wikimedia.org"
    rate = (Bucket.every(1.0, burst=3),)
    caps = {"images": Caps("none", paging=True, max_results=50)}
    priority = {"images": 45}
    impersonate = False
    homepage = "https://commons.wikimedia.org/w/api.php"
    basis = "Official MediaWiki Action API on Wikimedia Commons (freely licensed media), keyless."
    privacy = "Wikimedia receives the query and your IP address."
    limits = "No hard read limit; be serial and polite."

    def search_images(self, q: Query, limit: int) -> list[Result]:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap|drawing {q.text}",
            "gsrnamespace": "6",
            "gsrlimit": str(min(50, limit)),
            "gsroffset": str(q.offset),
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "iiurlwidth": "320",
            "format": "json",
            "formatversion": "2",
        }
        data = self.get("https://commons.wikimedia.org/w/api.php", params=params).json()
        pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            if not str(info.get("mime", "")).startswith("image/"):
                continue
            title = p.get("title", "").removeprefix("File:").rsplit(".", 1)[0]
            out.append(
                Result(
                    title=title,
                    url=info.get("descriptionurl") or "",
                    image=info.get("url"),
                    thumbnail=info.get("thumburl"),
                    width=info.get("width"),
                    height=info.get("height"),
                    source="Wikimedia Commons",
                )
            )
        return out


class Openverse(Provider):
    name = "openverse"
    kind = "api"
    upstream = "openverse"
    group = "api.openverse.org"
    rate = (Bucket.per_minute(20, burst=5), Bucket.per_day(200, burst=20))
    caps = {"images": Caps("none", safesearch=True, paging=True, max_results=20)}
    priority = {"images": 50}
    impersonate = False
    homepage = "https://docs.openverse.org/api/reference/authentication_and_throttling.html"
    basis = "Official Openverse API (openly licensed images), anonymous access allowed."
    privacy = "Openverse (WordPress.org) receives the query and your IP address."
    limits = "Anonymous: 20/minute burst, 200/day sustained."

    def search_images(self, q: Query, limit: int) -> list[Result]:
        page_size = min(20, max(limit, 10))
        params: dict[str, Any] = {
            "q": q.text,
            "page_size": str(page_size),
            "page": str(q.offset // page_size + 1),
            "mature": "true" if q.safesearch == "off" else "false",
        }
        o = q.options
        size = o.get("size")
        if size in ("small", "medium", "large"):
            params["size"] = size
        if o.get("layout") in ("square", "wide", "tall"):
            params["aspect_ratio"] = o["layout"]
        lic = (o.get("license") or "").lower()
        if lic in ("sharecommercially", "modifycommercially"):
            params["license_type"] = "commercial" + (",modification" if lic.startswith("modify") else "")
        elif lic == "modify":
            params["license_type"] = "modification"
        data = self.get("https://api.openverse.org/v1/images/", params=params).json()
        return [
            Result(
                title=i.get("title") or "",
                url=i.get("foreign_landing_url") or "",
                image=i.get("url"),
                thumbnail=i.get("thumbnail"),
                width=i.get("width"),
                height=i.get("height"),
                source=" · ".join(
                    x
                    for x in (
                        i.get("source"),
                        i.get("creator"),
                        (i.get("license") or "").upper()
                        and f"CC {i.get('license', '').upper()} {i.get('license_version') or ''}".strip(),
                    )
                    if x
                ),
            )
            for i in data.get("results", [])
        ][:limit]


class Mwmbl(Provider):
    name = "mwmbl"
    kind = "api"
    upstream = "mwmbl"
    group = "api.mwmbl.org"
    rate = (Bucket.every(10.0, burst=1),)
    caps = {"text": Caps("none", max_results=20)}
    priority = {"text": 30}
    impersonate = False
    homepage = "https://mwmbl.org"
    basis = "Non-profit open-source index with a public JSON search API; an engine in SearXNG. Its terms forbid excessive automated queries, so web4free keeps it last and slow."
    privacy = "Mwmbl says it keeps no account-tied search logs and does not store IPs on disk."
    limits = "Fair use only. web4free allows 1 request per 10 s."

    def search_text(self, q: Query, limit: int) -> list[Result]:
        data = self.get("https://api.mwmbl.org/api/v1/search/", params={"s": q.text}).json()
        if not isinstance(data, list):
            raise ProviderError(Outcome.PARSE, "unexpected Mwmbl response")

        def joined(parts: Any) -> str:
            if isinstance(parts, list):
                return "".join(p.get("value", "") for p in parts if isinstance(p, dict))
            return str(parts or "")

        return [
            Result(title=joined(i.get("title")), url=i.get("url", ""), snippet=joined(i.get("extract")))
            for i in data
        ][:limit]


class GoogleNews(Provider):
    name = "google_news"
    kind = "api"
    upstream = "google"
    group = "news.google.com"
    rate = (Bucket.every(3.0, burst=2),)
    caps = {"news": Caps("native", region=True, max_results=100)}
    priority = {"news": 76}
    impersonate = False
    check_relevance = False  # items carry a title only
    homepage = "https://news.google.com/rss"
    basis = "Google News public RSS search feed, read by feed readers and libraries such as GNews and pygooglenews."
    privacy = "Google receives the query and your IP address."
    limits = "Unpublished; feed allows normal reader polling."

    def search_news(self, q: Query, limit: int) -> list[Result]:
        text = q.text + (f" when:{GNEWS_WHEN[q.timelimit]}" if q.timelimit else "")
        country = (q.country or "us").upper()
        params = {"q": text, "hl": f"{q.lang}-{country}", "gl": country, "ceid": f"{country}:{q.lang}"}
        resp = self.get("https://news.google.com/rss/search", params=params)
        try:
            root = etree.fromstring(resp.text.encode("utf-8"), parser=etree.XMLParser(recover=True))
        except etree.XMLSyntaxError as ex:
            raise ProviderError(Outcome.PARSE, "invalid RSS") from ex
        if root is None:
            raise ProviderError(Outcome.PARSE, "invalid RSS")
        out = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            src = item.find("source")
            source = (src.text or "").strip() if src is not None else ""
            if source and title.endswith(" - " + source):
                title = title[: -len(source) - 3]
            out.append(
                Result(
                    title=title,
                    url=(item.findtext("link") or "").strip(),
                    snippet=source,
                    date=parse_date(item.findtext("pubDate")),
                    source=source or None,
                )
            )
        return out[q.offset : q.offset + limit]


class Gdelt(Provider):
    name = "gdelt"
    kind = "api"
    upstream = "gdelt"
    group = "api.gdeltproject.org"
    rate = (Bucket.every(6.0, burst=1),)
    caps = {"news": Caps("native", region=True, max_results=250)}
    priority = {"news": 40}
    default_enabled = False
    impersonate = False
    check_relevance = False  # items carry a title only
    homepage = "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/"
    basis = "GDELT DOC 2.0 API, free and keyless, global news monitoring (rolling 3 months). Off by default: it answered 429 to every call in testing (2026-11-16)."
    privacy = "GDELT receives the query and your IP address."
    limits = "One request every 5 seconds."

    def search_news(self, q: Query, limit: int) -> list[Result]:
        query = q.text
        lang = GDELT_LANG.get(q.lang)
        if lang:
            query += f" sourcelang:{lang}"
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(min(250, q.offset + limit)),
            "sort": "hybridrel",
            "timespan": GDELT_SPAN.get(q.timelimit or "", "3m"),
        }
        resp = self.http().get(
            "https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=self.ctx.request_timeout()
        )
        if resp.status == 429 or "limit requests" in resp.text[:300].lower():
            raise ProviderError(Outcome.RATE_LIMITED, "GDELT asks for one request every 5 seconds", 10.0)
        check(resp)
        body = resp.text.strip()
        if not body.startswith("{"):
            if "too short" in body.lower() or "too common" in body.lower():
                raise ProviderError(Outcome.CONFIG, body[:160])
            return []
        out = []
        for a in resp.json().get("articles", []):
            out.append(
                Result(
                    title=a.get("title") or "",
                    url=a.get("url") or "",
                    snippet=a.get("domain") or "",
                    date=parse_date(a.get("seendate")),
                    source=a.get("domain"),
                    image=a.get("socialimage") or None,
                )
            )
        return out[q.offset :]


class HackerNews(Provider):
    name = "hackernews"
    kind = "api"
    upstream = "hackernews"
    group = "hn.algolia.com"
    rate = (Bucket.every(1.0, burst=5),)
    caps = {
        "news": Caps("native", paging=True, max_results=100),
        "text": Caps("native", paging=True, max_results=100),
    }
    priority = {"news": 35, "text": 25}
    default_enabled = False
    impersonate = False
    homepage = "https://hn.algolia.com/api"
    basis = "Official Hacker News search API by Algolia, keyless. Off by default (tech-only); add with --include hackernews."
    privacy = "Algolia receives the query and your IP address."
    limits = "10,000 requests/hour per IP."

    def _search(self, q: Query, limit: int) -> list[Result]:
        params: dict[str, Any] = {
            "query": q.text,
            "tags": "story",
            "hitsPerPage": str(min(100, limit)),
            "page": str(q.offset // max(1, limit)),
        }
        since = q.since()
        if since:
            params["numericFilters"] = f"created_at_i>{int(since.timestamp())}"
        data = self.get("https://hn.algolia.com/api/v1/search", params=params).json()
        out = []
        for h in data.get("hits", []):
            item_url = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            url = h.get("url") or item_url
            out.append(
                Result(
                    title=h.get("title") or "",
                    url=url,
                    snippet=f"{h.get('points') or 0} points · {h.get('num_comments') or 0} comments · {item_url}",
                    date=parse_date(h.get("created_at")),
                    source=urlsplit(url).netloc or "news.ycombinator.com",
                )
            )
        return out

    search_news = _search
