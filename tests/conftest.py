from __future__ import annotations

import pytest

from web4free.config import DEFAULTS
from web4free.providers import Context
from web4free.ratelimit import RateLimiter
from web4free.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def ctx(store):
    import copy

    return Context(
        store=store, limiter=RateLimiter(store), config=copy.deepcopy(DEFAULTS), timeout=15, max_wait=30
    )
