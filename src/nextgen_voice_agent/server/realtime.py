from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.models.realtime import RealtimeSessionConfig, RealtimeTool


OPENAI_REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"


def build_realtime_session_config(settings: Settings) -> RealtimeSessionConfig:
    return RealtimeSessionConfig(
        model=settings.openai_realtime_model,
        voice=settings.openai_realtime_voice,
        instructions=(
            "You are the realtime voice shell for a local Codex-powered task agent. "
            "Answer simple conversational questions directly. For nontrivial research, "
            "coding, analysis, private-data, or multi-step tasks, call the task tools. "
            "Keep speech concise, stay interruptible, and never claim a Codex task is "
            "finished until read_codex_result returns a completed result."
        ),
        tools=[
            RealtimeTool(
                name="start_codex_task",
                description="Start a background Codex task for nontrivial work.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "task": {"type": "string"},
                        "context": {"type": ["string", "null"]},
                    },
                    "required": ["task", "context"],
                },
            ),
            RealtimeTool(
                name="amend_codex_task",
                description="Apply a user amendment to a running Codex task.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "task_id": {"type": "string"},
                        "amendment": {"type": "string"},
                    },
                    "required": ["task_id", "amendment"],
                },
            ),
            RealtimeTool(
                name="cancel_codex_task",
                description="Cancel a running Codex task.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "task_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["task_id", "reason"],
                },
            ),
            RealtimeTool(
                name="get_codex_task_status",
                description="Read user-visible progress for a Codex task.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            ),
            RealtimeTool(
                name="read_codex_result",
                description="Read the final structured result for a Codex task.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            ),
            RealtimeTool(
                name="approve_codex_action",
                description="Record user approval or denial for a Codex action.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "task_id": {"type": "string"},
                        "action_id": {"type": "string"},
                        "approved": {"type": "boolean"},
                    },
                    "required": ["task_id", "action_id", "approved"],
                },
            ),
        ],
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
    if settings.openai_api_key is None:
        raise RuntimeError("NVA_OPENAI_API_KEY is required to mint a Realtime client secret.")

    config = build_realtime_session_config(settings)
    payload = json.dumps(build_openai_client_secret_payload(config)).encode("utf-8")
    api_key = settings.openai_api_key.get_secret_value()

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
