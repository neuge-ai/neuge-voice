from __future__ import annotations

import importlib
import sys

import pytest

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.server.runtime_mode import UiServingMode


def test_importing_app_module_does_not_construct_providers(monkeypatch) -> None:
    calls: list[str] = []

    def _track(name: str):
        def _factory(*args, **kwargs):
            calls.append(name)
            raise RuntimeError("provider construction should not run on import")

        return _factory

    monkeypatch.setattr(
        "nextgen_voice_agent.server.dependencies.create_controller",
        _track("controller"),
    )
    monkeypatch.setattr("nextgen_voice_agent.server.dependencies.create_stt", _track("stt"))
    monkeypatch.setattr("nextgen_voice_agent.server.dependencies.create_tts", _track("tts"))

    module_name = "nextgen_voice_agent.server.app"
    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)

    assert calls == []
    sys.modules.pop(module_name, None)
    importlib.import_module("nextgen_voice_agent.server.dependencies")


@pytest.mark.asyncio
async def test_create_app_with_test_mode_skips_frontend_mount() -> None:
    from httpx import ASGITransport, AsyncClient

    sys.modules.pop("nextgen_voice_agent.server.app", None)
    from nextgen_voice_agent.server.app import create_app

    app = create_app(Settings(backend_run_mode="test", ui_serving_mode="none"))
    assert app.state.runtime_policy.ui_serving_mode == UiServingMode.NONE

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_webrtc_before_runtime() -> None:
    from nextgen_voice_agent.server.app import create_app

    calls: list[str] = []

    class FakeWebRtcManager:
        async def shutdown(self) -> None:
            calls.append("webrtc")

    class FakeRuntime:
        async def shutdown(self) -> None:
            calls.append("runtime")

    app = create_app(Settings(backend_run_mode="test", ui_serving_mode="none"))
    app.state.webrtc_session_manager = FakeWebRtcManager()
    app.state.controller.runtime = FakeRuntime()

    async with app.router.lifespan_context(app):
        pass

    assert calls == ["webrtc", "runtime"]
