from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nextgen_voice_agent.agent.controller import AgentController, TaskConflictError, TaskNotFoundError
from nextgen_voice_agent.models.runtime import RuntimeResult
from nextgen_voice_agent.models.realtime import RealtimeSessionConfig
from nextgen_voice_agent.models.task import (
    AmendTaskRequest,
    CancelTaskRequest,
    CancelTaskResponse,
    StartTaskRequest,
    StartTaskResponse,
    TaskStatusResponse,
)
from nextgen_voice_agent.models.voice import VoiceEvent
from nextgen_voice_agent.config import get_settings
from nextgen_voice_agent.server.dependencies import get_controller, get_tts_provider, get_voice_orchestrator
from nextgen_voice_agent.server.realtime import build_realtime_session_config, create_openai_realtime_client_secret
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator
from nextgen_voice_agent.voice.tts import TtsProvider, TtsProviderError, TtsRequest, TtsResult

router = APIRouter()


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
) -> dict[str, object]:
    args = request.arguments
    try:
        if tool_name == "start_codex_task":
            result = await controller.start_task(
                StartTaskRequest(task=str(args["task"]), context=args.get("context") if args.get("context") else None)
            )
            return result.model_dump(mode="json")
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

