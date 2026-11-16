"""SearXNG metasearch: your own instances plus vetted public instances.

Public instances come from searx.space (the official instance census). An instance
is used only if it passes vetting AND its operator has enabled the JSON output
format. Instances that answer with bot challenges or 403 for format=json have
chosen not to serve scripts; web4free respects that and does not scrape their HTML.

Vetting (from searx.space data): clearnet, no analytics, HTTP and TLS grades A+/A/B,
unmodified or CSS-only customised front end (html grade V or C), monthly uptime
>= min_uptime, recent search checks passing.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from web4free.http import Http, app_user_agent, check
from web4free.models import Outcome, ProviderError, Query, Result
from web4free.providers.base import Caps, Context, Provider
from web4free.quality import parse_date
from web4free.ratelimit import Bucket
from web4free.store import Store

SPACE_URL = "https://searx.space/data/instances.json"
LIST_KEY = "searxng:space"
JSON_KEY = "searxng:json"
LIST_TTL = 7 * 86400
JSON_TTL = 7 * 86400

# Passed vetting and served format=json when checked on 2026-11-16.
SEED_INSTANCES = ("https://etsi.me", "https://searx.oloke.xyz", "https://www.isci.si")

CATEGORY = {"text": "general", "news": "news", "images": "images", "videos": "videos"}
TIME_RANGE = {"d": "day", "w": "week", "m": "month", "y": "year"}
GOOD_GRADES = ("A+", "A", "B")


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or url).lower()


def vet(info: dict[str, Any], min_uptime: float) -> tuple[bool, str]:
    if info.get("network_type") != "normal":
        return False, "not clearnet"
    if info.get("analytics"):
        return False, "uses analytics"
    if not info.get("version"):
        return False, "no version (unreachable at last check)"
    http_grade = (info.get("http") or {}).get("grade")
    if http_grade not in GOOD_GRADES:
        return False, f"HTTP grade {http_grade}"
    tls_grade = (info.get("tls") or {}).get("grade")
    if tls_grade not in GOOD_GRADES:
        return False, f"TLS grade {tls_grade}"
    html_grade = (info.get("html") or {}).get("grade")
    if html_grade not in ("V", "C"):
        return False, f"front end grade {html_grade} (modified scripts or unknown)"
    uptime = (info.get("uptime") or {}).get("uptimeMonth") or 0
    if uptime < min_uptime:
        return False, f"monthly uptime {uptime}%"
    success = ((info.get("timing") or {}).get("search") or {}).get("success_percentage") or 0
    if success < 50:
        return False, f"search checks {success}% ok"
    return True, "ok"


def _space_http(ctx: Context) -> Http:
    return Http(proxy=ctx.proxy, timeout=20, impersonate=False, user_agent=app_user_agent(ctx.contact))


def _api_headers() -> dict[str, str]:
    # The SearXNG limiter wants browser-like Accept headers; the User-Agent stays honest.
    return {
        "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
        "Accept-Language": "en-US,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }


class Directory:
    """Instance list, vetting results and per-instance JSON capability memo."""

    def __init__(self, store: Store, config: dict[str, Any]) -> None:
        self.store = store
        self.cfg = config.get("searxng", {})

    def custom(self) -> list[str]:
        return [u.rstrip("/") for u in self.cfg.get("instances", []) if u]

    def listing(self) -> dict[str, Any] | None:
        return self.store.kv_get(LIST_KEY)

    def json_memo(self) -> dict[str, Any]:
        return self.store.kv_get(JSON_KEY) or {}

    def set_json(self, url: str, ok: bool, status: int | str) -> None:
        memo = self.json_memo()
        memo[host_of(url)] = {"json": ok, "status": status, "checked": time.time()}
        self.store.kv_set(JSON_KEY, memo)

    def vetted(self) -> list[tuple[str, str]]:
        """(url, reason) for every listed public instance, reason 'ok' when it passes."""
        data = self.listing()
        if not data:
            return []
        min_up = float(self.cfg.get("min_uptime", 90.0))
        return [(u.rstrip("/"), vet(i, min_up)[1]) for u, i in (data.get("instances") or {}).items()]

    def refresh_listing(self, ctx: Context) -> int:
        resp = check(_space_http(ctx).get(SPACE_URL))
        data = resp.json()
        slim = {"fetched": time.time(), "instances": data.get("instances", {})}
        self.store.kv_set(LIST_KEY, slim)
        return len(slim["instances"])

    def maybe_refresh_listing(self, ctx: Context) -> None:
        if not self.cfg.get("public", True):
            return
        age = self.store.kv_age(LIST_KEY)
        if age is not None and age < LIST_TTL:
            return
        try:
            self.refresh_listing(ctx)
        except ProviderError:
            # keep using the seed list; try again on a later run
            self.store.kv_set(LIST_KEY, self.listing() or {"fetched": time.time(), "instances": {}})

    def probe(self, ctx: Context, urls: list[str]) -> dict[str, tuple[bool, str]]:
        """Check format=json support (one request per instance)."""

        def one(url: str) -> tuple[str, bool, str]:
            http = Http(
                proxy=ctx.proxy,
                timeout=10,
                impersonate=False,
                user_agent=app_user_agent(ctx.contact),
                headers=_api_headers(),
            )
            try:
                r = http.get(f"{url}/search", params={"q": "weather", "format": "json"})
            except ProviderError as ex:
                return url, False, ex.kind.value
            ok = r.status == 200 and r.text.lstrip().startswith("{") and '"results"' in r.text[:5000]
            return url, ok, str(r.status) if not ok or r.status != 200 else "200"

        out: dict[str, tuple[bool, str]] = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            for url, ok, status in pool.map(one, urls):
                out[url] = (ok, status)
                if status not in ("network_error", "timeout"):
                    self.set_json(url, ok, status)
        return out

    def usable(self) -> list[tuple[str, str]]:
        """(url, source) to search, custom first. source: custom | public | explore."""
        out: list[tuple[str, str]] = [(u, "custom") for u in self.custom()]
        if not self.cfg.get("public", True):
            return out
        memo = self.json_memo()
        now = time.time()
        seen = {host_of(u) for u, _ in out}
        vetted = [u for u, reason in self.vetted() if reason == "ok"]
        explore: list[str] = []
        for url in list(SEED_INSTANCES) + vetted:
            h = host_of(url)
            if h in seen:
                continue
            seen.add(h)
            m = memo.get(h)
            if m is None:
                if url in SEED_INSTANCES:
                    out.append((url, "public"))
                else:
                    explore.append(url)
            elif m.get("json"):
                out.append((url, "public"))
            elif now - m.get("checked", 0) > JSON_TTL:
                explore.append(url)
        # one untested instance per run, so the pool grows slowly and politely
        if explore:
            out.append((explore[int(now) % len(explore)], "explore"))
        return out


class SearxngInstance(Provider):
    name = "searxng"
    kind = "meta"
    upstream = "mixed"
    group = "searxng"
    rate = (Bucket.every(10.0, burst=2),)
    caps = {
        "text": Caps("native", region=True, safesearch=True, paging=True, max_results=20),
        "news": Caps("native", region=True, safesearch=True, paging=True, max_results=20),
        "images": Caps("native", region=True, safesearch=True, paging=True, max_results=40),
        "videos": Caps("native", region=True, safesearch=True, paging=True, max_results=20),
    }
    priority = {"text": 65, "news": 65, "images": 65, "videos": 70}
    impersonate = False
    max_targets = 2
    homepage = "https://searx.space"
    basis = "Your own SearXNG instances, plus vetted public instances (searx.space census) whose operators enabled format=json. Bot-walled instances are never scraped."
    privacy = "The instance operator sees the query and your IP; vetting requires no analytics."
    limits = "Per instance: 1 request per 10 s here; instances may answer 429."

    def __init__(self, ctx: Context, url: str = "", source: str = "public") -> None:
        super().__init__(ctx)
        self.url = url.rstrip("/")
        self.source = source

    @property
    def id(self) -> str:
        return f"searxng:{host_of(self.url)}"

    @property
    def group_id(self) -> str:
        return self.id

    def base_priority(self, category: str) -> float:
        p = super().base_priority(category)
        if self.source == "custom":
            return p + 30
        if self.source == "explore":
            return p - 10
        return p

    def default_headers(self) -> dict[str, str]:
        return _api_headers()

    def search(self, q: Query, limit: int) -> list[Result]:
        per_page = 20 if q.category != "images" else 40
        first_page = q.offset // per_page + 1
        skip = q.offset % per_page
        out: list[Result] = []
        for i in range(2):
            if i and not self.pace():
                break
            batch = self._page(q, first_page + i)
            if i == 0:
                batch = batch[skip:]
            out.extend(batch)
            if len(out) >= limit or len(batch) < 5:
                break
        return out[:limit]

    def _page(self, q: Query, page: int) -> list[Result]:
        params: dict[str, Any] = {
            "q": q.text,
            "format": "json",
            "categories": CATEGORY[q.category],
            "pageno": str(page),
            "safesearch": {"off": "0", "moderate": "1", "on": "2"}[q.safesearch],
            "language": f"{q.lang}-{q.country.upper()}" if q.country else "all",
        }
        if q.timelimit:
            params["time_range"] = TIME_RANGE[q.timelimit]
        resp = self.http().get(f"{self.url}/search", params=params, timeout=self.ctx.request_timeout())
        directory = Directory(self.ctx.store, self.ctx.config)
        is_json = resp.text.lstrip().startswith("{")
        if resp.status == 403 or (resp.status == 200 and not is_json):
            if resp.status == 403 or self.source == "explore":
                directory.set_json(self.url, False, resp.status)
                raise ProviderError(Outcome.BLOCKED, f"format=json not served (HTTP {resp.status})")
            raise ProviderError(Outcome.BLOCKED, f"bot check or limiter page ({resp.title() or 'HTTP 200'})")
        check(resp)
        if self.source == "explore":
            directory.set_json(self.url, True, 200)
        data = resp.json()
        out = []
        for r in data.get("results", []):
            url = r.get("url") or ""
            out.append(
                Result(
                    title=r.get("title") or "",
                    url=url,
                    snippet=r.get("content") or "",
                    date=parse_date(r.get("publishedDate")),
                    source=(r.get("source") or (host_of(url) if q.category == "news" else None)),
                    image=r.get("img_src") or (r.get("thumbnail") if q.category == "news" else None),
                    thumbnail=r.get("thumbnail_src") or r.get("thumbnail"),
                    width=_dim(r.get("resolution"), 0),
                    height=_dim(r.get("resolution"), 1),
                    duration=_duration(r.get("length")),
                    publisher=r.get("author"),
                    embed_url=r.get("iframe_src"),
                )
            )
        if not out and data.get("unresponsive_engines"):
            names = ", ".join(str(e[0]) for e in data["unresponsive_engines"][:4] if e)
            raise ProviderError(Outcome.SERVER, f"instance engines failing: {names}")
        return out


def _dim(resolution: Any, index: int) -> int | None:
    if not resolution or not isinstance(resolution, str):
        return None
    parts = resolution.lower().replace(" ", "").replace("×", "x").split("x")
    try:
        return int(parts[index])
    except (IndexError, ValueError):
        return None


def _duration(length: Any) -> str | None:
    if length in (None, ""):
        return None
    if isinstance(length, (int, float)):
        s = int(length)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
    return str(length)
