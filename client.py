"""HTTPS client for the Tensorust public-records API (MCP JSON-RPC over HTTPS).

The only network endpoint this plugin talks to is ``MCP_URL``. Every call is a
single ``tools/call`` POST with a hard timeout, and failures raise
:class:`TensorustUnavailable` so callers can fall back to the bundled list
snapshot instead of blocking the agent. Standard library only: no third-party
dependency, nothing to install.

An optional Tensorust key unlocks full rows. It is handed in by the plugin's
``register()`` through a getter that reads Hermes's secret scope; this module
never touches the environment or any file.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

MCP_URL = "https://shop.tensorust.com/api/mcp"
USER_AGENT = "hermes-counterparty/0.1.0 (+https://github.com/mattbusel/hermes-counterparty)"

# One result per (tool, arguments) is remembered for this long. The lists move
# on a cadence of days, so re-asking about the same vendor inside a session is waste.
CACHE_TTL_SECONDS = 600
_MAX_RESPONSE_BYTES = 2_000_000


class TensorustUnavailable(RuntimeError):
    """The API did not answer in time, or answered with something unusable."""


class TensorustClient:
    def __init__(self, timeout: float = 6.0, api_key_getter: Optional[Callable[[], Optional[str]]] = None,
                 url: str = MCP_URL, opener: Optional[Callable[..., Any]] = None):
        self.timeout = max(1.0, float(timeout))
        self._api_key_getter = api_key_getter
        self.url = url
        self._opener = opener or urllib.request.urlopen
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    # -- public -------------------------------------------------------------------------------------

    def call(self, tool: str, arguments: Optional[dict] = None, *, full: bool = False) -> dict:
        """Run one API tool and return its parsed JSON result. ``full=True`` asks for full rows,
        which only does anything when a Tensorust key is configured."""
        arguments = dict(arguments or {})
        if full and self._api_key():
            arguments["full"] = True
        cache_key = tool + "\x00" + json.dumps(arguments, sort_keys=True, default=str)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
                return hit[1]
        result = self._post(tool, arguments)
        with self._lock:
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[cache_key] = (now, result)
        return result

    def has_key(self) -> bool:
        return bool(self._api_key())

    # -- internals ----------------------------------------------------------------------------------

    def _api_key(self) -> Optional[str]:
        if self._api_key_getter is None:
            return None
        try:
            value = self._api_key_getter()
        except Exception:
            return None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _post(self, tool: str, arguments: dict) -> dict:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                           "params": {"name": tool, "arguments": arguments}}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": USER_AGENT}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise TensorustUnavailable(f"Tensorust API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise TensorustUnavailable(f"Tensorust API unreachable: {exc}") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise TensorustUnavailable("Tensorust API response too large")
        return _parse_envelope(raw.decode("utf-8", errors="replace"))


def _parse_envelope(raw: str) -> dict:
    text = raw.lstrip()
    if text.startswith("event:") or text.startswith("data:"):
        data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise TensorustUnavailable("Tensorust API sent an empty event stream")
        text = data_lines[-1]
    try:
        envelope = json.loads(text)
    except ValueError as exc:
        raise TensorustUnavailable("Tensorust API sent non-JSON") from exc
    if not isinstance(envelope, dict):
        raise TensorustUnavailable("Tensorust API sent an unexpected envelope")
    if envelope.get("error"):
        raise TensorustUnavailable(f"Tensorust API error: {envelope['error']}")
    result = envelope.get("result") or {}
    if result.get("isError"):
        raise TensorustUnavailable("Tensorust API tool error")
    content = result.get("content") or []
    for item in content:
        if isinstance(item, dict) and item.get("type", "text") == "text" and isinstance(item.get("text"), str):
            try:
                parsed = json.loads(item["text"])
            except ValueError:
                return {"text": item["text"]}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
    raise TensorustUnavailable("Tensorust API sent no text content")
