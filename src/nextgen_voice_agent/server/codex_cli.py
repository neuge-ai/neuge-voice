import asyncio
import json
import logging

logger = logging.getLogger(__name__)

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
    """Executes `codex mcp list --json` and parses the JSON output."""
    output = await _run_async_command("codex", "mcp", "list", "--json")
    if not output:
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse MCP tools JSON: {e}")
        return []

async def add_mcp_tool(name: str, command: str, args: list[str]) -> bool:
    """Executes `codex mcp add {name} {command} {args}`."""
    cli_args = ["codex", "mcp", "add", name, command] + args
    await _run_async_command(*cli_args)
    return True

async def remove_mcp_tool(name: str) -> bool:
    """Executes `codex mcp remove {name}`."""
    await _run_async_command("codex", "mcp", "remove", name)
    return True
