"""Minimal client for remote MCP servers over streamable HTTP (JSON-RPC 2.0).

Only what keyless search needs: initialize, notifications/initialized, tools/call.
Sessions are cached in the store so repeated CLI runs skip the handshake.
"""

from __future__ import annotations

import json
import time
from typing import Any

from web4free import __version__
from web4free.http import Http, Response, check
from web4free.models import Outcome, ProviderError
from web4free.store import Store

PROTOCOL = "2025-06-18"
SESSION_TTL = 15 * 60
RATE_WORDS = ("rate limit", "rate-limit", "too many requests", "quota", "429")


class MCPClient:
    def __init__(self, url: str, http: Http, store: Store, cache_key: str) -> None:
        self.url = url
        self.http = http
        self.store = store
        self.cache_key = f"mcp_session:{cache_key}"
        self._id = 0

    def _headers(self, session: str | None) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        }
        if session:
            h["Mcp-Session-Id"] = session
        return h

    def _post(self, body: dict[str, Any], session: str | None, timeout: float) -> Response:
        return self.http.post(
            self.url, headers=self._headers(session), content=json.dumps(body).encode(), timeout=timeout
        )

    @staticmethod
    def _messages(resp: Response) -> list[dict[str, Any]]:
        text = resp.text.strip()
        if not text:
            return []
        if text.startswith("{") or text.startswith("["):
            data = resp.json()
            return data if isinstance(data, list) else [data]
        msgs = []
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        msgs.append(json.loads(payload))
                    except ValueError:
                        continue
        return msgs

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _session(self, timeout: float, fresh: bool = False) -> str | None:
        if not fresh:
            cached = self.store.kv_get(self.cache_key, max_age=SESSION_TTL)
            if cached:
                return str(cached)
        init = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "web4free", "version": __version__},
            },
        }
        resp = check(self._post(init, None, timeout))
        msgs = self._messages(resp)
        if msgs and "error" in msgs[0]:
            raise ProviderError(Outcome.CONFIG, f"MCP initialize failed: {msgs[0]['error']}")
        session = resp.header("mcp-session-id")
        check(
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session, timeout),
            (200, 202, 204),
        )
        if session:
            self.store.kv_set(self.cache_key, session)
        return session

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float) -> str:
        """Call a tool and return the text of its first content item."""
        started = time.time()
        session = self._session(timeout)
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        resp = self._post(body, session, timeout)
        if resp.status in (400, 404) and session:
            # session expired on the server: start a new one once
            self.store.kv_delete(self.cache_key)
            left = max(1.0, timeout - (time.time() - started))
            session = self._session(left, fresh=True)
            resp = self._post(body, session, left)
        check(resp)
        for msg in self._messages(resp):
            if msg.get("id") != body["id"]:
                continue
            if "error" in msg:
                err = msg["error"]
                text = f"{err.get('code')}: {err.get('message')}"
                kind = Outcome.RATE_LIMITED if any(w in text.lower() for w in RATE_WORDS) else Outcome.SERVER
                raise ProviderError(kind, text[:200])
            result = msg.get("result") or {}
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type", "text") == "text"]
            text = "\n".join(t for t in texts if t)
            if result.get("isError"):
                low = text.lower()
                if any(w in low for w in RATE_WORDS):
                    raise ProviderError(Outcome.RATE_LIMITED, text[:200])
                if "auth" in low or "api key" in low or "sign in" in low:
                    raise ProviderError(Outcome.BLOCKED, text[:200])
                raise ProviderError(Outcome.SERVER, text[:200] or "tool error")
            return text
        raise ProviderError(Outcome.PARSE, "no JSON-RPC response for tools/call")
