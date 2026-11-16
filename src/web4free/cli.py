"""Command-line interface."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

import click

from web4free import __version__
from web4free.config import ConfigError, config_path, data_dir, load_config, write_default_config
from web4free.engine import Engine, SelectionError
from web4free.health import Health
from web4free.models import CATEGORIES, Query
from web4free.output import (
    FORMATS,
    attempts_summary,
    format_from_path,
    render,
    result_count_line,
    table,
)
from web4free.providers import BY_NAME, PROVIDER_CLASSES, Context, Directory, build_targets
from web4free.ratelimit import RateLimiter
from web4free.store import Store
from web4free.tor import ProxyError, resolve_proxy

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


class DefaultGroup(click.Group):
    """`w4f some words` runs a web (text) search."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ("-h", "--help", "--version"):
            args = ["text", *args]
        return super().parse_args(ctx, args)


@click.group(cls=DefaultGroup, context_settings=CONTEXT_SETTINGS)
@click.version_option(__version__, prog_name="web4free")
def main() -> None:
    """Free, keyless web search. Many providers, tried in order, with rate limits,
    health tracking and failover.

    \b
    Examples:
      w4f "rust async runtimes"                 web search (same as: w4f text ...)
      w4f news -t d "electric vehicles"         news from the last day
      w4f images --size large "red panda"
      w4f text --include exa,bing -m 20 "sqlite wal mode"
      w4f text --strategy fuse "best e-ink tablets"   merge several providers
      w4f providers        w4f stats        w4f probe
    """


# --- shared plumbing -------------------------------------------------------------


def _split(values: tuple[str, ...] | list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or ():
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def _load_config() -> dict[str, Any]:
    try:
        return load_config()
    except ConfigError as ex:
        raise click.ClickException(str(ex)) from ex


def _open(
    cfg: dict[str, Any],
    proxy: str | None,
    tor: bool,
    timeout: float | None,
    max_wait: float | None,
    searxng_urls: tuple[str, ...] = (),
) -> tuple[Context, Store]:
    s = cfg["search"]
    value = (
        "tor"
        if tor
        else (proxy if proxy is not None else (s.get("proxy") or os.environ.get("WEB4FREE_PROXY")))
    )
    try:
        proxy_url, route = resolve_proxy(value)
    except ProxyError as ex:
        raise click.ClickException(str(ex)) from ex
    if searxng_urls:
        cfg["searxng"]["instances"] = list(cfg["searxng"].get("instances", [])) + list(searxng_urls)
    store = Store(data_dir() / "web4free.db")
    ctx = Context(
        store=store,
        limiter=RateLimiter(store),
        config=cfg,
        proxy=proxy_url,
        route=route,
        timeout=float(timeout if timeout is not None else s["timeout"]),
        max_wait=float(max_wait if max_wait is not None else s["max_wait"]),
        contact=str(cfg.get("http", {}).get("contact", "")),
    )
    return ctx, store


def search_options(f: Callable[..., Any]) -> Callable[..., Any]:
    options = [
        click.argument("words", nargs=-1),
        click.option("-q", "--query", help="Query text (instead of positional words)."),
        click.option("-m", "--max-results", type=click.IntRange(1, 200), help="Results wanted [10]."),
        click.option(
            "-t",
            "--time",
            "timelimit",
            type=click.Choice(["d", "w", "m", "y"]),
            help="Only results from the last day, week, month or year.",
        ),
        click.option(
            "-r", "--region", help="<country>-<language>, e.g. us-en, de-de; wt-wt for none [us-en]."
        ),
        click.option("-s", "--safesearch", type=click.Choice(["on", "moderate", "off"]), help="[moderate]"),
        click.option("-p", "--page", type=click.IntRange(1, 50), default=1, show_default=True),
        click.option(
            "-i",
            "--include",
            multiple=True,
            help="Use only these providers, tried in this order. Comma-separated or repeated. "
            "Names, @api, @scrape, @meta, or searxng:<host>.",
        ),
        click.option("-x", "--exclude", multiple=True, help="Never use these providers (same syntax)."),
        click.option(
            "--strategy",
            type=click.Choice(["fallback", "fuse"]),
            help="fallback: in order until enough results [default]; "
            "fuse: several providers at once, merged by rank fusion.",
        ),
        click.option("--fanout", type=click.IntRange(1, 10), help="Providers merged in fuse mode [3]."),
        click.option("--proxy", help="Proxy URL (http://, socks5h://) or 'tor'."),
        click.option("--tor", is_flag=True, help="Route through a running Tor client (SOCKS 9050/9150)."),
        click.option("--searxng-url", multiple=True, help="Your own SearXNG instance URL (repeatable)."),
        click.option("--timeout", type=float, help="Seconds per request [10]."),
        click.option("--deadline", type=float, help="Seconds for the whole search [45]."),
        click.option(
            "--max-wait", type=float, help="Longest wait for a rate-limit slot before moving on [3]."
        ),
        click.option("--force", is_flag=True, help="Ignore cooldowns (rate limits still apply)."),
        click.option(
            "--sort", type=click.Choice(["relevance", "date"]), default="relevance", show_default=True
        ),
        click.option("-f", "--format", "fmt", type=click.Choice(FORMATS), help="Output format [text]."),
        click.option("-j", "--json", "as_json", is_flag=True, help="Same as --format json."),
        click.option(
            "-o",
            "--output",
            type=click.Path(dir_okay=False, writable=True),
            help="Write results to a file; format from extension (.json .jsonl .csv .md).",
        ),
        click.option("--snippet-length", type=int, help="Max snippet characters, 0 = full [400]."),
        click.option("-v", "--verbose", is_flag=True, help="Show every provider attempt on stderr."),
    ]
    for opt in reversed(options):
        f = opt(f)
    return f


def run_search(category: str, opts: dict[str, Any], extra: dict[str, str]) -> None:
    cfg = _load_config()
    s = cfg["search"]
    text = opts["query"] or " ".join(opts["words"])
    if not text.strip():
        raise click.UsageError("missing query")
    try:
        q = Query(
            text=text,
            category=category,
            max_results=opts["max_results"] or int(s["max_results"]),
            timelimit=opts["timelimit"],
            region=opts["region"] or s["region"],
            safesearch=opts["safesearch"] or s["safesearch"],
            page=opts["page"],
            options={k: v for k, v in extra.items() if v},
        )
    except ValueError as ex:
        raise click.UsageError(str(ex)) from ex
    ctx, store = _open(
        cfg, opts["proxy"], opts["tor"], opts["timeout"], opts["max_wait"], opts["searxng_url"]
    )
    try:
        engine = Engine(ctx, build_targets(ctx))
        include = _split(opts["include"]) or list(s.get("include") or [])
        exclude = _split(opts["exclude"]) + list(s.get("exclude") or [])
        try:
            resp = engine.search(
                q,
                include=include,
                exclude=exclude,
                strategy=opts["strategy"] or s["strategy"],
                fanout=opts["fanout"] or int(s["fanout"]),
                retries=int(s["retries"]),
                deadline=float(opts["deadline"] or s["deadline"]),
                snippet_length=int(
                    opts["snippet_length"] if opts["snippet_length"] is not None else s["snippet_length"]
                ),
                force=opts["force"],
                sort=opts["sort"],
            )
        except SelectionError as ex:
            raise click.UsageError(str(ex)) from ex
    finally:
        store.close()

    fmt = "json" if opts["as_json"] else (opts["fmt"] or "text")
    err_color = sys.stderr.isatty()
    if opts["output"]:
        file_fmt = opts["fmt"] or ("json" if opts["as_json"] else format_from_path(opts["output"]))
        with open(opts["output"], "w", encoding="utf-8") as fh:
            fh.write(render(resp, file_fmt) + "\n")
        click.echo(f"wrote {len(resp.results)} results to {opts['output']}", err=True)
    else:
        body = render(resp, fmt, color=sys.stdout.isatty() and fmt == "text")
        if body:
            click.echo(body)
    if opts["verbose"]:
        click.echo(attempts_summary(resp.attempts, err_color), err=True)
        click.echo(result_count_line(resp), err=True)
    for note in resp.notes:
        click.echo(
            click.style(f"web4free: {note}", fg="yellow") if err_color else f"web4free: {note}", err=True
        )
        if not opts["verbose"]:
            click.echo(attempts_summary(resp.attempts, err_color), err=True)
    if not resp.results:
        sys.exit(1)


# --- search commands -----------------------------------------------------------------


@main.command("text", context_settings=CONTEXT_SETTINGS)
@search_options
def text_cmd(**opts: Any) -> None:
    """Web search. QUERY words may be given without quotes."""
    run_search("text", opts, {})


@main.command("news", context_settings=CONTEXT_SETTINGS)
@search_options
def news_cmd(**opts: Any) -> None:
    """News search."""
    run_search("news", opts, {})


@main.command("images", context_settings=CONTEXT_SETTINGS)
@search_options
@click.option("--size", type=click.Choice(["small", "medium", "large", "wallpaper"]))
@click.option(
    "--color",
    help="color, monochrome, red, orange, yellow, green, blue, purple, pink, brown, black, gray, teal, white",
)
@click.option("--type", "type_", type=click.Choice(["photo", "clipart", "gif", "transparent", "line"]))
@click.option("--layout", type=click.Choice(["square", "tall", "wide"]))
@click.option(
    "--license",
    "license_",
    type=click.Choice(["any", "public", "share", "sharecommercially", "modify", "modifycommercially"]),
)
def images_cmd(
    size: str | None,
    color: str | None,
    type_: str | None,
    layout: str | None,
    license_: str | None,
    **opts: Any,
) -> None:
    """Image search."""
    lic = None if license_ in (None, "any") else license_
    run_search(
        "images",
        opts,
        {
            "size": size or "",
            "color": color or "",
            "type": type_ or "",
            "layout": layout or "",
            "license": lic or "",
        },
    )


@main.command("videos", context_settings=CONTEXT_SETTINGS)
@search_options
@click.option("--duration", type=click.Choice(["short", "medium", "long"]))
@click.option("--resolution", type=click.Choice(["high", "standard"]))
@click.option("--license", "license_", type=click.Choice(["creativeCommon", "youtube"]))
def videos_cmd(duration: str | None, resolution: str | None, license_: str | None, **opts: Any) -> None:
    """Video search."""
    run_search(
        "videos",
        opts,
        {"duration": duration or "", "resolution": resolution or "", "license": license_ or ""},
    )


# --- providers / stats / probe ---------------------------------------------------------


def _ago(ts: float | None) -> str:
    if not ts:
        return "-"
    s = int(time.time() - ts)
    if s < 120:
        return f"{s}s ago"
    if s < 7200:
        return f"{s // 60}m ago"
    if s < 172800:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _left(until: float) -> str:
    s = max(0, int(until - time.time()))
    return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


@main.command("providers", context_settings=CONTEXT_SETTINGS)
@click.option("-c", "--category", type=click.Choice(CATEGORIES))
@click.option("-j", "--json", "as_json", is_flag=True)
@click.option("-l", "--long", "long_", is_flag=True, help="Show legal basis, privacy and limits.")
@click.option("--proxy", help="Show status for this route (proxy URL or 'tor').")
@click.option("--tor", is_flag=True)
def providers_cmd(category: str | None, as_json: bool, long_: bool, proxy: str | None, tor: bool) -> None:
    """List providers, what they support, and their current state."""
    cfg = _load_config()
    ctx, store = _open(cfg, proxy, tor, None, None)
    try:
        targets = build_targets(ctx, refresh_searxng=False)
        health = Health(store)
        rows: list[dict[str, Any]] = []
        for t in targets:
            cats = [c for c in CATEGORIES if t.supports(c) and (category is None or c == category)]
            if not cats:
                continue
            state = []
            for c in cats:
                cd = health.cooldown(t.id, c, t.group_id, ctx.route)
                if cd:
                    state.append(f"{c}: cooling {_left(cd[0])}")
            rows.append(
                {
                    "provider": t.id,
                    "kind": t.kind,
                    "default": t.enabled_by_default(),
                    "categories": {
                        c: {
                            "priority": t.base_priority(c),
                            "time": t.caps[c].timelimit,
                            "paging": t.caps[c].paging,
                        }
                        for c in cats
                    },
                    "upstream": t.upstream,
                    "state": "; ".join(state) or "ready",
                    "basis": t.basis,
                    "privacy": t.privacy,
                    "limits": t.limits,
                    "homepage": t.homepage,
                }
            )
    finally:
        store.close()
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    order = category or "text"
    rows.sort(
        key=lambda r: (
            -max(
                (v["priority"] for k, v in r["categories"].items() if k == order),
                default=max(v["priority"] for v in r["categories"].values()) - 1000,
            )
        )
    )
    out = [
        [
            r["provider"],
            r["kind"],
            "yes" if r["default"] else "no",
            " ".join(f"{c}:{int(v['priority'])}" for c, v in r["categories"].items()),
            r["state"],
        ]
        for r in rows
    ]
    click.echo(table(out, ["provider", "kind", "default", "categories:priority", "state"]))
    if long_:
        for r in rows:
            click.echo(f"\n{r['provider']} ({r['kind']}) {r['homepage']}")
            click.echo(f"  basis:   {r['basis']}")
            click.echo(f"  limits:  {r['limits']}")
            click.echo(f"  privacy: {r['privacy']}")
    else:
        click.echo(
            "\nOff-by-default providers run only with --include. Use -l for legal basis, limits, privacy."
        )


@main.command("stats", context_settings=CONTEXT_SETTINGS)
@click.option("-c", "--category", type=click.Choice(CATEGORIES))
@click.option("-e", "--events", type=int, default=0, help="Also show the last N attempts.")
@click.option("-j", "--json", "as_json", is_flag=True)
@click.option("--reset", is_flag=True, help="Delete stats, cooldowns and rate-limit state.")
@click.option("--clear-cooldowns", is_flag=True, help="Lift all cooldowns (or only PROVIDER's).")
@click.argument("provider", required=False)
def stats_cmd(
    category: str | None, events: int, as_json: bool, reset: bool, clear_cooldowns: bool, provider: str | None
) -> None:
    """Per-provider performance stats kept locally, with active cooldowns."""
    store = Store(data_dir() / "web4free.db")
    health = Health(store)
    try:
        if reset:
            health.reset(provider)
            click.echo(f"reset {'stats for ' + provider if provider else 'all stats'}")
            return
        if clear_cooldowns:
            n = health.clear_cooldowns(provider)
            click.echo(f"cleared {n} cooldown(s)")
            return
        rows = [
            r
            for r in health.rows(category)
            if not provider or r["target"] == provider or r["target"].startswith(provider + ":")
        ]
        cooldowns = health.active_cooldowns()
        recent = health.events(events, provider) if events else []
        if as_json:
            click.echo(json.dumps({"providers": rows, "cooldowns": cooldowns, "events": recent}, indent=2))
            return
        if not rows:
            click.echo("no stats yet; run a search or `w4f probe`")
        else:
            out = []
            for r in rows:
                tried = r["attempts"]
                fails = tried - r["ok"] - r["empty"]
                out.append(
                    [
                        r["target"],
                        r["category"],
                        r["route"],
                        tried,
                        f"{100 * r['ok'] / tried:.0f}%" if tried else "-",
                        fails,
                        f"{r['latency_ewma']:.1f}s" if r["latency_ewma"] is not None else "-",
                        f"{r['success_ewma']:.2f}" if r["success_ewma"] is not None else "-",
                        r["last_outcome"] or "-",
                        _ago(r["last_ok"]),
                    ]
                )
            click.echo(
                table(
                    out,
                    [
                        "provider",
                        "category",
                        "route",
                        "tries",
                        "ok",
                        "fails",
                        "latency",
                        "health",
                        "last",
                        "last ok",
                    ],
                )
            )
        if cooldowns:
            click.echo("\nactive cooldowns:")
            click.echo(
                table(
                    [
                        [c["key"], c["kind"], _left(c["until"]), c["strikes"], (c["reason"] or "")[:60]]
                        for c in cooldowns
                    ],
                    ["scope", "kind", "left", "strikes", "reason"],
                )
            )
        if recent:
            click.echo(f"\nlast {len(recent)} attempts:")
            click.echo(
                table(
                    [
                        [
                            time.strftime("%m-%d %H:%M:%S", time.localtime(e["ts"])),
                            e["target"],
                            e["category"],
                            e["outcome"],
                            e["n"] or "",
                            f"{e['latency']:.1f}s" if e["latency"] else "",
                            (e["detail"] or "")[:60],
                        ]
                        for e in recent
                    ],
                    ["time", "provider", "category", "outcome", "n", "latency", "detail"],
                )
            )
    finally:
        store.close()


@main.command("probe", context_settings=CONTEXT_SETTINGS)
@click.option("-c", "--category", type=click.Choice(CATEGORIES), default="text", show_default=True)
@click.option("-q", "--query", default=None, help="Test query [category default].")
@click.option("-i", "--include", multiple=True)
@click.option("-x", "--exclude", multiple=True)
@click.option("--all", "all_", is_flag=True, help="Include off-by-default providers.")
@click.option("--force", is_flag=True, help="Probe providers that are cooling down too.")
@click.option("--proxy")
@click.option("--tor", is_flag=True)
@click.option("--max-wait", type=float, default=25.0, show_default=True)
def probe_cmd(
    category: str,
    query: str | None,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    all_: bool,
    force: bool,
    proxy: str | None,
    tor: bool,
    max_wait: float,
) -> None:
    """Send one test search to each provider and record the outcome (respects rate limits)."""
    defaults = {
        "text": "python asyncio tutorial",
        "news": "climate",
        "images": "red panda",
        "videos": "how to tie a tie",
    }
    cfg = _load_config()
    ctx, store = _open(cfg, proxy, tor, None, max_wait)
    try:
        targets = build_targets(ctx)
        engine = Engine(ctx, targets)
        inc = _split(include)
        if all_ and not inc:
            inc = sorted({t.name for t in targets if t.supports(category)})
        q = Query(text=query or defaults[category], category=category, max_results=5)
        try:
            attempts = engine.probe(q, inc, _split(exclude), force)
        except SelectionError as ex:
            raise click.UsageError(str(ex)) from ex
    finally:
        store.close()
    click.echo(attempts_summary(attempts, sys.stdout.isatty()))


# --- searxng ---------------------------------------------------------------------------


@main.group("searxng", context_settings=CONTEXT_SETTINGS)
def searxng_group() -> None:
    """Manage SearXNG instances (yours and vetted public ones)."""


@searxng_group.command("list")
@click.option("-a", "--all", "all_", is_flag=True, help="Also list public instances that failed vetting.")
def searxng_list(all_: bool) -> None:
    """Show instances, vetting result and format=json support."""
    cfg = _load_config()
    store = Store(data_dir() / "web4free.db")
    try:
        d = Directory(store, cfg)
        memo = d.json_memo()
        usable = {u: src for u, src in d.usable()}
        rows = [[u, "custom", "trusted", "-", "yes"] for u in d.custom()]
        from web4free.providers.searxng import SEED_INSTANCES, host_of

        listed = d.vetted() or [(u, "ok") for u in SEED_INSTANCES]
        for url, reason in sorted(listed):
            if reason != "ok" and not all_:
                continue
            m = memo.get(host_of(url))
            js = "-" if m is None else ("yes" if m.get("json") else f"no ({m.get('status')})")
            rows.append([url, "public", reason, js, usable.get(url, "no") if url in usable else "no"])
        age = store.kv_age("searxng:space")
    finally:
        store.close()
    click.echo(table(rows, ["instance", "source", "vetting", "json", "used"]))
    click.echo(
        f"\ninstance list age: {_ago(time.time() - age) if age is not None else 'never fetched (seed list in use)'}"
        "; refresh with `w4f searxng refresh`"
    )


@searxng_group.command("refresh")
@click.option("--no-probe", is_flag=True, help="Only fetch the list; do not test format=json.")
def searxng_refresh(no_probe: bool) -> None:
    """Fetch searx.space data, vet instances, and test format=json on vetted ones (one request each)."""
    cfg = _load_config()
    ctx, store = _open(cfg, None, False, None, None)
    try:
        d = Directory(store, cfg)
        try:
            n = d.refresh_listing(ctx)
        except Exception as ex:  # noqa: BLE001
            raise click.ClickException(f"could not fetch searx.space data: {ex}") from ex
        vetted = [u for u, reason in d.vetted() if reason == "ok"]
        click.echo(f"{n} instances listed, {len(vetted)} pass vetting")
        if no_probe or not vetted:
            return
        results = d.probe(ctx, vetted)
        ok = [u for u, (good, _) in results.items() if good]
        click.echo(f"{len(ok)} serve format=json: {', '.join(ok) or 'none'}")
    finally:
        store.close()


# --- config -----------------------------------------------------------------------------


@main.group("config", context_settings=CONTEXT_SETTINGS)
def config_group() -> None:
    """Show or create the config file."""


@config_group.command("path")
def config_path_cmd() -> None:
    """Print config and data locations."""
    click.echo(f"config: {config_path()}")
    click.echo(f"data:   {data_dir()}")


@config_group.command("show")
def config_show() -> None:
    """Print the effective configuration (defaults merged with your file)."""
    click.echo(json.dumps(_load_config(), indent=2))


@config_group.command("init")
@click.option("--force", is_flag=True, help="Overwrite an existing file.")
def config_init(force: bool) -> None:
    """Write a commented config file with all defaults."""
    try:
        path = write_default_config(force=force)
    except ConfigError as ex:
        raise click.ClickException(str(ex)) from ex
    click.echo(f"wrote {path}")


__all__ = ["BY_NAME", "PROVIDER_CLASSES", "main"]
