from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import timedelta

from nextgen_voice_agent.agent.prompts import build_codex_task_prompt
from nextgen_voice_agent.config import Settings, get_settings
from nextgen_voice_agent.models.events import (
    AgentEvent,
    ApprovalRequestedEvent,
    StaleResultIgnoredEvent,
    TaskAmendedEvent,
    TaskCancelledEvent,
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskProgressEvent,
    TaskStartedEvent,
)
from nextgen_voice_agent.models.runtime import (
    RuntimeAmendment,
    RuntimeProgress,
    RuntimeResult,
    RuntimeResultStatus,
    RuntimeTaskRequest,
)
from nextgen_voice_agent.models.task import (
    AmendTaskRequest,
    CancelTaskRequest,
    CancelTaskResponse,
    StartTaskRequest,
    StartTaskResponse,
    Task,
    TaskMode,
    TaskStatus,
    TaskStatusResponse,
    utc_now,
)
from nextgen_voice_agent.runtimes.base import TaskRuntime


class TaskNotFoundError(ValueError):
    pass


class TaskConflictError(ValueError):
    pass


class EventHub:
    def __init__(self, history_limit: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[AgentEvent]] = set()
        self.history: list[AgentEvent] = []
        self._history_limit = history_limit

    async def publish(self, event: AgentEvent) -> None:
        self.history.append(event)
        if len(self.history) > self._history_limit:
            self.history = self.history[-self._history_limit :]
        for subscriber in list(self._subscribers):
            await subscriber.put(event)

    async def subscribe(self) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            for event in self.history[-50:]:
                yield event
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)


class AgentController:
    def __init__(
        self,
        runtime: TaskRuntime,
        event_hub: EventHub | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self.runtime = runtime
        self.event_hub = event_hub or EventHub(history_limit=self._settings.event_history_limit)
        self.tasks: dict[str, Task] = {}
        self.results: dict[str, RuntimeResult] = {}
        self.active_task_id: str | None = None
        self._workers: dict[str, asyncio.Task[None]] = {}

    async def start_task(self, request: StartTaskRequest) -> StartTaskResponse:
        if self.active_task_id is not None:
            active = self.tasks.get(self.active_task_id)
            if active and active.status in {TaskStatus.RUNNING, TaskStatus.AMENDING, TaskStatus.WAITING_FOR_APPROVAL}:
                raise TaskConflictError("A Codex task is already active.")

        task = Task(
            original_request=request.task,
            status=TaskStatus.ACKNOWLEDGED,
            type=request.mode.value,
            ui_title=request.ui_title,
            priority=request.urgency,
            user_visible_status="Starting background task",
        )
        acknowledgement = self._acknowledge(request.task)
        self.tasks[task.task_id] = task
        self.active_task_id = task.task_id

        await self.event_hub.publish(
            TaskStartedEvent(
                task_id=task.task_id,
                generation=task.generation,
                task=task,
                acknowledgement=acknowledgement,
            )
        )

        prompt = build_codex_task_prompt(task, request)
        runtime_request = RuntimeTaskRequest(
            task_id=task.task_id,
            generation=task.generation,
            prompt=prompt,
            original_request=request.task,
            context=request.context,
        )
        task.status = TaskStatus.RUNNING
        task.user_visible_status = "Working on the task"
        task.touch()
        self._workers[task.task_id] = asyncio.create_task(self._run_task(runtime_request))
        return StartTaskResponse(task=task, acknowledgement=acknowledgement)

    async def amend_task(self, task_id: str, request: AmendTaskRequest) -> TaskStatusResponse:
        task = self._get_task(task_id)
        if not task.amendable or task.status in {TaskStatus.CANCELLED, TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise TaskConflictError(f"Task {task_id} cannot be amended from status {task.status}.")

        task.generation += 1
        task.status = TaskStatus.AMENDING
        task.latest_user_constraints.append(request.amendment)
        task.user_visible_status = f"Applying amendment: {request.amendment}"
        task.touch()

        if self.runtime.supports_live_amendments:
            await self.runtime.amend_task(
                RuntimeAmendment(task_id=task.task_id, generation=task.generation, amendment=request.amendment)
            )
        else:
            await self._cancel_worker_for_restart(task.task_id)

        await self.event_hub.publish(
            TaskAmendedEvent(
                task_id=task.task_id,
                generation=task.generation,
                amendment=request.amendment,
                status=task.status,
            )
        )

        task.status = TaskStatus.RUNNING
        task.user_visible_status = "Continuing with the latest amendment"
        task.touch()
        if not self.runtime.supports_live_amendments:
            restart_request = StartTaskRequest(
                task=task.original_request,
                context="User amendments:\n" + "\n".join(task.latest_user_constraints),
                mode=TaskMode(task.type),
                urgency=task.priority,
            )
            runtime_request = RuntimeTaskRequest(
                task_id=task.task_id,
                generation=task.generation,
                prompt=build_codex_task_prompt(task, restart_request),
                original_request=task.original_request,
                context=restart_request.context,
            )
            self._workers[task.task_id] = asyncio.create_task(self._run_task(runtime_request))
        return TaskStatusResponse(task=task, progress=task.user_visible_status)

    async def _cancel_worker_for_restart(self, task_id: str) -> None:
        await self.runtime.cancel_task(task_id)
        worker = self._workers.get(task_id)
        if worker and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def cancel_task(self, task_id: str, request: CancelTaskRequest) -> CancelTaskResponse:
        task = self._get_task(task_id)
        task.status = TaskStatus.CANCELLING
        task.user_visible_status = "Cancelling task"
        task.touch()
        await self.runtime.cancel_task(task_id)

        worker = self._workers.get(task_id)
        if worker and not worker.done():
            worker.cancel()

        task.status = TaskStatus.CANCELLED
        task.user_visible_status = "Task cancelled"
        task.touch()
        if self.active_task_id == task_id:
            self.active_task_id = None

        await self.event_hub.publish(
            TaskCancelledEvent(
                task_id=task.task_id,
                generation=task.generation,
                reason=request.reason,
                status=task.status,
            )
        )
        self._prune_completed_tasks()
        return CancelTaskResponse(task_id=task_id, status=task.status, message="Task cancelled.")

    def get_status(self, task_id: str) -> TaskStatusResponse:
        task = self._get_task(task_id)
        return TaskStatusResponse(task=task, progress=task.user_visible_status)

    def read_result(self, task_id: str) -> RuntimeResult | None:
        self._get_task(task_id)
        return self.results.get(task_id)

    async def approve_action(self, task_id: str, action_id: str, approved: bool) -> TaskStatusResponse:
        task = self._get_task(task_id)
        task.user_visible_status = f"Approval {'granted' if approved else 'denied'} for {action_id}"
        task.status = TaskStatus.RUNNING if approved else TaskStatus.WAITING_FOR_APPROVAL
        task.touch()
        return TaskStatusResponse(task=task, progress=task.user_visible_status)

    async def events(self) -> AsyncIterator[AgentEvent]:
        async for event in self.event_hub.subscribe():
            yield event

    async def _run_task(self, request: RuntimeTaskRequest) -> None:
        try:
            async for message in self.runtime.start_task(request):
                if isinstance(message, RuntimeProgress):
                    await self._handle_progress(message)
                else:
                    await self._handle_result(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logging.exception("Task worker crashed for %s", request.task_id)
            task = self.tasks.get(request.task_id)
            if task is None:
                return
            error = str(exc)
            self.results[request.task_id] = RuntimeResult(
                task_id=request.task_id,
                generation=request.generation,
                status=RuntimeResultStatus.FAILED,
                error=error,
                technical_summary=error,
            )
            task.status = TaskStatus.FAILED
            task.user_visible_status = error or "Task failed"
            task.touch()
            if self.active_task_id == request.task_id:
                self.active_task_id = None
            await self.event_hub.publish(
                TaskFailedEvent(
                    task_id=task.task_id,
                    generation=task.generation,
                    error=task.user_visible_status,
                )
            )
        finally:
            self._workers.pop(request.task_id, None)

    async def _handle_progress(self, progress: RuntimeProgress) -> None:
        task = self.tasks.get(progress.task_id)
        if task is None or self._is_inactive(task):
            return
        if progress.generation != task.generation:
            await self._publish_stale(progress.task_id, progress.generation, task.generation)
            return
        task.user_visible_status = progress.message
        task.last_update_at = utc_now()
        await self.event_hub.publish(
            TaskProgressEvent(
                task_id=task.task_id,
                generation=task.generation,
                message=progress.message,
            )
        )

    async def _handle_result(self, result: RuntimeResult) -> None:
        task = self.tasks.get(result.task_id)
        if task is None:
            return
        if self._is_inactive(task):
            return
        if result.generation != task.generation:
            await self._publish_stale(result.task_id, result.generation, task.generation)
            return

        if result.status == RuntimeResultStatus.NEEDS_APPROVAL:
            task.status = TaskStatus.WAITING_FOR_APPROVAL
            task.user_visible_status = "Waiting for approval"
            task.touch()
            await self.event_hub.publish(
                ApprovalRequestedEvent(
                    task_id=task.task_id,
                    generation=task.generation,
                    actions=result.actions_requiring_approval,
                )
            )
            return

        if result.status == RuntimeResultStatus.NEEDS_CLARIFICATION:
            task.status = TaskStatus.WAITING_FOR_USER_CLARIFICATION
            task.user_visible_status = result.followup_question or "Waiting for clarification"
            task.touch()
            return

        if result.status == RuntimeResultStatus.FAILED:
            self.results[task.task_id] = result
            task.status = TaskStatus.FAILED
            task.user_visible_status = result.error or "Task failed"
            task.touch()
            if self.active_task_id == task.task_id:
                self.active_task_id = None
            await self.event_hub.publish(
                TaskFailedEvent(
                    task_id=task.task_id,
                    generation=task.generation,
                    error=task.user_visible_status,
                )
            )
            self._prune_completed_tasks()
            return

        if result.status == RuntimeResultStatus.CANCELLED:
            if self.active_task_id == task.task_id:
                self.active_task_id = None
            return

        self.results[task.task_id] = result
        task.status = TaskStatus.COMPLETED
        task.user_visible_status = "Result ready"
        task.touch()
        if self.active_task_id == task.task_id:
            self.active_task_id = None
        await self.event_hub.publish(
            TaskCompletedEvent(
                task_id=task.task_id,
                generation=task.generation,
                result=result,
            )
        )
        self._prune_completed_tasks()

    def _prune_completed_tasks(self) -> None:
        terminal = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
            TaskStatus.IGNORED_STALE_RESULT,
        }
        completed = [task for task in self.tasks.values() if task.status in terminal]
        if not completed:
            return
        ttl = timedelta(minutes=self._settings.completed_task_ttl_minutes)
        cutoff = utc_now() - ttl
        completed.sort(key=lambda task: task.last_update_at, reverse=True)
        keep_ids: set[str] = set()
        for task in completed[: self._settings.max_completed_tasks]:
            if task.last_update_at >= cutoff:
                keep_ids.add(task.task_id)
        if self.active_task_id:
            keep_ids.add(self.active_task_id)
        for task_id in list(self.tasks):
            if task_id not in keep_ids:
                self.tasks.pop(task_id, None)
                self.results.pop(task_id, None)

    async def _publish_stale(self, task_id: str, result_generation: int, current_generation: int) -> None:
        task = self.tasks.get(task_id)
        if task is not None:
            task.user_visible_status = "Ignored stale runtime output"
            task.touch()
        await self.event_hub.publish(
            StaleResultIgnoredEvent(
                task_id=task_id,
                generation=current_generation,
                result_generation=result_generation,
                current_generation=current_generation,
            )
        )

    def _get_task(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task {task_id} was not found.")
        return task

    @staticmethod
    def _is_inactive(task: Task) -> bool:
        return task.status in {TaskStatus.CANCELLED, TaskStatus.SUPERSEDED, TaskStatus.IGNORED_STALE_RESULT}

    @staticmethod
    def _acknowledge(task_text: str) -> str:
        return "I'll look into that in the background."
