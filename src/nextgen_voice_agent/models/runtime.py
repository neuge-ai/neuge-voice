from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuntimeResultStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_APPROVAL = "needs_approval"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class ApprovalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    description: str
    risk: str = "write"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeTaskRequest(BaseModel):
    task_id: str
    generation: int = Field(ge=1)
    prompt: str
    original_request: str
    context: str | None = None


class RuntimeAmendment(BaseModel):
    task_id: str
    generation: int = Field(ge=1)
    amendment: str


class RuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    generation: int = Field(ge=1)
    status: RuntimeResultStatus
    spoken_answer: str = ""
    technical_summary: str = ""
    sources_or_tools_used: list[str] = Field(default_factory=list)
    actions_requiring_approval: list[ApprovalAction] = Field(default_factory=list)
    followup_question: str | None = None
    error: str | None = None


class RuntimeProgress(BaseModel):
    task_id: str
    generation: int = Field(ge=1)
    message: str

