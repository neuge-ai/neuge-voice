from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from nextgen_voice_agent.models.runtime import ApprovalAction, RuntimeResult
from nextgen_voice_agent.models.task import Task, TaskStatus, utc_now


class EventType(StrEnum):
    TASK_STARTED = "task_started"
    TASK_AMENDED = "task_amended"
    TASK_CANCELLED = "task_cancelled"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    STALE_RESULT_IGNORED = "stale_result_ignored"
    APPROVAL_REQUESTED = "approval_requested"


class EventBase(BaseModel):
    event: EventType
    task_id: str
    generation: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class TaskStartedEvent(EventBase):
    event: Literal[EventType.TASK_STARTED] = EventType.TASK_STARTED
    task: Task
    acknowledgement: str


class TaskAmendedEvent(EventBase):
    event: Literal[EventType.TASK_AMENDED] = EventType.TASK_AMENDED
    amendment: str
    status: TaskStatus


class TaskCancelledEvent(EventBase):
    event: Literal[EventType.TASK_CANCELLED] = EventType.TASK_CANCELLED
    reason: str
    status: TaskStatus


class TaskProgressEvent(EventBase):
    event: Literal[EventType.TASK_PROGRESS] = EventType.TASK_PROGRESS
    message: str


class TaskCompletedEvent(EventBase):
    event: Literal[EventType.TASK_COMPLETED] = EventType.TASK_COMPLETED
    result: RuntimeResult


class TaskFailedEvent(EventBase):
    event: Literal[EventType.TASK_FAILED] = EventType.TASK_FAILED
    error: str


class StaleResultIgnoredEvent(EventBase):
    event: Literal[EventType.STALE_RESULT_IGNORED] = EventType.STALE_RESULT_IGNORED
    result_generation: int
    current_generation: int


class ApprovalRequestedEvent(EventBase):
    event: Literal[EventType.APPROVAL_REQUESTED] = EventType.APPROVAL_REQUESTED
    actions: list[ApprovalAction]


AgentEvent = Annotated[
    TaskStartedEvent
    | TaskAmendedEvent
    | TaskCancelledEvent
    | TaskProgressEvent
    | TaskCompletedEvent
    | TaskFailedEvent
    | StaleResultIgnoredEvent
    | ApprovalRequestedEvent,
    Field(discriminator="event"),
]

