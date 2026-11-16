"""Core data types: queries, results, outcomes and provider errors."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

CATEGORIES = ("text", "news", "images", "videos")

# Time limits accepted on the command line, with their span.
TIMELIMITS: dict[str, timedelta] = {
    "d": timedelta(days=1),
    "w": timedelta(days=7),
    "m": timedelta(days=31),
    "y": timedelta(days=366),
}

SAFESEARCH = ("on", "moderate", "off")


class Outcome(enum.StrEnum):
    """Result of one provider attempt. Drives stats and cooldowns."""

    OK = "ok"
    EMPTY = "empty"  # no results, page looked normal
    EMPTY_SUSPECT = "empty_suspect"  # no results while other providers had results
    POOR = "poor"  # results came back but look irrelevant to the query
    RATE_LIMITED = "rate_limited"  # 429, soft limit, quota exhausted
    BLOCKED = "blocked"  # captcha, bot challenge, 403, access denied
    SERVER = "server_error"  # 5xx
    NETWORK = "network_error"  # DNS, connect, TLS
    TIMEOUT = "timeout"
    PARSE = "parse_error"  # response could not be parsed (format drift)
    CONFIG = "config_error"  # bad request from our side (4xx other than above)
    SKIPPED = "skipped"  # not attempted (cooldown, rate wait, filters)

    @property
    def is_failure(self) -> bool:
        return self not in (Outcome.OK, Outcome.EMPTY, Outcome.SKIPPED)

    @property
    def is_transient(self) -> bool:
        """Failures worth one quick retry on the same provider."""
        return self in (Outcome.SERVER, Outcome.NETWORK, Outcome.TIMEOUT)


class ProviderError(Exception):
    """Raised by providers. The kind tells the scheduler how to react."""

    def __init__(self, kind: Outcome, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message or kind.value)
        self.kind = kind
        self.message = message or kind.value
        self.retry_after = retry_after

    def __str__(self) -> str:
        return f"{self.kind.value}: {self.message}"


@dataclass
class Query:
    text: str
    category: str = "text"
    max_results: int = 10
    timelimit: str | None = None  # d, w, m, y
    region: str = "us-en"  # <country>-<language>, "wt-wt" for no region
    safesearch: str = "moderate"  # on, moderate, off
    page: int = 1
    options: dict[str, str] = field(default_factory=dict)  # image and video filters

    def __post_init__(self) -> None:
        self.text = " ".join(self.text.split())
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}; use one of {', '.join(CATEGORIES)}")
        if self.timelimit is not None and self.timelimit not in TIMELIMITS:
            raise ValueError(f"unknown time limit {self.timelimit!r}; use d, w, m or y")
        if self.safesearch not in SAFESEARCH:
            raise ValueError(f"unknown safesearch {self.safesearch!r}; use on, moderate or off")
        if self.max_results < 1:
            raise ValueError("max_results must be at least 1")
        if self.page < 1:
            raise ValueError("page must be at least 1")
        self.region = (self.region or "wt-wt").lower()
        if "-" not in self.region:
            self.region = f"{self.region}-en"

    @property
    def country(self) -> str:
        """Two-letter country code, lower case, or "" for no region."""
        c = self.region.split("-", 1)[0]
        return "" if c in ("wt", "xa", "") else c

    @property
    def lang(self) -> str:
        """Language code, lower case. Defaults to en."""
        parts = self.region.split("-", 1)
        lang = parts[1] if len(parts) > 1 else "en"
        return "en" if lang in ("wt", "") else lang

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.max_results

    def since(self) -> datetime | None:
        if not self.timelimit:
            return None
        return datetime.now(UTC) - TIMELIMITS[self.timelimit]


# Fields kept per category, in output order.
FIELDS: dict[str, tuple[str, ...]] = {
    "text": ("title", "url", "snippet", "date"),
    "news": ("title", "url", "snippet", "date", "source", "image"),
    "images": ("title", "image", "thumbnail", "url", "width", "height", "source"),
    "videos": ("title", "url", "snippet", "duration", "publisher", "date", "thumbnail", "embed_url"),
}


@dataclass
class Result:
    title: str
    url: str
    snippet: str = ""
    date: str | None = None  # ISO 8601 when known
    source: str | None = None  # publisher or site name
    image: str | None = None  # images: full-size image; news: picture
    thumbnail: str | None = None
    width: int | None = None
    height: int | None = None
    duration: str | None = None
    publisher: str | None = None
    embed_url: str | None = None
    providers: list[str] = field(default_factory=list)

    def to_dict(self, category: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in FIELDS[category]:
            value = getattr(self, name)
            if value not in (None, "", 0):
                out[name] = value
        out["providers"] = list(self.providers)
        return out
