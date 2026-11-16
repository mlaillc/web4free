"""Quality checks, merging, rate limits, health and config."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from web4free.config import ConfigError, load_config, write_default_config
from web4free.health import Health
from web4free.merge import Merger, norm_url
from web4free.models import Outcome, Query, Result
from web4free.quality import assess, clean_text, parse_date, query_terms, valid_url, within
from web4free.ratelimit import Bucket, RateLimiter
from web4free.tor import ProxyError, resolve_proxy

# --- quality -----------------------------------------------------------------------


def test_clean_text_strips_markup():
    s = clean_text("## Title\n**bold** [link](https://x.y) <b>tag</b> &amp; \ue000hl\ue001", 0)
    assert s == "Title bold link tag & hl"
    long = "word " * 100
    assert len(clean_text(long, 50)) <= 51


def test_valid_url():
    assert valid_url("https://example.com/a")
    assert not valid_url("javascript:alert(1)")
    assert not valid_url("/relative")
    assert not valid_url("https://duckduckgo.com/y.js?ad=1")
    assert not valid_url("https://www.bing.com/aclick?ld=1")


def test_query_terms():
    assert query_terms('python "asyncio" site:docs.python.org -java OR the vehicles') == [
        "python",
        "asyncio",
        "vehicl",
    ]


def _r(title: str, url: str = "https://e.com/x", snippet: str = "") -> Result:
    return Result(title=title, url=url, snippet=snippet)


def test_assess_ok_poor_empty():
    q = Query("kubernetes pod eviction policy")
    good = [_r("Pod eviction policy in Kubernetes", f"https://e.com/{i}") for i in range(5)]
    assert assess(q, good).outcome is Outcome.OK
    # only the first word matched: the "Bing answered for one word" case
    bad = [
        _r("Kubernetes", f"https://k.io/{i}", "Production-grade container orchestration") for i in range(5)
    ]
    assert assess(q, bad).outcome is Outcome.POOR
    assert assess(q, []).outcome is Outcome.EMPTY
    assert assess(q, bad, check_relevance=False).outcome is Outcome.OK


def test_assess_dedupes_and_drops_invalid():
    q = Query("x y")
    a = assess(q, [_r("x y", "https://e.com/1"), _r("x y", "https://e.com/1"), _r("x y", "ftp://bad")])
    assert len(a.results) == 1 and a.dropped == 2


def test_parse_date_forms():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    assert parse_date("3 hours ago", now) == "2026-11-16T09:00:00+00:00"
    assert parse_date("1 month ago", now) == (now - timedelta(days=30)).isoformat()
    assert parse_date("Mon, 16 Nov 2026 03:13:00 GMT") == "2026-11-16T03:13:00+00:00"
    assert parse_date("2026-10-30T00:00:00Z") == "2026-10-30T00:00:00+00:00"
    assert parse_date("July 23, 2025") == "2025-07-23T00:00:00+00:00"
    assert parse_date("Jun 11, 2026 · ") == "2026-06-11T00:00:00+00:00"
    assert parse_date("20261116T120000Z") == "2026-11-16T12:00:00+00:00"
    assert parse_date("1794852000") is not None
    assert parse_date("garbage") is None


def test_within():
    since = datetime.now(UTC) - timedelta(days=1)
    assert within(datetime.now(UTC).isoformat(), since)
    assert not within((datetime.now(UTC) - timedelta(days=3)).isoformat(), since)
    assert not within(None, since)
    assert within(None, None)


def test_query_validation():
    with pytest.raises(ValueError):
        Query("x", category="music")
    with pytest.raises(ValueError):
        Query("x", timelimit="h")
    q = Query("x", region="de-de")
    assert (q.country, q.lang) == ("de", "de")
    assert Query("x", region="wt-wt").country == ""
    assert Query("x", page=3, max_results=10).offset == 20


# --- merge -------------------------------------------------------------------------


def test_norm_url():
    a = norm_url("https://www.Example.com/path/?utm_source=x&b=2&a=1#frag")
    b = norm_url("http://example.com/path?a=1&b=2")
    assert a == b
    assert norm_url("https://m.example.com/index.html") == norm_url("https://example.com/")


def test_merger_fallback_keeps_order_and_fills():
    m = Merger("text", "fallback")
    m.add("p1", [_r("A", "https://a.com"), _r("B", "https://b.com")])
    m.add("p2", [_r("B2", "https://www.b.com/", "longer snippet here"), _r("C", "https://c.com")])
    out = m.results()
    assert [r.url for r in out] == ["https://a.com", "https://b.com", "https://c.com"]
    assert out[1].providers == ["p1", "p2"] and out[1].snippet == "longer snippet here"


def test_merger_fuse_rewards_agreement():
    m = Merger("text", "fuse")
    m.add("p1", [_r("A", "https://a.com"), _r("B", "https://b.com")])
    m.add("p2", [_r("B", "https://b.com"), _r("C", "https://c.com")])
    assert m.results()[0].url == "https://b.com"


# --- rate limits -----------------------------------------------------------------------


def test_bucket_burst_then_wait(store):
    rl = RateLimiter(store)
    b = (Bucket.every(10.0, burst=2),)
    now = 1000.0
    assert rl.try_acquire("k", b, now) == 0
    assert rl.try_acquire("k", b, now) == 0
    wait = rl.try_acquire("k", b, now)
    assert 9.9 < wait <= 10.0
    assert rl.try_acquire("k", b, now + 10.0) == 0


def test_multiple_buckets_all_or_nothing(store):
    rl = RateLimiter(store)
    b = (Bucket.every(1.0, burst=5), Bucket.every(100.0, burst=1))
    assert rl.try_acquire("m", b, 0.0) == 0
    assert rl.try_acquire("m", b, 0.5) > 50  # second bucket empty
    assert rl.try_acquire("m", b, 101.0) == 0


def test_acquire_respects_max_wait(store):
    rl = RateLimiter(store)
    b = (Bucket.every(60.0, burst=1),)
    assert rl.acquire("w", b, max_wait=0.1) == (True, 0.0)
    ok, need = rl.acquire("w", b, max_wait=0.1)
    assert not ok and need > 50


# --- health ----------------------------------------------------------------------------


def test_rate_limit_sets_group_cooldown_and_success_clears(store):
    h = Health(store)
    until = h.record(
        target="bing",
        category="text",
        group="bing.com",
        route="direct",
        outcome=Outcome.RATE_LIMITED,
        detail="429",
    )
    assert until and until > time.time() + 200
    # group scope: other categories of the same host are cooling down too
    assert h.cooldown("bing", "images", "bing.com", "direct")
    # other routes are separate
    assert h.cooldown("bing", "text", "bing.com", "tor") is None
    h.record(target="bing", category="text", group="bing.com", route="direct", outcome=Outcome.OK, n=10)
    assert h.cooldown("bing", "images", "bing.com", "direct") is None


def test_cooldown_backoff_grows(store):
    h = Health(store)
    first = (
        h.record(target="b", category="text", group="g", route="direct", outcome=Outcome.BLOCKED)
        - time.time()
    )
    second = (
        h.record(target="b", category="text", group="g", route="direct", outcome=Outcome.BLOCKED)
        - time.time()
    )
    assert second > first * 1.8


def test_transient_needs_two_failures(store):
    h = Health(store)
    assert h.record(target="t", category="text", group="g", route="direct", outcome=Outcome.TIMEOUT) is None
    assert (
        h.record(target="t", category="text", group="g", route="direct", outcome=Outcome.TIMEOUT) is not None
    )


def test_retry_after_is_honoured(store):
    h = Health(store)
    until = h.record(
        target="t", category="text", group="g", route="direct", outcome=Outcome.RATE_LIMITED, retry_after=42
    )
    assert 40 < until - time.time() <= 43


def test_factor_prefers_healthy(store):
    h = Health(store)
    for _ in range(4):
        h.record(
            target="good",
            category="text",
            group="g1",
            route="direct",
            outcome=Outcome.OK,
            n=10,
            latency=0.5,
            relevance=0.9,
        )
        h.record(target="bad", category="text", group="g2", route="direct", outcome=Outcome.SERVER, latency=8)
    assert h.factor("good", "text", "direct") > 2 * h.factor("bad", "text", "direct")
    assert 0 < h.factor("never", "text", "direct") <= 1


# --- config / proxy ------------------------------------------------------------------------


def test_config_roundtrip(tmp_path):
    p = tmp_path / "c.toml"
    write_default_config(p)
    cfg = load_config(p)
    assert cfg["search"]["max_results"] == 10
    p.write_text("[search]\nmax_results = 5\n[providers.brave]\nenabled = false\n")
    cfg = load_config(p)
    assert cfg["search"]["max_results"] == 5 and cfg["search"]["region"] == "us-en"
    assert cfg["providers"]["brave"]["enabled"] is False
    p.write_text("[bogus]\nx=1\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_resolve_proxy():
    assert resolve_proxy(None) == (None, "direct")
    assert resolve_proxy("http://h:8080") == ("http://h:8080", "proxy")
    assert resolve_proxy("socks5://127.0.0.1:9050") == ("socks5h://127.0.0.1:9050", "tor")
    with pytest.raises(ProxyError):
        resolve_proxy("ftp://x")
