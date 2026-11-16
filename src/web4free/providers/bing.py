"""Bing: web via the RSS output (HTML as fallback), news via RSS, images and videos via async pages.

Same public endpoints that SearXNG and ddgs use.
"""

from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lxml import etree
from lxml import html as lxml_html

from web4free.http import Response, empty_or_blocked
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Provider
from web4free.quality import parse_date
from web4free.ratelimit import Bucket

ADLT = {"on": "strict", "moderate": "moderate", "off": "off"}
WEB_TIME = {"d": 'ex1:"ez1"', "w": 'ex1:"ez2"', "m": 'ex1:"ez3"'}
NEWS_TIME = {"d": 'interval="7"', "w": 'interval="8"', "m": 'interval="9"'}
AGE_MINUTES = {"d": 1440, "w": 10080, "m": 43200, "y": 525600}
IMAGE_SIZE = {"small": "small", "medium": "medium", "large": "large", "wallpaper": "wallpaper"}
IMAGE_TYPE = {
    "photo": "photo-photo",
    "clipart": "photo-clipart",
    "gif": "photo-animatedgif",
    "transparent": "photo-transparent",
    "line": "photo-linedrawing",
}
IMAGE_LAYOUT = {"square": "aspect-square", "wide": "aspect-wide", "tall": "aspect-tall"}
IMAGE_LICENSE = {
    "public": "license-L1",
    "share": "license-L2_L3_L4_L5_L6_L7",
    "sharecommercially": "license-L2_L3_L4",
    "modify": "license-L2_L3_L5_L6",
    "modifycommercially": "license-L2_L3",
}


def decode_ck(href: str) -> str:
    """https://www.bing.com/ck/a?...&u=a1<base64url> -> target URL."""
    if "bing.com/ck/a" not in href:
        return href
    u = parse_qs(urlsplit(href).query).get("u")
    if not u or not u[0].startswith("a1"):
        return href
    enc = u[0][2:]
    enc += "=" * (-len(enc) % 4)
    try:
        return base64.urlsafe_b64decode(enc).decode("utf-8", errors="replace")
    except ValueError:
        return href


def decode_apiclick(href: str) -> str:
    if "apiclick.aspx" not in href:
        return href
    u = parse_qs(urlsplit(href).query).get("url")
    return u[0] if u else href


def _rss_items(text: str) -> list[etree._Element] | None:
    """Items of an RSS document, or None if the text is not RSS."""
    if "<rss" not in text[:500]:
        return None
    try:
        root = etree.fromstring(text.encode("utf-8"), parser=etree.XMLParser(recover=True))
    except etree.XMLSyntaxError:
        return None
    return root.findall(".//item") if root is not None else None


def _child(item: etree._Element, local: str) -> str:
    for c in item:
        tag = c.tag if isinstance(c.tag, str) else ""
        if tag == local or tag.endswith("}" + local):
            return (c.text or "").strip()
    return ""


def parse_web_rss(text: str) -> list[Result] | None:
    items = _rss_items(text)
    if items is None:
        return None
    return [
        Result(title=_child(i, "title"), url=_child(i, "link"), snippet=_child(i, "description"))
        for i in items
    ]


def parse_web_html(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for li in tree.xpath("//li[contains(concat(' ', normalize-space(@class), ' '), ' b_algo ')]"):
        link = li.xpath(".//h2/a")
        if not link:
            continue
        for icon in li.xpath(".//span[contains(@class, 'algoSlug_icon')]"):
            icon.drop_tree()
        paras = li.xpath(".//p") or li.xpath(".//div[contains(@class, 'b_caption')]")
        out.append(
            Result(
                title=link[0].text_content(),
                url=decode_ck(link[0].get("href", "")),
                snippet=" ".join(p.text_content() for p in paras[:1]),
            )
        )
    return out


def parse_news_rss(text: str) -> list[Result] | None:
    items = _rss_items(text)
    if items is None:
        return None
    out = []
    for i in items:
        image = _child(i, "Image")
        out.append(
            Result(
                title=_child(i, "title"),
                url=decode_apiclick(_child(i, "link")),
                snippet=_child(i, "description"),
                date=parse_date(_child(i, "pubDate")),
                source=_child(i, "Source") or None,
                image=image.replace("http://", "https://", 1) if image else None,
            )
        )
    return out


def parse_images(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for a in tree.xpath("//a[contains(@class, 'iusc')][@m]"):
        try:
            m = json.loads(a.get("m"))
        except ValueError:
            continue
        out.append(
            Result(
                title=m.get("t") or m.get("desc") or "",
                url=m.get("purl") or "",
                image=m.get("murl"),
                thumbnail=m.get("turl"),
                source=urlsplit(m.get("purl") or "").netloc or None,
            )
        )
    return out


def parse_videos(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for div in tree.xpath("//div[contains(@class, 'vrhdata')][@vrhm]"):
        try:
            m = json.loads(div.get("vrhm"))
        except ValueError:
            continue
        card = div.getparent()
        views = age = channel = site = None
        if card is not None:
            views = _first_text(card, ".//span[contains(@class, 'meta_vc_content')]")
            age = _first_text(card, ".//span[contains(@class, 'meta_pd_content')]")
            channel = _first_text(card, ".//span[contains(@class, 'mc_vtvc_meta_row_channel')]")
            rows = card.xpath(
                ".//div[contains(@class, 'mc_vtvc_meta_row')][.//span[contains(@class, 'mc_vtvc_meta_row_channel')]]"
            )
            if rows:
                spans = [x.strip() for x in rows[0].itertext() if x.strip()]
                site = spans[0] if len(spans) > 1 else None
        out.append(
            Result(
                title=m.get("vt") or "",
                url=m.get("murl") or m.get("pgurl") or "",
                snippet=" · ".join(x for x in (views, site) if x),
                duration=m.get("du"),
                thumbnail=m.get("smturl"),
                publisher=channel,
                date=parse_date(age),
            )
        )
    return out


def _first_text(node: lxml_html.HtmlElement, xpath: str) -> str | None:
    found = node.xpath(xpath)
    return " ".join(found[0].text_content().split()) or None if found else None


class Bing(Provider):
    name = "bing"
    kind = "scrape"
    upstream = "bing"
    group = "bing.com"
    rate = (Bucket.every(3.0, burst=3),)
    caps = {
        "text": Caps("native", region=True, safesearch=True, paging=True, max_results=10),
        "news": Caps("native", region=True, safesearch=True, paging=True, max_results=12),
        "images": Caps("native", region=True, safesearch=True, paging=True, max_results=35),
        "videos": Caps("native", region=True, safesearch=True, paging=True, max_results=35),
    }
    priority = {"text": 62, "news": 88, "images": 85, "videos": 80}
    homepage = "https://www.bing.com"
    basis = "Public Bing pages and their RSS output (format=rss); the same pages SearXNG and ddgs use."
    privacy = "Microsoft receives the query and your IP address."
    limits = "Unpublished. For scripted clients Bing web often answers only the first query word (seen 2026-11-16), so its web results rank low and are checked for relevance."

    BASE = "https://www.bing.com"

    def _locale(self, q: Query) -> dict[str, str]:
        params = {"setlang": q.lang}
        if q.country and q.country not in ("us",):
            params["cc"] = q.country.upper()
        return params

    def _web_time(self, q: Query) -> dict[str, str]:
        if not q.timelimit:
            return {}
        if q.timelimit in WEB_TIME:
            return {"filters": WEB_TIME[q.timelimit]}
        today = (date.today() - date(1970, 1, 1)).days
        return {"filters": f'ex1:"ez5_{today - 365}_{today}"'}

    def search_text(self, q: Query, limit: int) -> list[Result]:
        def fetch(page: int) -> list[Result]:
            params: dict[str, Any] = {
                "q": q.text,
                "adlt": ADLT[q.safesearch],
                **self._locale(q),
                **self._web_time(q),
            }
            if page > 1:
                params["first"] = str((page - 1) * 10 + 1)
            resp = self.get(f"{self.BASE}/search", params={**params, "format": "rss"})
            results = parse_web_rss(resp.text)
            if results:
                return results
            # RSS empty or missing: fall back to the HTML page once
            if not self.pace():
                return []
            page_resp = self.get(f"{self.BASE}/search", params=params)
            return parse_web_html(page_resp.text) or self._empty(page_resp)

        return self.paged(q, limit, 10, fetch, max_pages=3)

    def _empty(self, resp: Response) -> list[Result]:
        empty_or_blocked(resp)
        if "There are no results for" in resp.text or "No results found for" in resp.text:
            return []
        if len(resp.text) > 30_000:
            raise ProviderError(Outcome.PARSE, "results page not recognised")
        return []

    def search_news(self, q: Query, limit: int) -> list[Result]:
        def fetch(page: int) -> list[Result]:
            params: dict[str, Any] = {"q": q.text, "format": "rss", **self._locale(q)}
            if q.timelimit:
                params["qft"] = NEWS_TIME.get(q.timelimit, 'interval="9"')
            if page > 1:
                params["first"] = str((page - 1) * 12 + 1)
            resp = self.get(f"{self.BASE}/news/search", params=params)
            results = parse_news_rss(resp.text)
            if results is None:
                return self._empty(resp)
            return results

        return self.paged(q, limit, 12, fetch, max_pages=2)

    def search_images(self, q: Query, limit: int) -> list[Result]:
        o = q.options
        qft = []
        if q.timelimit:
            qft.append(f"filterui:age-lt{AGE_MINUTES[q.timelimit]}")
        if o.get("size") in IMAGE_SIZE:
            qft.append(f"filterui:imagesize-{IMAGE_SIZE[o['size']]}")
        if o.get("color"):
            c = o["color"].lower()
            qft.append(
                "filterui:color2-bw"
                if c == "monochrome"
                else "filterui:color2-color"
                if c == "color"
                else f"filterui:color2-FGcls_{c.upper()}"
            )
        if o.get("type") in IMAGE_TYPE:
            qft.append(f"filterui:{IMAGE_TYPE[o['type']]}")
        if o.get("layout") in IMAGE_LAYOUT:
            qft.append(f"filterui:{IMAGE_LAYOUT[o['layout']]}")
        if (o.get("license") or "").lower() in IMAGE_LICENSE:
            qft.append(f"filterui:{IMAGE_LICENSE[o['license'].lower()]}")
        params: dict[str, Any] = {
            "q": q.text,
            "first": str(q.offset + 1),
            "count": str(min(35, max(limit, 10))),
            "async": "1",
            "adlt": ADLT[q.safesearch],
            **self._locale(q),
        }
        if qft:
            params["qft"] = "".join("+" + f for f in qft)
        resp = self.get(f"{self.BASE}/images/async", params=params)
        results = parse_images(resp.text)
        return results[:limit] if results else empty_or_blocked(resp)

    def search_videos(self, q: Query, limit: int) -> list[Result]:
        o = q.options
        qft = []
        if q.timelimit:
            qft.append(f"filterui:videoage-lt{AGE_MINUTES[q.timelimit]}")
        if o.get("duration") in ("short", "medium", "long"):
            qft.append(f"filterui:duration-{o['duration']}")
        if o.get("resolution") == "high":
            qft.append("filterui:resolution-720p")
        params: dict[str, Any] = {
            "q": q.text,
            "async": "content",
            "first": str(q.offset + 1),
            "count": str(min(35, max(limit, 10))),
            "adlt": ADLT[q.safesearch],
            **self._locale(q),
        }
        if qft:
            params["qft"] = "".join("+" + f for f in qft)
        resp = self.get(f"{self.BASE}/videos/asyncv2", params=params)
        results = parse_videos(resp.text)
        return results[:limit] if results else empty_or_blocked(resp)
