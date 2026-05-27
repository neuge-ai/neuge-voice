from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_task_id() -> str:
    return f"task_{uuid4().hex[:12]}"


class TaskStatus(StrEnum):
    CREATED = "created"
    ACKNOWLEDGED = "acknowledged"
    QUEUED = "queued"
    RUNNING = "running"
    AMENDING = "amending"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER_CLARIFICATION = "waiting_for_user_clarification"
    COMPLETED = "completed"
    DELIVERED = "delivered"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    IGNORED_STALE_RESULT = "ignored_stale_result"


class TaskMode(StrEnum):
    RESEARCH = "research"
    COMPUTE = "compute"
    CODE = "code"
    DATA = "data"
    PERSONAL_DATA = "personal_data"
    GENERAL = "general"


class TaskUrgency(StrEnum):
    FAST = "fast"
    NORMAL = "normal"
    DEEP = "deep"


class AmendmentType(StrEnum):
    SOFT = "soft"
    HARD = "hard"
    CONSTRAINT = "constraint"
    FORMAT = "format"
    CORRECTION = "correction"


class RelationToActiveTask(StrEnum):
    UNRELATED = "unrelated"
    AMENDMENT = "amendment"
    CANCELLATION = "cancellation"
    STATUS_REQUEST = "status_request"
    CLARIFICATION_ANSWER = "clarification_answer"
    NEW_TASK = "new_task"


class ClassifiedInput(BaseModel):
    relation_to_active_task: RelationToActiveTask
    target_task_id: str | None = None
    action: Literal[
        "answer_directly",
        "amend_task",
        "cancel_task",
        "start_new_task",
        "ask_clarification",
        "report_status",
    ]
    confidence: float = Field(ge=0.0, le=1.0)


class Task(BaseModel):
    task_id: str = Field(default_factory=new_task_id)
    generation: int = Field(default=1, ge=1)
    status: TaskStatus = TaskStatus.CREATED
    type: str = "general"
    ui_title: str | None = None
    original_request: str
    latest_user_constraints: list[str] = Field(default_factory=list)
    codex_session_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    last_update_at: datetime = Field(default_factory=utc_now)
    cancellable: bool = True
    amendable: bool = True
    priority: TaskUrgency = TaskUrgency.NORMAL
    result_policy: str = "speak_when_ready"
    user_visible_status: str = "Starting task"

    def touch(self) -> None:
        self.last_update_at = utc_now()


class StartTaskRequest(BaseModel):
    task: str = Field(min_length=1)
    context: str | None = None
    ui_title: str | None = None
    mode: TaskMode = TaskMode.GENERAL
    urgency: TaskUrgency = TaskUrgency.NORMAL
    requires_user_approval: bool = False


class StartTaskResponse(BaseModel):
    task: Task
    acknowledgement: str


class AmendTaskRequest(BaseModel):
    amendment: str = Field(min_length=1)
    amendment_type: AmendmentType = AmendmentType.SOFT


class CancelTaskRequest(BaseModel):
    reason: str = "User cancelled the task."


class CancelTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    message: str


class TaskStatusResponse(BaseModel):
    task: Task
    progress: str | None = None

