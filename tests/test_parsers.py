"""Parsers against captured pages (tests/fixtures, captured 2026-11-16)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from web4free.http import Response, empty_or_blocked
from web4free.models import Outcome, ProviderError
from web4free.providers.bing import (
    decode_apiclick,
    decode_ck,
    parse_images,
    parse_news_rss,
    parse_videos,
    parse_web_html,
    parse_web_rss,
)
from web4free.providers.duckduckgo import parse_html, unwrap
from web4free.providers.scrapers import parse_brave
from web4free.providers.yahoo import decode_yahoo, parse_news, parse_web

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_duckduckgo_html():
    rs = parse_html(read("ddg_html.html"))
    assert len(rs) >= 8
    assert all(r.url.startswith("http") and "duckduckgo.com" not in r.url for r in rs)
    assert any("Tokio" in r.title for r in rs)
    assert all(r.snippet for r in rs[:5])


def test_duckduckgo_unwrap():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=abc"
    assert unwrap(href) == "https://example.com/a?b=1"
    assert unwrap("https://example.com/x") == "https://example.com/x"


def test_bing_rss_and_html():
    rss = parse_web_rss(read("bing_web.rss"))
    assert rss and len(rss) == 10
    assert rss[0].url.startswith("https://") and rss[0].title
    html = parse_web_html(read("bing_web.html"))
    assert len(html) >= 8
    assert all("bing.com/ck/a" not in r.url for r in html)
    assert parse_web_rss("<html>not rss</html>") is None


def test_bing_decoders():
    ck = "https://www.bing.com/ck/a?!&&p=x&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9wYXRo&ntb=1"
    assert decode_ck(ck) == "https://example.com/path"
    api = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=1&url=https%3a%2f%2fexample.com%2fn&c=1"
    assert decode_apiclick(api) == "https://example.com/n"


def test_bing_news_rss():
    rs = parse_news_rss(read("bing_news.rss"))
    assert rs and len(rs) >= 10
    assert all(r.date and r.url.startswith("http") and "bing.com/news/apiclick" not in r.url for r in rs)
    assert any(r.source for r in rs)


def test_bing_images():
    rs = parse_images(read("bing_images.html"))
    assert len(rs) >= 30
    assert all(r.image and r.image.startswith("http") for r in rs)
    assert sum(1 for r in rs if r.url.startswith("http")) >= len(rs) * 0.6


def test_bing_videos():
    rs = parse_videos(read("bing_videos.html"))
    assert len(rs) >= 30
    first = rs[0]
    assert first.url.startswith("https://www.youtube.com/") and first.duration and first.publisher


def test_yahoo_web_and_news():
    web = parse_web(read("yahoo_web.html"))
    assert len(web) >= 5
    assert all("r.search.yahoo.com" not in r.url for r in web)
    assert any(r.date for r in web)
    news = parse_news(read("yahoo_news.html"))
    urls = [r.url for r in news]
    assert len(news) >= 8 and len(urls) == len(set(urls))
    assert all("fr=sycsrp" not in u for u in urls)


def test_yahoo_decode():
    href = "https://r.search.yahoo.com/_ylt=A;_ylu=B/RV=2/RE=1/RO=10/RU=https%3a%2f%2fexample.com%2fa%3ffr%3dsycsrp_catchall/RK=2/RS=x-"
    assert decode_yahoo(href) == "https://example.com/a"


def test_brave():
    rs = parse_brave(read("brave_web.html"))
    assert len(rs) >= 15
    assert all(r.url.startswith("http") for r in rs)
    assert sum(1 for r in rs if r.snippet) >= len(rs) - 2
    assert sum(1 for r in rs if r.date) >= 3
    assert not any(r.snippet.startswith(("1 month ago", "July")) for r in rs)


def test_mwmbl_shape():
    data = json.loads(read("mwmbl.json"))
    assert isinstance(data, list) and data and "url" in data[0]


@pytest.mark.parametrize(
    "name",
    ["block_ddg_202.html", "block_brave_429.html", "block_mojeek.html", "block_startpage_anubis.html"],
)
def test_block_pages_detected(name):
    resp = Response(200, read(name), {}, "https://example.com/")
    with pytest.raises(ProviderError) as ex:
        empty_or_blocked(resp)
    assert ex.value.kind is Outcome.BLOCKED


def test_normal_page_is_not_blocked():
    resp = Response(200, "<html><title>No results</title><body>No results found.</body></html>", {}, "u")
    assert empty_or_blocked(resp) == []
