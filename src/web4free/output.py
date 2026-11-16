"""Output formats: text (terminal), json, jsonl, csv, md."""

from __future__ import annotations

import csv
import io
import json
import shutil
import textwrap
from typing import Any

import click

from web4free.engine import Attempt, SearchResponse
from web4free.models import FIELDS, Result

FORMATS = ("text", "json", "jsonl", "csv", "md")


def format_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "json": "json",
        "jsonl": "jsonl",
        "ndjson": "jsonl",
        "csv": "csv",
        "md": "md",
        "markdown": "md",
    }.get(ext, "text")


def render(resp: SearchResponse, fmt: str, color: bool = False) -> str:
    if fmt == "json":
        return json.dumps(resp.to_dict(), ensure_ascii=False, indent=2)
    if fmt == "jsonl":
        return "\n".join(json.dumps(r.to_dict(resp.query.category), ensure_ascii=False) for r in resp.results)
    if fmt == "csv":
        return _csv(resp)
    if fmt == "md":
        return _markdown(resp)
    return _text(resp, color)


def _style(s: str, color: bool, **kw: Any) -> str:
    return click.style(s, **kw) if color else s


def _date(d: str | None) -> str:
    return (d or "")[:10]


def _text(resp: SearchResponse, color: bool) -> str:
    width = min(110, shutil.get_terminal_size((100, 20)).columns)
    cat = resp.query.category
    blocks = []
    for i, r in enumerate(resp.results, 1):
        num = f"{i:>2}. "
        pad = " " * len(num)
        lines = []
        title = r.title or "(no title)"
        if cat == "images" and r.width and r.height:
            title += f"  ({r.width}x{r.height})"
        if cat == "videos" and r.duration:
            title += f"  [{r.duration}]"
        lines.append(num + _style(title, color, bold=True))
        if cat == "images":
            lines.append(pad + "image: " + _style(r.image or "", color, fg="cyan"))
            if r.url:
                lines.append(pad + "page:  " + _style(r.url, color, fg="cyan"))
        else:
            lines.append(pad + _style(r.url, color, fg="cyan"))
        if r.snippet:
            for line in textwrap.wrap(r.snippet, width=max(40, width - len(pad))):
                lines.append(pad + line)
        meta = [x for x in (_date(r.date), r.source if cat != "images" else r.source, r.publisher) if x]
        meta.append("via " + ", ".join(r.providers))
        lines.append(pad + _style(" · ".join(meta), color, dim=True))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _csv(resp: SearchResponse) -> str:
    cat = resp.query.category
    cols = [*FIELDS[cat], "providers"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in resp.results:
        row = r.to_dict(cat)
        row["providers"] = " ".join(r.providers)
        w.writerow(row)
    return buf.getvalue().rstrip("\n")


def _md_escape(s: str) -> str:
    return s.replace("[", "\\[").replace("]", "\\]")


def _markdown(resp: SearchResponse) -> str:
    cat = resp.query.category
    out = [f"# {resp.query.text}", ""]
    for i, r in enumerate(resp.results, 1):
        if cat == "images":
            out.append(f"{i}. [{_md_escape(r.title)}]({r.url or r.image}) - ![]({r.thumbnail or r.image})")
            continue
        out.append(f"{i}. [{_md_escape(r.title)}]({r.url})")
        if r.snippet:
            out.append(f"   {r.snippet}")
        meta = " · ".join(x for x in (_date(r.date), r.source, r.duration) if x)
        if meta:
            out.append(f"   _{meta}_")
    return "\n".join(out)


def attempts_summary(attempts: list[Attempt], color: bool = False) -> str:
    lines = []
    for a in attempts:
        mark = {"ok": "+", "skipped": " "}.get(a.outcome, "-")
        bits = [f"{mark} {a.target:<28} {a.outcome:<14}"]
        if a.n:
            bits.append(f"{a.n:>3} results")
        if a.latency:
            bits.append(f"{a.latency:5.1f}s")
        if a.detail:
            bits.append(a.detail[:90])
        fg = "green" if a.outcome == "ok" else ("bright_black" if a.outcome == "skipped" else "yellow")
        lines.append(_style("  ".join(bits), color, fg=fg))
    return "\n".join(lines)


def table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows, strict=False)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*header), fmt.format(*["-" * w for w in widths])]
    out += [fmt.format(*[str(x) for x in row]) for row in rows]
    return "\n".join(out)


def result_count_line(resp: SearchResponse) -> str:
    used = sorted({p for r in resp.results for p in r.providers})
    return f"{len(resp.results)} results in {resp.elapsed:.1f}s via {', '.join(used) or 'none'}"


__all__ = [
    "FORMATS",
    "Result",
    "attempts_summary",
    "format_from_path",
    "render",
    "result_count_line",
    "table",
]
