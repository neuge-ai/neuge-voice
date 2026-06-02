from __future__ import annotations

from nextgen_voice_agent.agent.controller import AgentController, TaskConflictError
from nextgen_voice_agent.models.task import AmendTaskRequest, CancelTaskRequest, StartTaskRequest
from nextgen_voice_agent.models.voice import VoiceTransportKind
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator
from nextgen_voice_agent.voice.tool_catalog import REALTIME_CODEX_TOOL_NAMES, realtime_native_tool_names

REALTIME_DEFAULT_SESSION_ID = "realtime-default"


async def call_realtime_codex_tool(
    tool_name: str,
    args: dict[str, object],
    controller: AgentController,
) -> dict[str, object]:
    if tool_name == "start_codex_task":
        task_text = str(args["task"])
        try:
            result = await controller.start_task(
                StartTaskRequest(
                    task=task_text,
                    context=args.get("context") if args.get("context") else None,
                    ui_title=str(args["ui_title"]) if args.get("ui_title") else None,
                )
            )
            return {"ok": True, **result.model_dump(mode="json")}
        except TaskConflictError:
            active_task = controller.tasks.get(controller.active_task_id or "")
            return {
                "ok": False,
                "error_code": "codex_task_conflict",
                "recoverable": True,
                "message": "A Codex task is already active.",
                "attempted_task": task_text,
                "active_task": active_task.model_dump(mode="json") if active_task else None,
                "allowed_next_actions": [
                    "amend_active_task",
                    "cancel_active_task",
                    "keep_active_task",
                    "use_native_tool",
                    "answer_directly",
                ],
            }
    if tool_name == "amend_codex_task":
        result = await controller.amend_task(
            str(args["task_id"]),
            AmendTaskRequest(amendment=str(args["amendment"])),
        )
        return result.model_dump(mode="json")
    if tool_name == "cancel_codex_task":
        result = await controller.cancel_task(
            str(args["task_id"]),
            CancelTaskRequest(reason=str(args.get("reason") or "Cancelled from Realtime tool.")),
        )
        return result.model_dump(mode="json")
    if tool_name == "get_codex_task_status":
        return controller.get_status(str(args["task_id"])).model_dump(mode="json")
    if tool_name == "read_codex_result":
        result = controller.read_result(str(args["task_id"]))
        return {"result": result.model_dump(mode="json") if result else None}
    if tool_name == "approve_codex_action":
        result = await controller.approve_action(
            str(args["task_id"]),
            str(args["action_id"]),
            bool(args["approved"]),
        )
        return result.model_dump(mode="json")
    raise ValueError(f"Unsupported Realtime Codex tool: {tool_name}")


async def call_realtime_native_tool(
    tool_name: str,
    args: dict[str, object],
    orchestrator: VoiceSessionOrchestrator,
) -> dict[str, object]:
    normalized_args = {key: value for key, value in args.items() if value is not None}
    return await orchestrator.execute_native_tool(
        REALTIME_DEFAULT_SESSION_ID,
        VoiceTransportKind.BROWSER,
        tool_name,
        normalized_args,
    )


def is_realtime_codex_tool(tool_name: str) -> bool:
    return tool_name in REALTIME_CODEX_TOOL_NAMES


def is_realtime_native_tool(tool_name: str) -> bool:
    return tool_name in realtime_native_tool_names()
