import pytest
from httpx import ASGITransport, AsyncClient

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.server.app import create_app
from nextgen_voice_agent.voice.tts import (
    BrowserDevTtsProvider,
    NvidiaMagpieTtsProvider,
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


def test_create_tts_provider_uses_nvidia_magpie_when_configured() -> None:
    provider = create_tts_provider(Settings(tts_provider="nvidia_magpie"))

    assert isinstance(provider, NvidiaMagpieTtsProvider)
    assert provider.name == "nvidia_magpie"


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
