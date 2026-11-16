"""HTTP client wrapper around primp, with error classification and bot-wall detection."""

from __future__ import annotations

import json
import random
import re
import socket
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import primp

from web4free import __version__
from web4free.models import Outcome, ProviderError

# Desktop browser profiles only: mobile profiles get different SERP layouts.
DESKTOP_PROFILES = [
    ("chrome", "macos"),
    ("chrome", "windows"),
    ("chrome", "linux"),
    ("firefox", "macos"),
    ("firefox", "windows"),
    ("firefox", "linux"),
    ("safari", "macos"),
    ("edge", "windows"),
    ("edge", "macos"),
]

# Phrases seen on captcha, proof-of-work and "slow down" pages. Only checked when
# a parser found no results, so normal pages that mention these words are safe.
BLOCK_MARKERS = (
    "captcha",
    "anubis",
    "just a moment...",
    "cf-chl",
    "challenge-platform",
    "unusual traffic",
    "are you a robot",
    "not a robot",
    "bots use duckduckgo too",
    "making sure you're not a bot",
    "making sure you&#39;re not a bot",
    "browser verification required",
    "flagged as being suspicious",
    "security checkpoint",
    "aggressive bot activity",
    "enable javascript and cookies to continue",
    "access denied",
    "too many requests",
)


def app_user_agent(contact: str = "") -> str:
    ua = f"web4free/{__version__} (command-line search client)"
    return f"{ua} {contact}".strip() if contact else ua


@dataclass
class Response:
    status: int
    text: str
    headers: dict[str, str]
    url: str

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as ex:
            raise ProviderError(Outcome.PARSE, f"invalid JSON ({ex.__class__.__name__})") from ex

    def header(self, name: str) -> str | None:
        name = name.lower()
        for k, v in self.headers.items():
            if k.lower() == name:
                return v
        return None

    def retry_after(self) -> float | None:
        value = self.header("retry-after")
        if not value:
            return None
        value = value.strip()
        if value.isdigit():
            return float(value)
        try:
            from datetime import UTC, datetime

            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            return None

    def looks_blocked(self) -> bool:
        head = self.text[:200_000].lower()
        return any(m in head for m in BLOCK_MARKERS)

    def title(self) -> str:
        m = re.search(r"<title[^>]*>(.*?)</title>", self.text[:50_000], re.S | re.I)
        return " ".join(m.group(1).split())[:80] if m else ""


class Http:
    """One client per provider target. Impersonating clients look like a desktop browser;
    API clients send an honest web4free User-Agent."""

    def __init__(
        self,
        *,
        proxy: str | None,
        timeout: float,
        impersonate: bool,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> None:
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": follow_redirects,
            "proxy": proxy or None,
        }
        if impersonate:
            browser, os_name = random.choice(DESKTOP_PROFILES)
            kwargs["impersonate"] = browser
            kwargs["impersonate_os"] = os_name
        self.timeout = timeout
        self.client = primp.Client(**kwargs)
        base_headers = dict(headers or {})
        if not impersonate:
            base_headers.setdefault("User-Agent", user_agent or app_user_agent())
            base_headers.setdefault("Accept", "application/json, */*;q=0.5")
        if base_headers:
            self.client.headers_update(base_headers)

    def request(self, method: str, url: str, *, timeout: float | None = None, **kwargs: Any) -> Response:
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if timeout is not None:
            kwargs["timeout"] = max(1.0, timeout)
        try:
            r = self.client.request(method, url, **kwargs)
            text = r.text
        except primp.TimeoutError as ex:
            raise ProviderError(Outcome.TIMEOUT, "request timed out") from ex
        except (primp.DNSError, primp.DNSTimeoutError) as ex:
            raise ProviderError(Outcome.NETWORK, "DNS lookup failed") from ex
        except primp.ConnectError as ex:
            raise ProviderError(Outcome.NETWORK, _short(ex)) from ex
        except primp.PrimpError as ex:
            msg = _short(ex)
            kind = Outcome.TIMEOUT if "timed out" in msg.lower() else Outcome.NETWORK
            raise ProviderError(kind, msg) from ex
        return Response(r.status_code, text, dict(r.headers), str(r.url))

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)


def _short(ex: Exception) -> str:
    s = str(ex)
    # primp nests causes with " > "; the last part is usually the useful one
    parts = [p.strip() for p in s.split(" > ") if p.strip()]
    return (parts[-1] if parts else ex.__class__.__name__)[:160]


def check(resp: Response, ok: tuple[int, ...] = (200,)) -> Response:
    """Raise a classified ProviderError for a non-OK response."""
    if resp.status in ok:
        return resp
    s = resp.status
    if s == 429:
        raise ProviderError(Outcome.RATE_LIMITED, "HTTP 429", resp.retry_after())
    if s == 202:
        # DuckDuckGo and a few others answer 202 with a bot challenge when throttling
        raise ProviderError(
            Outcome.RATE_LIMITED, "HTTP 202 (soft rate limit / challenge)", resp.retry_after()
        )
    if s in (401, 403, 418, 451) or (s == 503 and resp.looks_blocked()):
        raise ProviderError(Outcome.BLOCKED, f"HTTP {s} {resp.title()}".strip())
    if s >= 500:
        raise ProviderError(Outcome.SERVER, f"HTTP {s}", resp.retry_after())
    if s in (400, 404, 405, 422):
        raise ProviderError(Outcome.CONFIG, f"HTTP {s}: {resp.text[:120]}")
    raise ProviderError(Outcome.SERVER, f"HTTP {s}")


def empty_or_blocked(resp: Response, what: str = "results") -> list[Any]:
    """Call when a parser found nothing: raise BLOCKED for bot walls, else return []."""
    if resp.looks_blocked():
        raise ProviderError(Outcome.BLOCKED, f"bot challenge page ({resp.title() or 'no title'})")
    return []


def tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
