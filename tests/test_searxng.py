"""SearXNG vetting and instance selection (offline, searx.space snapshot of 2026-11-16)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from web4free.providers.searxng import LIST_KEY, SEED_INSTANCES, Directory, vet

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "searx_space_sample.json").read_text())


def test_vetting_matches_manual_review():
    passed = {u.rstrip("/") for u, i in SAMPLE["instances"].items() if vet(i, 90.0)[0]}
    assert passed == {
        "https://etsi.me",
        "https://searx.oloke.xyz",
        "https://www.isci.si",
        "https://search.serpensin.com",
    }
    reasons = {u: vet(i, 90.0)[1] for u, i in SAMPLE["instances"].items()}
    assert reasons["https://searx.dresden.network/"].startswith("HTTP grade")
    assert reasons["https://searx.githo.st/"].startswith("TLS grade")
    assert reasons["https://searx.party/"].startswith("search checks")
    assert "front end grade F" in reasons["https://priv.au/"]


def test_usable_prefers_custom_then_known_json_then_one_explore(store):
    cfg = {"searxng": {"public": True, "instances": ["https://my.searx.example/"], "min_uptime": 90.0}}
    store.kv_set(LIST_KEY, {"fetched": time.time(), "instances": SAMPLE["instances"]})
    d = Directory(store, cfg)
    usable = d.usable()
    assert usable[0] == ("https://my.searx.example", "custom")
    public = [u for u, src in usable if src == "public"]
    assert set(public) == set(SEED_INSTANCES)
    # serpensin passes vetting but is untested: offered as the single exploration slot
    assert [u for u, src in usable if src == "explore"] == ["https://search.serpensin.com"]
    # once it refuses format=json it is no longer offered
    d.set_json("https://search.serpensin.com", False, 403)
    assert all(src != "explore" for _, src in d.usable())
    # a seed that stops serving JSON drops out too
    d.set_json(SEED_INSTANCES[0], False, 403)
    assert SEED_INSTANCES[0] not in [u for u, _ in d.usable()]


def test_public_off_uses_only_custom(store):
    d = Directory(store, {"searxng": {"public": False, "instances": ["https://a.example"]}})
    assert d.usable() == [("https://a.example", "custom")]
