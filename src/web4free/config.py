"""Paths and user configuration (TOML)."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

import platformdirs

APP = "web4free"

DEFAULTS: dict[str, Any] = {
    "search": {
        "max_results": 10,
        "region": "us-en",
        "safesearch": "moderate",
        "strategy": "fallback",  # fallback | fuse
        "fanout": 3,  # providers per wave in fuse mode
        "timeout": 10.0,  # seconds per request
        "deadline": 45.0,  # seconds for the whole search
        "max_wait": 3.0,  # longest wait for a rate-limit slot before moving down the list
        "retries": 1,  # quick retries on timeouts, network and 5xx errors
        "include": [],
        "exclude": [],
        "proxy": "",  # http://..., socks5h://..., or "tor"
        "snippet_length": 400,  # 0 = no limit
    },
    # Per-provider overrides, for example:
    # [providers.brave]
    # enabled = false
    # priority = 50
    # rate = 0.05     # requests per second
    # burst = 1
    "providers": {},
    "searxng": {
        "public": True,  # use vetted public instances from searx.space
        "instances": [],  # your own instances, always trusted, tried first
        "max_per_query": 2,  # instances tried per search
        "min_uptime": 90.0,  # monthly uptime percent for public instances
    },
    "http": {
        # Optional contact text appended to the User-Agent sent to open APIs
        # (Wikimedia asks for one). Leave empty to send none.
        "contact": "",
    },
}

DEFAULT_CONFIG_TOML = """\
# web4free configuration. Every key is optional; defaults are shown.

[search]
# max_results = 10
# region = "us-en"          # <country>-<language>, "wt-wt" for no region
# safesearch = "moderate"   # on | moderate | off
# strategy = "fallback"     # fallback: try providers in order until enough results
#                           # fuse: query several providers at once and merge (RRF)
# fanout = 3                # providers per wave in fuse mode
# timeout = 10.0            # seconds per request
# deadline = 45.0           # seconds for the whole search
# max_wait = 3.0            # longest wait for a rate-limit slot before moving on
# retries = 1               # quick retries on timeout / network / 5xx
# include = []              # e.g. ["exa", "bing", "searxng"]; order sets the try order
# exclude = []              # e.g. ["brave", "@scrape"]
# proxy = ""                # "http://host:port", "socks5h://host:port" or "tor"
# snippet_length = 400      # 0 = no limit

# Per-provider overrides:
# [providers.brave]
# enabled = false
# priority = 50
# rate = 0.05               # requests per second
# burst = 1

[searxng]
# public = true             # use vetted public instances (searx.space data)
# instances = []            # your own instances, e.g. ["https://searx.example.org"]
# max_per_query = 2
# min_uptime = 90.0

[http]
# contact = ""              # appended to the User-Agent sent to open APIs
"""


def _dirs() -> tuple[Path, Path]:
    base = os.environ.get("WEB4FREE_HOME")
    if base:
        p = Path(base).expanduser()
        return p, p
    return Path(platformdirs.user_config_dir(APP)), Path(platformdirs.user_data_dir(APP))


def config_path() -> Path:
    explicit = os.environ.get("WEB4FREE_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return _dirs()[0] / "config.toml"


def data_dir() -> Path:
    d = _dirs()[1]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(Exception):
    pass


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        user = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as ex:
        raise ConfigError(f"cannot read config {path}: {ex}") from ex
    unknown = set(user) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"unknown section(s) in {path}: {', '.join(sorted(unknown))}")
    return _merge(DEFAULTS, user)


def write_default_config(path: Path | None = None, *, force: bool = False) -> Path:
    path = path or config_path()
    if path.exists() and not force:
        raise ConfigError(f"{path} exists; use --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path
