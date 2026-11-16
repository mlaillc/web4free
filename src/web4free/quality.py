"""Cleaning, validation and relevance checks for provider results."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from web4free.models import Outcome, Query, Result

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "vs",
        "versus",
        "de",
        "la",
        "le",
        "el",
        "en",
        "et",
        "und",
        "der",
        "die",
        "das",
    ]
)

# Ad and tracker redirects that must never be returned as results.
JUNK_URL_PATTERNS = (
    "duckduckgo.com/y.js",
    "bing.com/aclick",
    "bing.com/aclk",
    "googleadservices.com",
    "doubleclick.net",
    "r.search.yahoo.com/cbclk",
    "search.yahoo.com/aclick",
)

_TAG_RE = re.compile(r"<[^>]+>")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS_RE = re.compile(r"(^|\s)#{1,6}\s+|\*\*|__|`{1,3}|^\s*[-*]\s+", re.M)
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def clean_text(value: str | None, limit: int = 0) -> str:
    """Strip HTML and markdown decoration, unescape entities, collapse whitespace."""
    if not value:
        return ""
    s = html.unescape(_TAG_RE.sub(" ", value))
    s = _MD_IMAGE_RE.sub(r"\1", s)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _MD_MARKS_RE.sub(" ", s)
    s = s.replace("", "").replace("", "")  # Bing highlight markers
    s = _WS_RE.sub(" ", s).strip()
    if limit and len(s) > limit:
        cut = s[:limit].rsplit(" ", 1)[0]
        s = (cut if len(cut) > limit * 0.6 else s[:limit]).rstrip(" ,.;:-") + "…"
    return s


def valid_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc or "." not in parts.netloc:
        return False
    low = url.lower()
    return not any(p in low for p in JUNK_URL_PATTERNS)


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def query_terms(text: str) -> list[str]:
    """Significant terms of a query, without operators and stopwords."""
    text = re.sub(r"(?i)\b(?:site|inurl|intitle|filetype|ext|lang):\S+", " ", text)
    text = re.sub(r"(^|\s)-\S+", " ", text)  # exclusions
    text = re.sub(r"\b(?:OR|AND|NOT)\b", " ", text)
    words = [w.lower() for w in _WORD_RE.findall(text)]
    terms = [_stem(w) for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]
    seen: set[str] = set()
    return [t for t in terms if not (t in seen or seen.add(t))]


def _haystack(r: Result) -> str:
    url_words = " ".join(_WORD_RE.findall((r.url or "") + " " + (r.image or "")))
    text = f"{r.title} {r.snippet} {r.source or ''} {url_words}".lower()
    return " ".join(_stem(w) for w in _WORD_RE.findall(text))


def relevance(terms: list[str], r: Result) -> float:
    """Share of query terms present in title, snippet or URL."""
    if not terms:
        return 1.0
    hay = _haystack(r)
    words = set(hay.split())
    hits = sum(1 for t in terms if t in words or (len(t) >= 5 and t in hay))
    return hits / len(terms)


@dataclass
class Assessment:
    results: list[Result]
    outcome: Outcome
    relevance: float | None
    dropped: int


def assess(
    query: Query, results: list[Result], snippet_length: int = 0, check_relevance: bool = True
) -> Assessment:
    """Clean and dedupe one provider's results and judge whether they are usable."""
    kept: list[Result] = []
    seen: set[str] = set()
    for r in results:
        r.title = clean_text(r.title)
        r.snippet = clean_text(r.snippet, snippet_length)
        if query.category == "images":
            if not valid_url(r.image):
                continue
            key = r.image or ""
            if r.url and not valid_url(r.url):
                r.url = ""
        else:
            if not valid_url(r.url):
                continue
            key = r.url
        if key in seen:
            continue
        seen.add(key)
        if not r.title:
            r.title = urlsplit(r.url or r.image or "").netloc
        kept.append(r)
    dropped = len(results) - len(kept)
    if not kept:
        return Assessment([], Outcome.EMPTY, None, dropped)
    if _CJK_RE.search(query.text) or not check_relevance:
        return Assessment(kept, Outcome.OK, None, dropped)
    terms = query_terms(query.text)
    if not terms:
        return Assessment(kept, Outcome.OK, None, dropped)
    scores = [relevance(terms, r) for r in kept]
    rel = sum(scores) / len(scores)
    matching = sum(1 for s in scores if s > 0)
    # Semantic engines may paraphrase, so the bar is low: flag result sets where most
    # results share no term with the query, or cover only a small part of it.
    if len(kept) >= 3 and (matching / len(kept) < 0.3 or (len(terms) >= 2 and rel < 0.35)):
        # e.g. an engine that silently searched only the first word of the query
        return Assessment(kept, Outcome.POOR, rel, dropped)
    return Assessment(kept, Outcome.OK, rel, dropped)


# --- dates ---------------------------------------------------------------------

_REL_RE = re.compile(
    r"(\d+)\s*(min(?:ute)?s?|mins?|h(?:ou)?rs?|hours?|d(?:ay)?s?|days?|w(?:ee)?ks?|weeks?|mo(?:nth)?s?|months?|y(?:ea)?rs?|years?)\s*ago",
    re.I,
)
_UNITS = {
    "min": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "mo": timedelta(days=30),
    "y": timedelta(days=365),
}
_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y", "%Y%m%dT%H%M%SZ")


def _unit(u: str) -> timedelta:
    u = u.lower()
    if u.startswith("mo"):
        return _UNITS["mo"]
    if u.startswith("mi"):
        return _UNITS["min"]
    return _UNITS[u[0]]


def parse_date(value: str | None, now: datetime | None = None) -> str | None:
    """Best effort: return an ISO 8601 UTC string, or None."""
    if not value:
        return None
    v = " ".join(str(value).split()).strip(" ·-")
    if not v:
        return None
    now = now or datetime.now(UTC)
    low = v.lower()
    if low in ("today", "just now"):
        return now.replace(microsecond=0).isoformat()
    if low == "yesterday":
        return (now - timedelta(days=1)).replace(microsecond=0).isoformat()
    m = _REL_RE.search(v)
    if m:
        return (now - int(m.group(1)) * _unit(m.group(2))).replace(microsecond=0).isoformat()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).replace(microsecond=0).isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(v).astimezone(UTC).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    if v.isdigit() and len(v) in (10, 13):  # unix epoch seconds or ms
        ts = int(v) / (1000 if len(v) == 13 else 1)
        return datetime.fromtimestamp(ts, UTC).replace(microsecond=0).isoformat()
    return None


def within(date_iso: str | None, since: datetime | None) -> bool:
    if since is None:
        return True
    if not date_iso:
        return False
    try:
        return datetime.fromisoformat(date_iso) >= since
    except ValueError:
        return False
