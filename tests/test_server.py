import pytest
from httpx import ASGITransport, AsyncClient

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.server.app import create_app
from nextgen_voice_agent.voice.tts import (
    BrowserDevTtsProvider,
    NvidiaNimTtsProvider,
    TtsProvider,
    TtsProviderError,
    TtsRequest,
    TtsResult,
    create_tts_provider,
)


class FailingTtsProvider(TtsProvider):
    name = "failing_tts"

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        raise TtsProviderError("Provider is unavailable.", provider=self.name, stage="synthesis")


def test_create_tts_provider_uses_nvidia_nim_when_configured() -> None:
    provider = create_tts_provider(Settings(tts_provider="nvidia_nim"))

    assert isinstance(provider, NvidiaNimTtsProvider)
    assert provider.name == "nvidia_nim"


def test_create_tts_provider_uses_browser_dev_when_configured() -> None:
    provider = create_tts_provider(Settings(tts_provider="browser_dev"))

    assert isinstance(provider, BrowserDevTtsProvider)
    assert provider.name == "browser_dev"


def test_create_tts_provider_rejects_unknown_provider() -> None:
    with pytest.raises(RuntimeError, match="Unsupported TTS provider 'unknown'"):
        create_tts_provider(Settings(tts_provider="unknown"))


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_frontend_index_served_when_dist_exists(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    web_dist = tmp_path / "dist"
    assets = web_dist / "assets"
    assets.mkdir(parents=True)
    (web_dist / "index.html").write_text("<html><body>Neuge UI</body></html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setenv("NVA_WEB_DIST", str(web_dist))

    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        index = await client.get("/")
        asset = await client.get("/assets/app.js")
        health = await client.get("/health")

    assert index.status_code == 200
    assert "Neuge UI" in index.text
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert health.status_code == 200


@pytest.mark.asyncio
async def test_task_endpoint_demo_flow() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start = await client.post("/tasks/start", json={"task": "Check demo weather."})

        assert start.status_code == 200
        payload = start.json()
        task_id = payload["task"]["task_id"]
        assert payload["acknowledgement"] == "I'll look into that in the background."

        status = await client.get(f"/tasks/{task_id}/status")
        assert status.status_code == 200
        assert status.json()["task"]["task_id"] == task_id

        cancel = await client.post(f"/tasks/{task_id}/cancel", json={"reason": "Stop that."})
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_realtime_session_config_exposes_task_tools() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/realtime/session-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "gpt-realtime-mini"
    assert "audio" in payload["modalities"]
    assert {tool["name"] for tool in payload["tools"]} >= {
        "start_codex_task",
        "amend_codex_task",
        "cancel_codex_task",
        "get_codex_task_status",
        "read_codex_result",
        "approve_codex_action",
        "start_timer",
        "start_activity",
        "get_background_task_status",
    }
    assert "STATE AND PLAUSIBILITY CHECKS" in payload["instructions"]
    assert "Only one Codex task may run at a time" in payload["instructions"]
    assert "related follow-ups to the active task" in payload["instructions"]
    assert "amend_codex_task" in payload["instructions"]


@pytest.mark.asyncio
async def test_realtime_tool_dispatch_starts_task() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/realtime/tools/start_codex_task",
            json={"arguments": {"task": "Check demo weather.", "context": None}},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["original_request"] == "Check demo weather."
    assert payload["acknowledgement"] == "I'll look into that in the background."


@pytest.mark.asyncio
async def test_realtime_tool_dispatch_returns_structured_conflict_for_second_codex_task() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/realtime/tools/start_codex_task",
            json={"arguments": {"task": "Analyze the repo.", "context": None}},
        )
        second = await client.post(
            "/realtime/tools/start_codex_task",
            json={"arguments": {"task": "Research tomorrow's weather.", "context": None}},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    payload = second.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "codex_task_conflict"
    assert payload["recoverable"] is True
    assert payload["active_task"]["original_request"] == "Analyze the repo."
    assert "amend_active_task" in payload["allowed_next_actions"]


@pytest.mark.asyncio
async def test_realtime_tool_dispatch_starts_native_timer() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/realtime/tools/start_timer",
            json={"arguments": {"duration_ms": 300000, "label": "five-minute run", "reason": None}},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["timer"]["label"] == "five-minute run"
    assert payload["timer"]["remaining_ms"] == 300000


@pytest.mark.asyncio
async def test_realtime_client_secret_requires_server_api_key() -> None:
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/realtime/client-secret")

    assert response.status_code == 503
    assert "NVA_OPENAI_API_KEY" in response.json()["detail"]


@pytest.mark.asyncio
async def test_config_status_includes_config_path() -> None:
    from nextgen_voice_agent.config import CONFIG_FILE

    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/config/status")

    assert response.status_code == 200
    assert response.json()["config_path"] == str(CONFIG_FILE)


@pytest.mark.asyncio
async def test_config_open_returns_success_when_launch_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextgen_voice_agent.config import CONFIG_FILE

    launched: list[str] = []

    def fake_launch(url: str, locate: bool = True) -> int:
        launched.append(url)
        return 0

    monkeypatch.setattr("nextgen_voice_agent.platform.open_file._editor_attempts", lambda path: iter([]))
    monkeypatch.setattr("click.launch", fake_launch)
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/config/open")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "opened": True,
        "path": str(CONFIG_FILE),
        "method": "click.launch",
    }
    assert launched == [str(CONFIG_FILE)]


@pytest.mark.asyncio
async def test_config_open_uses_editor_fallback_before_click_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextgen_voice_agent.config import CONFIG_FILE

    launched: list[str] = []

    def fake_launch(url: str, locate: bool = True) -> int:
        launched.append(url)
        return 0

    def fake_editor_attempts(path: str):
        yield "cursor", ["cursor", "--goto", path]

    monkeypatch.setattr("nextgen_voice_agent.platform.open_file._editor_attempts", fake_editor_attempts)
    monkeypatch.setattr("nextgen_voice_agent.platform.open_file._run", lambda args: 0)
    monkeypatch.setattr("click.launch", fake_launch)
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/config/open")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "opened": True,
        "path": str(CONFIG_FILE),
        "method": "cursor",
    }
    assert launched == []


@pytest.mark.asyncio
async def test_config_open_returns_graceful_failure_when_all_attempts_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextgen_voice_agent.config import CONFIG_FILE

    monkeypatch.setattr("nextgen_voice_agent.platform.open_file._editor_attempts", lambda path: iter([]))
    monkeypatch.setattr("click.launch", lambda url, locate=False: 1)
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/config/open")

    assert response.status_code == 200
    payload = response.json()
    assert payload["opened"] is False
    assert payload["path"] == str(CONFIG_FILE)
    assert payload["error"] == "Could not open config file (exit 1)"


@pytest.mark.asyncio
async def test_config_open_returns_graceful_failure_when_launch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextgen_voice_agent.config import CONFIG_FILE

    def fake_launch(url: str, locate: bool = True) -> int:
        raise RuntimeError("No application is registered to handle toml files")

    monkeypatch.setattr("nextgen_voice_agent.platform.open_file._editor_attempts", lambda path: iter([]))
    monkeypatch.setattr("click.launch", fake_launch)
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/config/open")

    assert response.status_code == 200
    payload = response.json()
    assert payload["opened"] is False
    assert payload["path"] == str(CONFIG_FILE)
    assert payload["error"] == "No application is registered to handle toml files"


@pytest.mark.asyncio
async def test_tts_endpoint_returns_browser_dev_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVA_TTS_PROVIDER", "browser_dev")
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/tts/synthesize", json={"text": "Hello"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "browser_dev"
    assert payload["text"] == "Hello"


@pytest.mark.asyncio
async def test_tts_endpoint_returns_structured_provider_error() -> None:
    app = create_app()
    app.state.tts_provider = FailingTtsProvider()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/tts/synthesize", json={"text": "Hello"})

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "provider": "failing_tts",
        "stage": "synthesis",
        "message": "Provider is unavailable.",
    }
