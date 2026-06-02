from __future__ import annotations

import asyncio
import logging
from typing import Any

from nextgen_voice_agent.runtimes.codex_runtime import CodexAppServerClient

logger = logging.getLogger(__name__)


def _normalize_mcp_status_response(response: Any) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    servers = response.get("servers") or response.get("items") or response.get("mcpServers") or []
    if not isinstance(servers, list):
        return []

    normalized: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        name = server.get("name") or server.get("id") or server.get("serverName")
        if not isinstance(name, str):
            continue
        entry = dict(server)
        entry["name"] = name
        if "status" not in entry:
            entry["status"] = server.get("startupStatus") or server.get("state") or "unknown"
        normalized.append(entry)
    return normalized


class CodexAppServerService:
    """App-owned Codex app-server and MCP CLI operations."""

    def __init__(self, client: CodexAppServerClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> CodexAppServerClient:
        if self._client is None:
            self._client = CodexAppServerClient()
        return self._client

    async def _run_async_command(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error("Command failed: %s - %s", " ".join(args), error_msg)
            raise RuntimeError(f"CLI Error: {error_msg}")

        return stdout.decode().strip()

    async def get_mcp_tools(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.request(
            "mcpServerStatus/list",
            {"detail": "toolsAndAuthOnly"},
        )
        return _normalize_mcp_status_response(response)

    async def add_mcp_tool(self, name: str, command: str, args: list[str]) -> bool:
        cli_args = ["codex", "mcp", "add", name, command] + args
        await self._run_async_command(*cli_args)
        return True

    async def remove_mcp_tool(self, name: str) -> bool:
        await self._run_async_command("codex", "mcp", "remove", name)
        return True

    async def shutdown(self) -> None:
        if self._client is None:
            return
        await self._client.stop()
        if self._owns_client:
            self._client = None
