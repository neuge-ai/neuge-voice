from __future__ import annotations

"""Optional OpenAI Realtime API integration.

The current integrated voice path runs through voice/orchestrator.py and
voice/llm_router.py. This module only builds session/tool config for a direct
OpenAI Realtime client path for future integration.
"""

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from nextgen_voice_agent.config import Settings, get_secret
from nextgen_voice_agent.models.realtime import RealtimeSessionConfig
from nextgen_voice_agent.voice.llm_router import SANITY_CHECK_INSTRUCTIONS
from nextgen_voice_agent.voice.tool_catalog import build_realtime_tools


OPENAI_REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"


def build_realtime_session_config(settings: Settings) -> RealtimeSessionConfig:
    return RealtimeSessionConfig(
        model=settings.openai_realtime_model,
        voice=settings.openai_realtime_voice,
        instructions=(
            "You are the realtime voice shell for a local Codex-powered task agent. "
            "Answer simple conversational questions directly. For nontrivial research, "
            "coding, analysis, private-data, or multi-step tasks, call the task tools. "
            "For timers, activity tracking, elapsed-time checks, and cancellation/status checks, "
            "call native deterministic tools instead of Codex. "
            "Only one Codex task may run at a time. If a Codex task is already active, "
            "do not start another complex task. For related follow-ups to the active task, call "
            "`amend_codex_task` with only the new instruction or correction; the Codex app-server keeps "
            "the same thread context. For clearly unrelated new complex work, ask whether to cancel/switch "
            "or keep the current task running. "
            "Native tools and simple conversation remain available while Codex runs. "
            "Keep speech concise, stay interruptible, and never claim a Codex task is "
            "finished until read_codex_result returns a completed result.\n\n"
            f"{SANITY_CHECK_INSTRUCTIONS}"
        ),
        tools=build_realtime_tools(),
    )


def build_openai_client_secret_payload(config: RealtimeSessionConfig) -> dict[str, Any]:
    return {
        "session": {
            "type": "realtime",
            "model": config.model,
            "instructions": config.instructions,
            "audio": {
                "output": {
                    "voice": config.voice,
                },
            },
            "tools": [tool.model_dump(mode="json") for tool in config.tools],
        }
    }


async def create_openai_realtime_client_secret(settings: Settings) -> dict[str, Any]:
    api_key = get_secret("openai_api_key")
    if api_key is None:
        raise RuntimeError("NVA_OPENAI_API_KEY is required to mint a Realtime client secret.")

    config = build_realtime_session_config(settings)
    payload = json.dumps(build_openai_client_secret_payload(config)).encode("utf-8")

    def request_client_secret() -> dict[str, Any]:
        request = urllib.request.Request(
            OPENAI_REALTIME_CLIENT_SECRETS_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI rejected Realtime client secret request: {detail}") from exc

    return await asyncio.to_thread(request_client_secret)
