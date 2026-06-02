from __future__ import annotations

import pytest

from nextgen_voice_agent.launcher.managed_backend import (
    ManagedBackendStartupError,
    parse_port_line,
    parse_url_line,
    wait_for_backend_health,
)


def test_parse_port_line() -> None:
    assert parse_port_line("PORT:8123") == 8123
    assert parse_port_line("  PORT:42  ") == 42
    assert parse_port_line("URL:http://127.0.0.1:42/#/") is None


def test_parse_url_line() -> None:
    assert parse_url_line("URL:http://127.0.0.1:8000/#/") == "http://127.0.0.1:8000/#/"


def test_wait_for_backend_health_times_out() -> None:
    with pytest.raises(ManagedBackendStartupError):
        wait_for_backend_health("127.0.0.1", 59999, timeout_ms=300, poll_interval_ms=50)
