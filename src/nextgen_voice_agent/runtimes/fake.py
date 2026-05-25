from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from nextgen_voice_agent.models.runtime import (
    RuntimeAmendment,
    RuntimeProgress,
    RuntimeResult,
    RuntimeResultStatus,
    RuntimeTaskRequest,
)
from nextgen_voice_agent.runtimes.base import RuntimeMessage, TaskRuntime


class FakeRuntime(TaskRuntime):
    """Deterministic runtime for tests and local demos."""

    def __init__(self, delay_seconds: float = 0.01) -> None:
        self.delay_seconds = delay_seconds
        self.cancelled_task_ids: set[str] = set()
        self.amendments: list[RuntimeAmendment] = []
        self.started_requests: list[RuntimeTaskRequest] = []

    async def start_task(self, request: RuntimeTaskRequest) -> AsyncIterator[RuntimeMessage]:
        self.started_requests.append(request)
        self.cancelled_task_ids.discard(request.task_id)
        yield RuntimeProgress(
            task_id=request.task_id,
            generation=request.generation,
            message="Fake runtime accepted the task.",
        )
        await asyncio.sleep(self.delay_seconds)

        if request.task_id in self.cancelled_task_ids:
            yield RuntimeResult(
                task_id=request.task_id,
                generation=request.generation,
                status=RuntimeResultStatus.CANCELLED,
                spoken_answer="The task was cancelled.",
                technical_summary="Fake runtime observed cancellation.",
            )
            return

        yield RuntimeProgress(
            task_id=request.task_id,
            generation=request.generation,
            message="Fake runtime is preparing a demo result.",
        )
        await asyncio.sleep(self.delay_seconds)

        yield RuntimeResult(
            task_id=request.task_id,
            generation=request.generation,
            status=RuntimeResultStatus.COMPLETED,
            spoken_answer=f"Demo result for: {request.original_request}",
            technical_summary="This result came from FakeRuntime; no external model was called.",
            sources_or_tools_used=["fake_runtime"],
        )

    async def amend_task(self, amendment: RuntimeAmendment) -> None:
        self.amendments.append(amendment)

    async def cancel_task(self, task_id: str) -> None:
        self.cancelled_task_ids.add(task_id)
