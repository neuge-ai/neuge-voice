from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.server.app import create_app
from nextgen_voice_agent.server.codex_app_server import CodexAppServerService


class FakeCodexAppServerService(CodexAppServerService):
    def __init__(self, label: str) -> None:
        super().__init__(client=None)
        self.label = label
        self.list_calls = 0
        self.add_calls: list[str] = []
        self.remove_calls: list[str] = []

    async def get_mcp_tools(self) -> list[dict[str, str]]:
        self.list_calls += 1
        return [{"name": self.label, "status": "active"}]

    async def add_mcp_tool(self, name: str, command: str, args: list[str]) -> bool:
        if name == "fail_tool":
            raise RuntimeError("CLI Error: Mocked failure")
        self.add_calls.append(name)
        return True

    async def remove_mcp_tool(self, name: str) -> bool:
        if name == "fail_tool":
            raise RuntimeError("CLI Error: Mocked failure")
        self.remove_calls.append(name)
        return True

    async def shutdown(self) -> None:
        return None


def _app_with_service(service: FakeCodexAppServerService):
    app = create_app(Settings(backend_run_mode="test", ui_serving_mode="none"))
    app.state.codex_app_server_service = service
    return app


@pytest.mark.asyncio
async def test_fetch_mcp_tools() -> None:
    service = FakeCodexAppServerService("app_a")
    app = _app_with_service(service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/codex/mcp-tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["servers"][0]["name"] == "app_a"
    assert service.list_calls == 1


@pytest.mark.asyncio
async def test_create_mcp_tool_success() -> None:
    service = FakeCodexAppServerService("app_a")
    app = _app_with_service(service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/mcp-tools",
            json={"name": "brave-search", "command": "npx", "args": ["-y", "brave-mcp"]},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert service.add_calls == ["brave-search"]


@pytest.mark.asyncio
async def test_create_mcp_tool_failure() -> None:
    service = FakeCodexAppServerService("app_a")
    app = _app_with_service(service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/mcp-tools",
            json={"name": "fail_tool", "command": "npx", "args": []},
        )

    assert response.status_code == 400
    assert "Mocked failure" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_mcp_tool_success() -> None:
    service = FakeCodexAppServerService("app_a")
    app = _app_with_service(service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/codex/mcp-tools/github")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert service.remove_calls == ["github"]


@pytest.mark.asyncio
async def test_delete_mcp_tool_failure() -> None:
    service = FakeCodexAppServerService("app_a")
    app = _app_with_service(service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/codex/mcp-tools/fail_tool")

    assert response.status_code == 400
    assert "Mocked failure" in response.json()["detail"]


@pytest.mark.asyncio
async def test_separate_apps_do_not_share_mcp_service_state() -> None:
    service_a = FakeCodexAppServerService("app_a")
    service_b = FakeCodexAppServerService("app_b")
    app_a = _app_with_service(service_a)
    app_b = _app_with_service(service_b)

    transport_a = ASGITransport(app=app_a)
    transport_b = ASGITransport(app=app_b)
    async with AsyncClient(transport=transport_a, base_url="http://test") as client_a:
        response_a = await client_a.get("/api/codex/mcp-tools")
    async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        response_b = await client_b.get("/api/codex/mcp-tools")

    assert response_a.json()["servers"][0]["name"] == "app_a"
    assert response_b.json()["servers"][0]["name"] == "app_b"
    assert service_a.list_calls == 1
    assert service_b.list_calls == 1
