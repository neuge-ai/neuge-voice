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
from nextgen_voice_agent.server.codex_app_server import CodexAppServerService
from nextgen_voice_agent.server.dependencies import (
    get_codex_app_server_service,
    get_controller,
    get_tts_provider,
    get_voice_orchestrator,
    get_webrtc_session_manager,
)
from nextgen_voice_agent.server.realtime import build_realtime_session_config, create_openai_realtime_client_secret
from nextgen_voice_agent.transports.webrtc import WebRTCSessionManager
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator
from nextgen_voice_agent.voice.realtime_dispatch import (
    call_realtime_codex_tool,
    call_realtime_native_tool,
    is_realtime_codex_tool,
    is_realtime_native_tool,
)
from nextgen_voice_agent.voice.tts import TtsProvider, TtsProviderError, TtsRequest, TtsResult

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
        if is_realtime_codex_tool(tool_name):
            return await call_realtime_codex_tool(tool_name, args, controller)
        if is_realtime_native_tool(tool_name):
            return await call_realtime_native_tool(tool_name, args, orchestrator)
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
    if not orchestrator.has_session(session_id):
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
            
    tools.extend(orchestrator.list_active_native_tools())
    return {"tools": tools}


@router.get("/api/codex/mcp-tools")
async def fetch_mcp_tools(
    codex_service: CodexAppServerService = Depends(get_codex_app_server_service),
) -> dict[str, object]:
    try:
        tools = await codex_service.get_mcp_tools()
        return {"servers": tools}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/codex/mcp-tools")
async def create_mcp_tool(
    request: AddMcpToolRequest,
    codex_service: CodexAppServerService = Depends(get_codex_app_server_service),
) -> dict[str, object]:
    try:
        await codex_service.add_mcp_tool(request.name, request.command, request.args)
        return {"success": True, "message": f"MCP tool '{request.name}' added."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/codex/mcp-tools/{tool_name}")
async def delete_mcp_tool(
    tool_name: str,
    codex_service: CodexAppServerService = Depends(get_codex_app_server_service),
) -> dict[str, object]:
    try:
        await codex_service.remove_mcp_tool(tool_name)
        return {"success": True, "message": f"MCP tool '{tool_name}' removed."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/webrtc/offer", response_model=dict[str, str])
async def webrtc_offer(
    request: WebRTCOfferRequest,
    webrtc_manager: WebRTCSessionManager = Depends(get_webrtc_session_manager),
) -> dict[str, str]:
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
