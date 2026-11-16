"""Provider registry."""

from __future__ import annotations

from web4free.providers.apis import Exa, Firecrawl, Keenable, Parallel
from web4free.providers.base import Caps, Context, Provider
from web4free.providers.bing import Bing
from web4free.providers.ddgs_provider import Ddgs
from web4free.providers.duckduckgo import DuckDuckGo
from web4free.providers.open_data import (
    Gdelt,
    GoogleNews,
    HackerNews,
    Mwmbl,
    Openverse,
    WikimediaCommons,
    Wikipedia,
)
from web4free.providers.scrapers import Brave, Mojeek, Startpage
from web4free.providers.searxng import Directory, SearxngInstance
from web4free.providers.yahoo import Yahoo

PROVIDER_CLASSES: tuple[type[Provider], ...] = (
    Exa,
    Keenable,
    Firecrawl,
    Parallel,
    DuckDuckGo,
    Bing,
    Brave,
    Yahoo,
    SearxngInstance,
    Ddgs,
    GoogleNews,
    Startpage,
    Mojeek,
    Wikipedia,
    Mwmbl,
    Gdelt,
    HackerNews,
    Openverse,
    WikimediaCommons,
)

BY_NAME: dict[str, type[Provider]] = {cls.name: cls for cls in PROVIDER_CLASSES}
KINDS = ("api", "scrape", "meta")


def build_targets(ctx: Context, refresh_searxng: bool = True) -> list[Provider]:
    """One target per provider; SearXNG expands to one target per usable instance."""
    targets: list[Provider] = []
    for cls in PROVIDER_CLASSES:
        if cls is SearxngInstance:
            directory = Directory(ctx.store, ctx.config)
            if refresh_searxng:
                directory.maybe_refresh_listing(ctx)
            for url, source in directory.usable():
                targets.append(SearxngInstance(ctx, url, source))
        else:
            targets.append(cls(ctx))
    return targets


__all__ = [
    "BY_NAME",
    "KINDS",
    "PROVIDER_CLASSES",
    "Caps",
    "Context",
    "Directory",
    "Provider",
    "SearxngInstance",
    "build_targets",
]
