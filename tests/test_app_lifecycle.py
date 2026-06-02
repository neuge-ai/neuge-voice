from __future__ import annotations

import pytest

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.server.app import _lifespan, create_app
from nextgen_voice_agent.server.codex_app_server import CodexAppServerService


class RecordingShutdown:
    def __init__(self, name: str, calls: list[str]) -> None:
        self._name = name
        self._calls = calls

    async def shutdown(self) -> None:
        self._calls.append(self._name)


class RecordingRuntime:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def shutdown(self) -> None:
        self._calls.append("controller_runtime")


@pytest.mark.asyncio
async def test_lifespan_shutdown_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        "nextgen_voice_agent.server.app.WebRTCSessionManager",
        lambda orchestrator: RecordingShutdown("webrtc", calls),
    )
    monkeypatch.setattr(
        "nextgen_voice_agent.server.app.CodexAppServerService",
        lambda: RecordingShutdown("codex_app_server", calls),
    )

    app = create_app(Settings(backend_run_mode="test", ui_serving_mode="none"))
    app.state.controller.runtime = RecordingRuntime(calls)

    async with _lifespan(app):
        pass

    assert calls == ["webrtc", "codex_app_server", "controller_runtime"]


@pytest.mark.asyncio
async def test_create_app_uses_independent_codex_services() -> None:
    app_a = create_app(Settings(backend_run_mode="test", ui_serving_mode="none"))
    app_b = create_app(Settings(backend_run_mode="test", ui_serving_mode="none"))
    assert app_a.state.codex_app_server_service is not app_b.state.codex_app_server_service
    assert isinstance(app_a.state.codex_app_server_service, CodexAppServerService)
