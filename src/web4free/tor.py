"""Proxy and Tor routing.

Tor needs a Tor client already running on this machine (the tor daemon, SOCKS port
9050, or Tor Browser, port 9150). No pip package ships a maintained Tor client, so
web4free does not start one. Over Tor, DuckDuckGo and Brave use their official
onion services.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from web4free.http import tcp_open

TOR_PORTS = (9050, 9150)


class ProxyError(Exception):
    pass


def find_tor() -> str | None:
    for port in TOR_PORTS:
        if tcp_open("127.0.0.1", port):
            return f"socks5h://127.0.0.1:{port}"
    return None


def resolve_proxy(value: str | None) -> tuple[str | None, str]:
    """Return (proxy_url, route) where route is direct, proxy or tor."""
    if not value:
        return None, "direct"
    v = value.strip()
    if v.lower() in ("tor", "tb"):
        url = find_tor()
        if not url:
            raise ProxyError(
                "no Tor SOCKS proxy found on 127.0.0.1:9050 or :9150. Start a Tor client first "
                "(for example `brew install tor` then `tor`, or open Tor Browser) and retry."
            )
        return url, "tor"
    parts = urlsplit(v)
    if parts.scheme not in ("http", "https", "socks5", "socks5h"):
        raise ProxyError(f"unsupported proxy {v!r}; use http://, https://, socks5:// or socks5h://")
    if parts.scheme == "socks5":
        # resolve names through the proxy too, so DNS does not leak around it
        v = "socks5h://" + v.split("://", 1)[1]
    route = "tor" if parts.hostname in ("127.0.0.1", "localhost") and parts.port in TOR_PORTS else "proxy"
    return v, route
