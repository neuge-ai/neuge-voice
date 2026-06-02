from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JsonRpcError(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int | str | None = None
    message: str | None = None
    data: Any = None


class JsonRpcResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: Any = None
    error: JsonRpcError | dict[str, Any] | None = None


class JsonRpcNotification(BaseModel):
    model_config = ConfigDict(extra="allow")

    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] | None = None


class ThreadRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    threadId: str | None = None


class TurnRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    turnId: str | None = None


class ThreadStartResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    thread: ThreadRef | None = None
    threadId: str | None = None
    id: str | None = None


class TurnStartResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    turn: TurnRef | None = None
    turnId: str | None = None
    id: str | None = None


class TurnItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str | None = None
    phase: str | None = None
    text: str | None = None


class TurnPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    items: list[TurnItem] = Field(default_factory=list)


class TurnNotificationParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    threadId: str | None = None
    turnId: str | None = None
    turn: TurnPayload | dict[str, Any] | None = None
    delta: str | None = None
    message: str | None = None
    error: str | None = None


KNOWN_NOTIFICATION_METHODS = frozenset({
    "turn/started",
    "turn/completed",
    "item/started",
    "item/completed",
    "plan/delta",
    "agentMessage/delta",
    "item/agentMessage/delta",
    "reasoningText/delta",
    "reasoningSummaryText/delta",
    "commandExecOutput/delta",
    "process/output/delta",
    "commandExecution/output/delta",
    "warning",
    "error",
})
