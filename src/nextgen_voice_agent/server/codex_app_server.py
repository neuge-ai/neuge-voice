import asyncio
import logging
from typing import Any

from nextgen_voice_agent.runtimes.codex_runtime import CodexAppServerClient

logger = logging.getLogger(__name__)

_app_server_client: CodexAppServerClient | None = None


def _get_app_server_client() -> CodexAppServerClient:
    global _app_server_client
    if _app_server_client is None:
        _app_server_client = CodexAppServerClient()
    return _app_server_client


async def _run_async_command(*args: str) -> str:
    """Helper to run a command asynchronously and return stdout."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error_msg = stderr.decode().strip()
        logger.error(f"Command failed: {' '.join(args)} - {error_msg}")
        raise RuntimeError(f"CLI Error: {error_msg}")
        
    return stdout.decode().strip()

async def get_mcp_tools() -> list:
    """List MCP servers/tools via the persistent Codex app-server."""
    response = await _get_app_server_client().request(
        "mcpServerStatus/list",
        {"detail": "toolsAndAuthOnly"},
    )
    return _normalize_mcp_status_response(response)


async def add_mcp_tool(name: str, command: str, args: list[str]) -> bool:
    """Executes `codex mcp add {name} {command} {args}`."""
    cli_args = ["codex", "mcp", "add", name, command] + args
    await _run_async_command(*cli_args)
    return True

async def remove_mcp_tool(name: str) -> bool:
    """Executes `codex mcp remove {name}`."""
    await _run_async_command("codex", "mcp", "remove", name)
    return True


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
