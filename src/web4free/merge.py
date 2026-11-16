"""Deduplicate and merge results from several providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from web4free.models import Result

TRACKING_PREFIXES = ("utm_", "mc_", "_hs", "pk_", "hsa_")
TRACKING_PARAMS = frozenset(
    [
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "ocid",
        "ref",
        "ref_src",
        "cmpid",
        "campaign_id",
        "spm",
        "scid",
        "guccounter",
        "guce_referrer",
        "guce_referrer_sig",
        "srsltid",
        "ved",
        "ei",
    ]
)

RRF_K = 60.0


def norm_url(url: str) -> str:
    """Canonical form used only as a dedup key."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix) :]
    if p.port and p.port not in (80, 443):
        host = f"{host}:{p.port}"
    path = p.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
        for index in ("/index.html", "/index.htm", "/index.php"):
            if path.endswith(index):
                path = path[: -len(index)] or "/"
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query.sort()
    return urlunsplit(("", host, path, urlencode(query), ""))


@dataclass
class _Entry:
    result: Result
    score: float = 0.0
    first_seen: int = 0
    rank_key: tuple[int, int] = (0, 0)
    providers: list[str] = field(default_factory=list)


class Merger:
    """fallback: keep provider order (first provider's ranking first, later ones fill in).
    fuse: reciprocal rank fusion across providers, weighted by provider priority."""

    def __init__(self, category: str, strategy: str = "fallback") -> None:
        self.category = category
        self.strategy = strategy
        self._entries: dict[str, _Entry] = {}
        self._batches = 0

    def key(self, r: Result) -> str:
        if self.category == "images":
            return norm_url(r.image or r.url)
        return norm_url(r.url)

    def add(self, provider: str, results: list[Result], weight: float = 1.0) -> int:
        """Add one provider's ranked results. Returns how many were new."""
        batch = self._batches
        self._batches += 1
        new = 0
        for rank, r in enumerate(results):
            k = self.key(r)
            e = self._entries.get(k)
            if e is None:
                e = _Entry(result=r, first_seen=len(self._entries), rank_key=(batch, rank))
                self._entries[k] = e
                new += 1
            else:
                _fill(e.result, r)
            if provider not in e.providers:
                e.providers.append(provider)
            e.score += weight / (RRF_K + rank + 1)
        return new

    def __len__(self) -> int:
        return len(self._entries)

    def results(self, limit: int | None = None) -> list[Result]:
        entries = list(self._entries.values())
        if self.strategy == "fuse":
            entries.sort(key=lambda e: (-e.score, e.first_seen))
        else:
            entries.sort(key=lambda e: e.rank_key)
        out = []
        for e in entries[: limit or None]:
            e.result.providers = list(e.providers)
            out.append(e.result)
        return out


def _fill(base: Result, other: Result) -> None:
    """Fill empty fields of base from a duplicate; keep the longer snippet."""
    if len(other.snippet or "") > len(base.snippet or ""):
        base.snippet = other.snippet
    for name in (
        "date",
        "source",
        "image",
        "thumbnail",
        "width",
        "height",
        "duration",
        "publisher",
        "embed_url",
    ):
        if not getattr(base, name) and getattr(other, name):
            setattr(base, name, getattr(other, name))
