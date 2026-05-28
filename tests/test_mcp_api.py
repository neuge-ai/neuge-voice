import pytest
from httpx import ASGITransport, AsyncClient

from nextgen_voice_agent.server.app import create_app
from nextgen_voice_agent.server import codex_cli

@pytest.fixture
def mock_codex_cli(monkeypatch):
    """Mocks the codex_cli async functions to prevent actual OS execution during tests."""
    
    async def mock_get_mcp_tools():
        return [
            {"name": "github", "status": "active"},
            {"name": "postgres", "status": "inactive"}
        ]
        
    async def mock_add_mcp_tool(name: str, command: str, args: list[str]):
        if name == "fail_tool":
            raise RuntimeError("CLI Error: Mocked failure")
        return True
        
    async def mock_remove_mcp_tool(name: str):
        if name == "fail_tool":
            raise RuntimeError("CLI Error: Mocked failure")
        return True

    monkeypatch.setattr(codex_cli, "get_mcp_tools", mock_get_mcp_tools)
    monkeypatch.setattr(codex_cli, "add_mcp_tool", mock_add_mcp_tool)
    monkeypatch.setattr(codex_cli, "remove_mcp_tool", mock_remove_mcp_tool)

@pytest.mark.asyncio
async def test_fetch_mcp_tools(mock_codex_cli):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/codex/mcp-tools")
        
    assert response.status_code == 200
    payload = response.json()
    assert "servers" in payload
    assert len(payload["servers"]) == 2
    assert payload["servers"][0]["name"] == "github"

@pytest.mark.asyncio
async def test_create_mcp_tool_success(mock_codex_cli):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/mcp-tools",
            json={"name": "brave-search", "command": "npx", "args": ["-y", "brave-mcp"]}
        )
        
    assert response.status_code == 200
    assert response.json()["success"] is True

@pytest.mark.asyncio
async def test_create_mcp_tool_failure(mock_codex_cli):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/mcp-tools",
            json={"name": "fail_tool", "command": "npx", "args": []}
        )
        
    assert response.status_code == 400
    assert "Mocked failure" in response.json()["detail"]

@pytest.mark.asyncio
async def test_delete_mcp_tool_success(mock_codex_cli):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/codex/mcp-tools/github")
        
    assert response.status_code == 200
    assert response.json()["success"] is True

@pytest.mark.asyncio
async def test_delete_mcp_tool_failure(mock_codex_cli):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/codex/mcp-tools/fail_tool")
        
    assert response.status_code == 400
    assert "Mocked failure" in response.json()["detail"]
