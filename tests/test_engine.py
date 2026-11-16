"""Scheduler behaviour with fake providers (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from web4free.engine import Engine, SelectionError
from web4free.health import Health
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Provider
from web4free.ratelimit import Bucket

ALL = ("text", "news", "images", "videos")


FAST = (Bucket.every(0.01, burst=100),)


def make(
    name,
    *,
    priority=50,
    outcome=None,
    results=None,
    kind="api",
    upstream=None,
    timelimit="native",
    rate=FAST,
    default=True,
    fail_times=0,
):
    """Build a fake provider class; outcome raises, results returns."""

    class Fake(Provider):
        pass

    Fake.name = name
    Fake.kind = kind
    Fake.upstream = upstream or name
    Fake.group = f"{name}.example"
    Fake.rate = rate
    Fake.caps = {c: Caps(timelimit, paging=True, safesearch=True) for c in ALL}
    Fake.priority = dict.fromkeys(ALL, priority)
    Fake.default_enabled = default
    Fake.calls = 0

    def search(self, q, limit):
        type(self).calls += 1
        if outcome is not None and (fail_times == 0 or type(self).calls <= fail_times):
            raise ProviderError(outcome, f"{name} failed")
        rs = (
            results
            if results is not None
            else [
                Result(title=f"{q.text} result {i}", url=f"https://{name}.example/{i}", snippet=q.text)
                for i in range(10)
            ]
        )
        return [Result(**{**r.__dict__, "providers": []}) for r in rs]

    Fake.search = search
    return Fake


def engine_with(ctx, *classes):
    return Engine(ctx, [c(ctx) for c in classes])


def test_fallback_uses_best_first_and_stops(ctx):
    a, b = make("alpha", priority=90), make("beta", priority=50)
    resp = engine_with(ctx, a, b).search(Query("hello world"))
    assert len(resp.results) == 10
    assert all(r.providers == ["alpha"] for r in resp.results)
    assert b.calls == 0


def test_moves_down_on_failure_and_records(ctx):
    a = make("alpha", priority=90, outcome=Outcome.RATE_LIMITED)
    b = make("beta", priority=50)
    eng = engine_with(ctx, a, b)
    resp = eng.search(Query("hello world"))
    assert [x.outcome for x in resp.attempts[:2]] == ["rate_limited", "ok"]
    assert resp.results[0].providers == ["beta"]
    # alpha is now cooling down and skipped next time
    resp2 = eng.search(Query("hello again"))
    skipped = [x for x in resp2.attempts if x.target == "alpha"]
    assert skipped and skipped[0].outcome == "skipped" and "cooling down" in skipped[0].detail
    assert a.calls == 1


def test_force_ignores_cooldown(ctx):
    a = make("alpha", priority=90)
    eng = engine_with(ctx, a)
    Health(ctx.store).record(
        target="alpha", category="text", group="alpha.example", route="direct", outcome=Outcome.BLOCKED
    )
    assert not eng.search(Query("x y")).results
    assert eng.search(Query("x y"), force=True).results


def test_fills_from_next_provider_when_short(ctx):
    a = make("alpha", priority=90, results=[Result("hello one", "https://a.example/1", "hello")])
    b = make("beta", priority=50)
    resp = engine_with(ctx, a, b).search(Query("hello", max_results=5))
    assert resp.results[0].providers == ["alpha"]
    assert len(resp.results) == 5 and b.calls == 1


def test_include_order_wins_over_priority(ctx):
    a, b = make("alpha", priority=90), make("beta", priority=10)
    resp = engine_with(ctx, a, b).search(Query("hello"), include=["beta", "alpha"])
    assert resp.results[0].providers == ["beta"] and a.calls == 0


def test_exclude_and_kind_groups(ctx):
    a = make("alpha", priority=90, kind="scrape")
    b = make("beta", priority=50, kind="api")
    resp = engine_with(ctx, a, b).search(Query("hello"), exclude=["@scrape"])
    assert a.calls == 0 and resp.results[0].providers == ["beta"]


def test_unknown_provider_is_an_error(ctx):
    with pytest.raises(SelectionError):
        engine_with(ctx, make("alpha")).search(Query("x"), include=["nope"])


def test_off_by_default_needs_include(ctx):
    a = make("alpha", default=False)
    eng = engine_with(ctx, a)
    assert not eng.search(Query("hello")).results
    assert eng.search(Query("hello"), include=["alpha"]).results


def test_poor_results_are_skipped_but_used_as_last_resort(ctx):
    junk = [Result("Kubernetes", f"https://k.example/{i}", "container orchestration") for i in range(5)]
    a = make("alpha", priority=90, results=junk)
    b = make("beta", priority=50)
    resp = engine_with(ctx, a, b).search(Query("kubernetes pod eviction policy"))
    assert resp.attempts[0].outcome == "poor"
    assert resp.results[0].providers == ["beta"]
    resp = engine_with(ctx, make("gamma", results=junk)).search(Query("kubernetes pod eviction policy"))
    assert resp.results and resp.results[0].providers == ["gamma"]


def test_rate_wait_moves_on(ctx):
    slow = make("alpha", priority=90, rate=(Bucket.every(3600, burst=1),))
    b = make("beta", priority=50)
    eng = engine_with(ctx, slow, b)
    eng.search(Query("first query"))
    resp = eng.search(Query("second query"))
    first = resp.attempts[0]
    assert first.target == "alpha" and first.outcome == "skipped" and "rate limit" in first.detail
    assert resp.results[0].providers == ["beta"]


def test_transient_error_retried_once(ctx):
    a = make("alpha", priority=90, outcome=Outcome.TIMEOUT, fail_times=1)
    resp = engine_with(ctx, a).search(Query("hello"))
    assert a.calls == 2 and resp.results


def test_time_filter_skips_and_post_filters(ctx):
    now = datetime.now(UTC)
    fresh = Result("hello new", "https://p.example/new", "hello", date=now.isoformat())
    old = Result("hello old", "https://p.example/old", "hello", date=(now - timedelta(days=30)).isoformat())
    undated = Result("hello undated", "https://p.example/u", "hello")
    none = make("nofilter", priority=90, timelimit="none")
    post = make("post", priority=80, timelimit="post", results=[fresh, old, undated])
    native = make("native", priority=70, timelimit="native", results=[old, undated])
    resp = engine_with(ctx, none, post, native).search(Query("hello", timelimit="w", max_results=3))
    assert none.calls == 0
    urls = [r.url for r in resp.results]
    assert "https://p.example/new" in urls and "https://p.example/old" not in urls
    # post-filtered provider drops undated items; native keeps them
    assert urls.count("https://p.example/u") == 1


def test_empty_while_others_succeed_is_suspect(ctx):
    a = make("alpha", priority=90, results=[])
    b = make("beta", priority=50)
    engine_with(ctx, a, b).search(Query("hello"))
    row = [r for r in Health(ctx.store).rows() if r["target"] == "alpha"][0]
    assert row["empty_suspect"] == 1


def test_fuse_merges_distinct_upstreams(ctx):
    a = make("alpha", priority=90, upstream="bing")
    b = make("beta", priority=80, upstream="bing")
    c = make("gamma", priority=70, upstream="own")
    resp = engine_with(ctx, a, b, c).search(Query("hello"), strategy="fuse", fanout=2)
    assert b.calls == 0  # beta shares alpha's upstream
    used = {p for r in resp.results for p in r.providers}
    assert used == {"alpha", "gamma"}
    assert ["gamma"] in [r.providers for r in resp.results[:3]]  # interleaved, not appended


def test_fuse_replaces_failures(ctx):
    a = make("alpha", priority=90, outcome=Outcome.BLOCKED)
    b = make("beta", priority=80)
    c = make("gamma", priority=70)
    resp = engine_with(ctx, a, b, c).search(Query("hello"), strategy="fuse", fanout=2)
    used = {p for r in resp.results for p in r.providers}
    assert used == {"beta", "gamma"}


def test_all_failed_gives_note(ctx):
    a = make("alpha", outcome=Outcome.BLOCKED)
    resp = engine_with(ctx, a).search(Query("hello"))
    assert not resp.results and resp.notes


def test_parser_exception_is_contained(ctx):
    class Broken(make("broken", priority=90)):
        def search(self, q, limit):
            raise KeyError("layout changed")

    b = make("beta", priority=50)
    resp = engine_with(ctx, Broken, b).search(Query("hello"))
    assert resp.attempts[0].outcome == "parse_error" and resp.results
