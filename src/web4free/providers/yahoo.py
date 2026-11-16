"""Yahoo web and news search pages (results come from Bing). Used by SearXNG and ddgs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from lxml import html as lxml_html

from web4free.http import empty_or_blocked
from web4free.models import Query, Result
from web4free.providers.base import Caps, Provider
from web4free.quality import parse_date
from web4free.ratelimit import Bucket

YAHOO_TIME = {"d": "d", "w": "w", "m": "m"}


def decode_yahoo(href: str) -> str:
    """r.search.yahoo.com/.../RU=<encoded target>/RK=... -> target."""
    if "/RU=" not in href:
        return href
    target = unquote(href.split("/RU=", 1)[1].split("/RK=", 1)[0].split("/RS=", 1)[0])
    # Yahoo appends its own referral tag, e.g. ?fr=sycsrp_catchall
    return re.sub(r"[?&]fr=sycsrp[^&]*$", "", target)


def parse_web(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for item in tree.xpath("//div[contains(@class, 'algo-sr') or contains(@class, 'algo ')]"):
        link = item.xpath(".//div[contains(@class, 'compTitle')]//a[@href]") or item.xpath(".//h3//a[@href]")
        if not link:
            continue
        title_el = item.xpath(".//h3//span[contains(@class, 'd-b')]") or item.xpath(".//h3")
        para = item.xpath(".//div[contains(@class, 'compText')]//p")
        date = None
        snippet = ""
        if para:
            when = para[0].xpath(".//span[contains(@class, 'fc-smoke')]")
            if when:
                date = parse_date(when[0].text_content())
                when[0].drop_tree()
            snippet = para[0].text_content()
        out.append(
            Result(
                title=title_el[0].text_content() if title_el else link[0].text_content(),
                url=decode_yahoo(link[0].get("href", "")),
                snippet=snippet,
                date=date,
            )
        )
    return out


def parse_news(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for li in tree.xpath("//div[@id='web']//li[.//h4][not(ancestor::li[.//h4])]"):
        link = li.xpath(".//h4//a[@href]")
        if not link:
            continue
        when = li.xpath(".//span[contains(@class, 'time')]")
        source = li.xpath(".//span[contains(@class, 'source')]")
        image = li.xpath(".//img/@data-src | .//img/@src")
        src_text = source[0].text_content().split(" via Yahoo")[0].strip(" ·") if source else None
        img = image[0] if image else None
        if img and "-/" in img:
            img = img[img.find("-/") + 2 :]
        out.append(
            Result(
                title=link[0].text_content(),
                url=decode_yahoo(link[0].get("href", "")),
                snippet=" ".join(p.text_content() for p in li.xpath(".//p")[:1]),
                date=parse_date(when[0].text_content()) if when else None,
                source=src_text or None,
                image=img if img and img.startswith("http") else None,
            )
        )
    return out


class Yahoo(Provider):
    name = "yahoo"
    kind = "scrape"
    upstream = "bing"
    group = "search.yahoo.com"
    rate = (Bucket.every(4.0, burst=2),)
    caps = {
        "text": Caps("native", safesearch=False, paging=True, max_results=7),
        "news": Caps("native", paging=True, max_results=10),
    }
    priority = {"text": 70, "news": 72}
    homepage = "https://search.yahoo.com"
    basis = "Public Yahoo search pages, used by SearXNG and ddgs. Results come from Bing."
    privacy = "Yahoo receives the query and your IP address."
    limits = "Unpublished."

    def search_text(self, q: Query, limit: int) -> list[Result]:
        def fetch(page: int) -> list[Result]:
            params: dict[str, Any] = {"p": q.text}
            if q.timelimit in YAHOO_TIME:
                params["btf"] = YAHOO_TIME[q.timelimit]
            if page > 1:
                params["b"] = str((page - 1) * 7 + 1)
            resp = self.get("https://search.yahoo.com/search", params=params)
            return parse_web(resp.text) or empty_or_blocked(resp)

        return self.paged(q, limit, 7, fetch, max_pages=3)

    def search_news(self, q: Query, limit: int) -> list[Result]:
        def fetch(page: int) -> list[Result]:
            params: dict[str, Any] = {"p": q.text}
            if q.timelimit in YAHOO_TIME:
                params["btf"] = YAHOO_TIME[q.timelimit]
            if page > 1:
                params["b"] = str((page - 1) * 10 + 1)
            resp = self.get("https://news.search.yahoo.com/search", params=params)
            return parse_news(resp.text) or empty_or_blocked(resp)

        return self.paged(q, limit, 10, fetch, max_pages=2)
