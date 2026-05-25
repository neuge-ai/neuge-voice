from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from nextgen_voice_agent.models.runtime import RuntimeAmendment, RuntimeProgress, RuntimeResult, RuntimeTaskRequest


RuntimeMessage = RuntimeProgress | RuntimeResult


class TaskRuntime(ABC):
    supports_live_amendments = False

    @abstractmethod
    async def start_task(self, request: RuntimeTaskRequest) -> AsyncIterator[RuntimeMessage]:
        """Start work and yield progress/result messages."""

    @abstractmethod
    async def amend_task(self, amendment: RuntimeAmendment) -> None:
        """Send an amendment to a running task when supported."""

    @abstractmethod
    async def cancel_task(self, task_id: str) -> None:
        """Cancel a running task when supported."""
