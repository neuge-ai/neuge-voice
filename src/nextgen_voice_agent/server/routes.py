from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nextgen_voice_agent.agent.controller import AgentController, TaskConflictError, TaskNotFoundError
from nextgen_voice_agent.models.runtime import RuntimeResult
from nextgen_voice_agent.models.realtime import RealtimeSessionConfig
from typing import Any
from nextgen_voice_agent.models.task import (
    AmendTaskRequest,
    CancelTaskRequest,
    CancelTaskResponse,
    StartTaskRequest,
    StartTaskResponse,
    TaskStatus,
    TaskStatusResponse,
)
from nextgen_voice_agent.models.voice import VoiceEvent, VoiceTransportKind
from nextgen_voice_agent.config import get_settings
from nextgen_voice_agent.server.dependencies import get_controller, get_tts_provider, get_voice_orchestrator
from nextgen_voice_agent.server.realtime import build_realtime_session_config, create_openai_realtime_client_secret
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator
from nextgen_voice_agent.voice.tts import TtsProvider, TtsProviderError, TtsRequest, TtsResult

from . import codex_cli

router = APIRouter()

class AddMcpToolRequest(BaseModel):
    name: str
    command: str
    args: list[str]


class ApprovalRequest(BaseModel):
    action_id: str
    approved: bool


class RealtimeToolCallRequest(BaseModel):
    arguments: dict[str, object]


class SynthesizeSpeechRequest(BaseModel):
    text: str
    voice: str = "default"
    style: str = "natural"


@router.get("/realtime/session-config", response_model=RealtimeSessionConfig)
async def get_realtime_session_config() -> RealtimeSessionConfig:
    return build_realtime_session_config(get_settings())


@router.post("/tts/synthesize", response_model=TtsResult)
async def synthesize_speech(
    request: SynthesizeSpeechRequest,
    tts_provider: TtsProvider = Depends(get_tts_provider),
) -> TtsResult:
    try:
        return await tts_provider.synthesize(TtsRequest(text=request.text, voice=request.voice, style=request.style))
    except TtsProviderError as exc:
        raise HTTPException(
            status_code=503,
            detail={"provider": exc.provider, "stage": exc.stage, "message": exc.message},
        ) from exc


@router.post("/realtime/client-secret", response_model=dict[str, object])
async def create_realtime_client_secret() -> dict[str, object]:
    try:
        return await create_openai_realtime_client_secret(get_settings())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/realtime/tools/{tool_name}", response_model=dict[str, object])
async def call_realtime_tool(
    tool_name: str,
    request: RealtimeToolCallRequest,
    controller: AgentController = Depends(get_controller),
    orchestrator: VoiceSessionOrchestrator = Depends(get_voice_orchestrator),
) -> dict[str, object]:
    args = request.arguments
    try:
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
                    "allowed_next_actions": ["cancel_active_task", "keep_active_task", "use_native_tool", "answer_directly"],
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
        if tool_name in {
            "start_timer",
            "get_timer_status",
            "list_active_timers",
            "cancel_timer",
            "start_activity",
            "get_activity_status",
            "list_active_activities",
            "end_activity",
            "cancel_activity",
            "list_active_background_tasks",
            "get_background_task_status",
            "cancel_background_task",
        }:
            state = await orchestrator.start_session("realtime-default", VoiceTransportKind.BROWSER)
            normalized_args = {key: value for key, value in args.items() if value is not None}
            return await orchestrator._execute_native_tool(state, tool_name, normalized_args)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"Missing required argument: {exc.args[0]}") from exc
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    raise HTTPException(status_code=404, detail=f"Unknown Realtime tool: {tool_name}")


@router.post("/tasks/start", response_model=StartTaskResponse)
async def start_task(
    request: StartTaskRequest,
    controller: AgentController = Depends(get_controller),
) -> StartTaskResponse:
    try:
        return await controller.start_task(request)
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/amend", response_model=TaskStatusResponse)
async def amend_task(
    task_id: str,
    request: AmendTaskRequest,
    controller: AgentController = Depends(get_controller),
) -> TaskStatusResponse:
    try:
        return await controller.amend_task(task_id, request)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: str,
    request: CancelTaskRequest,
    controller: AgentController = Depends(get_controller),
) -> CancelTaskResponse:
    try:
        return await controller.cancel_task(task_id, request)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    controller: AgentController = Depends(get_controller),
) -> TaskStatusResponse:
    try:
        return controller.get_status(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/result", response_model=RuntimeResult | None)
async def get_task_result(
    task_id: str,
    controller: AgentController = Depends(get_controller),
) -> RuntimeResult | None:
    try:
        return controller.read_result(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/approve", response_model=TaskStatusResponse)
async def approve_action(
    task_id: str,
    request: ApprovalRequest,
    controller: AgentController = Depends(get_controller),
) -> TaskStatusResponse:
    try:
        return await controller.approve_action(task_id, request.action_id, request.approved)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/voice/events", response_model=dict[str, object])
async def receive_voice_event(
    event: VoiceEvent,
    orchestrator: VoiceSessionOrchestrator = Depends(get_voice_orchestrator),
) -> dict[str, object]:
    await orchestrator.handle_voice_event(event.session_id, event)
    outbound = await orchestrator.drain_events(event.session_id)
    return {
        "status": "accepted",
        "event": event.event.value,
        "outbound": [item.model_dump(mode="json") for item in outbound],
    }


@router.get("/voice/sessions/{session_id}/events", response_model=list[VoiceEvent])
async def get_voice_session_events(
    session_id: str,
    orchestrator: VoiceSessionOrchestrator = Depends(get_voice_orchestrator),
) -> list[VoiceEvent]:
    if session_id not in orchestrator.sessions:
        return []
    return await orchestrator.drain_events(session_id)


@router.get("/voice/sessions/{session_id}/transcript", response_model=dict[str, str | None])
async def get_voice_session_transcript(
    session_id: str,
    orchestrator: VoiceSessionOrchestrator = Depends(get_voice_orchestrator),
) -> dict[str, str | None]:
    return await orchestrator.get_transcript(session_id)


class WebRTCOfferRequest(BaseModel):
    sdp: str
    type: str
    session_id: str

@router.get("/realtime/active-tools", response_model=dict[str, object])
async def get_active_tools(
    controller: AgentController = Depends(get_controller),
    orchestrator: VoiceSessionOrchestrator = Depends(get_voice_orchestrator),
) -> dict[str, object]:
    tools = []
    
    for task in controller.tasks.values():
        if task.status in {TaskStatus.RUNNING, TaskStatus.AMENDING, TaskStatus.WAITING_FOR_APPROVAL, TaskStatus.WAITING_FOR_TOOL, TaskStatus.WAITING_FOR_USER_CLARIFICATION}:
            tools.append({
                "id": task.task_id,
                "type": "codex",
                "title": getattr(task, "ui_title", None) or "Background Task",
                "started_at": task.created_at.astimezone().isoformat(),
            })
            
    for session in orchestrator.sessions.values():
        for timer in session.active_timers.values():
            if timer.status == "running":
                tools.append({
                    "id": timer.timer_id,
                    "type": "timer",
                    "title": getattr(timer, "ui_title", None) or "Timer",
                    "started_at": timer.started_at.astimezone().isoformat(),
                })
                
        for activity in session.active_activities.values():
            if activity.status == "active":
                tools.append({
                    "id": activity.activity_id,
                    "type": "activity",
                    "title": getattr(activity, "ui_title", None) or "Activity",
                    "started_at": activity.started_at.astimezone().isoformat(),
                })
                
    return {"tools": tools}


@router.get("/api/codex/mcp-tools")
async def fetch_mcp_tools() -> dict[str, object]:
    try:
        tools = await codex_cli.get_mcp_tools()
        return {"servers": tools}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/codex/mcp-tools")
async def create_mcp_tool(request: AddMcpToolRequest) -> dict[str, object]:
    try:
        await codex_cli.add_mcp_tool(request.name, request.command, request.args)
        return {"success": True, "message": f"MCP tool '{request.name}' added."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/codex/mcp-tools/{tool_name}")
async def delete_mcp_tool(tool_name: str) -> dict[str, object]:
    try:
        await codex_cli.remove_mcp_tool(tool_name)
        return {"success": True, "message": f"MCP tool '{tool_name}' removed."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

webrtc_manager = None

@router.post("/webrtc/offer", response_model=dict[str, str])
async def webrtc_offer(
    request: WebRTCOfferRequest,
    orchestrator: VoiceSessionOrchestrator = Depends(get_voice_orchestrator)
) -> dict[str, str]:
    global webrtc_manager
    if webrtc_manager is None:
        from nextgen_voice_agent.transports.webrtc import WebRTCSessionManager
        webrtc_manager = WebRTCSessionManager(orchestrator)
        
    answer = await webrtc_manager.create_connection(
        session_id=request.session_id,
        offer_sdp=request.sdp,
        offer_type=request.type
    )
    return {"sdp": answer.sdp, "type": answer.type}

from fastapi import Request

@router.get("/api/settings")
async def get_settings_api() -> dict[str, Any]:
    from nextgen_voice_agent.config import load_toml_config
    return load_toml_config()

@router.post("/api/settings")
async def update_settings_api(payload: dict[str, Any]) -> dict[str, str]:
    from nextgen_voice_agent.config import load_toml_config, save_toml_config
    current = load_toml_config()
    current.update(payload)
    save_toml_config(current)
    return {"status": "ok"}

@router.post("/api/settings/secrets")
async def update_settings_secrets(request: Request, payload: dict[str, str | None]) -> dict[str, str]:
    from nextgen_voice_agent.config import set_secret
    for key, value in payload.items():
        if value is not None:
            set_secret(key, value)
            
    if hasattr(request.app.state, "stt_provider"):
        request.app.state.stt_provider.warm_up()
    if hasattr(request.app.state, "tts_provider"):
        request.app.state.tts_provider.warm_up()
        
    return {"status": "ok"}
