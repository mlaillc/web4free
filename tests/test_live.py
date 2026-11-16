"""Live checks against real providers. Run with: uv run pytest -m live -k <provider> -s

They make real requests; keep them few. Blocks and rate limits are reported as skips,
because they say nothing about parser correctness.
"""

from __future__ import annotations

import os

import pytest

from web4free.models import Outcome, ProviderError, Query
from web4free.providers import BY_NAME, Directory, SearxngInstance
from web4free.quality import assess

QUERIES = {
    "text": "python asyncio taskgroup exception handling",
    "news": "electric vehicles",
    "images": "red panda",
    "videos": "how to tie a tie",
}

CASES = [(name, cat) for name, cls in BY_NAME.items() if name != "searxng" for cat in cls.caps]


def _run(provider, category):
    q = Query(QUERIES[category], category=category, max_results=8)
    try:
        raw = provider.search(q, 8)
    except ProviderError as ex:
        if ex.kind in (Outcome.RATE_LIMITED, Outcome.BLOCKED, Outcome.SKIPPED):
            pytest.skip(f"{provider.id}/{category}: {ex}")
        raise
    a = assess(q, raw)
    print(
        f"\n{provider.id}/{category}: {len(raw)} raw, {len(a.results)} kept, outcome={a.outcome}, rel={a.relevance}"
    )
    for r in a.results[:3]:
        print(
            "   ",
            r.title[:70],
            "|",
            (r.url or "")[:70],
            "|",
            (r.image or "")[:50],
            "|",
            r.date,
            "|",
            r.snippet[:80],
        )
    assert a.results, "no usable results"
    assert a.outcome is Outcome.OK, a.outcome
    return a.results


@pytest.mark.live
@pytest.mark.parametrize(("name", "category"), CASES, ids=[f"{n}-{c}" for n, c in CASES])
def test_provider_live(ctx, name, category):
    only = os.environ.get("W4F_LIVE_ONLY")
    if only and name not in only.split(","):
        pytest.skip("not selected")
    _run(BY_NAME[name](ctx), category)


@pytest.mark.live
@pytest.mark.parametrize("category", ["text", "news", "images"])
def test_searxng_seed_live(ctx, category):
    d = Directory(ctx.store, ctx.config)
    url = d.usable()[0][0]
    _run(SearxngInstance(ctx, url, "public"), category)


@pytest.mark.live
@pytest.mark.parametrize("category", ["text", "news", "images", "videos"])
@pytest.mark.parametrize("strategy", ["fallback", "fuse"])
def test_engine_live(ctx, category, strategy):
    from web4free.engine import Engine
    from web4free.providers import build_targets

    ctx.max_wait = 3
    eng = Engine(ctx, build_targets(ctx, refresh_searxng=False))
    resp = eng.search(
        Query(QUERIES[category], category=category, max_results=10, timelimit=None),
        strategy=strategy,
        deadline=60,
    )
    print(f"\n[{strategy}/{category}] {len(resp.results)} results in {resp.elapsed:.1f}s")
    for a in resp.attempts:
        print("   attempt", a.to_dict())
    for r in resp.results[:5]:
        print("   ", r.providers, r.title[:70], "|", (r.url or r.image or "")[:70])
    assert len(resp.results) >= 5


@pytest.mark.live
@pytest.mark.parametrize("category", ["text", "news"])
def test_engine_timelimit_live(ctx, category):
    from datetime import UTC, datetime, timedelta

    from web4free.engine import Engine
    from web4free.providers import build_targets

    eng = Engine(ctx, build_targets(ctx, refresh_searxng=False))
    resp = eng.search(
        Query("electric vehicle sales", category=category, max_results=10, timelimit="w"),
        strategy="fuse",
        fanout=3,
        deadline=60,
    )
    limit = datetime.now(UTC) - timedelta(days=8)
    print(f"\n[{category} -t w] {len(resp.results)} results")
    for a in resp.attempts:
        print("   attempt", a.to_dict())
    for r in resp.results:
        print("   ", r.date, r.providers, r.title[:60])
    assert resp.results
    dated = [r for r in resp.results if r.date]
    assert all(datetime.fromisoformat(r.date) >= limit for r in dated)
