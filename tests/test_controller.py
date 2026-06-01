import asyncio

import pytest

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.models.events import EventType
from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.task import AmendTaskRequest, CancelTaskRequest, StartTaskRequest, TaskStatus
from nextgen_voice_agent.runtimes.fake import FakeRuntime


async def wait_for_status(controller: AgentController, task_id: str, status: TaskStatus) -> None:
    for _ in range(100):
        if controller.get_status(task_id).task.status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not reach {status}.")


async def wait_for_started_requests(runtime: FakeRuntime, count: int) -> None:
    for _ in range(100):
        if len(runtime.started_requests) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Runtime did not receive {count} start requests.")


@pytest.mark.asyncio
async def test_start_task_completes_with_fake_runtime() -> None:
    controller = AgentController(runtime=FakeRuntime())

    response = await controller.start_task(StartTaskRequest(task="Check this week's weather."))
    task_id = response.task.task_id

    assert response.acknowledgement == "I'll look into that in the background."
    await wait_for_status(controller, task_id, TaskStatus.COMPLETED)

    result = controller.read_result(task_id)
    assert result is not None
    assert result.status == RuntimeResultStatus.COMPLETED
    assert "Demo result" in result.spoken_answer


@pytest.mark.asyncio
async def test_amendment_increments_generation_and_ignores_old_result() -> None:
    runtime = FakeRuntime(delay_seconds=1.0)
    controller = AgentController(runtime=runtime)
    response = await controller.start_task(StartTaskRequest(task="Analyze the weather."))
    task_id = response.task.task_id
    await wait_for_started_requests(runtime, 1)

    amended = await controller.amend_task(task_id, AmendTaskRequest(amendment="Actually use Kolkata."))
    await wait_for_started_requests(runtime, 2)

    assert amended.task.generation == 2
    assert len(runtime.started_requests) == 2
    assert runtime.started_requests[-1].generation == 2
    assert "Actually use Kolkata." in runtime.started_requests[-1].prompt
    await controller._handle_result(
        RuntimeResult(
            task_id=task_id,
            generation=1,
            status=RuntimeResultStatus.COMPLETED,
            spoken_answer="Old answer.",
        )
    )

    assert controller.read_result(task_id) is None
    assert controller.event_hub.history[-1].event == EventType.STALE_RESULT_IGNORED
    await controller.cancel_task(task_id, CancelTaskRequest(reason="cleanup"))


@pytest.mark.asyncio
async def test_cancel_task_ignores_late_output() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    response = await controller.start_task(StartTaskRequest(task="Check an IPL stat."))
    task_id = response.task.task_id

    cancelled = await controller.cancel_task(task_id, CancelTaskRequest(reason="Stop that."))

    assert cancelled.status == TaskStatus.CANCELLED
    await controller._handle_result(
        RuntimeResult(
            task_id=task_id,
            generation=1,
            status=RuntimeResultStatus.COMPLETED,
            spoken_answer="Late answer.",
        )
    )
    assert controller.read_result(task_id) is None


@pytest.mark.asyncio
async def test_failed_runtime_result_clears_active_task() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    response = await controller.start_task(StartTaskRequest(task="Check weather."))
    task_id = response.task.task_id

    await controller._handle_result(
        RuntimeResult(
            task_id=task_id,
            generation=1,
            status=RuntimeResultStatus.FAILED,
            error="Codex completed without writing the structured result file.",
        )
    )

    assert controller.active_task_id is None
    assert controller.get_status(task_id).task.status == TaskStatus.FAILED
    result = controller.read_result(task_id)
    assert result is not None
    assert result.status == RuntimeResultStatus.FAILED
    assert result.error == "Codex completed without writing the structured result file."
    assert controller.event_hub.history[-1].event == EventType.TASK_FAILED
