"""Shared managed-backend launcher helpers.

Protocol source of truth: docs/managed-backend-launcher.md
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request

PORT_LINE_RE = re.compile(r"^PORT:(\d+)\s*$")
URL_LINE_RE = re.compile(r"^URL:(\S+)\s*$")
DEFAULT_READINESS_TIMEOUT_MS = 15_000
DEFAULT_POLL_INTERVAL_MS = 200


class ManagedBackendStartupError(RuntimeError):
    pass


def parse_port_line(line: str) -> int | None:
    match = PORT_LINE_RE.match(line.strip())
    if not match:
        return None
    return int(match.group(1))


def parse_url_line(line: str) -> str | None:
    match = URL_LINE_RE.match(line.strip())
    if not match:
        return None
    return match.group(1)


def wait_for_backend_health(
    host: str,
    port: int,
    *,
    timeout_ms: int = DEFAULT_READINESS_TIMEOUT_MS,
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    url = f"http://{host}:{port}/health"
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
                last_error = f"unexpected status {response.status}"
        except urllib.error.URLError as exc:
            last_error = str(exc)
        time.sleep(poll_interval_ms / 1000.0)
    raise ManagedBackendStartupError(
        f"Backend at {url} did not become healthy within {timeout_ms}ms. Last error: {last_error}"
    )
