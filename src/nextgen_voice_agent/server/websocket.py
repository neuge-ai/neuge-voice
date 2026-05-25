from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.server.dependencies import get_controller

router = APIRouter()


@router.websocket("/events")
async def task_events(
    websocket: WebSocket,
    controller: AgentController = Depends(get_controller),
) -> None:
    await websocket.accept()
    try:
        async for event in controller.events():
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return

