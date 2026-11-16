"""Other public search pages: Brave (own index), Startpage (Google results), Mojeek (own index).

All three are engines in SearXNG and ddgs. Brave and Mojeek serve captchas quickly
to automated traffic, and Startpage sits behind an Anubis proof-of-work page; web4free
never solves these, it backs off. Startpage and Mojeek are off by default.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import html as lxml_html

from web4free.http import empty_or_blocked
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Provider
from web4free.quality import parse_date
from web4free.ratelimit import Bucket

BRAVE_ONION = "search.brave4u7jddbv7cyviptqjc7jusxh72uik7zt6adtckl5f4nwy2v72qd.onion"
BRAVE_TIME = {"d": "pd", "w": "pw", "m": "pm", "y": "py"}
_AGE_RE = re.compile(r"^\s*((?:\d+\s+\w+\s+ago)|(?:[A-Z][a-z]+ \d{1,2}, \d{4}))\s*[-–]\s*")


def parse_brave(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    items = tree.xpath("//div[@data-type='web'][@data-pos]") or tree.xpath("//div[@data-type='web']")
    for item in items:
        link = item.xpath(".//a[@href][.//div[contains(@class, 'title')]]") or item.xpath(
            ".//a[starts-with(@href, 'http')]"
        )
        if not link:
            continue
        title_el = item.xpath(".//div[contains(@class, 'search-snippet-title')]") or item.xpath(
            ".//div[contains(concat(' ', normalize-space(@class), ' '), ' title ')]"
        )
        title = (
            (title_el[0].get("title") or title_el[0].text_content()) if title_el else link[0].text_content()
        )
        content = item.xpath(
            ".//div[contains(@class, 'generic-snippet')]//div[contains(@class, 'content')]"
            " | .//div[contains(@class, 'snippet-description')] | .//p[contains(@class, 'snippet-description')]"
        )
        date = None
        snip = ""
        if content:
            c = content[0]
            when = c.xpath("./span[contains(@class, 't-secondary')]")
            if when:
                date = parse_date(when[0].text_content().strip(" -–"))
                when[0].drop_tree()
            snip = c.text_content()
        else:
            qa = item.xpath(".//div[contains(@class, 'inline-qa')]")
            snip = qa[0].text_content() if qa else ""
        m = _AGE_RE.match(" ".join(snip.split()))
        if m and not date:
            date = parse_date(m.group(1))
            snip = " ".join(snip.split())[m.end() :]
        out.append(Result(title=title, url=link[0].get("href", ""), snippet=snip, date=date))
    return [r for r in out if r.url.startswith("http") and "search.brave.com" not in r.url]


class Brave(Provider):
    name = "brave"
    kind = "scrape"
    upstream = "brave"
    group = "search.brave.com"
    rate = (Bucket.every(20.0, burst=1),)
    caps = {"text": Caps("native", region=True, safesearch=True, paging=True, max_results=20)}
    priority = {"text": 76}
    homepage = "https://search.brave.com"
    basis = "Public Brave Search pages (own index), an engine in SearXNG and ddgs. The keyed Brave Search API is not used: its free credits need a card."
    privacy = "Brave says it does not profile users; it receives the query and your IP address."
    limits = (
        "Strict: 429 / captcha after a few quick requests from one IP. web4free allows 1 request per 20 s."
    )

    def _base(self) -> str:
        return f"https://{BRAVE_ONION}" if self.ctx.route == "tor" else "https://search.brave.com"

    def search_text(self, q: Query, limit: int) -> list[Result]:
        cookies = {
            "safesearch": {"on": "strict", "moderate": "moderate", "off": "off"}[q.safesearch],
            "useLocation": "0",
            "summarizer": "0",
        }
        if q.country:
            cookies["country"] = q.country

        def fetch(page: int) -> list[Result]:
            params: dict[str, Any] = {"q": q.text, "source": "web"}
            if q.timelimit:
                params["tf"] = BRAVE_TIME[q.timelimit]
            if page > 1:
                params["offset"] = str(page - 1)
            resp = self.get(f"{self._base()}/search", params=params, cookies=cookies)
            results = parse_brave(resp.text)
            if results:
                return results
            empty_or_blocked(resp)
            if "Not many great matches" in resp.text or "No results found" in resp.text:
                return []
            raise ProviderError(Outcome.PARSE, "results page not recognised")

        return self.paged(q, limit, 20, fetch, max_pages=2)


def parse_startpage(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for item in tree.xpath(
        "//div[contains(@class, 'result')][.//a[contains(@class, 'result-title') or contains(@class, 'result-link')]]"
    ):
        link = item.xpath(".//a[contains(@class, 'result-title') or contains(@class, 'result-link')][@href]")
        title = item.xpath(".//h2") or link
        para = item.xpath(".//p[contains(@class, 'description')]") or item.xpath(".//p")
        href = link[0].get("href", "")
        if not href.startswith("http") or "startpage.com" in href or "google.com" in href:
            continue
        out.append(
            Result(title=title[0].text_content(), url=href, snippet=para[0].text_content() if para else "")
        )
    return out


class Startpage(Provider):
    name = "startpage"
    kind = "scrape"
    upstream = "google"
    group = "startpage.com"
    rate = (Bucket.every(10.0, burst=1),)
    caps = {"text": Caps("native", region=True, safesearch=True, paging=True, max_results=10)}
    priority = {"text": 55}
    default_enabled = False
    homepage = "https://www.startpage.com"
    basis = "Public Startpage pages (Google results), an engine in SearXNG and ddgs. Off by default: it now serves an Anubis proof-of-work page to scripted clients, which web4free does not solve."
    privacy = "Startpage proxies Google and says it does not log IPs."
    limits = "Anubis challenge for most scripted requests (seen 2026-11-16)."

    def search_text(self, q: Query, limit: int) -> list[Result]:
        home = self.get("https://www.startpage.com/")
        sc = lxml_html.fromstring(home.text).xpath("//form[@id='search']//input[@name='sc']/@value")
        if not sc:
            empty_or_blocked(home)
            raise ProviderError(Outcome.PARSE, "Startpage form token not found")
        if not self.pace():
            raise ProviderError(Outcome.SKIPPED, "rate limit after token request")

        def fetch(page: int) -> list[Result]:
            data: dict[str, Any] = {
                "query": q.text,
                "cat": "web",
                "t": "device",
                "sc": sc[0],
                "lui": "english",
                "language": "english",
                "qadf": {"on": "heavy", "moderate": "moderate", "off": "none"}[q.safesearch],
                "segment": "organic",
            }
            if q.country:
                data["qsr"] = f"{q.lang}_{q.country.upper()}"
            if q.timelimit:
                data["with_date"] = q.timelimit
            if page > 1:
                data["page"] = str(page)
            resp = self.post(
                "https://www.startpage.com/sp/search",
                data=data,
                headers={"Referer": "https://www.startpage.com/"},
            )
            return parse_startpage(resp.text) or empty_or_blocked(resp)

        return self.paged(q, limit, 10, fetch, max_pages=2)


def parse_mojeek(text: str) -> list[Result]:
    tree = lxml_html.fromstring(text)
    out = []
    for li in tree.xpath("//ul[contains(@class, 'results')]/li"):
        link = li.xpath(".//h2/a[@href]")
        if not link:
            continue
        snippet = li.xpath(".//p[contains(@class, 's')]")
        out.append(
            Result(
                title=link[0].text_content(),
                url=link[0].get("href", ""),
                snippet=snippet[0].text_content() if snippet else "",
            )
        )
    return out


class Mojeek(Provider):
    name = "mojeek"
    kind = "scrape"
    upstream = "mojeek"
    group = "mojeek.com"
    rate = (Bucket.every(10.0, burst=1),)
    caps = {"text": Caps("none", region=True, safesearch=True, paging=True, max_results=10)}
    priority = {"text": 50}
    default_enabled = False
    homepage = "https://www.mojeek.com"
    basis = "Public Mojeek pages (own index), an engine in SearXNG and ddgs. Off by default: it served a captcha to scripted clients in testing."
    privacy = "Mojeek states it does not track users."
    limits = "Captcha for scripted requests (seen 2026-11-16)."

    def search_text(self, q: Query, limit: int) -> list[Result]:
        cookies = {"arc": q.country or "us", "lb": q.lang}

        def fetch(page: int) -> list[Result]:
            params: dict[str, Any] = {"q": q.text}
            if q.safesearch == "on":
                params["safe"] = "1"
            if page > 1:
                params["s"] = str((page - 1) * 10 + 1)
            resp = self.get("https://www.mojeek.com/search", params=params, cookies=cookies)
            return parse_mojeek(resp.text) or empty_or_blocked(resp)

        return self.paged(q, limit, 10, fetch, max_pages=2)
