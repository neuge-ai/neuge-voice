import pytest
from pydantic import ValidationError

from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.task import StartTaskRequest, TaskStatus


def test_start_task_request_requires_task_text() -> None:
    with pytest.raises(ValidationError):
        StartTaskRequest(task="")


def test_runtime_result_validates_status_and_generation() -> None:
    result = RuntimeResult(
        task_id="task_demo",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="Done.",
    )

    assert result.status == RuntimeResultStatus.COMPLETED
    assert result.generation == 1


def test_task_status_values_match_mvp_lifecycle() -> None:
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.IGNORED_STALE_RESULT.value == "ignored_stale_result"

